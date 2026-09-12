# -*- coding: utf-8 -*-
"""灌入風險指數的 placeholder 假分數，讓前端能開發 dashboard 與雷達圖。

**這是假資料**，`risk_score_current.is_placeholder = 1`，API 也會照原樣回
`isPlaceholder: true`，前端可以在畫面標示「示意資料」。真的演算法接上之後，
換掉這支腳本、把 is_placeholder 設成 0 即可，API 與前端都不用改。

分數是用幼兒園 id 做雜湊算出來的（同一間園每次跑結果一樣），並刻意讓
高風險(>=80)／中風險(60-79)／一般(<60) 三種都出現，方便驗證整列上色規則。

用法：
    python tools/seed_risk_placeholder.py                 # 預設只灌新北市
    python tools/seed_risk_placeholder.py --county 臺北市
    python tools/seed_risk_placeholder.py --all           # 全國
    python tools/seed_risk_placeholder.py --clear         # 清掉假資料
"""
import argparse
import hashlib
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", write_through=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scraper"))

import db  # noqa: E402

MODEL_VERSION = "placeholder-v0"

# 必須與 aws/src/risk.py 的 DIMENSIONS 一致（key 與 weight）
DIMENSIONS = [
    ("finance", "財務異常", 0.30),
    ("compliance", "裁罰紀錄", 0.30),
    ("opinion", "輿情負面", 0.20),
    ("parent_report", "家長回報", 0.10),
    ("data_quality", "資料完整度", 0.10),
]
HIGH_THRESHOLD = 80
MEDIUM_THRESHOLD = 60


def level_of(score):
    if score >= HIGH_THRESHOLD:
        return "high"
    if score >= MEDIUM_THRESHOLD:
        return "medium"
    return "normal"


def fake_scores(kg_id, punish_count, fine_total):
    """用 id 的雜湊產生穩定的假分數；有裁罰紀錄的園分數會偏高（看起來合理一些）。

    刻意先抽一個「整體嚴重度」再對各維度加減雜訊，而不是每個維度獨立亂數：
    獨立亂數經過加權平均會全部擠在中間，就看不到高風險（紫色）的案例了。
    """
    digest = hashlib.sha256(str(kg_id).encode()).digest()
    severity = digest[0] / 255 * 100  # 0 ~ 100
    dims = []
    for i, (key, label, weight) in enumerate(DIMENSIONS):
        jitter = digest[i + 1] % 31 - 15  # -15 ~ +15
        base = severity + jitter
        if key == "compliance":
            # 有裁罰就往上加，罰鍰越多加越多
            base += min(punish_count * 12, 40) + (10 if fine_total else 0)
        dims.append(
            {
                "key": key,
                "label": label,
                "weight": weight,
                "score": int(max(0, min(100, round(base)))),
            }
        )

    total = round(sum(d["score"] * d["weight"] for d in dims), 2)
    return total, dims


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--county", default="新北市")
    ap.add_argument("--all", action="store_true", help="不限縣市")
    ap.add_argument("--clear", action="store_true", help="刪除所有 placeholder 分數")
    args = ap.parse_args()

    conn = db.connect()
    with conn.cursor() as cur:
        if args.clear:
            cur.execute("DELETE FROM risk_score_current WHERE is_placeholder = 1")
            print(f"已刪除 {cur.rowcount} 列 placeholder 分數")
            return

        if args.all:
            cur.execute("SELECT id FROM kindergarten")
        else:
            cur.execute("SELECT id FROM kindergarten WHERE county = %s", (args.county,))
        ids = [r["id"] for r in cur.fetchall()]
        if not ids:
            sys.exit("找不到符合條件的幼兒園")
        print(f"目標幼兒園：{len(ids)} 間（{'全國' if args.all else args.county}）")

        # 裁罰紀錄拿來讓假分數看起來跟真實資料有點關係
        cur.execute(
            "SELECT kindergarten_id, COUNT(*) AS n, COALESCE(SUM(fine_amount),0) AS fine "
            "FROM kindergarten_punishment WHERE kindergarten_id IS NOT NULL "
            "GROUP BY kindergarten_id"
        )
        punish = {r["kindergarten_id"]: (r["n"], int(r["fine"])) for r in cur.fetchall()}

        rows = []
        buckets = {"high": 0, "medium": 0, "normal": 0}
        for kg_id in ids:
            n, fine = punish.get(kg_id, (0, 0))
            total, dims = fake_scores(kg_id, n, fine)
            lvl = level_of(total)
            buckets[lvl] += 1
            rows.append(
                (
                    kg_id,
                    total,
                    lvl,
                    json.dumps(dims, ensure_ascii=False),
                    MODEL_VERSION,
                )
            )

        cur.executemany(
            """INSERT INTO risk_score_current
                   (kindergarten_id, total_score, risk_level, dimensions,
                    model_version, is_placeholder, computed_at)
               VALUES (%s, %s, %s, %s, %s, 1, UTC_TIMESTAMP())
               ON DUPLICATE KEY UPDATE
                   total_score=VALUES(total_score), risk_level=VALUES(risk_level),
                   dimensions=VALUES(dimensions), model_version=VALUES(model_version),
                   is_placeholder=1, computed_at=UTC_TIMESTAMP()""",
            rows,
        )
        print(f"寫入 {len(rows)} 列")
        print(f"  high(>=80，紫)   : {buckets['high']}")
        print(f"  medium(60-79，紅): {buckets['medium']}")
        print(f"  normal(<60)      : {buckets['normal']}")

        cur.execute(
            "SELECT k.school_name, r.total_score, r.risk_level "
            "FROM risk_score_current r JOIN kindergarten k ON k.id = r.kindergarten_id "
            "ORDER BY r.total_score DESC LIMIT 5"
        )
        print("\n分數最高的 5 間：")
        for r in cur.fetchall():
            print(f"  {r['total_score']:>6} {r['risk_level']:<7} {r['school_name']}")


if __name__ == "__main__":
    main()
