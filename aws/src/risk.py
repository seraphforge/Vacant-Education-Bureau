"""風險指數 —— 正式演算法（v1）。

四個維度，每個維度都是 0–100 分，各自有權重：

| key            | label    | 權重 | 分數怎麼來 |
|----------------|----------|------|-----------|
| `finance`      | 財務法遵 | 0.75 | 財報分析的「法遵風險指數」× 4，上限 100 |
| `parent_report`| 家長回報 | 1.00 | 還有未結案的回報 = 100，否則 0 |
| `opinion`      | 輿情關注 | 0.50 | 最近一次輿情掃描的「關注指數」× 2.5，上限 100 |
| `compliance`   | 裁罰紀錄 | 0.75 | 罰鍰總額 > 10 萬 = 100；有紀錄但未達 = 50；沒紀錄 = 0 |

總分 = Σ(score × weight) ÷ 3.0（四個權重之和），上限 100。
也就是**加權平均**，所以總分與各維度同樣是 0–100 的尺度，可以直接互相比較。

缺資料的處理（重要）
--------------------
財報只有 10 間學校有（決算書要人工蒐集），輿情要承辦人按按鈕才會有。
如果把「沒資料」當 0 分，等於「沒查過 = 安全」，會系統性低估。
所以缺資料的維度**不計分，把它的權重平均分給還有資料的維度**
（`weight` 回傳的是分配後的有效權重，`baseWeight` 是原始權重）。
最常見的情況就是財報與輿情都沒有 → 1.25 的權重平均分給家長回報與裁罰紀錄。

家長回報與裁罰紀錄一定有資料（沒有回報 / 沒有裁罰本身就是資訊），
所以至少有兩個維度會參與計分，不會出現無法計算總分的情況。

即時性
------
`get_risk()` 每次被呼叫都會重算並寫回 `risk_score_current`，
另外家長回報成案 / 狀態變更、輿情掃描完成時也會呼叫 `recompute()`，
所以 dashboard 清單（讀 `risk_score_current`）不會停留在舊分數。
"""

import json

import auth
from common import error, iso, respond, utcnow

MODEL_VERSION = "risk-v1"

# 雷達圖的軸。順序就是前端顯示順序；weight 是原始權重。
DIMENSIONS = [
    {"key": "finance", "label": "財務法遵", "weight": 0.75},
    {"key": "parent_report", "label": "家長回報", "weight": 1.00},
    {"key": "opinion", "label": "輿情關注", "weight": 0.50},
    {"key": "compliance", "label": "裁罰紀錄", "weight": 0.75},
]
TOTAL_WEIGHT = sum(d["weight"] for d in DIMENSIONS)  # 3.0

# 各維度的換算參數（集中在這裡，改門檻不用翻程式）
FINANCE_MULTIPLIER = 4.0     # 法遵風險指數 -> 0-100
OPINION_MULTIPLIER = 2.5     # 輿情關注指數 -> 0-100
FINE_HIGH_THRESHOLD = 100000  # 罰鍰總額超過這個金額就是 100 分
FINE_ANY_SCORE = 50.0         # 有裁罰紀錄但罰鍰未達門檻

# 「未結案」的回報狀態。pending_verification 不算（還沒通過 Email 驗證，
# 政府端根本看不到）；closed / rejected 是已結案。
OPEN_REPORT_STATUSES = ("submitted", "investigating")

# 風險等級的分界。四維度只提供粗訊號（家長回報與裁罰紀錄都是階梯分數），
# 加權平均後分數天然偏低，所以閾值訂在 65 / 45 而不是 80 / 60，
# 否則「罰鍰超過 10 萬」這種明確的風險訊號會被歸成一般。
HIGH_THRESHOLD = 65
MEDIUM_THRESHOLD = 45

DISCLAIMER = (
    "風險指數是「需要優先關注的程度」，由財報法遵指標、家長回報、輿情線索與"
    "官方裁罰紀錄加權而成，不是違法事實的認定。缺少資料的維度不計分，"
    "其權重會平均分配給其他維度。"
)


def level_of(score):
    if score is None:
        return None
    if score >= HIGH_THRESHOLD:
        return "high"
    if score >= MEDIUM_THRESHOLD:
        return "medium"
    return "normal"


def _clamp(value):
    return max(0.0, min(100.0, round(float(value), 2)))


