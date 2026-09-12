"""輿情分析 —— 政府端一鍵啟動的非同步掃描 API。

為什麼要非同步
--------------
web search + 抓取 + LLM 摘要動輒數十秒，API Gateway HTTP API 上限 29 秒、
ApiFunction 的 Timeout 是 20 秒，同步做一定超時。所以：

    POST .../opinion/scans        建立 job -> 非同步 invoke worker -> 回 jobId
    GET  .../opinion/scans/{job}  前端輪詢進度
    GET  .../opinion              取最新一次完成的結果（開 Tab 時就顯示舊資料）

權限與濫用防護
--------------
* 縣市範圍一律由 token 決定（auth.scoped_county），不接受前端傳 county，
  否則改個網址就能對別的縣市的園所發動掃描。
* 同一園所有 job 在跑就直接回那個 job，不重複發動。
* 剛完成的掃描在 COOLDOWN_MINUTES 內回 429：這是對外部網站的基本禮貌，
  也避免有人拿政府端當爬蟲跳板。
* 每次啟動都寫 opinion_scan_audit。

資料語意
--------
回給前端的每一筆都是「需要人工關注的線索」，不是已證實的事實；
disclaimer 欄位由後端統一提供，前端直接顯示，不要自己編。
"""

import json
import os

import auth
from common import error, iso, respond, utcnow

# 掃描完成後多久內不准重跑（分鐘）
COOLDOWN_MINUTES = 10

# 還在跑的狀態
ACTIVE_STATUSES = ("queued", "searching", "analyzing")

# 前端顯示用的狀態文字。後端定案，前端不要自己 mapping。
STATUS_LABELS = {
    "queued": "已排入佇列",
    "searching": "正在搜尋公開資訊",
    "analyzing": "AI 正在整理與判讀",
    "done": "已完成",
    "failed": "執行失敗",
}

ATTRIBUTION_LABELS = {
    "confirmed": "已比對到本園全名",
    "ambiguous": "同名／多園候選，需人工確認",
    "unrelated": "一般幼教議題，未歸屬本園",
}

DISCLAIMER = (
    "本頁資料由 AI 自動蒐集公開網路資訊產生，僅代表「需要人工關注的程度」，"
    "不是違法事實、不代表指控成立，也不得單獨作為裁處依據。"
    "同名或附設園所可能誤判，請以人工複核與官方紀錄為準。"
)


def _kindergarten_in_scope(cur, kg_id, identity):
    """回 (school_row, error_response)。縣市不在權限內一律當 404，不洩漏存在性。"""
    county = auth.scoped_county(identity)
    if county is None and not identity["isAdmin"]:
        return None, error(
            403, "FORBIDDEN", "這個帳號沒有設定縣市（custom:county），請聯絡管理者"
        )
    cur.execute(
        "SELECT id, school_name, county, district FROM kindergarten WHERE id = %s",
        (kg_id,),
    )
    school = cur.fetchone()
    if not school or (county and school["county"] != county):
        return None, error(404, "KINDERGARTEN_NOT_FOUND", "查無此幼兒園")
    return school, None


