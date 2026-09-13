# -*- coding: utf-8 -*-
"""把財報法遵分析結果（risk_scores.csv）載進 finance_report_current，並重算風險指數。

來源：data/finance_pdf_cleaning/analysis/outputs/risk_scores.csv
      （決算書 PDF -> 清理 -> 指標計算的產出，一校一年一列，109~113）

**只取 113**（最新決算年度）。風險指數只看最新年度，逐年趨勢已經被壓進
指標的「歷年 z 分數」裡；要做趨勢圖再另開 history 表，不影響這支。

園名比對：CSV 只有簡稱（例如「安溪」），資料庫是全名。這 10 間都是新北市的
非營利幼兒園，所以用 `新北市{簡稱}非營利幼兒園%` 比對 —— 這條規則刻意寫得嚴，
比對不到或比對到多筆就報錯跳過，不做模糊猜測（猜錯會把風險掛到別間學校）。

用法：
    $env:DB_PASSWORD = "..."
    python tools/load_finance_risk.py
    python tools/load_finance_risk.py --dry-run   # 只印出比對結果，不寫入
"""
import argparse
import csv
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", write_through=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scraper"))
sys.path.insert(0, os.path.join(ROOT, "aws", "src"))

import db  # noqa: E402

CSV_PATH = os.path.join(
    ROOT, "data", "finance_pdf_cleaning", "analysis", "outputs", "risk_scores.csv"
)
FISCAL_YEAR = "113"
COUNTY = "新北市"

# 指標：(key, 顯示名稱, CSV 欄位前綴)。必須與 aws/src/finance.py 的 INDICATORS 一致。
INDICATORS = [
    ("per_student_personnel", "每生人事費", "每生人事費"),
    ("personnel_yoy", "人事費年增率", "人事費年增率"),
    ("budget_deviation", "預決算偏離率", "預決算偏離率"),
    ("fund_reallocation", "經費流用比例", "經費流用比例"),
    ("student_teacher_ratio", "師生比", "師生比"),
    ("staff_turnover", "教職員流動率", "教職員流動率"),
    ("overtime_load", "加班費負荷", "加班費負荷"),
    ("misconduct_incident", "不當管教事件", "不當管教事件"),
]

# 財報頁要顯示的數字：(群組, 顯示名稱, CSV 欄位, 單位, 是否為比率(要 ×100))
METRIC_GROUPS = [
    (
        "收支與餘絀",
        [
            ("收入合計（決算）", "收入合計_決算", "元", False),
            ("支出合計（決算）", "支出合計_決算", "元", False),
            ("本期稅後餘絀", "本期稅後餘絀", "元", False),
            ("收支餘絀率", "收支餘絀率", "%", True),
            ("收入成長率", "收入成長率", "%", True),
            ("支出成長率", "支出成長率", "%", True),
        ],
    ),
    (
        "資產負債與現金",
        [
            ("流動資產", "流動資產", "元", False),
            ("流動負債", "流動負債", "元", False),
            ("流動比率", "流動比率", "倍", False),
            ("負債比率", "負債比率", "%", True),
            ("期末現金淨增加", "期末現金淨增加", "元", False),
        ],
    ),
    (
        "規模與單位成本",
        [
            ("學生人數", "學生人數", "人", False),
            ("教保人員數", "教保人員數", "人", False),
            ("師生比", "師生比", "", False),
            ("每生總支出", "每生總支出", "元", False),
            ("每生人事費", "每生人事費", "元", False),
            ("人事費占比", "人事費_占比", "%", True),
        ],
    ),
    (
        "預算執行",
        [
            ("人事費（預算）", "人事費_預算", "元", False),
            ("人事費（決算）", "人事費_決算", "元", False),
            ("人事費執行率", "人事費_執行率", "%", True),
            ("預決算偏離率", "預算偏離率", "%", True),
            ("經費流用比例", "經費流用比例", "%", True),
        ],
    ),
]


