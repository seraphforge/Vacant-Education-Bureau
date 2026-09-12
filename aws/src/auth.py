"""認證與資料範圍控管。

/api/secure/* 這些路由在 API Gateway 就掛了 JWT authorizer，
所以進到 Lambda 的 event 一定已經通過簽章與過期驗證，claims 可以直接信任。
公開路由不會有 claims。

權限邊界的原則：**縣市範圍一律由 token 決定，永遠不信任前端送來的 county**，
否則改個網址就能看別的縣市的資料。staff_profile 這張表只用於顯示名稱與 fallback。
"""

import re


def get_claims(event):
    """取出已驗證的 JWT claims；公開路由回 {}。"""
    return (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )


def get_identity(event):
    """把 claims 整理成好用的形式。沒登入回 None。"""
    claims = get_claims(event)
    if not claims:
        return None

    # cognito:groups 在 claims 裡可能是 list，也可能是 "[admin]" 這種字串
    raw_groups = claims.get("cognito:groups") or []
    if isinstance(raw_groups, str):
        raw_groups = [g for g in re.split(r"[\[\]\s,]+", raw_groups) if g]

    return {
        "sub": claims.get("sub"),
        "username": claims.get("cognito:username") or claims.get("sub"),
        "county": (claims.get("custom:county") or "").strip(),
        "agency": (claims.get("custom:agency") or "").strip(),
        "groups": raw_groups,
        "isAdmin": "admin" in raw_groups,
    }


def scoped_county(identity, requested=""):
    """回傳這個使用者實際可以查的縣市。

    - admin 群組：可查全國（回 None 代表不加縣市條件），
      也可以指定某個縣市來檢視。
    - 一般人員：一律鎖回自己的 custom:county，
      **完全忽略前端送來的值**。
    """
    if identity["isAdmin"]:
        return (requested or "").strip() or None
    return identity["county"] or None


def upsert_staff_profile(cur, identity):
    """登入時把 token 的身分寫進 staff_profile（JIT provisioning），並回傳該列。

    display_name 由人工在 DB 設定（回覆家長時的署名），所以這裡只在
    第一次建檔時寫入，之後不覆蓋。county / agency 每次以 token 為準同步，
    因為 token 才是權威來源。
    """
    if not identity.get("sub"):
        return None
    cur.execute(
        """INSERT INTO staff_profile
               (cognito_sub, username, county, agency, role, last_login_at)
           VALUES (%s, %s, %s, %s, %s, UTC_TIMESTAMP())
           ON DUPLICATE KEY UPDATE
               username = VALUES(username),
               county   = VALUES(county),
               agency   = VALUES(agency),
               role     = VALUES(role),
               last_login_at = UTC_TIMESTAMP()""",
        (
            identity["sub"],
            identity["username"],
            identity["county"] or None,
            identity["agency"] or None,
            "admin" if identity["isAdmin"] else "staff",
        ),
    )
    cur.execute(
        "SELECT cognito_sub, username, display_name, county, agency, role "
        "FROM staff_profile WHERE cognito_sub = %s",
        (identity["sub"],),
    )
    return cur.fetchone()


def staff_display_name(profile, identity):
    """回覆家長時的署名：優先用 DB 的 display_name，退回 agency / username。"""
    parts = []
    agency = (profile or {}).get("agency") or identity.get("agency")
    name = (profile or {}).get("display_name")
    if agency:
        parts.append(agency)
    parts.append(name or identity.get("username") or "承辦人")
    return " ".join(parts)
