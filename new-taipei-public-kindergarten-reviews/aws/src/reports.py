"""家長回報（公開端點）。

流程（詳見 API_SPEC.md §2）：

    POST /api/reports/drafts                    建草稿 + 寄驗證碼
    POST /api/reports/drafts/{id}/attachments/presign   取得 S3 直傳網址
    POST /api/reports/drafts/{id}/attachments           登錄已上傳的附件
    POST /api/reports/drafts/{id}/otp/verify           驗證成功才正式成案
    POST /api/reports/drafts/{id}/otp/resend           重寄驗證碼
    GET  /api/reports/{token}                   追蹤進度

關鍵設計：未驗證的草稿也存在 parent_report，但 status='pending_verification'，
政府端的每一支查詢都會排除它，等同 UI_SPEC 4.2 要求的「驗證後才進政府資料庫」。
"""

import datetime
import hmac
import os
import re
import secrets
from hashlib import sha256

import mailer
import storage
from common import error, iso, mask_email, respond, text_field, utcnow

OTP_TTL_MINUTES = int(os.environ.get("OTP_TTL_MINUTES", "10"))
OTP_MAX_ATTEMPTS = 5
# 驗證碼只存 HMAC，不存明碼。pepper 由 CloudFormation 以 NoEcho 參數帶入。
OTP_PEPPER = os.environ.get("OTP_PEPPER", "dev-pepper").encode("utf-8")

MAX_CONTENT_LEN = 5000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

STATUS_PENDING = "pending_verification"
STATUS_LABELS = {
    "submitted": "已通報",
    "investigating": "調查中",
    "closed": "調查完畢",
    "rejected": "不受理",
}
# 政府端可以指定的狀態（不含未驗證草稿）
PUBLIC_STATUSES = tuple(STATUS_LABELS.keys())
# 一般案件的三個階段，依序
NORMAL_FLOW = ("submitted", "investigating", "closed")


def case_no(row):
    """對外案號，例：R2509-000057。由建立時間 + id 推導，不另存欄位。"""
    created = row.get("created_at")
    prefix = created.strftime("%y%m") if created else "0000"
    return f"R{prefix}-{row['id']:06d}"


def build_steps(status):
    """回傳追蹤頁要畫的節點集合。

    前端不做狀態機推導，一律照這裡給的畫（見 API_SPEC.md §3）：
      * 一般案件三節點：已通報 -> 調查中 -> 調查完畢
      * 不受理只有兩節點：已通報(done) -> 不受理(current)
    """
    if status == "rejected":
        return [
            {"key": "submitted", "label": STATUS_LABELS["submitted"], "state": "done"},
            {"key": "rejected", "label": STATUS_LABELS["rejected"], "state": "current"},
        ]
    try:
        current = NORMAL_FLOW.index(status)
    except ValueError:
        current = 0
    steps = []
    for i, key in enumerate(NORMAL_FLOW):
        state = "done" if i < current else ("current" if i == current else "pending")
        steps.append({"key": key, "label": STATUS_LABELS[key], "state": state})
    return steps


# ---------------------------------------------------------------------------
# 驗證碼
# ---------------------------------------------------------------------------
def _new_otp():
    return f"{secrets.randbelow(1000000):06d}"


def _hash_otp(code):
    return hmac.new(OTP_PEPPER, code.encode("utf-8"), sha256).hexdigest()


def _otp_payload(code, expires_at):
    """dev mode 才把驗證碼回給前端，正式模式只能從信箱取得。"""
    body = {"otpExpiresAt": iso(expires_at)}
    if mailer.MAIL_MODE != "ses":
        body["devOtp"] = code
    return body


def _issue_otp(cur, draft_id):
    code = _new_otp()
    expires_at = utcnow() + datetime.timedelta(minutes=OTP_TTL_MINUTES)
    cur.execute(
        "UPDATE parent_report SET otp_hash=%s, otp_expires_at=%s, otp_attempts=0 "
        "WHERE id=%s",
        (_hash_otp(code), expires_at, draft_id),
    )
    return code, expires_at


