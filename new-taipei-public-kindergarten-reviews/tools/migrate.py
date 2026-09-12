# -*- coding: utf-8 -*-
"""跑 db/migrations/*.sql（照檔名排序），並記錄已套用的版本。

連線方式沿用 scraper/db.py：透過 bastion EC2 開 SSH tunnel 連私有 RDS。

用法：
    python tools/migrate.py            # 套用尚未執行的 migration
    python tools/migrate.py --status    # 只看狀態，不執行
    python tools/migrate.py --force 001_parent_report.sql   # 重跑指定檔案

每個檔案的 SQL 都寫成 CREATE TABLE IF NOT EXISTS，所以重跑是安全的。
"""
import argparse
import hashlib
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", write_through=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS = os.path.join(ROOT, "db", "migrations")
sys.path.insert(0, os.path.join(ROOT, "scraper"))

import db  # noqa: E402

TRACKING_DDL = """
CREATE TABLE IF NOT EXISTS schema_migration (
    filename   VARCHAR(200) NOT NULL,
    sha1       CHAR(40)     NOT NULL,
    applied_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (filename)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def split_statements(sql):
    """去掉 -- 註解後，用分號切成一條條 statement。"""
    lines = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        lines.append(line)
    body = "\n".join(lines)
    return [s.strip() for s in body.split(";") if s.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true", help="只顯示狀態")
    ap.add_argument("--force", nargs="*", default=[], help="強制重跑的檔名")
    args = ap.parse_args()

    files = sorted(f for f in os.listdir(MIGRATIONS) if f.endswith(".sql"))
    if not files:
        sys.exit("db/migrations 下沒有 .sql 檔")

    conn = db.connect()
    with conn.cursor() as cur:
        cur.execute(TRACKING_DDL)

        # 順手確認時區：程式端一律用 UTC naive datetime，DB 必須也是 UTC
        cur.execute("SELECT @@global.time_zone AS tz, NOW() AS now, UTC_TIMESTAMP() AS utc")
        row = cur.fetchone()
        print(f"DB time_zone={row['tz']}  NOW()={row['now']}  UTC_TIMESTAMP()={row['utc']}")
        if row["now"] != row["utc"]:
            print("  !! 警告：DB 的 NOW() 不等於 UTC，parent_report 的時間欄位會有時差")

        cur.execute("SELECT filename, sha1, applied_at FROM schema_migration")
        applied = {r["filename"]: r for r in cur.fetchall()}

        print(f"\n找到 {len(files)} 個 migration：")
        for name in files:
            path = os.path.join(MIGRATIONS, name)
            sql = open(path, encoding="utf-8").read()
            digest = hashlib.sha1(sql.encode("utf-8")).hexdigest()
            prev = applied.get(name)

            if prev and prev["sha1"] == digest and name not in args.force:
                print(f"  [已套用] {name}  ({prev['applied_at']})")
                continue
            if prev and prev["sha1"] != digest:
                print(f"  [內容已變更] {name} -> 重新執行")
            elif not prev:
                print(f"  [新增] {name}")

            if args.status:
                continue

            for stmt in split_statements(sql):
                cur.execute(stmt)
            cur.execute(
                "INSERT INTO schema_migration (filename, sha1) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE sha1=VALUES(sha1), applied_at=CURRENT_TIMESTAMP",
                (name, digest),
            )
            print(f"      -> 完成")

        # 收尾：列出本專案相關的表與列數
        print("\n目前資料表：")
        cur.execute("SHOW TABLES")
        key = list(cur.description)[0][0]
        for r in cur.fetchall():
            table = list(r.values())[0]
            cur.execute(f"SELECT COUNT(*) AS n FROM `{table}`")
            print(f"  {table:32s} {cur.fetchone()['n']:>8,} 列")

    print("\ndone.")


if __name__ == "__main__":
    main()