# ---------------------------------------------------------------------------
# 純計算（不碰 DB，方便批次工具與測試重用）
# ---------------------------------------------------------------------------
def score_dimensions(signals):
    """把原始訊號換算成四個維度的分數與 detail。

    signals 需要的 key（缺的視為 None / 0）：
        financeIndex  法遵風險指數（None = 沒有財報資料）
        financeYear / financeLevel / financeWarning  只用於 detail 顯示
        openReports   未結案回報件數
        totalReports  這間園的回報總件數
        opinionScore  最近一次掃描的關注指數（None = 從未掃描）
        opinionAt     掃描完成時間
        punishCount   裁罰紀錄筆數
        fineTotal     罰鍰總額
    回傳 list[dict]，每個 dict 有 key/label/score/detail。
    """
    finance_index = signals.get("financeIndex")
    finance_score = (
        None if finance_index is None
        else _clamp(float(finance_index) * FINANCE_MULTIPLIER)
    )

    open_reports = int(signals.get("openReports") or 0)
    opinion_raw = signals.get("opinionScore")
    opinion_score = (
        None if opinion_raw is None
        else _clamp(float(opinion_raw) * OPINION_MULTIPLIER)
    )

    punish_count = int(signals.get("punishCount") or 0)
    fine_total = int(signals.get("fineTotal") or 0)
    if fine_total > FINE_HIGH_THRESHOLD:
        compliance_score = 100.0
    elif punish_count > 0:
        compliance_score = FINE_ANY_SCORE
    else:
        compliance_score = 0.0

    detail = {
        "finance": {
            "complianceIndex": (
                None if finance_index is None else float(finance_index)
            ),
            "fiscalYear": signals.get("financeYear"),
            "overallLevel": signals.get("financeLevel"),
            "note": (
                "法遵風險指數 × 4" if finance_index is not None
                else "尚無決算書分析資料，本維度不計分"
            ),
        },
        "parent_report": {
            "openCount": open_reports,
            "totalCount": int(signals.get("totalReports") or 0),
            "note": (
                f"尚有 {open_reports} 件未結案回報" if open_reports
                else "目前沒有未結案回報"
            ),
        },
        "opinion": {
            "attentionScore": None if opinion_raw is None else float(opinion_raw),
            "scannedAt": iso(signals.get("opinionAt")),
            "note": (
                "輿情關注指數 × 2.5" if opinion_raw is not None
                else "尚未執行輿情分析，本維度不計分"
            ),
        },
        "compliance": {
            "recordCount": punish_count,
            "totalFine": fine_total,
            "note": (
                f"罰鍰總額 {fine_total:,} 元" if punish_count
                else "查無裁罰紀錄"
            ),
        },
    }

    scores = {
        "finance": finance_score,
        "parent_report": 100.0 if open_reports > 0 else 0.0,
        "opinion": opinion_score,
        "compliance": compliance_score,
    }
    return [
        {
            "key": d["key"],
            "label": d["label"],
            "score": scores[d["key"]],
            "detail": detail[d["key"]],
        }
        for d in DIMENSIONS
    ]


def apply_weights(dimensions):
    """把缺資料維度的權重平均分給有資料的維度，回 (total_score, dimensions)。

    dimensions 會就地補上 weight（有效權重）與 baseWeight（原始權重）。
    """
    base = {d["key"]: d["weight"] for d in DIMENSIONS}
    present = [d for d in dimensions if d["score"] is not None]
    missing_weight = sum(
        base[d["key"]] for d in dimensions if d["score"] is None
    )
    share = (missing_weight / len(present)) if present else 0.0

    for d in dimensions:
        d["baseWeight"] = round(base[d["key"]], 4)
        d["weight"] = (
            round(base[d["key"]] + share, 4) if d["score"] is not None else 0.0
        )

    if not present:
        return None, dimensions
    total = _clamp(sum(d["score"] * d["weight"] for d in present) / TOTAL_WEIGHT)
    return total, dimensions