# ---------------------------------------------------------------------------
# 建立草稿
# ---------------------------------------------------------------------------
def create_draft(cur, data):
    name, name_long = text_field(data, "reporterName", 100)
    if name_long:
        return error(400, "NAME_TOO_LONG", "姓名長度上限 100 字")

    email = str(data.get("reporterEmail") or "").strip()
    if not EMAIL_RE.fullmatch(email) or len(email) > 255:
        return error(400, "INVALID_EMAIL", "Email 格式不正確")

    content, content_long = text_field(data, "content", MAX_CONTENT_LEN)
    if content_long:
        return error(400, "CONTENT_TOO_LONG", f"回報事由長度上限 {MAX_CONTENT_LEN} 字")
    if not content:
        return error(400, "CONTENT_REQUIRED", "請填寫回報事由")

    try:
        kg_id = int(data.get("kindergartenId"))
    except (TypeError, ValueError):
        return error(400, "KINDERGARTEN_NOT_FOUND", "請選擇幼兒園")

    cur.execute("SELECT id, school_name FROM kindergarten WHERE id=%s", (kg_id,))
    school = cur.fetchone()
    if not school:
        return error(400, "KINDERGARTEN_NOT_FOUND", "查無此幼兒園")

    cur.execute(
        """INSERT INTO parent_report
               (status, reporter_name, reporter_email, kindergarten_id, content)
           VALUES (%s, %s, %s, %s, %s)""",
        (STATUS_PENDING, name or None, email, kg_id, content),
    )
    draft_id = cur.lastrowid

    code, expires_at = _issue_otp(cur, draft_id)
    mailer.send_otp(email, code, school["school_name"], OTP_TTL_MINUTES)

    body = {"draftId": draft_id, "reporterEmailMasked": mask_email(email)}
    body.update(_otp_payload(code, expires_at))
    return respond(201, body)


def _load_draft(cur, draft_id):
    cur.execute(
        """SELECT r.*, k.school_name
           FROM parent_report r
           JOIN kindergarten k ON k.id = r.kindergarten_id
           WHERE r.id = %s""",
        (draft_id,),
    )
    return cur.fetchone()


def _require_pending(cur, draft_id):
    """取出仍在待驗證狀態的草稿。回 (row, error_response)。"""
    row = _load_draft(cur, draft_id)
    if not row:
        return None, error(404, "DRAFT_NOT_FOUND", "找不到這筆回報草稿")
    if row["status"] != STATUS_PENDING:
        return None, error(400, "DRAFT_ALREADY_VERIFIED", "這筆回報已經完成驗證")
    return row, None


def resend_otp(cur, draft_id):
    row, err = _require_pending(cur, draft_id)
    if err:
        return err
    code, expires_at = _issue_otp(cur, draft_id)
    mailer.send_otp(row["reporter_email"], code, row["school_name"], OTP_TTL_MINUTES)
    return respond(200, _otp_payload(code, expires_at))


