"""在長表 (metric_long.csv) 上計算特徵：YoY 變化、Z-score、跨表衍生指標。

嚴格套用資料年份限制規則：
  規則 1 (YoY Baseline)
    - 收支類科目：110 年的 YoY 基準為 109（可算）。
    - 非收支類：趨勢只能從 111 年起算（以 110 為基準），110 年 YoY = NaN。
  規則 2 (跨表衍生指標)
    - 每生支出、師生比等需要營運資料（學生數、教保員數）。
    - 以 how='outer' 合併，缺營運資料的年度（如 109）衍生指標 = NaN，絕不補 0。
  規則 3 (歷史基準比對 / Z-score / rolling)
    - 動態時間窗：收支類歷史可含 109，非收支類僅從 110 起算。
    - rolling / expanding 一律 min_periods=1，避免年份不足報錯。

可擴充性：
  營運資料 (operating_long.csv) 若存在，會自動 outer merge 進來；沒有也不影響
  財務特徵計算。新增其他資料源時，比照 operating_long 的長表格式放進 outputs/ 即可。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent / "outputs"
LONG = OUT / "metric_long.csv"
OPERATING = OUT / "operating_long.csv"  # 選用：學生數、教保員數等營運資料

# 收支類最早年份 109；非收支類最早年份 110。
EARLIEST = {"收支": 109, "非收支": 110}


def load_long() -> pd.DataFrame:
    df = pd.read_csv(LONG, dtype={"園代碼": str})
    df["學年度"] = df["學年度"].astype("Int64")
    df["數值"] = pd.to_numeric(df["數值"], errors="coerce")
    return df


def add_yoy(df: pd.DataFrame) -> pd.DataFrame:
    """對每個 (園,資料表,指標) 序列計算 YoY。非收支類的最早年 YoY 補 NaN。"""
    df = df.sort_values(["園代碼", "資料表", "指標", "學年度"]).copy()
    keys = ["園代碼", "資料表", "指標"]

    # 前一年（差 1 學年）的值：用 merge 對齊 學年度-1，避免遇到缺年直接用 shift 誤配
    prev = df[keys + ["學年度", "數值"]].copy()
    prev["學年度"] = prev["學年度"] + 1
    prev = prev.rename(columns={"數值": "前一年數值"})
    df = df.merge(prev, on=keys + ["學年度"], how="left")

    df["YoY變化額"] = df["數值"] - df["前一年數值"]
    with np.errstate(divide="ignore", invalid="ignore"):
        df["YoY變化率"] = np.where(
            (df["前一年數值"].notna()) & (df["前一年數值"] != 0),
            (df["數值"] - df["前一年數值"]) / df["前一年數值"].abs(),
            np.nan,
        )

    # 規則 1：非收支類在其最早年份一律 NaN（該年沒有合法基準）
    for cat, earliest in EARLIEST.items():
        mask = (df["資料類別"] == cat) & (df["學年度"] == earliest)
        df.loc[mask, ["前一年數值", "YoY變化額", "YoY變化率"]] = np.nan
    return df


def add_zscore(df: pd.DataFrame) -> pd.DataFrame:
    """歷年自我比較 Z-score 與移動平均，動態時間窗，min_periods=1。"""
    df = df.sort_values(["園代碼", "資料表", "指標", "學年度"]).copy()
    keys = ["園代碼", "資料表", "指標"]

    # 只用該類別合法起算年以後的資料當歷史基準
    def clip_history(g: pd.DataFrame) -> pd.DataFrame:
        cat = g["資料類別"].iloc[0]
        earliest = EARLIEST.get(cat, g["學年度"].min())
        g = g[g["學年度"] >= earliest]
        return g

    df = df.groupby(keys, group_keys=False)[df.columns].apply(clip_history)

    grp = df.groupby(keys)["數值"]
    # expanding：截至當年（含）的歷史平均與標準差，min_periods=1
    df["歷史均值"] = grp.transform(lambda s: s.expanding(min_periods=1).mean())
    df["歷史標準差"] = grp.transform(lambda s: s.expanding(min_periods=2).std())
    df["移動平均_近3年"] = grp.transform(
        lambda s: s.rolling(window=3, min_periods=1).mean()
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        df["Zscore"] = np.where(
            (df["歷史標準差"].notna()) & (df["歷史標準差"] != 0),
            (df["數值"] - df["歷史均值"]) / df["歷史標準差"],
            np.nan,
        )
    return df


def pivot_for_derived(df: pd.DataFrame) -> pd.DataFrame:
    """把常用財務指標轉成寬表，供跨表衍生指標計算（每園每年一列）。"""
    wanted = {
        ("收支餘絀表", "人事費"): "人事費",
        ("收支餘絀表", "支出合計"): "支出合計",
        ("收支餘絀表", "本期稅後餘絀"): "本期稅後餘絀",
        ("資產負債表", "資產總計"): "資產總計",
    }
    rows = []
    for (table, metric), alias in wanted.items():
        sub = df[(df["資料表"] == table) & (df["指標"] == metric)]
        for _, r in sub.iterrows():
            rows.append(
                {"園代碼": r["園代碼"], "園名": r["園名"], "學年度": r["學年度"], "指標名": alias, "值": r["數值"]}
            )
    if not rows:
        return pd.DataFrame(columns=["園代碼", "園名", "學年度"])
    wide = (
        pd.DataFrame(rows)
        .pivot_table(index=["園代碼", "園名", "學年度"], columns="指標名", values="值", aggfunc="first")
        .reset_index()
    )
    return wide


def add_derived(wide: pd.DataFrame) -> pd.DataFrame:
    """跨表衍生指標。營運資料存在才算每生/師生比，否則保持 NaN（規則 2）。"""
    wide = wide.copy()

    if OPERATING.exists():
        op = pd.read_csv(OPERATING, dtype={"園代碼": str})
        op["學年度"] = op["學年度"].astype("Int64")
        # 期望營運長表含欄位：園代碼, 學年度, 學生人數, 教保人員數
        op_wide = op.pivot_table(
            index=["園代碼", "學年度"], columns="指標", values="數值", aggfunc="first"
        ).reset_index()
        # 規則 2：outer merge，不填 0
        wide = wide.merge(op_wide, on=["園代碼", "學年度"], how="outer")
    else:
        wide["學生人數"] = np.nan
        wide["教保人員數"] = np.nan

    def safe_div(a, b):
        a = pd.to_numeric(wide.get(a), errors="coerce")
        b = pd.to_numeric(wide.get(b), errors="coerce")
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where((b.notna()) & (b != 0), a / b, np.nan)

    wide["每生人事費"] = safe_div("人事費", "學生人數")
    wide["每生支出"] = safe_div("支出合計", "學生人數")
    wide["師生比"] = safe_div("學生人數", "教保人員數")
    return wide


def main():
    df = load_long()
    df = add_yoy(df)
    df = add_zscore(df)

    long_out = OUT / "features_long.csv"
    cols = [
        "園代碼", "園名", "學年度", "資料表", "指標", "資料類別", "數值",
        "前一年數值", "YoY變化額", "YoY變化率",
        "歷史均值", "歷史標準差", "移動平均_近3年", "Zscore",
    ]
    df[cols].to_csv(long_out, index=False, encoding="utf-8-sig")
    print(f"寫出特徵長表：{long_out}（{len(df)} 列）")

    wide = pivot_for_derived(df)
    wide = add_derived(wide)
    wide_out = OUT / "features_derived.csv"
    wide.to_csv(wide_out, index=False, encoding="utf-8-sig")
    print(f"寫出衍生指標寬表：{wide_out}（{len(wide)} 列）")

    # 簡易健檢：確認規則有落實
    nonrev_110_yoy = df[(df["資料類別"] == "非收支") & (df["學年度"] == 110)]["YoY變化率"]
    print(f"\n健檢：非收支類 110 年 YoY 全為 NaN？ {nonrev_110_yoy.isna().all()}")
    rev_110_yoy = df[(df["資料類別"] == "收支") & (df["學年度"] == 110)]["YoY變化率"]
    print(f"健檢：收支類 110 年 YoY 有值（可回溯 109）？ {rev_110_yoy.notna().any()}")
    if "每生人事費" in wide:
        na109 = wide[wide["學年度"] == 109]["每生人事費"]
        print(f"健檢：109 年每生人事費全為 NaN（無營運資料）？ {na109.isna().all() if len(na109) else '無109列'}")


if __name__ == "__main__":
    main()
