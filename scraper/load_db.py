# -*- coding: utf-8 -*-
"""Load scraped 新北市 punishment records into RDS MySQL (schema `readme`).

Creates table `kindergarten_punishment`, linkable back to `kindergarten`
via the natural key (county, district, school_name).

Run:  python scraper/load_db.py
"""
import io
import json
import os
import re
import sys
import hashlib

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", write_through=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

DDL = """
CREATE TABLE IF NOT EXISTS kindergarten_punishment (
    id                  INT NOT NULL AUTO_INCREMENT,
    county              VARCHAR(30)  NOT NULL,
    district            VARCHAR(30)  NOT NULL,
    school_name         VARCHAR(255) NOT NULL,
    -- 對應 kindergarten 的自然鍵 (county, district, school_name)。
    -- kindergarten_code 為便利欄位：以最新學年度、同名同區的園所 code 帶入，可能為 NULL（已停業/查無）。
    kindergarten_code   VARCHAR(20)  NULL,
    ownership           VARCHAR(20)  NULL,   -- 設立別（爬取當下）
    address             VARCHAR(500) NULL,
    phone               VARCHAR(50)  NULL,
    capacity            INT          NULL,   -- 核定人數
    op_status           VARCHAR(30)  NULL,   -- 營運狀態
    -- 裁罰紀錄本身
    punish_date         DATE         NULL,   -- 處分日期
    school_name_at_time VARCHAR(255) NULL,   -- 處分時園名
    doc_no              VARCHAR(100) NULL,   -- 裁處文號
    legal_basis         VARCHAR(500) NULL,   -- 處分依據
    violated_rule       TEXT         NULL,   -- 違反之規定
    person              VARCHAR(255) NULL,   -- 負責人/行為人
    content             VARCHAR(500) NULL,   -- 處分內容（原文）
    fine_amount         INT          NULL,   -- 罰鍰金額（元），從 content 解析
    record_hash         CHAR(40)     NOT NULL, -- SHA1 of full logical key（去重用）
    source_url          VARCHAR(255) NULL,
    scraped_at          TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_link (county, district, school_name),
    KEY idx_kg_code (kindergarten_code),
    KEY idx_punish_date (punish_date),
    UNIQUE KEY uq_record (record_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""

SOURCE_URL = "https://ap.ece.moe.edu.tw/webecems/punishSearch.aspx"


def parse_date(s):
    m = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", s or "")
    if not m:
        return None
    y, mo, d = map(int, m.groups())
    return f"{y:04d}-{mo:02d}-{d:02d}"


def parse_fine(content):
    # e.g. 罰鍰：50,000 元
    m = re.search(r"罰鍰[:：]?\s*([\d,]+)", content or "")
    if not m:
        return None
    return int(m.group(1).replace(",", ""))


def parse_capacity(s):
    m = re.search(r"\d+", s or "")
    return int(m.group()) if m else None


def main():
    data = json.load(open(os.path.join(HERE, "punishments.json"), encoding="utf-8"))

    # 1) dedupe records across randomized sch tokens.
    # A single 裁處文號 can cite multiple 違反之規定, so the full logical key includes
    # rule/basis/person. We hash it into record_hash for the UNIQUE constraint.
    seen = set()
    rows = []
    for school in data:
        county = (school.get("county") or "").strip()
        district = (school.get("district") or "").strip()
        name = (school.get("school_name") or "").strip()
        for rec in school.get("records", []):
            pdate = parse_date(rec.get("punish_date"))
            content = (rec.get("content") or "").strip()
            doc = (rec.get("doc_no") or "").strip()
            basis = (rec.get("legal_basis") or "").strip()
            rule = (rec.get("violated_rule") or "").strip()
            person = (rec.get("person") or "").strip()
            key = (county, district, name, doc, pdate or "", content, basis, rule, person)
            if key in seen:
                continue
            seen.add(key)
            rhash = hashlib.sha1("\u0001".join(key).encode("utf-8")).hexdigest()
            rows.append({
                "county": county, "district": district, "school_name": name,
                "ownership": (school.get("ownership") or "").strip() or None,
                "address": (school.get("address") or "").strip() or None,
                "phone": (school.get("phone") or "").strip() or None,
                "capacity": parse_capacity(school.get("capacity")),
                "op_status": (school.get("status") or "").strip() or None,
                "punish_date": pdate,
                "school_name_at_time": (rec.get("school_name_at_time") or "").strip() or None,
                "doc_no": doc or None,
                "legal_basis": basis or None,
                "violated_rule": rule or None,
                "person": person or None,
                "content": content or None,
                "fine_amount": parse_fine(content),
                "record_hash": rhash,
            })

    print(f"deduped records: {len(rows)}")
    print(f"distinct schools: {len({(r['county'], r['district'], r['school_name']) for r in rows})}")

    conn = db.connect()
    with conn.cursor() as cur:
        cur.execute(DDL)
        print("table ready: kindergarten_punishment")

        # 2) resolve kindergarten_code from latest academic year by (county, district, school_name)
        cur.execute("SELECT MAX(CAST(academic_year AS UNSIGNED)) y FROM kindergarten")
        latest = str(cur.fetchone()["y"])
        cur.execute(
            "SELECT county, district, school_name, code FROM kindergarten WHERE academic_year=%s",
            (latest,),
        )
        kg_rows = cur.fetchall()
        code_map = {(r["county"], r["district"], r["school_name"]): r["code"] for r in kg_rows}

        def normalize(s):
            # 全形/半形括號統一、去空白，取「新北市...幼兒園」核心名
            s = (s or "").replace("（", "(").replace("）", ")").replace(" ", "")
            return s

        # 以正規化後的 (district, name) 建立備用索引
        norm_map = {}
        for r in kg_rows:
            norm_map.setdefault((r["district"], normalize(r["school_name"])), r["code"])

        matched = 0
        fuzzy = 0
        for r in rows:
            code = code_map.get((r["county"], r["district"], r["school_name"]))
            if not code:
                # 備用：正規化括號/空白後比對
                code = norm_map.get((r["district"], normalize(r["school_name"])))
                if code:
                    fuzzy += 1
            r["kindergarten_code"] = code
            if code:
                matched += 1
        print(f"linked to kindergarten (latest year {latest}): {matched}/{len(rows)} rows "
              f"(exact {matched - fuzzy}, normalized {fuzzy})")

        # 3) insert (idempotent via UNIQUE + upsert)
        sql = """
        INSERT INTO kindergarten_punishment
          (county, district, school_name, kindergarten_code, ownership, address, phone,
           capacity, op_status, punish_date, school_name_at_time, doc_no, legal_basis,
           violated_rule, person, content, fine_amount, record_hash, source_url)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
           kindergarten_code=VALUES(kindergarten_code),
           ownership=VALUES(ownership), address=VALUES(address), phone=VALUES(phone),
           capacity=VALUES(capacity), op_status=VALUES(op_status),
           legal_basis=VALUES(legal_basis), violated_rule=VALUES(violated_rule),
           person=VALUES(person), fine_amount=VALUES(fine_amount)
        """
        params = [(
            r["county"], r["district"], r["school_name"], r["kindergarten_code"],
            r["ownership"], r["address"], r["phone"], r["capacity"], r["op_status"],
            r["punish_date"], r["school_name_at_time"], r["doc_no"], r["legal_basis"],
            r["violated_rule"], r["person"], r["content"], r["fine_amount"],
            r["record_hash"], SOURCE_URL,
        ) for r in rows]
        cur.executemany(sql, params)
        print(f"inserted/updated rows: {cur.rowcount}")

    print("done.")


if __name__ == "__main__":
    main()
