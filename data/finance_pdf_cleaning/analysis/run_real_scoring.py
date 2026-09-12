"""用真實資料跑風險評分引擎。

把 metric_long.csv + operating_long.csv 轉成引擎期待的寬表（一列一(園,年)），
再跑完整 pipeline，輸出 outputs/risk_scores.csv。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import risk_scoring_engine as rse

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

OUT = Path(__file__).resolve().parent / "outputs"

# 收支餘絀表科目 -> 引擎欄位（決算）
INCOME_ITEMS = {
    "人事費": "人事費_決算",
    "業務費": "業務費_決算",
    "材料費": "材料費_決算",
    "維護費": "維護費_決算",
    "修繕購置費": "修繕購置費_決算",
    "業務發展費": "業務發展費_決算",
    "其他支出": "其他支出_決算",
    "支出合計": "支出合計_決算",
    "收入合計": "收入合計_決算",
    "本期稅後餘絀": "本期稅後餘絀",
}
BALANCE_ITEMS = {
    "流動資產合計": "流動資產",
    "流動負債合計": "流動負債",
    "資產總計": "資產總計",
    "負債總額": "負債總額",
}


def _pick(series_map: dict, keys: list[str]):
    for k in keys:
        if k in series_map and pd.notna(series_map[k]):
            return series_map[k]
    return np.nan


def build_wide() -> pd.DataFrame:
    ml = pd.read_csv(OUT / "metric_long.csv", dtype={"園代碼": str})
    ml["數值"] = pd.to_numeric(ml["數值"], errors="coerce")

    rows = {}
    for (code, name, year), g in ml.groupby(["園代碼", "園名", "學年度"]):
        rows[(code, year)] = {"幼兒園ID": code, "園名": name, "年份": int(year), "縣市": "新北市"}

    # 收支餘絀表決算
    inc = ml[ml["資料表"] == "收支餘絀表"]
    for (code, year), g in inc.groupby(["園代碼", "學年度"]):
        m = dict(zip(g["指標"], g["數值"]))
        row = rows.setdefault((code, year), {"幼兒園ID": code, "年份": int(year), "縣市": "新北市"})
        for item, col in INCOME_ITEMS.items():
            if item in m:
                row[col] = m[item]

    # 資產負債表
    bal = ml[ml["資料表"] == "資產負債表"]
    for (code, year), g in bal.groupby(["園代碼", "學年度"]):
        m = dict(zip(g["指標"], g["數值"]))
        row = rows.setdefault((code, year), {"幼兒園ID": code, "年份": int(year), "縣市": "新北市"})
        for item, col in BALANCE_ITEMS.items():
            if item in m:
                row[col] = m[item]

    # 現金流量表：期末現金淨增加（名稱多變，模糊比對）
    cf = ml[ml["資料表"] == "現金流量表"]
    for (code, year), g in cf.groupby(["園代碼", "學年度"]):
        m = dict(zip(g["指標"], g["數值"]))
        net = _pick(m, [k for k in m if "本期" in k and "現金" in k and ("增加" in k or "減少" in k)])
        row = rows.setdefault((code, year), {"幼兒園ID": code, "年份": int(year), "縣市": "新北市"})
        row["期末現金淨增加"] = net

    # 附表二：經費流用比例（以「業務發展費」等有流用性質科目的偏離總量近似；此處用差異率彙總）
    a2 = ml[ml["資料表"] == "附表二"]
    for (code, year), g in a2.groupby(["園代碼", "學年度"]):
        # 決算相對預算超出的科目視為流用；此處以決算/差異率無直接預算，改用附表三的偏離
        row = rows.setdefault((code, year), {"幼兒園ID": code, "年份": int(year), "縣市": "新北市"})

    # 附表三：本年度決算數/執行率 → 推預算偏離率（以人事費為代表科目）
    a3 = ml[ml["資料表"] == "附表三"]
    for (code, year), g in a3.groupby(["園代碼", "學年度"]):
        m = dict(zip(g["指標"], g["數值"]))
        # 人事費決算與執行率
        dec = _pick(m, ["人事費-決算數", "人事費"])
        exe = _pick(m, ["人事費-執行率"])
        row = rows.setdefault((code, year), {"幼兒園ID": code, "年份": int(year), "縣市": "新北市"})
        # 預算偏離率 = 決算/預算 - 1 = 執行率/100 - 1
        if pd.notna(exe):
            row["預算偏離率"] = exe / 100.0 - 1.0
        # 全體科目超支比例：附表三本年度執行率>100 的科目數 / 有執行率科目數
        exe_vals = [v for k, v in m.items() if k.endswith("-執行率") and pd.notna(v)]
        if exe_vals:
            over = sum(1 for v in exe_vals if v > 100)
            row["經費流用比例"] = over / len(exe_vals)
            # 人事費預算 = 決算 / (執行率/100)
        if pd.notna(dec) and pd.notna(exe) and exe:
            row["人事費_預算"] = dec / (exe / 100.0)

    wide = pd.DataFrame(list(rows.values()))

    # 併入營運資料
    op_path = OUT / "operating_long.csv"
    if op_path.exists():
        op = pd.read_csv(op_path, dtype={"園代碼": str})
        op["數值"] = pd.to_numeric(op["數值"], errors="coerce")
        opw = op.pivot_table(index=["園代碼", "學年度"], columns="指標", values="數值", aggfunc="first").reset_index()
        opw = opw.rename(columns={"園代碼": "幼兒園ID", "學年度": "年份"})
        opw["年份"] = opw["年份"].astype(int)
        keep = ["幼兒園ID", "年份"] + [c for c in ["學生人數", "教保人員數", "員工人數"] if c in opw.columns]
        wide = wide.merge(opw[keep], on=["幼兒園ID", "年份"], how="left")

    return wide.sort_values(["幼兒園ID", "年份"]).reset_index(drop=True)


def main():
    wide = build_wide()
    result = rse.run_pipeline(wide, events=None, event_year=113, lookback=2)
    out_path = OUT / "risk_scores.csv"
    result.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"寫出風險評分：{out_path}（{len(result)} 列，{result['幼兒園ID'].nunique()} 園）")

    cols = ["幼兒園ID", "年份", "每生人事費", "師生比", "加班費負荷", "法遵風險指數", "整體風險等級"]
    cols = [c for c in cols if c in result.columns]
    v = result[result["年份"] == 113][cols].copy()
    for c in ("每生人事費",):
        if c in v:
            v[c] = v[c].round(0)
    if "師生比" in v:
        v["師生比"] = v["師生比"].round(2)
    print("\n=== 113 學年度風險排序（高→低）===")
    print(v.sort_values("法遵風險指數", ascending=False).to_string(index=False))


if __name__ == "__main__":
    main()