def _audit(cur, kg_id, identity, action, job_id=None, detail=None):
    cur.execute(
        """INSERT INTO opinion_scan_audit
               (job_id, kindergarten_id, actor_sub, actor_username, actor_county,
                action, detail, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            job_id,
            kg_id,
            identity.get("sub"),
            identity.get("username"),
            identity.get("county") or None,
            action,
            detail,
            utcnow(),
        ),
    )


def _job_json(row, school=None):
    """job 列 -> API 回應。school 有給就一併帶出校名，前端不用再查一次。"""
    out = {
        "jobId": row["id"],
        "kindergartenId": row["kindergarten_id"],
        "status": row["status"],
        "statusLabel": STATUS_LABELS.get(row["status"], row["status"]),
        "requestedAt": iso(row["requested_at"]),
        "startedAt": iso(row.get("started_at")),
        "finishedAt": iso(row.get("finished_at")),
        "requestedBy": row.get("requested_username"),
        "queryCount": row.get("query_count") or 0,
        "itemCount": row.get("item_count") or 0,
        "confirmedCount": row.get("confirmed_count") or 0,
        "negativeCount": row.get("negative_count") or 0,
        "opinionScore": (
            None if row.get("opinion_score") is None else float(row["opinion_score"])
        ),
        "summary": row.get("summary"),
        "searchProvider": row.get("search_provider"),
        "modelId": row.get("model_id"),
        "error": row.get("error"),
    }
    if school:
        out["schoolName"] = school["school_name"]
    return out


def _item_json(row):
    return {
        "id": row["id"],
        "title": row["title"],
        "url": row["url"],
        "source": row["source"],
        "sourceType": row["source_type"],
        "publishedAt": iso(row["published_at"]),
        "snippet": row["snippet"],
        "sentiment": row["sentiment"],
        "negativeScore": (
            None if row["negative_score"] is None else float(row["negative_score"])
        ),
        "riskTags": [t for t in (row["risk_tags"] or "").split(",") if t],
        "attribution": row["attribution"],
        "attributionLabel": ATTRIBUTION_LABELS.get(
            row["attribution"], row["attribution"]
        ),
        "confidence": (
            None if row["confidence"] is None else float(row["confidence"])
        ),
        "verified": bool(row["verified"]),
    }


def _dispatch_worker(job_id):
    """非同步叫醒 worker Lambda。回 (ok, message)。

    API Lambda 待在沒有 NAT 的子網，所以 lambda:Invoke 必須走 Lambda 的
    interface VPC endpoint（template.yaml 有建，作法與 SES endpoint 相同）。
    """
    function = os.environ.get("OPINION_WORKER_FUNCTION", "")
    if not function:
        return False, "OPINION_WORKER_FUNCTION 未設定"
    try:
        import boto3

        boto3.client("lambda").invoke(
            FunctionName=function,
            InvocationType="Event",  # 非同步：不等結果
            Payload=json.dumps({"jobId": job_id}).encode("utf-8"),
        )
        return True, None
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR dispatch worker: {type(exc).__name__}: {exc}")
        return False, f"{type(exc).__name__}: {exc}"[:255]


def start_scan(cur, kg_id, identity):
    """POST /api/secure/kindergartens/{id}/opinion/scans"""
    school, err = _kindergarten_in_scope(cur, kg_id, identity)
    if err:
        return err

    # 已經有 job 在跑就沿用，不要重複發動
    cur.execute(
        "SELECT * FROM opinion_scan_job WHERE kindergarten_id = %s AND status IN %s "
        "ORDER BY id DESC LIMIT 1",
        (kg_id, ACTIVE_STATUSES),
    )
    running = cur.fetchone()
    if running:
        body = _job_json(running, school)
        body["reused"] = True
        return respond(200, body)

    # 冷卻期：剛跑完就別再跑
    cur.execute(
        "SELECT id, finished_at, "
        "TIMESTAMPDIFF(SECOND, finished_at, UTC_TIMESTAMP()) AS age_seconds "
        "FROM opinion_scan_job "
        "WHERE kindergarten_id = %s AND status = 'done' AND finished_at IS NOT NULL "
        "ORDER BY id DESC LIMIT 1",
        (kg_id,),
    )
    last = cur.fetchone()
    if last and last["age_seconds"] is not None:
        remaining = COOLDOWN_MINUTES * 60 - int(last["age_seconds"])
        if remaining > 0:
            _audit(
                cur,
                kg_id,
                identity,
                "scan_rejected",
                last["id"],
                f"cooldown {remaining}s",
            )
            return error(
                429,
                "SCAN_COOLDOWN",
                f"這間幼兒園剛在 {COOLDOWN_MINUTES} 分鐘內掃描過，請稍後再試",
                {"retryAfterSeconds": remaining, "lastJobId": last["id"]},
            )

    now = utcnow()
    cur.execute(
        """INSERT INTO opinion_scan_job
               (kindergarten_id, status, requested_by, requested_username,
                requested_county, requested_at)
           VALUES (%s, 'queued', %s, %s, %s, %s)""",
        (
            kg_id,
            identity.get("sub"),
            identity.get("username"),
            identity.get("county") or None,
            now,
        ),
    )
    job_id = cur.lastrowid
    _audit(cur, kg_id, identity, "scan_requested", job_id)

    ok, message = _dispatch_worker(job_id)
    if not ok:
        cur.execute(
            "UPDATE opinion_scan_job SET status='failed', error=%s, finished_at=%s "
            "WHERE id = %s",
            (f"無法啟動分析程序：{message}", utcnow(), job_id),
        )

    cur.execute("SELECT * FROM opinion_scan_job WHERE id = %s", (job_id,))
    body = _job_json(cur.fetchone(), school)
    body["disclaimer"] = DISCLAIMER
    return respond(202 if ok else 500, body)


def get_job(cur, kg_id, job_id, identity):
    """GET /api/secure/kindergartens/{id}/opinion/scans/{jobId}"""
    school, err = _kindergarten_in_scope(cur, kg_id, identity)
    if err:
        return err
    cur.execute(
        "SELECT * FROM opinion_scan_job WHERE id = %s AND kindergarten_id = %s",
        (job_id, kg_id),
    )
    row = cur.fetchone()
    if not row:
        return error(404, "SCAN_JOB_NOT_FOUND", "查無此掃描工作")
    body = _job_json(row, school)
    body["disclaimer"] = DISCLAIMER
    if row["status"] == "done":
        body["items"] = _load_items(cur, row["id"])
    return respond(200, body)


def _load_items(cur, job_id):
    # confirmed 排前面，其次負面分數高的；一般幼教議題排最後
    cur.execute(
        """SELECT * FROM opinion_item WHERE job_id = %s
           ORDER BY FIELD(attribution, 'confirmed', 'ambiguous', 'unrelated'),
                    negative_score DESC, published_at DESC, id DESC""",
        (job_id,),
    )
    return [_item_json(r) for r in cur.fetchall()]


def get_latest(cur, kg_id, identity):
    """GET /api/secure/kindergartens/{id}/opinion

    開 Tab 時呼叫。從來沒掃過就回 hasData=false，讓前端顯示「尚未分析」+ 按鈕。
    """
    school, err = _kindergarten_in_scope(cur, kg_id, identity)
    if err:
        return err

    cur.execute(
        "SELECT * FROM opinion_scan_job WHERE kindergarten_id = %s "
        "ORDER BY id DESC LIMIT 1",
        (kg_id,),
    )
    latest = cur.fetchone()

    body = {
        "kindergartenId": kg_id,
        "schoolName": school["school_name"],
        "disclaimer": DISCLAIMER,
        "cooldownMinutes": COOLDOWN_MINUTES,
        "hasData": False,
        "job": None,
        "items": [],
    }
    if not latest:
        return respond(200, body)

    body["job"] = _job_json(latest, school)

    # 最近一次可能還在跑或失敗；表格要顯示的是「最後一次成功的結果」
    if latest["status"] == "done":
        done = latest
    else:
        cur.execute(
            "SELECT * FROM opinion_scan_job WHERE kindergarten_id = %s "
            "AND status = 'done' ORDER BY id DESC LIMIT 1",
            (kg_id,),
        )
        done = cur.fetchone()

    if done:
        body["hasData"] = True
        body["resultJobId"] = done["id"]
        body["resultAt"] = iso(done["finished_at"])
        body["summary"] = done["summary"]
        body["opinionScore"] = (
            None if done["opinion_score"] is None else float(done["opinion_score"])
        )
        body["items"] = _load_items(cur, done["id"])
    return respond(200, body)
