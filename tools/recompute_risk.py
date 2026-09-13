# -*- coding: utf-8 -*-
"""全量重算風險指數（正式演算法），並清掉舊的 placeholder 假分數。

演算法定義在 aws/src/risk.py（這支只負責批次餵資料，不重複實作一份公式）：
  財務法遵 0.75 / 家長回報 1.00 / 輿情關注 0.50 / 裁罰紀錄 0.75，
  缺資料的維度不計分，權重平均分給其他維度，總分 = 加權平均（0-100）。

用法：
    $env:DB_PASSWORD = "..."
    python tools/recompute_risk.py                # 新北市（1108 間）
    python tools/recompute_risk.py --all          # 全國 6747 間
    python tools/recompute_risk.py --keep-placeholder   # 不刪舊的假資料
    python tools/recompute_risk.py --dry-run      # 只算不寫

為什麼要批次跑：API 是「打開風險 Tab 才即時重算」，但 dashboard 清單直接讀
risk_score_current 排序。沒跑過的學校在清單上會是空白，所以第一次上線／改完
公式後要跑這支把全部補齊。之後家長回報與輿情事件會自動增量更新。
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

import db  # noqa: E402


def load_risk_module():
    """risk.py 會 import common，而 common 在 import 時就要 DB_* 環境變數。"""
    os.environ.setdefault("DB_HOST", "127.0.0.1")
    os.environ.setdefault("DB_PORT", str(db.LOCAL_PORT))
    os.environ.setdefault("DB_USER", db.DB_USER)
    os.environ.setdefault("DB_PASSWORD", db.DB_PASSWORD)
    os.environ.setdefault("DB_NAME", db.DB_NAME)
    import risk  # noqa: E402

    return risk


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--county", default="新北市")
    ap.add_argument("--all", action="store_true", help="不限縣市")
    ap.add_argument(
        "--keep-placeholder", action="store_true", help="不要刪除舊的 placeholder 假分數"
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = db.connect()
    risk = load_risk_module()

    with conn.cursor() as cur:
        if not args.keep_placeholder and not args.dry_run:
            cur.execute("DELETE FROM risk_score_current WHERE is_placeholder = 1")
            print(f"清掉 placeholder 假分數：{cur.rowcount} 列")

        if args.all:
            cur.execute("SELECT id FROM kindergarten ORDER BY id")
        else:
            cur.execute(
                "SELECT id FROM kindergarten WHERE county = %s ORDER BY id",
                (args.county,),
            )
        ids = [r["id"] for r in cur.fetchall()]
        if not ids:
            sys.exit("找不到符合條件的幼兒園")
        print(f"目標：{len(ids)} 間（{'全國' if args.all else args.county}）")

        # 一次把四種訊號抓成 dict，避免每間學校 4 個 round trip（6747 間會很慢）
        cur.execute(
            "SELECT kindergarten_id, compliance_index, fiscal_year, overall_level "
            "FROM finance_report_current"
        )
        finance = {r["kindergarten_id"]: r for r in cur.fetchall()}

        cur.execute(
            "SELECT kindergarten_id, "
            "  SUM(status IN %s) AS open_count, "
            "  SUM(status <> 'pending_verification') AS total_count "
            "FROM parent_report GROUP BY kindergarten_id",
            (risk.OPEN_REPORT_STATUSES,),
        )
        reports = {r["kindergarten_id"]: r for r in cur.fetchall()}

        # 每間園最後一次成功掃描的關注指數
        cur.execute(
            """SELECT j.kindergarten_id, j.opinion_score, j.finished_at
               FROM opinion_scan_job j
               JOIN (SELECT kindergarten_id, MAX(id) AS id FROM opinion_scan_job
                     WHERE status = 'done' AND opinion_score IS NOT NULL
                     GROUP BY kindergarten_id) last
                 ON last.id = j.id"""
        )
        opinions = {r["kindergarten_id"]: r for r in cur.fetchall()}

        cur.execute(
            "SELECT kindergarten_id, COUNT(*) AS n, COALESCE(SUM(fine_amount),0) AS fine "
            "FROM kindergarten_punishment WHERE kindergarten_id IS NOT NULL "
            "GROUP BY kindergarten_id"
        )
        punishments = {r["kindergarten_id"]: r for r in cur.fetchall()}

        print(
            f"訊號來源：財報 {len(finance)} 間 / 有回報 {len(reports)} 間 / "
            f"有輿情 {len(opinions)} 間 / 有裁罰 {len(punishments)} 間"
        )

        rows = []
        buckets = {"high": 0, "medium": 0, "normal": 0, "none": 0}
        for kg_id in ids:
            fin = finance.get(kg_id) or {}
            rep = reports.get(kg_id) or {}
            opi = opinions.get(kg_id) or {}
            pun = punishments.get(kg_id) or {}
            signals = {
                "financeIndex": (
                    None if fin.get("compliance_index") is None
                    else float(fin["compliance_index"])
                ),
                "financeYear": fin.get("fiscal_year"),
                "financeLevel": fin.get("overall_level"),
                "openReports": int(rep.get("open_count") or 0),
                "totalReports": int(rep.get("total_count") or 0),
                "opinionScore": (
                    None if opi.get("opinion_score") is None
                    else float(opi["opinion_score"])
                ),
                "opinionAt": opi.get("finished_at"),
                "punishCount": int(pun.get("n") or 0),
                "fineTotal": int(pun.get("fine") or 0),
            }
            total, dims = risk.apply_weights(risk.score_dimensions(signals))
            level = risk.level_of(total)
            buckets[level or "none"] += 1
            rows.append(
                (
                    kg_id,
                    total,
                    level,
                    json.dumps(dims, ensure_ascii=False),
                    risk.MODEL_VERSION,
                )
            )

        print(f"\n分佈：high(>={risk.HIGH_THRESHOLD}) {buckets['high']} / "
              f"medium({risk.MEDIUM_THRESHOLD}-{risk.HIGH_THRESHOLD - 1}) {buckets['medium']} "
              f"/ normal(<{risk.MEDIUM_THRESHOLD}) {buckets['normal']} / 無法計算 {buckets['none']}")

        if args.dry_run:
            print("--dry-run：沒有寫入")
        else:
            cur.executemany(
                """INSERT INTO risk_score_current
                       (kindergarten_id, total_score, risk_level, dimensions,
                        model_version, is_placeholder, computed_at)
                   VALUES (%s, %s, %s, %s, %s, 0, UTC_TIMESTAMP())
                   ON DUPLICATE KEY UPDATE
                       total_score = VALUES(total_score),
                       risk_level = VALUES(risk_level),
                       dimensions = VALUES(dimensions),
                       model_version = VALUES(model_version),
                       is_placeholder = 0,
                       computed_at = UTC_TIMESTAMP()""",
                rows,
            )
            print(f"寫入 {len(rows)} 列（is_placeholder = 0）")

        cur.execute(
            "SELECT k.id, k.school_name, r.total_score, r.risk_level "
            "FROM risk_score_current r JOIN kindergarten k ON k.id = r.kindergarten_id "
            "WHERE r.is_placeholder = 0 AND r.total_score > 0 "
            "ORDER BY r.total_score DESC, k.id LIMIT 15"
        )
        print("\n分數最高的 15 間：")
        for r in cur.fetchall():
            print(f"  {r['total_score']:>6} {r['risk_level']:<7} {r['school_name']}")

        cur.execute(
            "SELECT COUNT(*) AS n, SUM(is_placeholder) AS ph FROM risk_score_current"
        )
        row = cur.fetchone()
        print(f"\nrisk_score_current 共 {row['n']} 列，其中 placeholder {int(row['ph'] or 0)} 列")


if __name__ == "__main__":
    main()
