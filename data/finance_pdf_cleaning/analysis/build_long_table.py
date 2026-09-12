"""將 processed/ 下各園各年度的財報 CSV 彙整成統一長表 (long format)。

輸出欄位：
    園代碼, 園名, 學年度, 資料表, 指標, 數值, 資料類別

設計目的（可擴充性）：
    所有下游特徵工程都只讀這張長表。日後要新增資料源（學生人數、教保人員數、
    營運與人事事件……），只要另外產生一份符合相同欄位的長表並 append 進來即可，
    不需要修改特徵工程或評分程式。

資料類別（category）用來套用年份限制規則：
    - "收支"：收支餘絀表科目，最早有 109 年資料，YoY 基準可回溯 109。
    - "非收支"：其他所有表（資產負債、現金流量、附表二/三/四……），最早僅 110 年，
                110 年的 YoY 應為 NaN，歷史基準僅能從 110 起算。
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

PROC = Path(__file__).resolve().parents[1] / "processed"
OUT = Path(__file__).resolve().parent / "outputs"
OUT.mkdir(parents=True, exist_ok=True)

COLUMNS = ["園代碼", "園名", "學年度", "資料表", "指標", "數值", "資料類別"]


def money_to_float(value) -> float | None:
    """把財報金額字串轉成 float；括號視為負數，破折號/空值回 None。"""
    if value is None:
        return None
    s = str(value).strip()
    if s in ("", "-", "—", "None", "nan"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "").replace("$", "").replace("＄", "").strip()
    if s in ("", "-"):
        return None
    try:
        num = float(s)
    except ValueError:
        return None
    return -num if neg else num


def parse_code_name(school_dir_name: str, filename: str) -> tuple[str | None, str]:
    """由檔名前綴取代碼與園名，例如 N01安溪。"""
    m = re.match(r"(N\d+)(.+?)_", filename)
    if m:
        return m.group(1), m.group(2)
    return None, school_dir_name


def year_of(path: Path) -> int | None:
    m = re.search(r"(\d{3})學年度", str(path))
    return int(m.group(1)) if m else None


records: list[dict] = []


def add(code, name, year, table, metric, value, category):
    records.append(
        {
            "園代碼": code,
            "園名": name,
            "學年度": year,
            "資料表": table,
            "指標": metric,
            "數值": value,
            "資料類別": category,
        }
    )


def load_income(path: Path):
    """收支餘絀表：科目 -> 數值（決算數/金額）。類別=收支。"""
    df = pd.read_csv(path, dtype=str).fillna("")
    year = year_of(path)  # 報告學年度資料夾
    code, name = parse_code_name(path.parent.name, path.name)
    for _, r in df.iterrows():
        metric = r.get("科目", "").strip()
        if not metric:
            continue
        add(code, name, year, "收支餘絀表", metric, money_to_float(r.get("金額")), "收支")


def load_balance(path: Path):
    """資產負債表：只取本年度欄（年度類別=本年度），科目 -> 金額。類別=非收支。"""
    df = pd.read_csv(path, dtype=str).fillna("")
    year = year_of(path)
    code, name = parse_code_name(path.parent.name, path.name)
    for _, r in df.iterrows():
        if r.get("年度類別", "") and r.get("年度類別") != "本年度":
            continue
        metric = r.get("科目", "").strip()
        if not metric:
            continue
        add(code, name, year, "資產負債表", metric, money_to_float(r.get("金額")), "非收支")


def load_cash_flow(path: Path):
    """現金流量表：項目 -> 本年度金額欄（欄名形如 113學年度金額）。類別=非收支。"""
    df = pd.read_csv(path, dtype=str).fillna("")
    year = year_of(path)
    code, name = parse_code_name(path.parent.name, path.name)
    cur_col = next((c for c in df.columns if c.endswith("學年度金額")), None)
    if cur_col is None:
        return
    for _, r in df.iterrows():
        metric = r.get("項目", "").strip()
        if not metric:
            continue
        add(code, name, year, "現金流量表", metric, money_to_float(r.get(cur_col)), "非收支")


def load_appendix2(path: Path):
    """附表二：項目 -> 決算數、執行率(差異率)。類別=非收支。"""
    df = pd.read_csv(path, dtype=str).fillna("")
    year = year_of(path)
    code, name = parse_code_name(path.parent.name, path.name)
    for _, r in df.iterrows():
        item = r.get("項目", "").strip()
        if not item:
            continue
        add(code, name, year, "附表二", f"{item}-決算數", money_to_float(r.get("決算數")), "非收支")
        rate = money_to_float(r.get("差異率"))
        add(code, name, year, "附表二", f"{item}-差異率", rate, "非收支")


def load_appendix3(path: Path):
    """附表三：只取本年度欄的決算數與執行率。類別=非收支。"""
    df = pd.read_csv(path, dtype=str).fillna("")
    year = year_of(path)
    code, name = parse_code_name(path.parent.name, path.name)
    dec_col = next((c for c in df.columns if re.search(r"\d{3}學年度決算數$", c)), None)
    exe_col = next((c for c in df.columns if re.search(r"\d{3}學年度執行率$", c)), None)
    for _, r in df.iterrows():
        item = r.get("項目", "").strip()
        if not item:
            continue
        if dec_col:
            add(code, name, year, "附表三", f"{item}-決算數", money_to_float(r.get(dec_col)), "非收支")
        if exe_col:
            add(code, name, year, "附表三", f"{item}-執行率", money_to_float(r.get(exe_col)), "非收支")


def load_appendix4(path: Path):
    """附表四財產清冊：彙總為財產筆數與帳面價值總額兩個指標。類別=非收支。"""
    df = pd.read_csv(path, dtype=str).fillna("")
    year = year_of(path)
    code, name = parse_code_name(path.parent.name, path.name)
    count = len(df)
    total = sum((money_to_float(v) or 0) for v in df.get("帳面價值", []))
    add(code, name, year, "附表四", "財產筆數", float(count), "非收支")
    add(code, name, year, "附表四", "帳面價值總額", float(total), "非收支")


LOADERS = [
    (re.compile(r"_\d{3}學年度收支餘絀表\.csv$"), load_income),
    (re.compile(r"_資產負債表\.csv$"), load_balance),
    (re.compile(r"_現金流量表\.csv$"), load_cash_flow),
    (re.compile(r"_附表二_"), load_appendix2),
    (re.compile(r"_附表三_"), load_appendix3),
    (re.compile(r"_附表四_"), load_appendix4),
]


def main():
    for f in sorted(PROC.rglob("*.csv")):
        if "驗證" in f.name or "page_index" in f.name or "checkpoint" in str(f):
            continue
        for pat, loader in LOADERS:
            if pat.search(f.name):
                try:
                    loader(f)
                except Exception as e:  # noqa: BLE001 - 單檔失敗不中斷整體
                    print(f"警告：解析 {f.name} 失敗：{e}")
                break

    df = pd.DataFrame.from_records(records, columns=COLUMNS)
    df = df.dropna(subset=["園代碼"])
    df = df.sort_values(["園代碼", "資料表", "指標", "學年度"]).reset_index(drop=True)
    out_path = OUT / "metric_long.csv"
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"寫出長表：{out_path}")
    print(f"  總列數：{len(df)}")
    print(f"  園所數：{df['園代碼'].nunique()}，年度：{sorted(df['學年度'].dropna().unique())}")
    print(f"  資料表：{sorted(df['資料表'].unique())}")
    print("  各類別年份範圍：")
    for cat, g in df.groupby("資料類別"):
        print(f"    {cat}: {sorted(g['學年度'].dropna().unique())}")


if __name__ == "__main__":
    main()
