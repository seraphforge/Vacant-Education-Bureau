"""Lambda 共用工具：DB 連線、HTTP 回應、request body 解析、時間格式。

所有模組（app / reports / reports_admin / risk）都從這裡取，避免各自實作一份。

時間約定
--------
DB 的 time_zone 是 UTC，程式端一律使用 **UTC naive datetime**（不帶 tzinfo），
寫進 DB 與從 DB 讀出都不做轉換；只有在輸出 JSON 時由 iso() 補上 'Z'。
"""

import base64
import datetime
import json
import os

import pymysql

DB_HOST = os.environ["DB_HOST"]
DB_PORT = int(os.environ.get("DB_PORT", "3306"))
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
DB_NAME = os.environ.get("DB_NAME", "readme")

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


def error(status, code, message, detail=None):
    """統一的錯誤格式：{message, code, detail?}（見 API_SPEC.md §0.5）。"""
    body = {"message": message, "code": code}
    if detail is not None:
        body["detail"] = detail
    return respond(status, body)


def to_int(value, default, lo, hi):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def parse_body(event):
    """取出 JSON body。回 (dict, None) 或 (None, error_response)。"""
    raw = event.get("body")
    if raw is None or raw == "":
        return {}, None
    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except Exception:
            return None, error(400, "INVALID_JSON", "request body 無法解碼")
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None, error(400, "INVALID_JSON", "request body 不是合法的 JSON")
    if not isinstance(data, dict):
        return None, error(400, "INVALID_JSON", "request body 必須是 JSON 物件")
    return data, None


def utcnow():
    """UTC naive datetime，與 DB 的 NOW() 同基準。"""
    return datetime.datetime.utcnow().replace(microsecond=0)


def iso(value):
    """datetime -> '2026-09-12T13:20:00Z'；date -> '2026-09-12'；None -> None。"""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, datetime.date):
        return value.strftime("%Y-%m-%d")
    return str(value)


def mask_email(email):
    """parent@example.com -> p*****@example.com（清單畫面用）。"""
    if not email or "@" not in email:
        return None
    local, domain = email.split("@", 1)
    if len(local) <= 1:
        return f"{local}***@{domain}"
    return f"{local[0]}{'*' * min(len(local) - 1, 5)}@{domain}"


def text_field(data, key, max_len, required=False):
    """取字串欄位，去頭尾空白。回 (value, too_long)。"""
    raw = data.get(key)
    if raw is None:
        return (None, False) if not required else ("", False)
    value = str(raw).strip()
    if len(value) > max_len:
        return value[:max_len], True
    return value, False