# ---------------------------------------------------------------------------
# 讀訊號 + 寫回 risk_score_current
# ---------------------------------------------------------------------------
def collect_signals(cur, kg_id):
    """從各張表撈出一間幼兒園的原始訊號。"""
    signals = {"kindergartenId": kg_id}

    cur.execute(
        "SELECT compliance_index, fiscal_year, overall_level, early_warning "
        "FROM finance_report_current WHERE kindergarten_id = %s",
        (kg_id,),
    )
    row = cur.fetchone()
    if row and row["compliance_index"] is not None:
        signals["financeIndex"] = float(row["compliance_index"])
        signals["financeYear"] = row["fiscal_year"]
        signals["financeLevel"] = row["overall_level"]
        signals["financeWarning"] = row["early_warning"]

    cur.execute(
        "SELECT "
        "  SUM(status IN %s) AS open_count, "
        "  SUM(status <> 'pending_verification') AS total_count "
        "FROM parent_report WHERE kindergarten_id = %s",
        (OPEN_REPORT_STATUSES, kg_id),
    )
    row = cur.fetchone() or {}
    signals["openReports"] = int(row.get("open_count") or 0)
    signals["totalReports"] = int(row.get("total_count") or 0)

    cur.execute(
        "SELECT opinion_score, finished_at FROM opinion_scan_job "
        "WHERE kindergarten_id = %s AND status = 'done' AND opinion_score IS NOT NULL "
        "ORDER BY id DESC LIMIT 1",
        (kg_id,),
    )
    row = cur.fetchone()
    if row:
        signals["opinionScore"] = float(row["opinion_score"])
        signals["opinionAt"] = row["finished_at"]

    cur.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(fine_amount), 0) AS fine "
        "FROM kindergarten_punishment WHERE kindergarten_id = %s",
        (kg_id,),
    )
    row = cur.fetchone() or {}
    signals["punishCount"] = int(row.get("n") or 0)
    signals["fineTotal"] = int(row.get("fine") or 0)
    return signals


def store(cur, kg_id, total, dimensions):
    cur.execute(
        """INSERT INTO risk_score_current
               (kindergarten_id, total_score, risk_level, dimensions,
                model_version, is_placeholder, computed_at)
           VALUES (%s, %s, %s, %s, %s, 0, %s)
           ON DUPLICATE KEY UPDATE
               total_score = VALUES(total_score),
               risk_level = VALUES(risk_level),
               dimensions = VALUES(dimensions),
               model_version = VALUES(model_version),
               is_placeholder = 0,
               computed_at = VALUES(computed_at)""",
        (
            kg_id,
            total,
            level_of(total),
            json.dumps(dimensions, ensure_ascii=False),
            MODEL_VERSION,
            utcnow(),
        ),
    )


def compute(cur, kg_id, persist=True):
    """算一間幼兒園的風險指數。回 (total_score, dimensions)。"""
    total, dimensions = apply_weights(score_dimensions(collect_signals(cur, kg_id)))
    if persist:
        store(cur, kg_id, total, dimensions)
    return total, dimensions


def recompute(cur, kg_id):
    """事件驅動的重算入口（家長回報、輿情掃描完成時呼叫）。

    刻意吞掉例外：風險分數只是衍生資料，重算失敗不該讓「回覆家長」或
    「輿情掃描」這種主要流程整個失敗。失敗會留在 CloudWatch log 裡，
    而且下一次有人打開風險 Tab 就會自動補算回來。
    """
    try:
        return compute(cur, kg_id)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN risk.recompute({kg_id}) failed: {type(exc).__name__}: {exc}")
        return None, None


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def get_risk(cur, kg_id, identity):
    """GET /api/secure/kindergartens/{id}/risk

    每次呼叫都即時重算（四個維度的來源都是同一個 DB，成本是 4 個索引查詢），
    順手寫回 risk_score_current 讓清單頁的分數同步更新。
    """
    county = auth.scoped_county(identity)
    if county is None and not identity["isAdmin"]:
        return error(
            403, "FORBIDDEN", "這個帳號沒有設定縣市（custom:county），請聯絡管理者"
        )

    cur.execute("SELECT id, county, school_name FROM kindergarten WHERE id = %s", (kg_id,))
    school = cur.fetchone()
    if not school or (county and school["county"] != county):
        return error(404, "KINDERGARTEN_NOT_FOUND", "查無此幼兒園")

    total, dimensions = compute(cur, kg_id)
    return respond(
        200,
        {
            "kindergartenId": kg_id,
            "schoolName": school["school_name"],
            "totalScore": total,
            "riskLevel": level_of(total),
            "isPlaceholder": False,
            "modelVersion": MODEL_VERSION,
            "computedAt": iso(utcnow()),
            "dimensions": dimensions,
            "disclaimer": DISCLAIMER,
        },
    )
