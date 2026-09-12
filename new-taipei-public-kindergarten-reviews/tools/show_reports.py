# -*- coding: utf-8 -*-
"""印出家長回報目前的資料，並清掉測試用的 staff_profile（username 以 zz_ 開頭）。

用法： python tools/show_reports.py [--clean-staff]
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
    ap.add_argument("--clean-staff", action="store_true",
                    help="刪掉 username 以 zz_ 開頭的測試 staff_profile")
    args = ap.parse_args()

    conn = db.connect()
    with conn.cursor() as cur:
        if args.clean_staff:
            cur.execute("DELETE FROM staff_profile WHERE username LIKE 'zz\\_%'")
            print(f"刪除測試 staff_profile：{cur.rowcount} 列\n")

        cur.execute(
            """SELECT r.id, r.status, r.reporter_email, r.assignee,
                      r.created_at, r.tracking_token, k.school_name,
                      (SELECT COUNT(*) FROM parent_report_attachment a
                        WHERE a.report_id = r.id) AS files,
                      (SELECT COUNT(*) FROM parent_report_message m
                        WHERE m.report_id = r.id) AS msgs
               FROM parent_report r
               JOIN kindergarten k ON k.id = r.kindergarten_id
               ORDER BY r.id"""
        )
        rows = cur.fetchall()
        if not rows:
            print("parent_report 目前沒有資料")
        for r in rows:
            print(f"#{r['id']}  {r['status']:<20} {r['reporter_email']}")
            print(f"     {r['school_name']}  附件={r['files']} 訊息={r['msgs']} 承辦={r['assignee']}")
            print(f"     建立 {r['created_at']}  token={r['tracking_token']}")

        cur.execute("SELECT username, display_name, county, agency FROM staff_profile")
        staff = cur.fetchall()
        print(f"\nstaff_profile（{len(staff)} 人）：")
        for s in staff:
            print(f"  {s['username']:<16} display_name={s['display_name']}  "
                  f"{s['county']} / {s['agency']}")


if __name__ == "__main__":
    main()
