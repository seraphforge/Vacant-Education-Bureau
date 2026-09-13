"""家長回報（政府端，/api/secure/reports*）。

UI_SPEC 原本沒寫這一段，補上的需求是：公務人員要能看到自己縣市的家長回報清單、
開啟詳情看附件、回覆家長、以及變更案件狀態（調查中／調查完畢／不受理）。

權限：縣市範圍一律由 auth.scoped_county(identity) 決定（來自 Cognito ID token），
單筆存取時再確認該案的幼兒園縣市落在範圍內；不在範圍內回 404 而非 403，
避免洩漏「這個案號存在」這件事。
"""

import re

import auth
import mailer
import reports
import risk
from common import error, iso, mask_email, respond, to_int, utcnow

SORTABLE = {
    "created_at": "r.created_at",
    "status": "r.status",
    "school_name": "k.school_name",
    "id": "r.id",
}
EXCERPT_LEN = 60
MAX_MESSAGE_LEN = 2000


def _scope(identity):
    """回傳 (county, error)。county 為 None 表示 admin 看全國。"""
    county = auth.scoped_county(identity)
    if county is None and not identity["isAdmin"]:
        return None, error(
            403, "FORBIDDEN", "這個帳號沒有設定縣市（custom:county），請聯絡管理者"
        )
    return county, None


def _status_filter(raw):
    """把 query 的 status 參數轉成白名單內的清單；空的代表全部（不含未驗證草稿）。"""
    wanted = [s.strip() for s in (raw or "").split(",") if s.strip()]
    valid = [s for s in wanted if s in reports.PUBLIC_STATUSES]
    return valid or list(reports.PUBLIC_STATUSES)


def list_reports(cur, qs, identity):
    county, err = _scope(identity)
    if err:
        return err

    statuses = _status_filter(qs.get("status"))
    where = ["r.status IN (" + ",".join(["%s"] * len(statuses)) + ")"]
    params = list(statuses)

    if county:
        where.append("k.county = %s")
        params.append(county)

    kg_id = qs.get("kindergartenId")
    if kg_id and str(kg_id).isdigit():
        where.append("r.kindergarten_id = %s")
        params.append(int(kg_id))

    keyword = (qs.get("q") or "").strip()
    if keyword:
        where.append("(r.content LIKE %s OR k.school_name LIKE %s)")
        params += [f"%{keyword}%", f"%{keyword}%"]

    assignee = (qs.get("assignee") or "").strip()
    if assignee == "unassigned":
        where.append("r.assignee IS NULL")
    elif assignee:
        where.append("r.assignee = %s")
        params.append(assignee)

    date_from = (qs.get("dateFrom") or "").strip()
    date_to = (qs.get("dateTo") or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_from):
        where.append("r.created_at >= %s")
        params.append(f"{date_from} 00:00:00")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_to):
        where.append("r.created_at <= %s")
        params.append(f"{date_to} 23:59:59")

    where_sql = "WHERE " + " AND ".join(where)
    page = to_int(qs.get("page"), 1, 1, 10000)
    page_size = to_int(qs.get("pageSize"), 20, 1, 100)
    sort_by = SORTABLE.get((qs.get("sortBy") or "").strip(), "r.created_at")
    sort_dir = "ASC" if (qs.get("sortDir") or "").lower() == "asc" else "DESC"

    cur.execute(
        f"""SELECT COUNT(*) AS total FROM parent_report r
            JOIN kindergarten k ON k.id = r.kindergarten_id
            {where_sql}""",
        params,
    )
    total = cur.fetchone()["total"]

    cur.execute(
        f"""SELECT r.id, r.status, r.created_at, r.reporter_name, r.reporter_email,
                   r.assignee, r.kindergarten_id, r.content,
                   k.school_name, k.county, k.district,
                   (SELECT COUNT(*) FROM parent_report_attachment a
                     WHERE a.report_id = r.id) AS attachment_count,
                   (SELECT MAX(m.created_at) FROM parent_report_message m
                     WHERE m.report_id = r.id AND m.kind = 'reply') AS last_message_at
            FROM parent_report r
            JOIN kindergarten k ON k.id = r.kindergarten_id
            {where_sql}
            ORDER BY {sort_by} {sort_dir}, r.id DESC
            LIMIT %s OFFSET %s""",
        params + [page_size, (page - 1) * page_size],
    )
    items = []
    for r in cur.fetchall():
        content = r["content"] or ""
        items.append(
            {
                "id": r["id"],
                "caseNo": reports.case_no(r),
                "status": r["status"],
                "statusLabel": reports.STATUS_LABELS.get(r["status"], r["status"]),
                "createdAt": iso(r["created_at"]),
                "kindergartenId": r["kindergarten_id"],
                "schoolName": r["school_name"],
                "county": r["county"],
                "district": r["district"],
                "reporterName": r["reporter_name"],
                # 清單畫面只給遮蔽版，完整 email 只在詳情出現
                "reporterEmailMasked": mask_email(r["reporter_email"]),
                "contentExcerpt": content[:EXCERPT_LEN]
                + ("…" if len(content) > EXCERPT_LEN else ""),
                "attachmentCount": r["attachment_count"],
                "assignee": r["assignee"],
                "lastMessageAt": iso(r["last_message_at"]),
            }
        )

    return respond(
        200,
        {
            "items": items,
            "total": total,
            "page": page,
            "pageSize": page_size,
            "county": county,
        },
    )


