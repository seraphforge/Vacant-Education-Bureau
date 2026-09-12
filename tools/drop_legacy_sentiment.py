# -*- coding: utf-8 -*-
"""刪除前一次失敗嘗試（AgentCore Browser 版）留下的 sentiment_scan* 表。

已由專案負責人確認這兩張表是廢棄物，現行輿情功能用的是 opinion_* 三張表
（db/migrations/004_opinion.sql）。

用法：
    $env:DB_PASSWORD = "（從 aws/deploy.config.ps1 取得）"
    python tools/drop_legacy_sentiment.py --confirm
"""
import argparse
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", write_through=True)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scraper"))

import db  # noqa: E402

# 子表先刪：sentiment_scan_source 有 FK 指向 sentiment_scan
TABLES = ("sentiment_scan_source", "sentiment_scan")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", action="store_true", help="真的執行 DROP")
    args = parser.parse_args()

    conn = db.connect()
    with conn.cursor() as cur:
        for table in TABLES:
            cur.execute(
                "SELECT COUNT(*) AS n FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = %s",
                (table,),
            )
            if not cur.fetchone()["n"]:
                print(f"{table}: 不存在，略過")
                continue
            cur.execute(f"SELECT COUNT(*) AS n FROM `{table}`")
            rows = cur.fetchone()["n"]
            if not args.confirm:
                print(f"{table}: {rows} 列（加 --confirm 才會真的刪除）")
                continue
            cur.execute(f"DROP TABLE `{table}`")
            print(f"{table}: 已刪除（原有 {rows} 列）")

        print("\n目前資料表：")
        cur.execute("SHOW TABLES")
        for row in cur.fetchall():
            print("  " + list(row.values())[0])
    conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