def verify_otp(cur, draft_id, data):
    row, err = _require_pending(cur, draft_id)
    if err:
        return err

    code = str(data.get("code") or "").strip()
    if not row["otp_hash"]:
        return error(400, "OTP_LOCKED", "驗證碼已失效，請重新填寫回報表單")
    if row["otp_expires_at"] and row["otp_expires_at"] < utcnow():
        return error(400, "OTP_EXPIRED", "驗證碼已逾時，請重新寄送")
    if row["otp_attempts"] >= OTP_MAX_ATTEMPTS:
        cur.execute("UPDATE parent_report SET otp_hash=NULL WHERE id=%s", (draft_id,))
        return error(400, "OTP_LOCKED", "驗證碼錯誤次數過多，請重新填寫回報表單")

    if not hmac.compare_digest(row["otp_hash"], _hash_otp(code)):
        attempts = row["otp_attempts"] + 1
        left = max(OTP_MAX_ATTEMPTS - attempts, 0)
        if left == 0:
            cur.execute(
                "UPDATE parent_report SET otp_attempts=%s, otp_hash=NULL WHERE id=%s",
                (attempts, draft_id),
            )
            return error(400, "OTP_LOCKED", "驗證碼錯誤次數過多，請重新填寫回報表單")
        cur.execute(
            "UPDATE parent_report SET otp_attempts=%s WHERE id=%s", (attempts, draft_id)
        )
        return error(400, "OTP_INVALID", "驗證碼不正確", {"attemptsLeft": left})

    # 驗證通過：正式成案
    token = secrets.token_urlsafe(32)
    now = utcnow()
    cur.execute(
        """UPDATE parent_report
           SET status='submitted', verified_at=%s, status_updated_at=%s,
               tracking_token=%s, otp_hash=NULL, otp_expires_at=NULL, otp_attempts=0
           WHERE id=%s AND status=%s""",
        (now, now, token, draft_id, STATUS_PENDING),
    )
    if cur.rowcount == 0:
        # 同時有兩個請求驗證同一張草稿，另一個先成功了
        return error(400, "DRAFT_ALREADY_VERIFIED", "這筆回報已經完成驗證")

    cur.execute(
        """INSERT INTO parent_report_message
               (report_id, kind, visible_to_parent, author_type, author_display,
                body, to_status)
           VALUES (%s, 'system', 1, 'system', '系統', %s, 'submitted')""",
        (draft_id, "回報已完成 Email 驗證，案件成立並送交主管機關。"),
    )

    row = _load_draft(cur, draft_id)
    no = case_no(row)
    mailer.send_tracking_link(
        row["reporter_email"], no, token, row["school_name"]
    )
    return respond(
        200,
        {
            "caseNo": no,
            "status": "submitted",
            "trackingToken": token,
            "trackingUrl": f"/report/{token}",
        },
    )


# ---------------------------------------------------------------------------
# 附件
# ---------------------------------------------------------------------------
def _attachment_count(cur, report_id):
    cur.execute(
        "SELECT COUNT(*) AS n FROM parent_report_attachment WHERE report_id=%s",
        (report_id,),
    )
    return cur.fetchone()["n"]


def _validate_files(cur, draft_id, data):
    """檢查 files 陣列（數量／型別／大小）。回 (checked, error)。"""
    files = data.get("files")
    if not isinstance(files, list) or not files:
        return None, error(400, "INVALID_JSON", "files 必須是非空陣列")

    already = _attachment_count(cur, draft_id)
    if already + len(files) > storage.MAX_FILES:
        return None, error(
            400,
            "TOO_MANY_FILES",
            f"最多只能上傳 {storage.MAX_FILES} 個檔案",
            {"already": already},
        )

    checked = []
    for f in files:
        if not isinstance(f, dict):
            return None, error(400, "INVALID_JSON", "files 內容格式錯誤")
        content_type = str(f.get("contentType") or "").strip().lower()
        if content_type not in storage.ALLOWED_TYPES:
            return None, error(
                400, "UNSUPPORTED_FILE_TYPE", "僅接受圖片檔（jpg/png/gif/webp/heic）"
            )
        try:
            size = int(f.get("sizeBytes") or 0)
        except (TypeError, ValueError):
            size = 0
        if size <= 0 or size > storage.MAX_BYTES:
            return None, error(400, "FILE_TOO_LARGE", "單一檔案大小需小於 5MB")
        name, _ = text_field(f, "fileName", 255)
        checked.append(
            {
                "fileName": name or None,
                "contentType": content_type,
                "sizeBytes": size,
                "key": str(f.get("key") or ""),
            }
        )
    return checked, None


def presign_attachments(cur, draft_id, data):
    row, err = _require_pending(cur, draft_id)
    if err:
        return err
    # 先做參數驗證再看 bucket 有沒有設定，這樣即使附件功能沒開，
    # 前端也還是會收到「檔案太大 / 格式不對」這種有用的錯誤。
    checked, err = _validate_files(cur, draft_id, data)
    if err:
        return err
    if not storage.enabled():
        return error(503, "ATTACHMENTS_DISABLED", "附件功能尚未啟用（未設定 S3 bucket）")

    uploads = []
    expires_at = utcnow() + datetime.timedelta(seconds=storage.UPLOAD_TTL)
    for f in checked:
        key = storage.build_key(draft_id, f["contentType"])
        signed = storage.presign_upload(key, f["contentType"])
        uploads.append(
            {
                "fileName": f["fileName"],
                "key": key,
                "url": signed["url"],
                "fields": signed["fields"],
                "expiresAt": iso(expires_at),
            }
        )
    return respond(200, {"uploads": uploads})