def summary(cur, identity):
    county, err = _scope(identity)
    if err:
        return err

    params = list(reports.PUBLIC_STATUSES)
    sql = (
        "SELECT r.status, COUNT(*) AS n FROM parent_report r "
        "JOIN kindergarten k ON k.id = r.kindergarten_id "
        "WHERE r.status IN (" + ",".join(["%s"] * len(params)) + ")"
    )
    if county:
        sql += " AND k.county = %s"
        params.append(county)
    sql += " GROUP BY r.status"
    cur.execute(sql, params)

    by_status = {s: 0 for s in reports.PUBLIC_STATUSES}
    for row in cur.fetchall():
        by_status[row["status"]] = row["n"]
    return respond(200, {"total": sum(by_status.values()), "byStatus": by_status})


def _load_report(cur, report_id, county):
    """取單筆案件，並確認落在權限範圍內。回 (row, error)。"""
    cur.execute(
        """SELECT r.*, k.school_name, k.county, k.district, k.address, k.phone
           FROM parent_report r
           JOIN kindergarten k ON k.id = r.kindergarten_id
           WHERE r.id = %s AND r.status <> %s""",
        (report_id, reports.STATUS_PENDING),
    )
    row = cur.fetchone()
    # 找不到、或不是自己縣市的案件，一律回 404
    if not row or (county and row["county"] != county):
        return None, error(404, "REPORT_NOT_FOUND", "查無此回報案件")
    return row, None


def _detail_payload(cur, row):
    cur.execute(
        """SELECT id, kind, visible_to_parent, author_type, author_username,
                  author_display, body, from_status, to_status, emailed_at, created_at
           FROM parent_report_message
           WHERE report_id = %s ORDER BY created_at, id""",
        (row["id"],),
    )
    messages = [
        {
            "id": m["id"],
            "kind": m["kind"],
            "visibleToParent": bool(m["visible_to_parent"]),
            "authorType": m["author_type"],
            "authorUsername": m["author_username"],
            "authorDisplay": m["author_display"],
            "body": m["body"],
            "fromStatus": m["from_status"],
            "toStatus": m["to_status"],
            "emailedAt": iso(m["emailed_at"]),
            "createdAt": iso(m["created_at"]),
        }
        for m in cur.fetchall()
    ]

    return {
        "id": row["id"],
        "caseNo": reports.case_no(row),
        "status": row["status"],
        "statusLabel": reports.STATUS_LABELS.get(row["status"], row["status"]),
        "statusReason": row["status_reason"],
        "createdAt": iso(row["created_at"]),
        "verifiedAt": iso(row["verified_at"]),
        "statusUpdatedAt": iso(row["status_updated_at"]),
        "assignee": row["assignee"],
        "reporter": {"name": row["reporter_name"], "email": row["reporter_email"]},
        "kindergarten": {
            "id": row["kindergarten_id"],
            "schoolName": row["school_name"],
            "county": row["county"],
            "district": row["district"],
            "address": row["address"],
            "phone": row["phone"],
        },
        "content": row["content"],
        "attachments": reports.attachment_payload(cur, row["id"], with_size=True),
        "messages": messages,
    }


def get_detail(cur, report_id, identity):
    county, err = _scope(identity)
    if err:
        return err
    row, err = _load_report(cur, report_id, county)
    if err:
        return err
    return respond(200, _detail_payload(cur, row))


