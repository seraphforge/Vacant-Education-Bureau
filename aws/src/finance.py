"""財報法遵分析 —— 政府端的財報 Tab 資料來源。

資料從哪來
----------
決算書 PDF → `data/finance_pdf_cleaning/`（清理與指標計算）→
`analysis/outputs/risk_scores.csv` → `tools/load_finance_risk.py` 載進
`finance_report_current` 表（只取最新決算年度 113）。

回什麼
------
1. **法遵風險指數**（CSV 的「法遵風險指數」）與它換算成風險維度的分數
   （× 4，上限 100，公式在 `risk.py`，這裡只引用，不自己再算一份）。
2. **各風險指標的等級**：GREEN / YELLOW / RED，對應 CSV 的「等級分數」1 / 2 / 3，
   0 = 這間學校缺該指標所需的欄位。前端用等級上色，並列出哪幾項踩到紅黃燈。
3. 主要財務數字（收支、餘絀、流動比率、師生比…），讓承辦人看得到指標的依據。

只有 10 間學校有資料（決算書要人工蒐集），沒資料時回 `hasData: false`，
前端顯示「尚無決算書分析資料」而不是 0 分 —— 缺資料不等於沒風險。
"""

import json

import auth
import risk
from common import error, iso, respond

# 指標的顯示順序與中文名稱。key 與 CSV 欄位前綴一對一。
INDICATORS = [
    {"key": "per_student_personnel", "label": "每生人事費", "source": "每生人事費"},
    {"key": "personnel_yoy", "label": "人事費年增率", "source": "人事費年增率"},
    {"key": "budget_deviation", "label": "預決算偏離率", "source": "預決算偏離率"},
    {"key": "fund_reallocation", "label": "經費流用比例", "source": "經費流用比例"},
    {"key": "student_teacher_ratio", "label": "師生比", "source": "師生比"},
    {"key": "staff_turnover", "label": "教職員流動率", "source": "教職員流動率"},
    {"key": "overtime_load", "label": "加班費負荷", "source": "加班費負荷"},
    {"key": "misconduct_incident", "label": "不當管教事件", "source": "不當管教事件"},
]

# 等級分數（CSV 的「_等級分數」欄）-> 顯示文字。0 是「這間學校缺算這項指標的欄位」。
LEVEL_LABELS = {
    0: "無資料",
    1: "正常",
    2: "注意",
    3: "警示",
}

DISCLAIMER = (
    "財報指標由決算書公開資料自動計算，等級只代表「與自身歷年及同業相比的偏離程度」，"
    "偏離不等於違規。實際判定仍須人工檢視決算書與相關憑證。"
)


def _loads(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _indicator_rows(stored):
    """把 DB 存的 indicators 套回定案的指標清單上（缺的補無資料）。"""
    by_key = {}
    for item in stored or []:
        if isinstance(item, dict) and item.get("key"):
            by_key[item["key"]] = item

    rows = []
    for meta in INDICATORS:
        item = by_key.get(meta["key"]) or {}
        score = item.get("levelScore")
        try:
            score = int(score)
        except (TypeError, ValueError):
            score = 0
        rows.append(
            {
                "key": meta["key"],
                "label": meta["label"],
                # GREEN / YELLOW / RED / N/A（原樣保留，前端不需要自己推）
                "level": item.get("level") or "N/A",
                "levelScore": score,
                "levelLabel": LEVEL_LABELS.get(score, "無資料"),
                # z 分數：歷年 = 跟自己過去比，同業 = 跟其他園比
                "yearZ": item.get("yearZ"),
                "peerZ": item.get("peerZ"),
            }
        )
    return rows


def get_finance(cur, kg_id, identity):
    """GET /api/secure/kindergartens/{id}/finance"""
    county = auth.scoped_county(identity)
    if county is None and not identity["isAdmin"]:
        return error(
            403, "FORBIDDEN", "這個帳號沒有設定縣市（custom:county），請聯絡管理者"
        )

    cur.execute(
        "SELECT id, school_name, county, district FROM kindergarten WHERE id = %s",
        (kg_id,),
    )
    school = cur.fetchone()
    if not school or (county and school["county"] != county):
        return error(404, "KINDERGARTEN_NOT_FOUND", "查無此幼兒園")

    cur.execute(
        "SELECT * FROM finance_report_current WHERE kindergarten_id = %s", (kg_id,)
    )
    row = cur.fetchone()

    body = {
        "kindergartenId": kg_id,
        "schoolName": school["school_name"],
        "hasData": False,
        "disclaimer": DISCLAIMER,
        # 前端顯示「法遵風險指數 -> 風險分數」的換算說明，數值由後端定案
        "scoreFormula": f"法遵風險指數 × {risk.FINANCE_MULTIPLIER:g}（上限 100）",
        "indicators": [],
    }
    if not row:
        return respond(200, body)

    index = None if row["compliance_index"] is None else float(row["compliance_index"])
    indicators = _indicator_rows(_loads(row["indicators"]))
    body.update(
        {
            "hasData": True,
            "financeId": row["finance_id"],
            "alias": row["school_alias"],
            "fiscalYear": row["fiscal_year"],
            "complianceIndex": index,
            "overallLevel": row["overall_level"],
            "earlyWarning": row["early_warning"],
            # 進雷達圖 finance 軸的分數（與 risk.py 同一套公式）
            "riskScore": (
                None if index is None
                else min(100.0, round(index * risk.FINANCE_MULTIPLIER, 2))
            ),
            "riskWeight": next(
                d["weight"] for d in risk.DIMENSIONS if d["key"] == "finance"
            ),
            "indicators": indicators,
            # 只把踩到黃燈以上的挑出來，前端不用再過濾一次
            "flagged": [i for i in indicators if i["levelScore"] >= 2],
            "metrics": _loads(row["metrics"]) or {},
            "sourceFile": row["source_file"],
            "updatedAt": iso(row["updated_at"]),
        }
    )
    return respond(200, body)