def register_attachments(cur, draft_id, data):
    row, err = _require_pending(cur, draft_id)
    if err:
        return err
    checked, err = _validate_files(cur, draft_id, data)
    if err:
        return err

    prefix = f"reports/{draft_id}/"
    for f in checked:
        # key 必須是我們剛才簽給這張草稿的，否則就是有人想掛別人的檔案
        if not f["key"].startswith(prefix):
            return error(400, "INVALID_ATTACHMENT_KEY", "附件 key 不屬於這筆回報")

    for f in checked:
        cur.execute(
            """INSERT INTO parent_report_attachment
                   (report_id, s3_key, original_name, content_type, size_bytes)
               VALUES (%s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE original_name=VALUES(original_name)""",
            (draft_id, f["key"], f["fileName"], f["contentType"], f["sizeBytes"]),
        )

    cur.execute(
        "SELECT id, original_name, size_bytes FROM parent_report_attachment "
        "WHERE report_id=%s ORDER BY id",
        (draft_id,),
    )
    rows = cur.fetchall()
    return respond(
        201,
        {
            "attachments": [
                {
                    "id": r["id"],
                    "fileName": r["original_name"],
                    "sizeBytes": r["size_bytes"],
                }
                for r in rows
            ],
            "count": len(rows),
        },
    )


def attachment_payload(cur, report_id, with_size=False):
    """組出附件清單（含短效 presigned GET）。給追蹤頁與政府端詳情共用。"""
    cur.execute(
        "SELECT id, s3_key, original_name, content_type, size_bytes "
        "FROM parent_report_attachment WHERE report_id=%s ORDER BY id",
        (report_id,),
    )
    items = []
    for r in cur.fetchall():
        item = {
            "id": r["id"],
            "fileName": r["original_name"],
            "contentType": r["content_type"],
            "url": storage.presign_download(r["s3_key"], r["original_name"])
            if storage.enabled()
            else None,
        }
        if with_size:
            item["sizeBytes"] = r["size_bytes"]
        items.append(item)
    return items


# ---------------------------------------------------------------------------
# 追蹤頁
# ---------------------------------------------------------------------------
def get_tracking(cur, token):
    cur.execute(
        """SELECT r.id, r.status, r.status_reason, r.created_at, r.verified_at,
                  k.school_name
           FROM parent_report r
           JOIN kindergarten k ON k.id = r.kindergarten_id
           WHERE r.tracking_token = %s AND r.status <> %s""",
        (token, STATUS_PENDING),
    )
    row = cur.fetchone()
    if not row:
        # 不存在與無權限一律回同一種錯誤，不洩漏案件是否存在
        return error(404, "REPORT_NOT_FOUND", "查無此回報案件")

    # 只給家長看得到、且有內文的訊息（狀態變更由 steps 呈現，不重複列）
    cur.execute(
        """SELECT created_at, author_display, body
           FROM parent_report_message
           WHERE report_id=%s AND visible_to_parent=1 AND body IS NOT NULL
           ORDER BY created_at, id""",
        (row["id"],),
    )
    messages = [
        {
            "createdAt": iso(m["created_at"]),
            "authorDisplay": m["author_display"],
            "body": m["body"],
        }
        for m in cur.fetchall()
    ]

    return respond(
        200,
        {
            # 刻意不回傳回報人姓名與 email
            "caseNo": case_no(row),
            "schoolName": row["school_name"],
            "submittedAt": iso(row["verified_at"] or row["created_at"]),
            "status": row["status"],
            "statusLabel": STATUS_LABELS.get(row["status"], row["status"]),
            "statusReason": row["status_reason"],
            "steps": build_steps(row["status"]),
            "messages": messages,
            "attachments": attachment_payload(cur, row["id"]),
        },
    )