def num(value):
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.upper() in ("N/A", "NAN", "NONE"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def build_indicators(row):
    out = []
    for key, label, prefix in INDICATORS:
        level = (row.get(f"{prefix}_等級") or "").strip() or "N/A"
        score = num(row.get(f"{prefix}_等級分數"))
        out.append(
            {
                "key": key,
                "label": label,
                "level": level,
                "levelScore": int(score) if score is not None else 0,
                "yearZ": _round(num(row.get(f"{prefix}_歷年z"))),
                "peerZ": _round(num(row.get(f"{prefix}_同業z"))),
            }
        )
    return out


def _round(value, digits=3):
    return None if value is None else round(value, digits)


def build_metrics(row):
    groups = []
    for group_label, fields in METRIC_GROUPS:
        items = []
        for label, column, unit, is_ratio in fields:
            value = num(row.get(column))
            if value is not None and is_ratio:
                value = round(value * 100, 2)
            elif value is not None:
                value = round(value, 2)
            items.append({"label": label, "value": value, "unit": unit})
        groups.append({"label": group_label, "items": items})
    return {"groups": groups}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=CSV_PATH)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.csv):
        sys.exit(f"找不到 CSV：{args.csv}")

    with open(args.csv, encoding="utf-8-sig", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if (r.get("年份") or "").strip() == FISCAL_YEAR]
    if not rows:
        sys.exit(f"CSV 裡沒有年份={FISCAL_YEAR} 的資料")
    print(f"CSV：{args.csv}")
    print(f"年份 {FISCAL_YEAR} 的資料：{len(rows)} 列\n")

    conn = db.connect()
    matched, skipped = [], []
    with conn.cursor() as cur:
        for row in rows:
            alias = (row.get("園名") or "").strip()
            pattern = f"{COUNTY}{alias}非營利幼兒園%"
            cur.execute(
                "SELECT id, school_name FROM kindergarten "
                "WHERE county = %s AND school_name LIKE %s ORDER BY id",
                (COUNTY, pattern),
            )
            hits = cur.fetchall()
            if len(hits) != 1:
                skipped.append((alias, len(hits)))
                print(f"  [跳過] {alias}：比對到 {len(hits)} 筆（需要剛好 1 筆）")
                continue

            kg = hits[0]
            index = num(row.get("法遵風險指數"))
            record = (
                kg["id"],
                (row.get("幼兒園ID") or "").strip() or None,
                alias or None,
                FISCAL_YEAR,
                index,
                (row.get("整體風險等級") or "").strip() or None,
                (row.get("事件前預警") or "").strip()[:255] or None,
                json.dumps(build_indicators(row), ensure_ascii=False),
                json.dumps(build_metrics(row), ensure_ascii=False),
                os.path.basename(args.csv),
            )
            matched.append(record)
            flagged = [
                f"{i['label']}({i['level']}/{i['levelScore']})"
                for i in build_indicators(row)
                if i["levelScore"] >= 2
            ]
            print(
                f"  {kg['id']:>6}  法遵風險指數 {index:>5}  "
                f"風險分數 {min(100.0, (index or 0) * 4):>5}  {kg['school_name']}"
            )
            print(f"          黃/紅燈指標：{'、'.join(flagged) if flagged else '無'}")

        if args.dry_run:
            print(f"\n--dry-run：沒有寫入。可寫入 {len(matched)} 列，跳過 {len(skipped)} 列")
            return

        cur.executemany(
            """INSERT INTO finance_report_current
                   (kindergarten_id, finance_id, school_alias, fiscal_year,
                    compliance_index, overall_level, early_warning, indicators,
                    metrics, source_file, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP())
               ON DUPLICATE KEY UPDATE
                   finance_id = VALUES(finance_id),
                   school_alias = VALUES(school_alias),
                   fiscal_year = VALUES(fiscal_year),
                   compliance_index = VALUES(compliance_index),
                   overall_level = VALUES(overall_level),
                   early_warning = VALUES(early_warning),
                   indicators = VALUES(indicators),
                   metrics = VALUES(metrics),
                   source_file = VALUES(source_file),
                   updated_at = UTC_TIMESTAMP()""",
            matched,
        )
        print(f"\n寫入 finance_report_current：{len(matched)} 列（跳過 {len(skipped)}）")

        # 有財報資料的學校要立刻重算風險指數（finance 維度從「缺資料」變成有分數）
        os.environ.setdefault("DB_HOST", "127.0.0.1")
        os.environ.setdefault("DB_PORT", str(db.LOCAL_PORT))
        os.environ.setdefault("DB_USER", db.DB_USER)
        os.environ.setdefault("DB_PASSWORD", db.DB_PASSWORD)
        os.environ.setdefault("DB_NAME", db.DB_NAME)
        import risk  # noqa: E402  （要等環境變數設好才能 import common）

        print("\n重算風險指數：")
        for record in matched:
            kg_id = record[0]
            total, dims = risk.compute(cur, kg_id)
            parts = " ".join(
                f"{d['label']}={'—' if d['score'] is None else d['score']}"
                for d in dims
            )
            print(f"  {kg_id:>6}  總分 {total:>6}  {parts}")


if __name__ == "__main__":
    main()
