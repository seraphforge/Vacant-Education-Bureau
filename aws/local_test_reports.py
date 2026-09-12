"""本機測試家長回報全流程（不需部署，透過 bastion SSH tunnel 連 RDS）。

用法：
    cd aws
    python local_test_reports.py

會做的事：
  1. 家長端：建草稿 -> 用錯的驗證碼 -> 用對的驗證碼 -> 追蹤頁
  2. 附件：presign（有設 ATTACH_BUCKET 才測）
  3. 政府端：清單 / 摘要 / 詳情 / 回覆 / 狀態變更 / 跨縣市防護
  4. 風險 placeholder
  5. 收尾把測試資料刪掉（--keep 可保留）

MAIL_MODE 固定為 dev，所以不會真的寄信，驗證碼直接從回應的 devOtp 取得。
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
# local_test 在 import 時就會把 sys.stdout 換成 UTF-8 wrapper，
# 所以這支不要自己再包一層（會把底層 buffer 關掉）。
import local_test  # noqa: E402  重用它的 read_config / open_tunnel

PASS = []
FAIL = []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    mark = "OK  " if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f"  {extra}" if extra else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="不要刪除測試資料")
    ap.add_argument("--bucket", default="", help="測 presign 用的 S3 bucket")
    args = ap.parse_args()

    conf = local_test.read_config()
    tunnel = local_test.open_tunnel()
    try:
        os.environ.update({
            "DB_HOST": "127.0.0.1",
            "DB_PORT": str(local_test.PORT),
            "DB_USER": conf["DbUser"],
            "DB_PASSWORD": conf["DbPassword"],
            "DB_NAME": conf["DbName"],
            "MAIL_MODE": "dev",
            "OTP_PEPPER": "local-test-pepper",
            "PUBLIC_BASE_URL": "http://localhost:4200",
            "ATTACH_BUCKET": args.bucket,
        })
        sys.path.insert(0, os.path.join(ROOT, "src"))
        import app  # noqa: E402

        def call(method, path, body=None, identity=None):
            event = {
                "rawPath": path,
                "requestContext": {"http": {"method": method}},
                "queryStringParameters": {},
            }
            if "?" in path:
                base, query = path.split("?", 1)
                event["rawPath"] = base
                event["queryStringParameters"] = dict(
                    kv.split("=", 1) for kv in query.split("&") if "=" in kv
                )
            if body is not None:
                event["body"] = json.dumps(body, ensure_ascii=False)
            if identity:
                claims = {
                    "sub": identity["sub"],
                    "cognito:username": identity["username"],
                    "custom:county": identity.get("county", ""),
                    "custom:agency": identity.get("agency", ""),
                }
                if identity.get("groups"):
                    claims["cognito:groups"] = identity["groups"]
                event["requestContext"]["authorizer"] = {"jwt": {"claims": claims}}
            res = app.handler(event, None)
            return res["statusCode"], json.loads(res["body"])

        # 找一間新北市的幼兒園來當回報對象
        with app.get_conn().cursor() as cur:
            cur.execute(
                "SELECT id, school_name, county FROM kindergarten "
                "WHERE county='新北市' ORDER BY id LIMIT 1"
            )
            school = cur.fetchone()
            cur.execute(
                "SELECT id, school_name, county FROM kindergarten "
                "WHERE county<>'新北市' ORDER BY id LIMIT 1"
            )
            other_school = cur.fetchone()
        print(f"\n測試用幼兒園：{school['school_name']}（id={school['id']}, {school['county']}）")
        print(f"跨縣市對照：{other_school['school_name']}（{other_school['county']}）")

        ntpc = {"sub": "00000000-0000-0000-0000-00000000ntpc"[:36],
                "username": "test.ntpc", "county": "新北市", "agency": "新北市教育局"}
        other = {"sub": "00000000-0000-0000-0000-0000000other"[:36],
                 "username": "test.other", "county": other_school["county"],
                 "agency": "對照縣市教育局"}

        # ---------- 1. 家長端 ----------
        print("\n=== 1. 家長端：建立草稿 ===")
        code, body = call("POST", "/api/reports/drafts", {
            "reporterName": "測試家長",
            "reporterEmail": "parent@example.com",
            "kindergartenId": school["id"],
            "content": "測試回報內容：孩子回家說老師會大聲斥責並罰站很久。",
        })
        check("建立草稿回 201", code == 201, f"status={code} {body.get('code','')}")
        draft_id = body.get("draftId")
        otp = body.get("devOtp")
        check("回傳 devOtp（dev mode）", bool(otp), f"otp={otp}")
        check("email 已遮蔽", body.get("reporterEmailMasked") == "p*****@example.com",
              str(body.get("reporterEmailMasked")))

        print("\n--- 驗證失敗的情況 ---")
        code, body = call("POST", f"/api/reports/drafts/{draft_id}/otp/verify",
                          {"code": "000000"})
        check("錯誤驗證碼回 OTP_INVALID", body.get("code") == "OTP_INVALID",
              f"attemptsLeft={body.get('detail', {}).get('attemptsLeft')}")

        code, body = call("POST", "/api/reports/drafts", {
            "reporterEmail": "bad-email", "kindergartenId": school["id"], "content": "x"})
        check("Email 格式錯誤回 INVALID_EMAIL", body.get("code") == "INVALID_EMAIL")
        code, body = call("POST", "/api/reports/drafts", {
            "reporterEmail": "a@b.co", "kindergartenId": 99999999, "content": "x"})
        check("幼兒園不存在回 KINDERGARTEN_NOT_FOUND",
              body.get("code") == "KINDERGARTEN_NOT_FOUND")
        code, body = call("POST", "/api/reports/drafts", {
            "reporterEmail": "a@b.co", "kindergartenId": school["id"], "content": "  "})
        check("事由空白回 CONTENT_REQUIRED", body.get("code") == "CONTENT_REQUIRED")

        print("\n--- 未驗證的草稿不該出現在政府端 ---")
        code, body = call("GET", "/api/secure/reports", identity=ntpc)
        pending_visible = any(i["id"] == draft_id for i in body.get("items", []))
        check("政府端清單看不到未驗證草稿", not pending_visible)

        print("\n=== 2. 附件 presign ===")
        code, body = call("POST", f"/api/reports/drafts/{draft_id}/attachments/presign",
                          {"files": [{"fileName": "a.jpg", "contentType": "image/jpeg",
                                      "sizeBytes": 1024}]})
        if args.bucket:
            check("presign 回 200", code == 200, str(body)[:120])
            up = (body.get("uploads") or [{}])[0]
            check("有 url 與 fields", bool(up.get("url")) and bool(up.get("fields")))
            check("key 綁在這張草稿底下",
                  str(up.get("key", "")).startswith(f"reports/{draft_id}/"),
                  str(up.get("key")))
        else:
            check("未設 bucket 時回 ATTACHMENTS_DISABLED",
                  body.get("code") == "ATTACHMENTS_DISABLED",
                  "（要測 presign 請加 --bucket）")
        code, body = call("POST", f"/api/reports/drafts/{draft_id}/attachments/presign",
                          {"files": [{"fileName": "a.pdf", "contentType": "application/pdf",
                                      "sizeBytes": 1024}]})
        check("非圖片回 UNSUPPORTED_FILE_TYPE",
              body.get("code") == "UNSUPPORTED_FILE_TYPE")
        code, body = call("POST", f"/api/reports/drafts/{draft_id}/attachments/presign",
                          {"files": [{"fileName": "big.jpg", "contentType": "image/jpeg",
                                      "sizeBytes": 6 * 1024 * 1024}]})
        check("超過 5MB 回 FILE_TOO_LARGE", body.get("code") == "FILE_TOO_LARGE")
        code, body = call("POST", f"/api/reports/drafts/{draft_id}/attachments",
                          {"files": [{"key": "reports/999999/evil.jpg",
                                      "contentType": "image/jpeg", "sizeBytes": 10}]})
        check("掛別人的 key 回 INVALID_ATTACHMENT_KEY",
              body.get("code") == "INVALID_ATTACHMENT_KEY")

        print("\n=== 3. 驗證成功、正式成案 ===")
        code, body = call("POST", f"/api/reports/drafts/{draft_id}/otp/verify",
                          {"code": otp})
        check("驗證成功回 200", code == 200, str(body)[:120])
        token = body.get("trackingToken")
        case_no = body.get("caseNo")
        check("拿到 trackingToken", bool(token) and len(token) >= 40)
        check("案號格式 R yymm-000000",
              bool(case_no) and case_no.startswith("R") and "-" in case_no, str(case_no))
        code, body = call("POST", f"/api/reports/drafts/{draft_id}/otp/verify",
                          {"code": otp})
        check("重複驗證回 DRAFT_ALREADY_VERIFIED",
              body.get("code") == "DRAFT_ALREADY_VERIFIED")

        print("\n=== 4. 追蹤頁 ===")
        code, body = call("GET", f"/api/reports/{token}")
        check("追蹤頁回 200", code == 200, str(body)[:120])
        check("狀態為 已通報", body.get("statusLabel") == "已通報")
        steps = body.get("steps") or []
        check("三個階段節點", len(steps) == 3, str([s["label"] for s in steps]))
        check("第一節點 current",
              steps and steps[0]["state"] == "current" and steps[1]["state"] == "pending")
        check("不回傳回報人 email", "email" not in json.dumps(body, ensure_ascii=False))
        check("有系統訊息", len(body.get("messages") or []) >= 1)
        code, body = call("GET", "/api/reports/" + "x" * 43)
        check("錯誤 token 回 404 REPORT_NOT_FOUND",
              code == 404 and body.get("code") == "REPORT_NOT_FOUND")

        # ---------- 5. 政府端 ----------
        print("\n=== 5. 政府端清單 / 摘要 ===")
        code, body = call("GET", "/api/secure/reports", identity=ntpc)
        check("清單回 200", code == 200, f"total={body.get('total')}")
        mine = [i for i in body.get("items", []) if i["id"] == draft_id]
        check("成案後出現在清單", len(mine) == 1)
        if mine:
            item = mine[0]
            check("清單 email 已遮蔽", item["reporterEmailMasked"] == "p*****@example.com")
            check("清單有摘要與附件數",
                  "contentExcerpt" in item and "attachmentCount" in item)
        check("縣市範圍鎖定", body.get("county") == "新北市")

        code, body = call("GET", "/api/secure/reports/summary", identity=ntpc)
        check("摘要回 200 且有 byStatus", code == 200 and "byStatus" in body,
              str(body.get("byStatus")))

        code, body = call("GET", "/api/secure/reports?status=closed", identity=ntpc)
        check("status 過濾有效（closed 不含本案）",
              all(i["id"] != draft_id for i in body.get("items", [])))

        code, body = call("GET", "/api/secure/reports", identity=other)
        check("別縣市看不到本案",
              all(i["id"] != draft_id for i in body.get("items", [])))
        code, body = call("GET", f"/api/secure/reports/{draft_id}", identity=other)
        check("別縣市讀單筆回 404", code == 404 and body.get("code") == "REPORT_NOT_FOUND")

        print("\n=== 6. 詳情 / 回覆 / 狀態變更 ===")
        code, body = call("GET", f"/api/secure/reports/{draft_id}", identity=ntpc)
        check("詳情回 200", code == 200)
        check("詳情有完整 email",
              body.get("reporter", {}).get("email") == "parent@example.com")
        check("詳情帶幼兒園資訊",
              body.get("kindergarten", {}).get("schoolName") == school["school_name"])

        code, body = call("POST", f"/api/secure/reports/{draft_id}/messages",
                          {"kind": "reply", "body": "您的案件已受理，將於 7 個工作日內查核。",
                           "notifyParent": True}, identity=ntpc)
        check("回覆家長回 201", code == 201, str(body)[:120])
        check("回覆的署名帶機關",
              "新北市教育局" in (body.get("message", {}).get("authorDisplay") or ""),
              str(body.get("message", {}).get("authorDisplay")))
        check("dev mode 下 emailed=False", body.get("emailed") is False)

        code, body = call("POST", f"/api/secure/reports/{draft_id}/messages",
                          {"kind": "internal_note", "body": "已電話聯繫園方。"},
                          identity=ntpc)
        check("內部備註 visibleToParent=False",
              body.get("message", {}).get("visibleToParent") is False)

        code, body = call("PATCH", f"/api/secure/reports/{draft_id}",
                          {"status": "investigating", "assignee": "test.ntpc"},
                          identity=ntpc)
        check("狀態改調查中回 200", code == 200 and body.get("status") == "investigating",
              str(body.get("statusLabel")))
        check("PATCH 直接回完整詳情", "messages" in body and "attachments" in body)
        kinds = [m["kind"] for m in body.get("messages", [])]
        check("自動產生 status_change 訊息", "status_change" in kinds, str(kinds))
        check("承辦人已指派", body.get("assignee") == "test.ntpc")

        code, body = call("PATCH", f"/api/secure/reports/{draft_id}",
                          {"status": "rejected"}, identity=ntpc)
        check("不受理未填理由回 STATUS_REASON_REQUIRED",
              body.get("code") == "STATUS_REASON_REQUIRED")
        code, body = call("PATCH", f"/api/secure/reports/{draft_id}",
                          {"status": "nonsense"}, identity=ntpc)
        check("狀態值不合法回 INVALID_STATUS", body.get("code") == "INVALID_STATUS")

        print("\n--- 家長端看到的變化 ---")
        code, body = call("GET", f"/api/reports/{token}")
        check("追蹤頁狀態同步為 調查中", body.get("statusLabel") == "調查中")
        steps = body.get("steps")
        check("第二節點 current，第一節點 done",
              steps[0]["state"] == "done" and steps[1]["state"] == "current")
        bodies = [m["body"] for m in body.get("messages", [])]
        check("家長看得到回覆", any("已受理" in (b or "") for b in bodies))
        check("家長看不到內部備註",
              not any("電話聯繫園方" in (b or "") for b in bodies), str(bodies))

        print("\n--- 不受理的節點呈現 ---")
        code, body = call("PATCH", f"/api/secure/reports/{draft_id}",
                          {"status": "rejected", "statusReason": "非本局管轄範圍",
                           "notifyParent": True}, identity=ntpc)
        check("改不受理成功", code == 200 and body.get("status") == "rejected")
        code, body = call("GET", f"/api/reports/{token}")
        steps = body.get("steps") or []
        check("不受理只有兩個節點", len(steps) == 2, str([s["label"] for s in steps]))
        check("第二節點是不受理且 current",
              steps[-1]["key"] == "rejected" and steps[-1]["state"] == "current")
        check("理由顯示給家長", body.get("statusReason") == "非本局管轄範圍")

        # ---------- 7. 風險 placeholder ----------
        print("\n=== 7. 風險指數 placeholder ===")
        code, body = call("GET", f"/api/secure/kindergartens/{school['id']}/risk",
                          identity=ntpc)
        check("風險端點回 200", code == 200, str(body)[:150])
        dims = body.get("dimensions") or []
        check("五個維度且 key 固定", len(dims) == 5 and dims[0]["key"] == "finance",
              str([d["key"] for d in dims]))
        check("isPlaceholder=True", body.get("isPlaceholder") is True)
        code, body = call("GET", f"/api/secure/kindergartens/{other_school['id']}/risk",
                          identity=ntpc)
        check("別縣市的園回 404", code == 404)

        code, body = call("GET", "/api/secure/kindergartens?pageSize=3", identity=ntpc)
        first = (body.get("items") or [{}])[0]
        check("secure 清單帶 risk_score / risk_level 欄位",
              "risk_score" in first and "risk_level" in first, str(list(first.keys())))

        code, body = call(
            "GET", "/api/secure/kindergartens?pageSize=5&sortBy=risk_score&sortDir=desc",
            identity=ntpc)
        scores = [i.get("risk_score") for i in body.get("items", [])]
        seeded = any(s is not None for s in scores)
        check("依 risk_score 排序（需先跑 tools/seed_risk_placeholder.py）",
              code == 200 and (not seeded or scores == sorted(
                  [s for s in scores if s is not None], reverse=True)),
              str(scores))
        levels = {i.get("risk_level") for i in body.get("items", [])}
        check("風險等級值在白名單內",
              levels <= {"high", "medium", "normal", None}, str(levels))

        code, body = call("GET", "/api/kindergartens?pageSize=1")
        check("公開清單不含風險欄位（僅政府端可見）",
              "risk_score" not in (body.get("items") or [{}])[0])

        code, body = call("GET", "/api/secure/me", identity=ntpc)
        check("/api/secure/me 有 displayName 欄位", "displayName" in body, str(body))
        with app.get_conn().cursor() as cur:
            cur.execute("SELECT COUNT(*) n FROM staff_profile WHERE username=%s",
                        ("test.ntpc",))
            check("staff_profile 已 JIT 建檔", cur.fetchone()["n"] == 1)

        # ---------- 收尾 ----------
        if not args.keep:
            with app.get_conn().cursor() as cur:
                cur.execute("DELETE FROM parent_report WHERE id=%s", (draft_id,))
                cur.execute("DELETE FROM parent_report WHERE reporter_email IN "
                            "('parent@example.com','a@b.co')")
                cur.execute("DELETE FROM staff_profile WHERE username IN "
                            "('test.ntpc','test.other')")
            print("\n已清除測試資料（--keep 可保留）")
        else:
            print(f"\n保留測試資料：parent_report.id={draft_id}, token={token}")

        print(f"\n===== 結果：{len(PASS)} 通過 / {len(FAIL)} 失敗 =====")
        if FAIL:
            for name in FAIL:
                print("  FAILED:", name)
            sys.exit(1)
    finally:
        tunnel.terminate()


if __name__ == "__main__":
    main()
