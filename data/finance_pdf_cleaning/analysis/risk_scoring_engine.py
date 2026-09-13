"""幼兒園法遵風險預警系統 (Scoring Engine)。

核心理念：不以單一財務指標直接判定違規，而是透過
  (1) 歷史趨勢異常 (Self-Historical)
  (2) 同類機構相對異常 (Peer-Relative)
  (3) 多源事件資料交叉驗證 (Event Cross-Validation)
辨識高風險機構，提供人工查核之優先排序。

四階段模組化：
  Stage 1 財務異常特徵工程 (FinancialFeatureEngineer)
  Stage 2 營運異常特徵工程 (OperationalFeatureEngineer)
  Stage 3 事件資料整合接口 (EventDataIntegrator)
  Stage 4 交叉驗證評分引擎 (KindergartenRiskScoringEngine)

資料年份限制（沿用專案規範）：
  - 收支餘絀（收支類）有 109~113；其他表（非收支類）僅 110~113。
  - 非收支類最早年 (110) 的 YoY 為 NaN；收支類 110 可回溯 109。
  - Z-score / rolling 一律 min_periods>=1；缺失不補 0。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
import pandas as pd

# 確保含中文的輸出在 Windows 終端（cp950）也能正常顯示
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 各資料類別最早合法年份
EARLIEST = {"收支": 109, "非收支": 110}

# 費用科目（用於結構占比、波動度、YoY）
EXPENSE_ITEMS = ["人事費", "業務費", "材料費", "食材費", "維護費", "修繕購置費", "業務發展費", "其他支出"]

# 嚴重程度分級（以文字＋數字表示，數字越大越嚴重，取代原本的顏色符號）
LEVEL_RED = "RED"        # 高度異常
LEVEL_YELLOW = "YELLOW"  # 中度異常
LEVEL_GREEN = "GREEN"    # 正常
LEVEL_NA = "N/A"         # 資料不足，無法判定
LEVEL_RANK = {LEVEL_RED: 3, LEVEL_YELLOW: 2, LEVEL_GREEN: 1, LEVEL_NA: 0}
LEVEL_POINTS = {LEVEL_RED: 2.0, LEVEL_YELLOW: 1.0, LEVEL_GREEN: 0.0, LEVEL_NA: 0.0}


# ---------------------------------------------------------------------------
# 防呆工具
# ---------------------------------------------------------------------------
def safe_divide(numerator, denominator):
    """安全除法：分母為 0、NaN 或 None 時回傳 NaN，不補 0。"""
    num = pd.to_numeric(pd.Series(numerator), errors="coerce")
    den = pd.to_numeric(pd.Series(denominator), errors="coerce")
    with np.errstate(divide="ignore", invalid="ignore"):
        result = np.where((den.notna()) & (den != 0) & (num.notna()), num / den, np.nan)
    return pd.Series(result, index=num.index)


def zscore(series: pd.Series, min_periods: int = 2) -> pd.Series:
    """對整個序列算 z-score；標準差為 0 或樣本不足時回 NaN。"""
    s = pd.to_numeric(series, errors="coerce")
    if s.notna().sum() < min_periods:
        return pd.Series(np.nan, index=s.index)
    mu, sd = s.mean(), s.std(ddof=1)
    if not sd or np.isnan(sd):
        return pd.Series(np.nan, index=s.index)
    return (s - mu) / sd


# ===========================================================================
# Stage 1: 財務異常特徵工程
# ===========================================================================
class FinancialFeatureEngineer:
    """從財務報表計算五層財務異常指標。

    預期輸入 DataFrame（寬表，一列一(園,年)）至少含：
      幼兒園ID, 年份, 以及各費用科目決算欄、對應預算欄、
      收入合計/支出合計、資產/負債流動與非流動、期末現金淨增加、本期稅後餘絀。
    缺欄位時對應指標為 NaN，不中斷。
    """

    def __init__(self, expense_items: Iterable[str] = EXPENSE_ITEMS):
        self.expense_items = list(expense_items)

    # ---- 1. 年度變化異常 ----
    def time_series_anomalies(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.sort_values(["幼兒園ID", "年份"]).copy()
        for item in self.expense_items:
            col = f"{item}_決算"
            if col not in df:
                continue
            g = df.groupby("幼兒園ID")[col]
            prev = g.shift(1)
            df[f"{item}_YoY"] = safe_divide(df[col] - prev, prev.abs())
            # 連續成長旗標：連續 >0 視為持續成長；於評分層再判定微幅 vs 暴增
            def _consecutive_growth(s: pd.Series) -> pd.Series:
                pct = s.pct_change(fill_method=None)
                grew = pct > 0
                # 以「非成長」為斷點，累計連續成長年數
                return grew.groupby((~grew).cumsum()).cumsum()

            df[f"{item}_連續成長年數"] = (
                g.apply(_consecutive_growth).reset_index(level=0, drop=True)
            )
            # 波動度 = 該園該科目 110~113 標準差/平均（跨年，回填每列）
            vol = g.transform(
                lambda s: (s.std(ddof=1) / abs(s.mean())) if s.notna().sum() >= 2 and s.mean() else np.nan
            )
            df[f"{item}_波動度"] = vol
        return df

    # ---- 2. 預決算異常 ----
    def budget_anomalies(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        over_cnt = pd.Series(0, index=df.index)
        total_cnt = pd.Series(0, index=df.index)
        for item in self.expense_items:
            dec, bud = f"{item}_決算", f"{item}_預算"
            if dec not in df or bud not in df:
                continue
            exec_rate = safe_divide(df[dec], df[bud])
            df[f"{item}_執行率"] = exec_rate
            df[f"{item}_偏離率"] = safe_divide(df[dec] - df[bud], df[bud])
            has_both = df[dec].notna() & df[bud].notna() & (df[bud] != 0)
            total_cnt = total_cnt + has_both.astype(int)
            over_cnt = over_cnt + (has_both & (df[dec] > df[bud])).astype(int)
        df["預算超支科目比例"] = safe_divide(over_cnt, total_cnt)
        return df

    # ---- 3. 費用結構異常 ----
    def structure_anomalies(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.sort_values(["幼兒園ID", "年份"]).copy()
        if "支出合計_決算" not in df:
            return df
        for item in self.expense_items:
            col = f"{item}_決算"
            if col not in df:
                continue
            ratio = safe_divide(df[col], df["支出合計_決算"])
            df[f"{item}_占比"] = ratio
            df[f"{item}_占比變化"] = df.groupby("幼兒園ID")[f"{item}_占比"].diff()
        return df

    # ---- 4. 規模標準化指標（每生） ----
    def per_student_metrics(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        n = df.get("學生人數")
        if n is None:
            return df
        mapping = {
            "每生人事費": "人事費_決算",
            "每生食材費": "食材費_決算",
            "每生總支出": "支出合計_決算",
            "每生業務費": "業務費_決算",
            "每生修繕費": "修繕購置費_決算",
        }
        for out_col, src in mapping.items():
            if src in df:
                df[out_col] = safe_divide(df[src], n)
        # 若有供餐天數，每生每日餐食成本
        if "食材費_決算" in df and "供餐天數" in df:
            df["每生每日餐食成本"] = safe_divide(
                safe_divide(df["食材費_決算"], n), df["供餐天數"]
            )
        return df

    # ---- 5. 跨表勾稽與其他財務指標 ----
    def cross_validation_metrics(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.sort_values(["幼兒園ID", "年份"]).copy()

        # 收支合理性：支出成長率 - 收入成長率；收支餘絀率
        if "收入合計_決算" in df and "支出合計_決算" in df:
            inc_g = df.groupby("幼兒園ID")["收入合計_決算"]
            exp_g = df.groupby("幼兒園ID")["支出合計_決算"]
            df["收入成長率"] = safe_divide(df["收入合計_決算"] - inc_g.shift(1), inc_g.shift(1).abs())
            df["支出成長率"] = safe_divide(df["支出合計_決算"] - exp_g.shift(1), exp_g.shift(1).abs())
            df["收支成長率差"] = df["支出成長率"] - df["收入成長率"]
            df["收支餘絀率"] = safe_divide(
                df["收入合計_決算"] - df["支出合計_決算"], df["收入合計_決算"]
            )

        # 資產負債表：流動比率、負債比率
        if {"流動資產", "流動負債"}.issubset(df.columns):
            df["流動比率"] = safe_divide(df["流動資產"], df["流動負債"])
        if {"負債總額", "資產總計"}.issubset(df.columns):
            df["負債比率"] = safe_divide(df["負債總額"], df["資產總計"])

        # 現金流量：帳面盈餘但現金淨增加為負 → 異常旗標
        if {"本期稅後餘絀", "期末現金淨增加"}.issubset(df.columns):
            df["現金流異常"] = (
                (pd.to_numeric(df["本期稅後餘絀"], errors="coerce") > 0)
                & (pd.to_numeric(df["期末現金淨增加"], errors="coerce") < 0)
            )

        return df

    def scan_notes_keywords(self, notes_df: pd.DataFrame, keywords: Iterable[str]) -> pd.DataFrame:
        """NLP 關鍵字檢索接口（Stage 1-5 附註）。

        notes_df 需含 幼兒園ID, 年份, 內容。回傳每(園,年)命中關鍵字次數。
        """
        keywords = list(keywords)
        if notes_df is None or notes_df.empty:
            return pd.DataFrame(columns=["幼兒園ID", "年份"] + [f"附註_{k}" for k in keywords])
        rows = []
        for (kid, year), g in notes_df.groupby(["幼兒園ID", "年份"]):
            text = " ".join(str(x) for x in g["內容"].fillna(""))
            row = {"幼兒園ID": kid, "年份": year}
            for k in keywords:
                row[f"附註_{k}"] = text.count(k)
            rows.append(row)
        return pd.DataFrame(rows)

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        df = self.time_series_anomalies(df)
        df = self.budget_anomalies(df)
        df = self.structure_anomalies(df)
        df = self.per_student_metrics(df)
        df = self.cross_validation_metrics(df)
        return df


# ===========================================================================
# Stage 2: 營運異常特徵工程
# ===========================================================================
class OperationalFeatureEngineer:
    """計算營運風險指標：師生比、教職員流動率、加班費負荷。

    預期欄位：學生人數, 教保人員數, 員工人數(選), 離職人數(選), 加班費(選), 人事費_決算。
    """

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.sort_values(["幼兒園ID", "年份"]).copy()

        # 1. 師生比 = 學生人數 / 教保人員數；惡化幅度 = 逐年變化（比值上升代表每位教保員照顧更多學生）
        if {"學生人數", "教保人員數"}.issubset(df.columns):
            df["師生比"] = safe_divide(df["學生人數"], df["教保人員數"])
            df["師生比惡化幅度"] = df.groupby("幼兒園ID")["師生比"].diff()

        # 2. 教職員流動率 = 離職人數 / 員工人數；與歷年比較（YoY 變化）
        staff_base = "員工人數" if "員工人數" in df else ("教保人員數" if "教保人員數" in df else None)
        if "離職人數" in df and staff_base:
            df["教職員流動率"] = safe_divide(df["離職人數"], df[staff_base])
            df["流動率變化"] = df.groupby("幼兒園ID")["教職員流動率"].diff()

        # 3. 加班費負荷 = 加班費 / 總人事費
        if "加班費" in df and "人事費_決算" in df:
            df["加班費負荷"] = safe_divide(df["加班費"], df["人事費_決算"])
            df["加班費負荷變化"] = df.groupby("幼兒園ID")["加班費負荷"].diff()

        return df


# ===========================================================================
# Stage 3: 事件資料整合接口
# ===========================================================================
@dataclass
class EventDataIntegrator:
    """合併多源事件資料（管教回報、負評比例、官方裁罰）。

    預留接口：呼叫 register_* 提供各來源的長/寬表（含 幼兒園ID, 年份），
    merge() 會以 how='left' 併回主特徵表，缺資料的年度為 NaN（不補 0）。
    真實串接時只要提供符合欄位的 DataFrame 即可，不需改動評分邏輯。
    """

    reports: pd.DataFrame | None = None      # 需含：幼兒園ID, 年份, 不當管教回報數
    reviews: pd.DataFrame | None = None      # 需含：幼兒園ID, 年份, 負面評論比例
    penalties: pd.DataFrame | None = None    # 需含：幼兒園ID, 年份, 裁罰次數
    extra_sources: list[pd.DataFrame] = field(default_factory=list)

    def register_reports(self, df: pd.DataFrame) -> "EventDataIntegrator":
        self.reports = df
        return self

    def register_reviews(self, df: pd.DataFrame) -> "EventDataIntegrator":
        self.reviews = df
        return self

    def register_penalties(self, df: pd.DataFrame) -> "EventDataIntegrator":
        self.penalties = df
        return self

    def register_source(self, df: pd.DataFrame) -> "EventDataIntegrator":
        """通用接口：任何含 幼兒園ID, 年份 的事件表都能掛進來。"""
        self.extra_sources.append(df)
        return self

    def merge(self, base: pd.DataFrame) -> pd.DataFrame:
        out = base.copy()
        for src in [self.reports, self.reviews, self.penalties, *self.extra_sources]:
            if src is None or src.empty:
                continue
            keys = [k for k in ("幼兒園ID", "年份") if k in src.columns]
            if not keys:
                continue
            out = out.merge(src, on=keys, how="left")
        # 事件計數型欄位：真實情境「無回報」可視為 0，但預設保留 NaN 以免干擾統計；
        # 由呼叫端決定是否 fillna。這裡不強制補值。
        return out


# ===========================================================================
# Stage 4: 交叉驗證與風險評分 MVP 模型
# ===========================================================================
# 風險方向類型（輸出時標明是哪一種風險）
RISK_HIGH = "HIGH"    # 高於正常範圍 → 風險
RISK_LOW = "LOW"      # 低於正常範圍 → 風險
RISK_SHIFT = "SHIFT"  # 突然變化（不分方向）→ 風險


@dataclass
class IndicatorSpec:
    """單一評分指標的設定，支援三種風險方向判斷。

    每個指標可同時啟用多種方向：
      high_risk : 偏離度往「高」的方向達門檻 → 風險（過高，如師生比過高）
      low_risk  : 偏離度往「低」的方向達門檻 → 風險（過低，如人事費/生過低=人力投入不足）
      shift_risk: 不分方向，只要「突然變化」（相對自身歷史的 |z| 大）→ 風險
    綜合等級取三種方向中最嚴重者，並記錄觸發的風險方向類型。

    higher_bad 保留為相容參數：若未明確指定三旗標，預設 high_risk=True。
    """
    key: str
    column: str
    面向: str = "財務"
    high_risk: bool = True
    low_risk: bool = False
    shift_risk: bool = False
    # 相容舊介面
    higher_bad: bool | None = None

    def __post_init__(self):
        if self.higher_bad is not None:
            # 舊式：higher_bad=True → high_risk；False → low_risk
            self.high_risk = self.higher_bad
            self.low_risk = not self.higher_bad


# MVP 8 核心指標（依「過高/過低/突變」三方向設定）
CORE_INDICATORS = [
    # 每生人事費：過高=成本過重、過低=人力投入不足、突變=查帳務人員異動
    IndicatorSpec("每生人事費", "每生人事費", "財務", high_risk=True, low_risk=True, shift_risk=True),
    # 人事費年增率：突變最重要（多年穩定後突然 +30%），過高/過低皆須查
    IndicatorSpec("人事費年增率", "人事費_YoY", "財務", high_risk=True, low_risk=True, shift_risk=True),
    # 預決算偏離率：過高=超支、過低=編列失準/計畫未執行、突變=某年大幅偏離
    IndicatorSpec("預決算偏離率", "預算偏離率", "財務", high_risk=True, low_risk=True, shift_risk=True),
    # 經費流用比例：過高=紀律鬆散、突變=某年突然大量流用（重點查核）
    IndicatorSpec("經費流用比例", "經費流用比例", "財務", high_risk=True, low_risk=False, shift_risk=True),
    # 師生比(學生/教保)：過高=負擔過重、突變=教保驟減
    IndicatorSpec("師生比", "師生比", "營運", high_risk=True, low_risk=False, shift_risk=True),
    # 教職員流動率：過高=人力不穩、突變=某年大量離職（重要警訊）
    IndicatorSpec("教職員流動率", "教職員流動率", "營運", high_risk=True, low_risk=False, shift_risk=True),
    # 加班費負荷：過高=過勞、突變=某年暴增
    IndicatorSpec("加班費負荷", "加班費負荷", "營運", high_risk=True, low_risk=False, shift_risk=True),
    # 不當管教事件：過高=直接警訊
    IndicatorSpec("不當管教事件", "不當管教回報數", "事件", high_risk=True, low_risk=False, shift_risk=False),
]


class KindergartenRiskScoringEngine:
    """交叉驗證評分引擎：對 8 核心指標做三維度比較並產出法遵風險指數。

    三維度：
      1. 歷年自我比較 (Self-Historical)：與該園前 1-3 年比，Z-score。
      2. 同業相對比較 (Peer-Relative)：同縣市、同規模(人數帶)群體，Z-score。
      3. 事件前後比較 (Pre/Post-Event)：給定事件年份，回溯事件前 1-2 年哪些指標已達 RED。

    嚴重程度規則（可調）：任一維度偏離度 |z| >= red_th → RED；>= yellow_th → YELLOW；否則 GREEN。
    資料不足為 N/A。各等級數字見 LEVEL_RANK（RED=3, YELLOW=2, GREEN=1, N/A=0）。
    法遵風險指數 = 各指標 RED/YELLOW 的加權計分之總和（0~100 正規化）。
    """

    def __init__(
        self,
        indicators: list[IndicatorSpec] = None,
        yellow_th: float = 1.0,
        red_th: float = 2.0,
        peer_size_band: int = 20,
        weights: dict[str, float] = None,
    ):
        self.indicators = indicators or CORE_INDICATORS
        self.yellow_th = yellow_th
        self.red_th = red_th
        self.peer_size_band = peer_size_band
        # 各面向權重（事件面通常最重）
        self.weights = weights or {"財務": 1.0, "營運": 1.2, "事件": 2.0}

    # ---- 維度一：歷年自我比較 ----
    def _self_historical_z(self, df: pd.DataFrame, col: str, min_history: int = 2) -> pd.Series:
        """歷年自我比較：以「當年之前」的歷史為基準算 z-score。

        關鍵：基準用 shift(1) 後的 expanding，使「當年值」不會稀釋自身的偏離度
        （突發暴增才能得到高 z）。歷史點數不足 min_history 時回 NaN（不假陽性）。
        """
        def per_group(s: pd.Series) -> pd.Series:
            past = s.shift(1)
            mu = past.expanding(min_periods=min_history).mean()
            sd = past.expanding(min_periods=min_history).std(ddof=1)
            with np.errstate(divide="ignore", invalid="ignore"):
                z = np.where((sd.notna()) & (sd != 0), (s - mu) / sd, np.nan)
            return pd.Series(z, index=s.index)

        return (
            df.groupby("幼兒園ID")[col]
            .transform(lambda s: per_group(s))
        )

    # ---- 維度二：同業相對比較 ----
    def _peer_relative_z(self, df: pd.DataFrame, col: str, min_peers: int = 3) -> pd.Series:
        """同縣市 + 同規模帶 + 同年份的群體 z-score。

        若某群體樣本數不足 min_peers（同規模帶同縣市家數太少），
        自動退回「同縣市同年份」→ 再退回「同年份全體」，確保仍能算出相對位置。
        """
        d = df.copy()
        size_col = "學生人數" if "學生人數" in d else None
        if size_col is not None:
            d["_規模帶"] = (pd.to_numeric(d[size_col], errors="coerce") // self.peer_size_band).astype("Int64")
        else:
            d["_規模帶"] = 0
        city_col = "縣市" if "縣市" in d else None

        def grp_z(s: pd.Series) -> pd.Series:
            if s.notna().sum() < 2:
                return pd.Series(np.nan, index=s.index)
            mu, sd = s.mean(), s.std(ddof=1)
            if not sd or np.isnan(sd):
                return pd.Series(np.nan, index=s.index)
            return (s - mu) / sd

        # 由細到粗的分組層級
        levels = []
        if city_col:
            levels.append(["年份", "_規模帶", city_col])
            levels.append(["年份", city_col])
        levels.append(["年份", "_規模帶"])
        levels.append(["年份"])

        result = pd.Series(np.nan, index=d.index)
        for group_cols in levels:
            # 只對「該分組樣本數 >= min_peers 且目前仍為 NaN」的列計算
            sizes = d.groupby(group_cols)[col].transform(lambda s: s.notna().sum())
            z = d.groupby(group_cols)[col].transform(grp_z)
            fill = result.isna() & (sizes >= min_peers)
            result[fill] = z[fill]
        return result

    def _level_from_z(self, signed_z: float) -> str:
        """由『帶方向的偏離度』判等級：越大越嚴重。"""
        if pd.isna(signed_z):
            return LEVEL_NA
        if signed_z >= self.red_th:
            return LEVEL_RED
        if signed_z >= self.yellow_th:
            return LEVEL_YELLOW
        return LEVEL_GREEN

    def _evaluate(self, spec: IndicatorSpec, z_hist: float, z_peer: float, z_shift: float):
        """綜合過高/過低/突變三方向，回傳 (等級, 觸發的風險方向清單)。

        z_hist/z_peer：歷年、同業偏離度（正=高於基準，負=低於基準）。
        z_shift      ：突變偏離度（用歷年 z 的絕對值，不分方向）。
        """
        candidates = []  # (等級, 風險方向)

        # 過高：任一維度往高偏離
        if spec.high_risk:
            hi = max(
                self._level_from_z(z_hist) if pd.notna(z_hist) else LEVEL_NA,
                self._level_from_z(z_peer) if pd.notna(z_peer) else LEVEL_NA,
                key=lambda x: LEVEL_RANK[x],
            )
            candidates.append((hi, RISK_HIGH))

        # 過低：任一維度往低偏離（取負號後判定）
        if spec.low_risk:
            lo = max(
                self._level_from_z(-z_hist) if pd.notna(z_hist) else LEVEL_NA,
                self._level_from_z(-z_peer) if pd.notna(z_peer) else LEVEL_NA,
                key=lambda x: LEVEL_RANK[x],
            )
            candidates.append((lo, RISK_LOW))

        # 突變：用歷年偏離度的絕對值（不分正負）
        if spec.shift_risk:
            sh = self._level_from_z(abs(z_shift)) if pd.notna(z_shift) else LEVEL_NA
            candidates.append((sh, RISK_SHIFT))

        if not candidates:
            return LEVEL_NA, ""

        # 取最嚴重的等級；同時收集所有達 YELLOW 以上的風險方向
        best_level = max(candidates, key=lambda c: LEVEL_RANK[c[0]])[0]
        dirs = sorted(
            {d for lv, d in candidates if LEVEL_RANK[lv] >= LEVEL_RANK[LEVEL_YELLOW]},
            key=lambda d: {RISK_HIGH: 0, RISK_LOW: 1, RISK_SHIFT: 2}[d],
        )
        if best_level == LEVEL_NA:
            return LEVEL_NA, "無資料"
        if not dirs:
            return best_level, "正常"  # 有資料但未達風險門檻
        return best_level, "+".join(dirs)

    def score(
        self,
        df: pd.DataFrame,
        event_year: int | None = None,
        lookback: int = 2,
    ) -> pd.DataFrame:
        """對特徵表計算三維度 z-score、燈號與法遵風險指數。

        參數：
          event_year：事件發生年份，啟用事件前後回溯（維度三）。
          lookback  ：回溯事件前幾年（預設 2）。
        回傳：逐(園,年)一列，含各指標 z-score、燈號、風險指數。
        """
        df = df.sort_values(["幼兒園ID", "年份"]).copy()
        result_cols = {}

        # 逐指標算兩種常態維度的 z-score 與燈號
        for spec in self.indicators:
            col = spec.column
            if col not in df:
                # 指標缺欄位：整欄 NaN，等級 N/A，風險方向留空（無資料）
                df[f"{spec.key}_歷年z"] = np.nan
                df[f"{spec.key}_同業z"] = np.nan
                df[f"{spec.key}_等級"] = LEVEL_NA
                df[f"{spec.key}_等級分數"] = LEVEL_RANK[LEVEL_NA]
                df[f"{spec.key}_風險方向"] = "無資料"
                continue
            zh = self._self_historical_z(df, col)
            zp = self._peer_relative_z(df, col)
            df[f"{spec.key}_歷年z"] = zh
            df[f"{spec.key}_同業z"] = zp
            # 綜合等級（過高/過低/突變三方向）：突變用歷年 z 的絕對值
            levels, dirs = [], []
            for a, b in zip(zh, zp):
                lv, dr = self._evaluate(spec, a, b, a)
                levels.append(lv)
                dirs.append(dr)
            df[f"{spec.key}_等級"] = levels
            df[f"{spec.key}_等級分數"] = [LEVEL_RANK[x] for x in levels]
            df[f"{spec.key}_風險方向"] = dirs

        # 法遵風險指數：各指標等級點數 × 面向權重 加總，再正規化到 0~100
        max_possible = sum(2.0 * self.weights.get(s.面向, 1.0) for s in self.indicators)

        def row_index(row) -> float:
            total = 0.0
            for spec in self.indicators:
                level = row.get(f"{spec.key}_等級", LEVEL_NA)
                total += LEVEL_POINTS.get(level, 0.0) * self.weights.get(spec.面向, 1.0)
            return round(100 * total / max_possible, 1) if max_possible else np.nan

        df["法遵風險指數"] = df.apply(row_index, axis=1)

        def overall_level(idx: float) -> str:
            if pd.isna(idx):
                return LEVEL_NA
            if idx >= 50:
                return "高風險"
            if idx >= 25:
                return "中風險"
            return "低風險"

        df["整體風險等級"] = df["法遵風險指數"].apply(overall_level)

        # ---- 維度三：事件前後回溯 ----
        if event_year is not None:
            df = self._event_time_travel(df, event_year, lookback)

        return df

    def _event_time_travel(self, df: pd.DataFrame, event_year: int, lookback: int) -> pd.DataFrame:
        """標記事件前 1~lookback 年已達 RED/YELLOW 的指標（Pre-Event 預警回溯）。"""
        pre_years = list(range(event_year - lookback, event_year))
        pre = df[df["年份"].isin(pre_years)].copy()
        warn = {}
        for (kid), g in pre.groupby("幼兒園ID"):
            hits = []
            for spec in self.indicators:
                col = f"{spec.key}_等級"
                if col in g and (g[col].isin([LEVEL_RED, LEVEL_YELLOW]).any()):
                    worst = LEVEL_RED if (g[col] == LEVEL_RED).any() else LEVEL_YELLOW
                    hits.append(f"{spec.key}({worst})")
            warn[kid] = "、".join(hits) if hits else "（事件前無預警）"
        df["事件前預警"] = df["幼兒園ID"].map(warn)
        df["事件年份"] = event_year
        return df


# ===========================================================================
# 完整 Pipeline 組裝
# ===========================================================================
def run_pipeline(
    feature_df: pd.DataFrame,
    events: EventDataIntegrator | None = None,
    event_year: int | None = None,
    lookback: int = 2,
) -> pd.DataFrame:
    """一鍵跑完 Stage 1~4，回傳含風險指數的最終 DataFrame。"""
    fin = FinancialFeatureEngineer().transform(feature_df)
    ope = OperationalFeatureEngineer().transform(fin)
    merged = events.merge(ope) if events is not None else ope
    engine = KindergartenRiskScoringEngine()
    return engine.score(merged, event_year=event_year, lookback=lookback)


# ===========================================================================
# Dummy Data 產生器（Example Usage）
# ===========================================================================
def make_dummy_data() -> tuple[pd.DataFrame, EventDataIntegrator]:
    """模擬 3 所幼兒園 109~113 年資料。

    設計情境：
      KG-A：113 年人事費暴增、加班費負荷升高、師生比惡化（高風險，事件前已預警）。
      KG-B：平穩正常（低風險）。
      KG-C：預算偏離大、每生人事費偏高（中風險）。
    """
    rng = np.random.default_rng(42)
    years = [109, 110, 111, 112, 113]
    rows = []

    def base_row(kid, city, year, students, teachers, personnel, budget_personnel,
                 income, expense, overtime, resign, assets, cur_assets, cur_liab, liab, cash_net):
        return {
            "幼兒園ID": kid, "縣市": city, "年份": year,
            "學生人數": students, "教保人員數": teachers, "員工人數": teachers + 3,
            "離職人數": resign, "加班費": overtime,
            "人事費_決算": personnel, "人事費_預算": budget_personnel,
            "業務費_決算": expense * 0.08, "業務費_預算": expense * 0.09,
            "材料費_決算": expense * 0.06, "材料費_預算": expense * 0.07,
            "食材費_決算": expense * 0.10, "食材費_預算": expense * 0.10,
            "修繕購置費_決算": expense * 0.03, "修繕購置費_預算": expense * 0.04,
            "業務發展費_決算": expense * 0.05, "業務發展費_預算": expense * 0.02,
            "其他支出_決算": expense * 0.04, "其他支出_預算": expense * 0.04,
            "收入合計_決算": income, "支出合計_決算": expense, "支出合計_預算": expense * 0.98,
            "資產總計": assets, "流動資產": cur_assets, "流動負債": cur_liab, "負債總額": liab,
            "本期稅後餘絀": income - expense, "期末現金淨增加": cash_net,
            "經費流用比例": 0.0, "預算偏離率": np.nan, "供餐天數": 200,
        }

    # KG-A：113 惡化
    for i, y in enumerate(years):
        spike = 1.0
        if y == 113:
            spike = 1.6  # 人事費暴增
        students = [118, 120, 122, 120, 90][i]     # 113 人數驟減 → 師生比惡化
        teachers = [12, 12, 12, 11, 7][i]          # 113 教保員驟減
        personnel = int([7.0, 7.2, 7.5, 7.8, 7.8][i] * 1_000_000 * spike)
        rows.append(base_row("KG-A", "新北市", y, students, teachers, personnel,
                             int(personnel * 0.9),
                             income=int(10_000_000 * (1 + 0.03 * i)),
                             expense=int(9_500_000 * (1 + 0.05 * i) * spike),
                             overtime=int(personnel * ([0.03, 0.03, 0.04, 0.05, 0.15][i])),
                             resign=[1, 1, 2, 2, 6][i],
                             assets=20_000_000, cur_assets=8_000_000,
                             cur_liab=3_000_000, liab=12_000_000,
                             cash_net=[500_000, 400_000, 300_000, 200_000, -800_000][i]))
        rows[-1]["經費流用比例"] = [0.02, 0.03, 0.04, 0.05, 0.22][i]
        rows[-1]["預算偏離率"] = [0.01, 0.02, 0.03, 0.05, 0.28][i]
        if y == 113:
            rows[-1]["不當管教回報數"] = 4
            rows[-1]["負面評論比例"] = 0.35
            rows[-1]["裁罰次數"] = 1

    # KG-B：平穩
    for i, y in enumerate(years):
        students = [100, 102, 101, 103, 102][i]
        teachers = [10, 10, 10, 10, 10][i]
        personnel = int(6_500_000 * (1 + 0.02 * i))
        rows.append(base_row("KG-B", "新北市", y, students, teachers, personnel,
                             int(personnel * 0.98),
                             income=int(9_000_000 * (1 + 0.02 * i)),
                             expense=int(8_600_000 * (1 + 0.02 * i)),
                             overtime=int(personnel * 0.03),
                             resign=1,
                             assets=18_000_000, cur_assets=7_000_000,
                             cur_liab=2_500_000, liab=9_000_000,
                             cash_net=300_000))
        rows[-1]["經費流用比例"] = 0.03
        rows[-1]["預算偏離率"] = 0.02

    # KG-C：預算偏離、每生人事費偏高
    for i, y in enumerate(years):
        students = [70, 72, 71, 70, 72][i]
        teachers = [9, 9, 9, 9, 9][i]
        personnel = int(7_800_000 * (1 + 0.03 * i))  # 人少但人事費高 → 每生人事費偏高
        rows.append(base_row("KG-C", "新北市", y, students, teachers, personnel,
                             int(personnel * 0.75),               # 預算偏離大
                             income=int(8_500_000 * (1 + 0.02 * i)),
                             expense=int(8_800_000 * (1 + 0.03 * i)),
                             overtime=int(personnel * 0.06),
                             resign=2,
                             assets=15_000_000, cur_assets=5_000_000,
                             cur_liab=3_500_000, liab=10_000_000,
                             cash_net=100_000))
        rows[-1]["經費流用比例"] = [0.05, 0.06, 0.08, 0.10, 0.12][i]
        rows[-1]["預算偏離率"] = [0.20, 0.22, 0.24, 0.25, 0.27][i]

    df = pd.DataFrame(rows)

    # 事件資料（Stage 3）：以獨立表提供，示範接口
    events = EventDataIntegrator()
    reports = df[["幼兒園ID", "年份", "不當管教回報數"]].dropna() if "不當管教回報數" in df else pd.DataFrame()
    reviews = df[["幼兒園ID", "年份", "負面評論比例"]].dropna() if "負面評論比例" in df else pd.DataFrame()
    penalties = df[["幼兒園ID", "年份", "裁罰次數"]].dropna() if "裁罰次數" in df else pd.DataFrame()
    events.register_reports(reports).register_reviews(reviews).register_penalties(penalties)
    # 從主表移除事件欄，改由 Stage 3 併回（示範真實流程）
    df = df.drop(columns=[c for c in ["不當管教回報數", "負面評論比例", "裁罰次數"] if c in df])
    return df, events


def _demo():
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 60)
    pd.set_option("display.unicode.east_asian_width", True)

    feature_df, events = make_dummy_data()
    result = run_pipeline(feature_df, events=events, event_year=113, lookback=2)

    print("=" * 90)
    print("法遵風險評分結果（各園各年）")
    print("=" * 90)
    show = ["幼兒園ID", "年份", "每生人事費", "師生比", "加班費負荷",
            "法遵風險指數", "整體風險等級"]
    show = [c for c in show if c in result.columns]
    view = result[show].copy()
    if "每生人事費" in view:
        view["每生人事費"] = view["每生人事費"].round(0)
    if "師生比" in view:
        view["師生比"] = view["師生比"].round(2)
    if "加班費負荷" in view:
        view["加班費負荷"] = view["加班費負荷"].round(3)
    print(view.to_string(index=False))

    print("\n" + "=" * 90)
    print("113 年各園異常等級明細與事件前預警（事件年份=113，回溯前 2 年 111-112）")
    print("=" * 90)
    y113 = result[result["年份"] == 113]
    level_cols = [c for c in result.columns if c.endswith("_等級")]
    for _, r in y113.iterrows():
        print(f"\n【{r['幼兒園ID']}】 風險指數 {r['法遵風險指數']} → {r['整體風險等級']}")
        lit = []
        for c in level_cols:
            if r[c] in (LEVEL_RED, LEVEL_YELLOW):
                key = c.replace("_等級", "")
                direction = r.get(f"{key}_風險方向", "")
                lit.append(f"{key}:{r[c]}({direction})")
        print("  異常指標：" + ("、".join(lit) if lit else "無"))
        if "事件前預警" in r:
            print(f"  事件前預警：{r['事件前預警']}")

    print("\n" + "=" * 90)
    print("最終 DataFrame 欄位（可輸出 CSV 供平台使用）")
    print("=" * 90)
    key_cols = ["幼兒園ID", "年份"] + [c for c in result.columns if c.endswith(("_歷年z", "_同業z", "_等級", "_等級分數"))]
    print("欄位數：", len(result.columns))
    print("關鍵欄位：", key_cols[:12], "...")
    return result


if __name__ == "__main__":
    _demo()
