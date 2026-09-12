"""
幼兒園查詢 API — 單一 Lambda 處理所有路由（HTTP API payload v2.0）。

路由：
  GET /api/health                 健康檢查（會實際 ping DB）
  GET /api/counties               縣市清單（去掉 "[01]" 前綴後去重）
  GET /api/kindergartens          查詢清單（縣市 + 名稱 LIKE + 分頁）

/api/kindergartens 支援的 query string：
  county        縣市名稱，例如 新北市（可省略 = 全部）
  name          學校名稱關鍵字，用 LIKE %name% 比對
  academicYear  學年度，例如 114。預設 = 資料庫中最新學年度
  page          第幾頁，從 1 開始，預設 1
  pageSize      每頁筆數，預設 20，最大 100
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


def latest_academic_year(cur):
    cur.execute("SELECT MAX(CAST(academic_year AS UNSIGNED)) AS y FROM kindergarten")
    row = cur.fetchone()
    return str(row["y"]) if row and row["y"] else None


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


def handle_kindergartens(cur, qs):
    county = (qs.get("county") or "").strip()
    name = (qs.get("name") or "").strip()
    ownership = (qs.get("ownership") or "").strip()
    year = (qs.get("academicYear") or "").strip()
    page = to_int(qs.get("page"), 1, 1, 10000)
    page_size = to_int(qs.get("pageSize"), 20, 1, 100)

    sort_by = SORTABLE.get((qs.get("sortBy") or "").strip(), "id")
    sort_dir = "DESC" if (qs.get("sortDir") or "").lower() == "desc" else "ASC"

    if not year:
        year = latest_academic_year(cur)

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
        },
    )


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
            if path in ("/", "/api", "/api/health"):
                return handle_health(cur)
            if path == "/api/counties":
                return handle_counties(cur)
            if path == "/api/academic-years":
                return handle_academic_years(cur)
            if path == "/api/kindergartens":
                return handle_kindergartens(cur, qs)
        return respond(404, {"message": f"Not found: {method} {path}"})
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR {type(exc).__name__}: {exc}")
        return respond(500, {"message": "Internal error", "detail": str(exc)})
