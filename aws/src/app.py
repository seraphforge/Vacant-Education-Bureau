"""
幼兒園查詢 API — 單一 Lambda 處理所有路由（HTTP API payload v2.0）。

資料現況：kindergarten 表只保留最新學年度（114）一份，等同「一校一列」，
id 即為學校身分。kindergarten_punishment 透過外鍵 kindergarten_id 指回 kindergarten(id)。

路由：

公開（不需登入）：
  GET /api/health                          健康檢查（會實際 ping DB）
  GET /api/counties                        縣市清單
  GET /api/academic-years                  學年度清單（目前只有 114）
  GET /api/kindergartens                   查詢清單（縣市 + 名稱 LIKE + 分頁）
  GET /api/punishments                     裁罰紀錄查詢（縣市/鄉鎮/名稱/日期/罰鍰 + 分頁）
  GET /api/kindergartens/{id}/punishments  單一幼兒園的裁罰紀錄

需登入（Cognito ID token，API Gateway 已先驗過簽章與過期）：
  GET /api/secure/me                       我是誰 / 我能看哪個範圍
  GET /api/secure/kindergartens            同上查詢，但縣市鎖在使用者權限內
  GET /api/secure/punishments              同裁罰查詢，但縣市鎖在使用者權限內

/api/kindergartens 支援的 query string：
  county        縣市名稱，例如 新北市（可省略 = 全部）
  name          學校名稱關鍵字，用 LIKE %name% 比對
  ownership     公立 / 私立
  academicYear  學年度（可省略；目前資料只有 114，保留此參數僅為相容）
  page          第幾頁，從 1 開始，預設 1
  pageSize      每頁筆數，預設 20，最大 100
  sortBy/sortDir 排序（白名單欄位）

/api/punishments 支援的 query string：
  county        縣市名稱，例如 新北市（可省略 = 全部）
  district      鄉鎮市區，例如 板橋區（可省略）
  name          學校名稱關鍵字，用 LIKE %name% 比對
  hasFine       true 只回有罰鍰金額者
  dateFrom      處分日期起（YYYY-MM-DD）
  dateTo        處分日期迄（YYYY-MM-DD）
  page/pageSize 分頁，pageSize 上限 100
  sortBy/sortDir 排序（白名單欄位：punish_date/fine_amount/school_name/district/id）
"""

import json
import os
import re

import pymysql

DB_HOST = os.environ["DB_HOST"]
DB_PORT = int(os.environ.get("DB_PORT", "3306"))
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
DB_NAME = os.environ.get("DB_NAME", "readme")

# 只允許排序這些欄位，避免 SQL injection
SORTABLE = {
    "id": "id",
    "school_name": "school_name",
    "county": "county",
    "district": "district",
    "academic_year": "academic_year",
    "ownership": "ownership",
}

# 裁罰查詢可排序的欄位白名單
PUNISH_SORTABLE = {
    "id": "p.id",
    "punish_date": "p.punish_date",
    "fine_amount": "p.fine_amount",
    "school_name": "p.school_name",
    "district": "p.district",
}

# Lambda 容器重用時共用連線，省下每次重新握手的時間
_conn = None


def get_conn():
    global _conn
    if _conn is not None:
        try:
            _conn.ping(reconnect=True)
            return _conn
        except Exception:
            _conn = None
    _conn = pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5,
        read_timeout=10,
        write_timeout=10,
        autocommit=True,
    )
    return _conn


def respond(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, ensure_ascii=False, default=str),
    }


def to_int(value, default, lo, hi):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


# ---------------------------------------------------------------------------
# 認證
#
# /api/secure/* 這些路由在 API Gateway 就掛了 JWT authorizer，
# 所以進到這裡的 event 一定已經通過簽章與過期驗證，claims 可以直接信任。
# 公開路由（/api/kindergartens 等）不會有 claims。
# ---------------------------------------------------------------------------
def get_claims(event):
    """取出已驗證的 JWT claims；公開路由回 {}。"""
    return (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )


