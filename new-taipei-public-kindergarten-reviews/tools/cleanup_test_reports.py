# -*- coding: utf-8 -*-
"""刪掉測試用的家長回報資料。

用法：
    python tools/cleanup_test_reports.py                # 刪 email 像 *@example.com 的案件
    python tools/cleanup_test_reports.py --email a@b.co # 只刪指定 email

附件檔案在 S3 上要另外刪：
    aws s3 rm s3://<bucket>/reports/ --recursive
"""
import argparse
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", write_through=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scraper"))

import db  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default="", help="只刪這個 email 的案件")
    args = ap.parse_args()

    conn = db.connect()
    with conn.cursor() as cur:
        if args.email:
            cur.execute(
                "DELETE FROM parent_report WHERE reporter_email = %s", (args.email,)
            )
        else:
            # 測試都用 example.com，真實回報不會是這個網域
            cur.execute(
                "DELETE FROM parent_report WHERE reporter_email LIKE %s",
                ("%@example.com",),
            )
        print(f"刪除 parent_report：{cur.rowcount} 列（附件與訊息會連帶刪除）")

        for table in ("parent_report", "parent_report_attachment", "parent_report_message"):
            cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
            print(f"  {table:28s} 剩 {cur.fetchone()['n']} 列")


if __name__ == "__main__":
    main()