def add_message(cur, report_id, data, identity):
    county, err = _scope(identity)
    if err:
        return err
    row, err = _load_report(cur, report_id, county)
    if err:
        return err

    kind = str(data.get("kind") or "reply").strip()
    if kind not in ("reply", "internal_note"):
        return error(400, "INVALID_MESSAGE_KIND", "kind 只能是 reply 或 internal_note")

    body = str(data.get("body") or "").strip()
    if not body:
        return error(400, "CONTENT_REQUIRED", "請填寫內容")
    if len(body) > MAX_MESSAGE_LEN:
        return error(400, "CONTENT_TOO_LONG", f"內容長度上限 {MAX_MESSAGE_LEN} 字")

    profile = auth.upsert_staff_profile(cur, identity)
    display = auth.staff_display_name(profile, identity)
    visible = 1 if kind == "reply" else 0

    emailed_at = None
    if kind == "reply" and data.get("notifyParent") and row["tracking_token"]:
        # 通知信只寫「有新回覆」＋追蹤連結，不夾帶回覆內容
        if mailer.send_reply_notice(
            row["reporter_email"], reports.case_no(row), row["tracking_token"]
        ):
            emailed_at = utcnow()

    cur.execute(
        """INSERT INTO parent_report_message
               (report_id, kind, visible_to_parent, author_type, author_username,
                author_display, body, emailed_at)
           VALUES (%s, %s, %s, 'staff', %s, %s, %s, %s)""",
        (report_id, kind, visible, identity["username"], display, body, emailed_at),
    )
    message_id = cur.lastrowid

    cur.execute(
        """SELECT id, kind, visible_to_parent, author_type, author_username,
                  author_display, body, from_status, to_status, emailed_at, created_at
           FROM parent_report_message WHERE id = %s""",
        (message_id,),
    )
    m = cur.fetchone()
    return respond(
        201,
        {
            "message": {
                "id": m["id"],
                "kind": m["kind"],
                "visibleToParent": bool(m["visible_to_parent"]),
                "authorType": m["author_type"],
                "authorUsername": m["author_username"],
                "authorDisplay": m["author_display"],
                "body": m["body"],
                "fromStatus": m["from_status"],
                "toStatus": m["to_status"],
                "emailedAt": iso(m["emailed_at"]),
                "createdAt": iso(m["created_at"]),
            },
            "emailed": emailed_at is not None,
        },
    )


def patch_report(cur, report_id, data, identity):
    county, err = _scope(identity)
    if err:
        return err
    row, err = _load_report(cur, report_id, county)
    if err:
        return err

    sets = []
    params = []
    new_status = None

    if "status" in data:
        new_status = str(data.get("status") or "").strip()
        if new_status not in reports.PUBLIC_STATUSES:
            return error(400, "INVALID_STATUS", "狀態值不合法")
        reason = str(data.get("statusReason") or "").strip()
        if new_status == "rejected" and not reason:
            return error(400, "STATUS_REASON_REQUIRED", "標記不受理時必須填寫理由")
        if new_status != row["status"]:
            sets += ["status = %s", "status_updated_at = %s"]
            params += [new_status, utcnow()]
        else:
            new_status = None
        if "statusReason" in data:
            sets.append("status_reason = %s")
            params.append(reason or None)
    elif "statusReason" in data:
        sets.append("status_reason = %s")
        params.append(str(data.get("statusReason") or "").strip() or None)

    if "assignee" in data:
        assignee = data.get("assignee")
        assignee = str(assignee).strip() if assignee else None
        sets.append("assignee = %s")
        params.append(assignee or None)

    if not sets:
        return error(400, "NOTHING_TO_UPDATE", "沒有要更新的欄位")

    cur.execute(
        f"UPDATE parent_report SET {', '.join(sets)} WHERE id = %s",
        params + [report_id],
    )

    if new_status:
        profile = auth.upsert_staff_profile(cur, identity)
        display = auth.staff_display_name(profile, identity)
        cur.execute(
            """INSERT INTO parent_report_message
                   (report_id, kind, visible_to_parent, author_type, author_username,
                    author_display, from_status, to_status)
               VALUES (%s, 'status_change', 1, 'staff', %s, %s, %s, %s)""",
            (report_id, identity["username"], display, row["status"], new_status),
        )
        if data.get("notifyParent") and row["tracking_token"]:
            mailer.send_reply_notice(
                row["reporter_email"], reports.case_no(row), row["tracking_token"]
            )
        # 結案 / 重啟調查都會改變「未結案回報」的件數，立刻重算風險指數
        risk.recompute(cur, row["kindergarten_id"])

    row, err = _load_report(cur, report_id, county)
    if err:
        return err
    return respond(200, _detail_payload(cur, row))
