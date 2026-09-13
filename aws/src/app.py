"""
幼兒園查詢 + 家長回報 API — 單一 Lambda 處理所有路由（HTTP API payload v2.0）。

資料現況：kindergarten 表只保留最新學年度（114）一份，等同「一校一列」，
id 即為學校身分。kindergarten_punishment 透過外鍵 kindergarten_id 指回 kindergarten(id)。

模組分工（deploy.ps1 是平鋪複製 src/*.py，所以所有檔案都放在 src/ 根層）：
  app.py           路由分派 + 幼兒園/裁罰查詢
  common.py        DB 連線、HTTP 回應、body 解析、時間格式
  auth.py          JWT claims、縣市範圍控管、staff_profile
  reports.py       家長回報（公開端點）
  reports_admin.py 家長回報（政府端）
  mailer.py        SESv2 寄信（含 dev mode）
  storage.py       S3 presigned URL（附件）
  risk.py          風險指數（四維度加權：財務法遵／家長回報／輿情關注／裁罰紀錄）
  finance.py       財報法遵分析（法遵風險指數 + 各指標等級）
  opinion.py       輿情分析 job API（實際分析在 opinion_worker.py）

完整的請求／回應格式定義在專案根目錄的 API_SPEC.md。

路由：

公開（不需登入）：
  GET  /api/health                          健康檢查（會實際 ping DB）
  GET  /api/counties                        縣市清單
  GET  /api/academic-years                  學年度清單（目前只有 114）
  GET  /api/kindergartens                   查詢清單（縣市 + 名稱 LIKE + 分頁）
  GET  /api/punishments                     裁罰紀錄查詢（縣市/鄉鎮/名稱/日期/罰鍰 + 分頁）
  GET  /api/kindergartens/{id}/punishments  單一幼兒園的裁罰紀錄
  POST /api/reports/drafts                  建立回報草稿並寄出 Email 驗證碼
  POST /api/reports/drafts/{id}/attachments/presign  取得 S3 直傳網址
  POST /api/reports/drafts/{id}/attachments          登錄已上傳的附件
  POST /api/reports/drafts/{id}/otp/verify           驗證碼正確才正式成案
  POST /api/reports/drafts/{id}/otp/resend           重寄驗證碼
  GET  /api/reports/{token}                 回報進度追蹤（憑 token）

需登入（Cognito ID token，API Gateway 已先驗過簽章與過期）：
  GET   /api/secure/me                      我是誰 / 我能看哪個範圍
  GET   /api/secure/kindergartens           同上查詢，但縣市鎖在權限內，並帶出風險指數
  GET   /api/secure/punishments             同裁罰查詢，但縣市鎖在使用者權限內
  GET   /api/secure/reports                 家長回報清單（依縣市過濾）
  GET   /api/secure/reports/summary         各狀態件數
  GET   /api/secure/reports/{id}            案件詳情（含附件與訊息串）
  POST  /api/secure/reports/{id}/messages   回覆家長 / 內部備註
  PATCH /api/secure/reports/{id}            變更狀態 / 指派承辦
  GET   /api/secure/kindergartens/{id}/risk 風險評估（四維度加權，讀取時即時重算）
  GET   /api/secure/kindergartens/{id}/finance                財報法遵分析（113 決算年度）
  POST  /api/secure/kindergartens/{id}/opinion/scans          啟動輿情分析（非同步）
  GET   /api/secure/kindergartens/{id}/opinion/scans/{jobId}  查掃描進度
  GET   /api/secure/kindergartens/{id}/opinion                最新一次輿情結果

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

import re

import auth
import finance
import opinion
import reports
import reports_admin
import risk
from common import error, get_conn, parse_body, respond, to_int

# 只允許排序這些欄位，避免 SQL injection
SORTABLE = {
    "id": "id",
    "school_name": "school_name",
    "county": "county",
    "district": "district",
    "academic_year": "academic_year",
    "ownership": "ownership",
    # 受保護端點才有意義（公開版沒有 JOIN risk_score_current，會被忽略）
    "risk_score": "risk_score",
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
# （get_conn / respond / to_int 已移到 common.py，本檔只 import 使用）


# ---------------------------------------------------------------------------
# 認證
#
# 實作在 auth.py（get_identity / scoped_county / upsert_staff_profile）。
# ---------------------------------------------------------------------------
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


def handle_kindergartens(cur, qs, force_county=None, with_risk=False):
    """查詢幼兒園清單。

    force_county 不是 None 時，縣市條件一律用它，忽略 query string，
    這是給 /api/secure/* 做資料範圍控管用的。

    with_risk=True 時 LEFT JOIN risk_score_current，每列多帶
    risk_score / risk_level（給 /admin/dashboard 的風險指數欄與整列上色用）。
    """
    county = (qs.get("county") or "").strip() if force_county is None else force_county
    name = (qs.get("name") or "").strip()
    ownership = (qs.get("ownership") or "").strip()
    year = (qs.get("academicYear") or "").strip()
    page = to_int(qs.get("page"), 1, 1, 10000)
    page_size = to_int(qs.get("pageSize"), 20, 1, 100)

    sort_by = SORTABLE.get((qs.get("sortBy") or "").strip(), "id")
    sort_dir = "DESC" if (qs.get("sortDir") or "").lower() == "desc" else "ASC"
    if sort_by == "risk_score":
        # 沒 JOIN 風險表時這個欄位不存在，退回預設排序
        sort_by = "r.total_score" if with_risk else "id"

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

    # risk_score_current 沒有與 kindergarten 同名的欄位，所以 WHERE 裡的
    # 不加前綴寫法（county = %s）在 JOIN 之後仍然不會有歧義。
    risk_cols = (
        ", r.total_score AS risk_score, r.risk_level, r.is_placeholder AS risk_is_placeholder"
        if with_risk
        else ""
    )
    risk_join = (
        "LEFT JOIN risk_score_current r ON r.kindergarten_id = kindergarten.id"
        if with_risk
        else ""
    )

    offset = (page - 1) * page_size
    cur.execute(
        f"""SELECT kindergarten.id, academic_year, code, school_name, ownership,
                   county, district, address, phone{risk_cols}
            FROM kindergarten
            {risk_join}
            {where_sql}
            ORDER BY {sort_by} {sort_dir}, kindergarten.id ASC
            LIMIT %s OFFSET %s""",
        params + [page_size, offset],
    )
    items = cur.fetchall()
    if with_risk:
        for item in items:
            if item.get("risk_score") is not None:
                item["risk_score"] = float(item["risk_score"])

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
# 範圍控管的模式：從 identity 拿 county，用 auth.scoped_county() 決定範圍，
# 再把它當成 SQL 的強制條件。家長回報清單（reports_admin.py）也照這個模式。
# ---------------------------------------------------------------------------
def handle_secure_me(cur, identity):
    """回傳「我是誰、我能看哪個範圍」，前端登入後用來顯示身分。

    順手把身分寫進 staff_profile（JIT provisioning），
    這樣 UI_SPEC 6.2「從資料庫讀取所屬縣市」有東西可讀，
    回覆家長時也才有 display_name 可以署名。
    """
    profile = auth.upsert_staff_profile(cur, identity)
    return respond(
        200,
        {
            "username": identity["username"],
            "displayName": (profile or {}).get("display_name"),
            "county": identity["county"] or None,
            "agency": identity["agency"] or None,
            "groups": identity["groups"],
            "isAdmin": identity["isAdmin"],
            "scope": "全國" if identity["isAdmin"] else (identity["county"] or "未設定縣市"),
        },
    )


def require_county(identity):
    """非 admin 又沒設定縣市的帳號一律擋掉。回 (county, error_response)。"""
    county = auth.scoped_county(identity)
    if county is None and not identity["isAdmin"]:
        return None, error(
            403, "FORBIDDEN", "這個帳號沒有設定縣市（custom:county），請聯絡管理者"
        )
    return county, None


def handle_secure_kindergartens(cur, qs, identity):
    """跟公開的查詢同一份資料，但縣市被鎖在使用者權限內，並帶出風險指數。"""
    county, err = require_county(identity)
    if err:
        return err
    return handle_kindergartens(cur, qs, force_county=county or "", with_risk=True)


def handle_secure_punishments(cur, qs, identity):
    """裁罰查詢，但縣市鎖在使用者權限範圍內（同 handle_secure_kindergartens 模式）。"""
    county, err = require_county(identity)
    if err:
        return err
    return handle_punishments(cur, qs, force_county=county or "")


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
def route_public_reports(cur, method, path, event):
    """家長回報（公開端點）。回 None 表示這條路徑不屬於這一區。"""
    # /api/reports/drafts
    if path == "/api/reports/drafts" and method == "POST":
        body, err = parse_body(event)
        return err or reports.create_draft(cur, body)

    m = re.fullmatch(r"/api/reports/drafts/(\d+)/otp/(verify|resend)", path)
    if m and method == "POST":
        draft_id, action = int(m.group(1)), m.group(2)
        body, err = parse_body(event)
        if err:
            return err
        if action == "verify":
            return reports.verify_otp(cur, draft_id, body)
        return reports.resend_otp(cur, draft_id)

    m = re.fullmatch(r"/api/reports/drafts/(\d+)/attachments(/presign)?", path)
    if m and method == "POST":
        draft_id = int(m.group(1))
        body, err = parse_body(event)
        if err:
            return err
        if m.group(2):
            return reports.presign_attachments(cur, draft_id, body)
        return reports.register_attachments(cur, draft_id, body)

    # /api/reports/{token}（放最後，避免吃掉上面的 /drafts 路徑）
    m = re.fullmatch(r"/api/reports/([A-Za-z0-9_-]{20,64})", path)
    if m and method == "GET":
        return reports.get_tracking(cur, m.group(1))

    return None


def route_secure(cur, method, path, event, identity):
    """需登入的端點。回 None 表示這條路徑不存在。"""
    if path == "/api/secure/me":
        return handle_secure_me(cur, identity)
    if path == "/api/secure/kindergartens":
        return handle_secure_kindergartens(cur, qs_of(event), identity)
    if path == "/api/secure/punishments":
        return handle_secure_punishments(cur, qs_of(event), identity)

    # ---- 家長回報（政府端）----
    if path == "/api/secure/reports" and method == "GET":
        return reports_admin.list_reports(cur, qs_of(event), identity)
    if path == "/api/secure/reports/summary" and method == "GET":
        return reports_admin.summary(cur, identity)

    m = re.fullmatch(r"/api/secure/reports/(\d+)", path)
    if m:
        report_id = int(m.group(1))
        if method == "GET":
            return reports_admin.get_detail(cur, report_id, identity)
        if method == "PATCH":
            body, err = parse_body(event)
            return err or reports_admin.patch_report(cur, report_id, body, identity)

    m = re.fullmatch(r"/api/secure/reports/(\d+)/messages", path)
    if m and method == "POST":
        body, err = parse_body(event)
        return err or reports_admin.add_message(cur, int(m.group(1)), body, identity)

    # ---- 風險評估（四維度加權，讀取時即時重算）----
    m = re.fullmatch(r"/api/secure/kindergartens/(\d+)/risk", path)
    if m and method == "GET":
        return risk.get_risk(cur, int(m.group(1)), identity)

    # ---- 財報法遵分析 ----
    m = re.fullmatch(r"/api/secure/kindergartens/(\d+)/finance", path)
    if m and method == "GET":
        return finance.get_finance(cur, int(m.group(1)), identity)

    # ---- 輿情分析（非同步 job）----
    # 順序有意義：/opinion/scans/{jobId} 要排在 /opinion 之前比對。
    m = re.fullmatch(r"/api/secure/kindergartens/(\d+)/opinion/scans/(\d+)", path)
    if m and method == "GET":
        return opinion.get_job(cur, int(m.group(1)), int(m.group(2)), identity)

    m = re.fullmatch(r"/api/secure/kindergartens/(\d+)/opinion/scans", path)
    if m and method == "POST":
        return opinion.start_scan(cur, int(m.group(1)), identity)

    m = re.fullmatch(r"/api/secure/kindergartens/(\d+)/opinion", path)
    if m and method == "GET":
        return opinion.get_latest(cur, int(m.group(1)), identity)

    return None


def qs_of(event):
    return event.get("queryStringParameters") or {}


def handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/")
    # 拿掉 stage 前綴（$default stage 不會有，但保險起見）
    path = re.sub(r"^/(prod|dev|\$default)(?=/)", "", path).rstrip("/") or "/"
    qs = qs_of(event)

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

            if path.startswith("/api/reports"):
                res = route_public_reports(cur, method, path, event)
                if res is not None:
                    return res

            # ---- 受保護端點：token 已由 API Gateway 驗過 ----
            if path.startswith("/api/secure/"):
                identity = auth.get_identity(event)
                if identity is None:
                    # 正常情況不會走到這（API Gateway 會先回 401）；
                    # 除非有人把 route 的 authorizer 拿掉了。
                    return error(401, "UNAUTHORIZED", "需要登入")
                res = route_secure(cur, method, path, event, identity)
                if res is not None:
                    return res

        return error(404, "NOT_FOUND", f"Not found: {method} {path}")
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR {type(exc).__name__}: {exc}")
        return error(500, "INTERNAL_ERROR", "Internal error", {"detail": str(exc)})