def get_identity(event):
    """把 claims 整理成好用的形式。"""
    claims = get_claims(event)
    if not claims:
        return None

    # cognito:groups 在 claims 裡可能是 list，也可能是 "[admin]" 這種字串
    raw_groups = claims.get("cognito:groups") or []
    if isinstance(raw_groups, str):
        raw_groups = [g for g in re.split(r"[\[\]\s,]+", raw_groups) if g]

    return {
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
      **完全忽略前端送來的值**，否則改個網址就能看別的縣市。
    """
    if identity["isAdmin"]:
        return (requested or "").strip() or None
    return identity["county"] or None


def handle_health(cur):
    cur.execute("SELECT COUNT(*) AS total FROM kindergarten")
    return respond(200, {"ok": True, "total": cur.fetchone()["total"]})


def handle_counties(cur):
    # county 已在資料庫端清乾淨（不再有 "[01]" 前綴），
    # 所以直接 GROUP BY 欄位本身，可以吃到 idx_county 索引。
    cur.execute(
        "SELECT county, COUNT(*) AS count FROM kindergarten "
        "WHERE county IS NOT NULL AND county <> '' "
        "GROUP BY county ORDER BY count DESC"
    )
    return respond(200, {"items": cur.fetchall()})


def handle_academic_years(cur):
    cur.execute(
        "SELECT academic_year, COUNT(*) AS count FROM kindergarten "
        "GROUP BY academic_year ORDER BY CAST(academic_year AS UNSIGNED) DESC"
    )
    return respond(200, {"items": cur.fetchall()})


def handle_kindergartens(cur, qs, force_county=None):
    """查詢幼兒園清單。

    force_county 不是 None 時，縣市條件一律用它，忽略 query string，
    這是給 /api/secure/* 做資料範圍控管用的。
    """
    county = (qs.get("county") or "").strip() if force_county is None else force_county
    name = (qs.get("name") or "").strip()
    ownership = (qs.get("ownership") or "").strip()
    year = (qs.get("academicYear") or "").strip()
    page = to_int(qs.get("page"), 1, 1, 10000)
    page_size = to_int(qs.get("pageSize"), 20, 1, 100)

    sort_by = SORTABLE.get((qs.get("sortBy") or "").strip(), "id")
    sort_dir = "DESC" if (qs.get("sortDir") or "").lower() == "desc" else "ASC"

    # 資料只保留最新學年度（114）一份，一校一列，所以不再強制帶學年度。
    # academicYear 仍可當選填過濾條件（相容舊呼叫）。

    where = []
    params = []

    if year:
        where.append("academic_year = %s")
        params.append(year)
    if county:
        # 直接比對欄位（不套函式），才用得到 idx_county 索引
        where.append("county = %s")
        params.append(county)
    if name:
        where.append("school_name LIKE %s")
        params.append(f"%{name}%")
    if ownership in ("公立", "私立"):
        where.append("ownership = %s")
        params.append(ownership)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    cur.execute(f"SELECT COUNT(*) AS total FROM kindergarten {where_sql}", params)
    total = cur.fetchone()["total"]

    offset = (page - 1) * page_size
    cur.execute(
        f"""SELECT id, academic_year, code, school_name, ownership,
                   county, district, address, phone
            FROM kindergarten
            {where_sql}
            ORDER BY {sort_by} {sort_dir}, id ASC
            LIMIT %s OFFSET %s""",
        params + [page_size, offset],
    )
    items = cur.fetchall()

    return respond(
        200,
        {
            "items": items,
            "total": total,
            "page": page,
            "pageSize": page_size,
            "academicYear": year,
            # 實際生效的縣市範圍（受保護端點會是使用者被鎖定的縣市）
            "county": county or None,
        },
    )


# ---------------------------------------------------------------------------
# 裁罰紀錄查詢
#
# 資料表 kindergarten_punishment（來源：全國教保資訊網裁罰紀錄查詢，目前只有新北市）。
# 透過外鍵 kindergarten_id 指回 kindergarten(id)；已停業/查無的學校 kindergarten_id 為 NULL。
# ---------------------------------------------------------------------------
def _punish_filters(qs, force_county=None):
    """組出裁罰查詢的 WHERE 子句與參數（給清單與 handle 共用）。"""
    county = (qs.get("county") or "").strip() if force_county is None else force_county
    district = (qs.get("district") or "").strip()
    name = (qs.get("name") or "").strip()
    has_fine = (qs.get("hasFine") or "").lower() in ("1", "true", "yes")
    date_from = (qs.get("dateFrom") or "").strip()
    date_to = (qs.get("dateTo") or "").strip()

    where = []
    params = []
    if county:
        where.append("p.county = %s")
        params.append(county)
    if district:
        where.append("p.district = %s")
        params.append(district)
    if name:
        where.append("p.school_name LIKE %s")
        params.append(f"%{name}%")
    if has_fine:
        where.append("p.fine_amount IS NOT NULL AND p.fine_amount > 0")
    # 只接受 YYYY-MM-DD，避免奇怪輸入
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_from):
        where.append("p.punish_date >= %s")
        params.append(date_from)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_to):
        where.append("p.punish_date <= %s")
        params.append(date_to)
    return where, params, (county or None)


def handle_punishments(cur, qs, force_county=None):
    """裁罰紀錄清單查詢（含分頁、排序、彙總罰鍰）。

    force_county 不是 None 時，縣市條件一律用它，忽略 query string，
    給 /api/secure/* 做資料範圍控管用。
    """
    where, params, county = _punish_filters(qs, force_county)
    page = to_int(qs.get("page"), 1, 1, 10000)
    page_size = to_int(qs.get("pageSize"), 20, 1, 100)
    sort_by = PUNISH_SORTABLE.get((qs.get("sortBy") or "").strip(), "p.punish_date")
    sort_dir = "ASC" if (qs.get("sortDir") or "").lower() == "asc" else "DESC"

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    cur.execute(
        f"SELECT COUNT(*) AS total, COALESCE(SUM(p.fine_amount),0) AS totalFine "
        f"FROM kindergarten_punishment p {where_sql}",
        params,
    )
    agg = cur.fetchone()

    offset = (page - 1) * page_size
    cur.execute(
        f"""SELECT p.id, p.kindergarten_id, p.county, p.district, p.school_name,
                   p.ownership, p.op_status, p.punish_date, p.school_name_at_time,
                   p.doc_no, p.legal_basis, p.violated_rule, p.person,
                   p.content, p.fine_amount,
                   k.address, k.phone
            FROM kindergarten_punishment p
            LEFT JOIN kindergarten k ON k.id = p.kindergarten_id
            {where_sql}
            ORDER BY {sort_by} {sort_dir}, p.id ASC
            LIMIT %s OFFSET %s""",
        params + [page_size, offset],
    )
    items = cur.fetchall()

    return respond(
        200,
        {
            "items": items,
            "total": agg["total"],
            "totalFine": int(agg["totalFine"]),
            "page": page,
            "pageSize": page_size,
            "county": county,
        },
    )


def handle_kindergarten_punishments(cur, kg_id):
    """單一幼兒園（kindergarten.id）的所有裁罰紀錄。"""
    try:
        kg_id = int(kg_id)
    except (TypeError, ValueError):
        return respond(400, {"message": "無效的 id"})

    cur.execute(
        "SELECT id, school_name, county, district, address, phone FROM kindergarten WHERE id = %s",
        (kg_id,),
    )
    school = cur.fetchone()
    if not school:
        return respond(404, {"message": f"查無此幼兒園 id={kg_id}"})

    cur.execute(
        """SELECT id, punish_date, school_name_at_time, doc_no, legal_basis,
                  violated_rule, person, content, fine_amount
           FROM kindergarten_punishment
           WHERE kindergarten_id = %s
           ORDER BY punish_date DESC, id ASC""",
        (kg_id,),
    )
    records = cur.fetchall()
    total_fine = sum((r["fine_amount"] or 0) for r in records)

    return respond(
        200,
        {
            "kindergarten": school,
            "records": records,
            "count": len(records),
            "totalFine": total_fine,
        },
    )


# ---------------------------------------------------------------------------
# 受保護端點（/api/secure/*）
#
# 目前只放「確認登入與權限範圍」用的兩支，
# 家長回報 / 財報 / 風險分析的實作照 handle_secure_kindergartens 的樣子加即可：
# 從 identity 拿到 county，用 scoped_county() 決定範圍，再進 SQL。
# ---------------------------------------------------------------------------
def handle_secure_me(identity):
    """回傳「我是誰、我能看哪個範圍」，前端登入後用來顯示身分。"""
    return respond(
        200,
        {
            "username": identity["username"],
            "county": identity["county"] or None,
            "agency": identity["agency"] or None,
            "groups": identity["groups"],
            "isAdmin": identity["isAdmin"],
            "scope": "全國" if identity["isAdmin"] else (identity["county"] or "未設定縣市"),
        },
    )


def handle_secure_kindergartens(cur, qs, identity):
    """跟公開的查詢同一份資料，但縣市被鎖在使用者權限內。

    這支的用途是示範 row-level 範圍控管怎麼做，
    之後家長回報 / 財報 / 風險分析都照這個模式寫。
    """
    county = scoped_county(identity, qs.get("county", ""))
    if county is None and not identity["isAdmin"]:
        return respond(403, {"message": "這個帳號沒有設定縣市（custom:county），請聯絡管理者"})
    return handle_kindergartens(cur, qs, force_county=county or "")


def handle_secure_punishments(cur, qs, identity):
    """裁罰查詢，但縣市鎖在使用者權限範圍內（同 handle_secure_kindergartens 模式）。"""
    county = scoped_county(identity, qs.get("county", ""))
    if county is None and not identity["isAdmin"]:
        return respond(403, {"message": "這個帳號沒有設定縣市（custom:county），請聯絡管理者"})
    return handle_punishments(cur, qs, force_county=county or "")


def handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/")
    # 拿掉 stage 前綴（$default stage 不會有，但保險起見）
    path = re.sub(r"^/(prod|dev|\$default)(?=/)", "", path).rstrip("/") or "/"
    qs = event.get("queryStringParameters") or {}

    if method == "OPTIONS":
        return respond(200, {})

    try:
        conn = get_conn()
        with conn.cursor() as cur:
            # ---- 公開端點 ----
            if path in ("/", "/api", "/api/health"):
                return handle_health(cur)
            if path == "/api/counties":
                return handle_counties(cur)
            if path == "/api/academic-years":
                return handle_academic_years(cur)
            if path == "/api/kindergartens":
                return handle_kindergartens(cur, qs)
            if path == "/api/punishments":
                return handle_punishments(cur, qs)
            # 單一幼兒園的裁罰紀錄：/api/kindergartens/{id}/punishments
            m = re.fullmatch(r"/api/kindergartens/(\d+)/punishments", path)
            if m:
                return handle_kindergarten_punishments(cur, m.group(1))

            # ---- 受保護端點：token 已由 API Gateway 驗過 ----
            if path.startswith("/api/secure/"):
                identity = get_identity(event)
                if identity is None:
                    # 正常情況不會走到這（API Gateway 會先回 401）；
                    # 除非有人把 route 的 authorizer 拿掉了。
                    return respond(401, {"message": "需要登入"})
                if path == "/api/secure/me":
                    return handle_secure_me(identity)
                if path == "/api/secure/kindergartens":
                    return handle_secure_kindergartens(cur, qs, identity)
                if path == "/api/secure/punishments":
                    return handle_secure_punishments(cur, qs, identity)

        return respond(404, {"message": f"Not found: {method} {path}"})
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR {type(exc).__name__}: {exc}")
        return respond(500, {"message": "Internal error", "detail": str(exc)})
