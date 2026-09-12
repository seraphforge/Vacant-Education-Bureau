# -*- coding: utf-8 -*-
"""端到端測試輿情分析：真的 worker + 真的 RDS + 真的 Bedrock／Comprehend。

不需要部署。透過 bastion SSH tunnel 連 RDS（作法與 aws/local_test.py 相同），
把 opinion_worker 指到 tunnel 的本機 port，然後跑完整流程並印出結果。

用法（在專案根目錄）：
    $env:DB_PASSWORD = "（從 aws/deploy.config.ps1 取得）"
    python tools/opinion_e2e.py                  # 自動挑一間有裁罰紀錄的新北市園所
    python tools/opinion_e2e.py --kg-id 12345    # 指定園所
    python tools/opinion_e2e.py --keep           # 保留這次的 job（預設會清掉）

預設會在最後把這次建立的 job 與 items 刪掉，避免測試資料留在 Demo 用的資料庫裡。
"""
import argparse
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", write_through=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scraper"))
sys.path.insert(0, os.path.join(ROOT, "aws", "src"))

import db as tunnel_db  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kg-id", type=int, default=None)
    parser.add_argument("--keep", action="store_true", help="保留測試產生的 job")
    parser.add_argument(
        "--remote",
        metavar="FUNCTION",
        nargs="?",
        const="ntpc-kg-opinion-worker",
        default=None,
        help="改成叫已部署的 worker Lambda（驗證真實的 VPC/NAT 網路），而不是在本機跑",
    )
    args = parser.parse_args()

    # 先開 tunnel，再把 opinion_worker 的 DB_* 指到 tunnel 的本機 port。
    # common.py 在 import 時就讀環境變數，所以順序不能顛倒。
    tunnel_db.open_tunnel()
    os.environ["DB_HOST"] = "127.0.0.1"
    os.environ["DB_PORT"] = str(tunnel_db.LOCAL_PORT)
    os.environ["DB_USER"] = tunnel_db.DB_USER
    os.environ["DB_PASSWORD"] = tunnel_db.DB_PASSWORD
    os.environ["DB_NAME"] = tunnel_db.DB_NAME

    import common  # noqa: E402
    import opinion  # noqa: E402
    import opinion_worker  # noqa: E402

    conn = common.get_conn()
    with conn.cursor() as cur:
        if args.kg_id:
            cur.execute(
                "SELECT id, school_name, county FROM kindergarten WHERE id = %s",
                (args.kg_id,),
            )
            school = cur.fetchone()
        else:
            # 挑一間有裁罰紀錄的，這樣一定有可歸屬本園的內容可以驗證
            cur.execute(
                """SELECT k.id, k.school_name, k.county, COUNT(p.id) AS punishments
                   FROM kindergarten k
                   JOIN kindergarten_punishment p ON p.kindergarten_id = k.id
                   WHERE k.county = '新北市'
                   GROUP BY k.id, k.school_name, k.county
                   ORDER BY punishments DESC LIMIT 1"""
            )
            school = cur.fetchone()
        if not school:
            sys.exit("找不到測試用的幼兒園")

        print(f"測試園所：[{school['id']}] {school['school_name']}（{school['county']}）")

        # 直接建 job（等同 POST /opinion/scans 做的事，但不需要 API Gateway 與 token）
        cur.execute(
            """INSERT INTO opinion_scan_job
                   (kindergarten_id, status, requested_by, requested_username,
                    requested_county, requested_at)
               VALUES (%s, 'queued', 'e2e-test', 'e2e-test', %s, %s)""",
            (school["id"], school["county"], common.utcnow()),
        )
        job_id = cur.lastrowid
    print(f"建立 job {job_id}")

    if args.remote:
        # 同步 invoke，這樣可以直接看到 worker 在 Lambda 裡的回傳與錯誤。
        # 正式流程是非同步（InvocationType='Event'），但驗證網路用同步比較好看。
        import boto3

        print(f"呼叫已部署的 worker：{args.remote}（Lambda 內執行，測 VPC/NAT 網路）\n")
        client = boto3.client("lambda", region_name="us-east-1")
        response = client.invoke(
            FunctionName=args.remote,
            InvocationType="RequestResponse",
            Payload=json.dumps({"jobId": job_id}).encode("utf-8"),
        )
        payload = response["Payload"].read().decode("utf-8")
        if response.get("FunctionError"):
            print(f"Lambda 回報錯誤（{response['FunctionError']}）：{payload[:1500]}")
            result = {"ok": False}
        else:
            result = json.loads(payload)
    else:
        print(f"在本機執行 worker（會刻意放慢，請等 1–3 分鐘）\n")
        result = opinion_worker.handler({"jobId": job_id}, None)
    print(f"\nworker 回傳：{result}\n")

    failures = []

    def check(label, condition, detail=""):
        print(("  [OK]   " if condition else "  [FAIL] ") + label + (f" -> {detail}" if detail else ""))
        if not condition:
            failures.append(label)

    with conn.cursor() as cur:
        cur.execute("SELECT * FROM opinion_scan_job WHERE id = %s", (job_id,))
        job = cur.fetchone()
        cur.execute(
            "SELECT * FROM opinion_item WHERE job_id = %s "
            "ORDER BY FIELD(attribution,'confirmed','ambiguous','unrelated'), id",
            (job_id,),
        )
        items = cur.fetchall()
        cur.execute(
            "SELECT action, detail FROM opinion_scan_audit WHERE job_id = %s", (job_id,)
        )
        audits = cur.fetchall()
        cur.execute(
            "SELECT dimensions FROM risk_score_current WHERE kindergarten_id = %s",
            (school["id"],),
        )
        risk = cur.fetchone()

    print("== job ==")
    check("狀態為 done", job["status"] == "done", job["status"] + (f" / {job['error']}" if job["error"] else ""))
    check("有 finished_at", job["finished_at"] is not None)
    check("有摘要", bool(job["summary"]))
    check("有 opinion_score", job["opinion_score"] is not None, str(job["opinion_score"]))
    check("記錄了使用的模型", bool(job["model_id"]), str(job["model_id"]))
    check("item_count 與實際列數一致", job["item_count"] == len(items),
          f"job={job['item_count']} rows={len(items)}")

    print("\n摘要：")
    for line in (job["summary"] or "").splitlines():
        print("   " + line)

    print(f"\n== items（{len(items)} 筆）==")
    for item in items[:12]:
        print(
            f"   [{item['attribution']:<9}] [{item['source_type']:<6}] "
            f"v={item['verified']} s={item['sentiment']} neg={item['negative_score']} "
            f"tags={item['risk_tags']}"
        )
        print(f"      {item['title'][:80]}")
        print(f"      {item['url'][:90]}")
    if len(items) > 12:
        print(f"   …另外 {len(items) - 12} 筆")

    check("有寫入 items", len(items) > 0, str(len(items)))
    check("裁罰紀錄被標為 confirmed 且 verified",
          any(i["attribution"] == "confirmed" and i["verified"] for i in items)
          or all(i["source_type"] != "gov" for i in items),
          "（若該園沒有裁罰紀錄則不適用）")
    check("每筆都有 url_hash", all(i["url_hash"] for i in items))
    check("非官方來源不會被標成 verified",
          all(not i["verified"] for i in items if i["source_type"] in ("news", "web", "social")))

    print("\n== audit ==")
    for audit in audits:
        print(f"   {audit['action']}: {audit['detail']}")
    check("有 scan_finished 稽核紀錄",
          any(a["action"] == "scan_finished" for a in audits))

    print("\n== 風險雷達圖 ==")
    print("   dimensions:", (risk or {}).get("dimensions"))
    check("opinion 維度已寫回 risk_score_current",
          risk is not None and "opinion" in str(risk.get("dimensions")))

    print("\n== API 回應（GET /opinion）==")
    identity = {"sub": "e2e", "username": "e2e", "county": school["county"],
                "agency": "", "groups": [], "isAdmin": False}
    with conn.cursor() as cur:
        response = opinion.get_latest(cur, school["id"], identity)
    body = json.loads(response["body"])
    print(f"   HTTP {response['statusCode']} hasData={body['hasData']} "
          f"items={len(body['items'])} score={body.get('opinionScore')}")
    check("API 回 200", response["statusCode"] == 200)
    check("API hasData=true", body["hasData"] is True)
    check("API 帶免責說明", "不得單獨作為裁處依據" in body["disclaimer"])
    check("API items 欄位齊全",
          not body["items"]
          or {"title", "url", "source", "publishedAt", "attribution"} <= set(body["items"][0]))

    print("\n== 縣市權限 ==")
    other = dict(identity, county="臺北市")
    with conn.cursor() as cur:
        denied = opinion.get_latest(cur, school["id"], other)
    check("其他縣市的帳號查不到（404）", denied["statusCode"] == 404, str(denied["statusCode"]))

    if not args.keep:
        with conn.cursor() as cur:
            # opinion_item 與 audit 都是 ON DELETE CASCADE / 手動清
            cur.execute("DELETE FROM opinion_scan_audit WHERE job_id = %s", (job_id,))
            cur.execute("DELETE FROM opinion_scan_job WHERE id = %s", (job_id,))
        print(f"\n已清除測試 job {job_id}（--keep 可保留）")
        print("   註：risk_score_current 的 opinion 維度會留下這次算出的分數，"
              "因為那是該園真實資料的計算結果，不算測試殘留。")

    conn.close()
    print()
    if failures:
        print(f"FAILED {len(failures)}: " + "; ".join(failures))
        return 1
    print("端到端測試全部通過")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
