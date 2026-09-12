"""風險指數 —— **目前是 placeholder**。

演算法還沒定案，所以這裡的規則是：
  * 維度的 key / label 已經定案（前端可以照著把雷達圖寫死），只有分數會換掉
  * 分數存在 risk_score_current.dimensions（JSON），改指標不用改 schema
  * 沒有資料時回 totalScore=null、各維度 score=null，並且 isPlaceholder=true
  * risk_level 的 80 / 60 閾值寫在這裡，前端只依 riskLevel 上色，
    之後調整閾值不用改前端

之後接上真的模型時，只要有東西去寫 risk_score_current 這張表，這支就不用動。
"""

import json

import auth
from common import error, iso, respond

# 雷達圖的軸。順序就是前端顯示順序。
DIMENSIONS = [
    {"key": "finance", "label": "財務異常", "weight": 0.30},
    {"key": "compliance", "label": "裁罰紀錄", "weight": 0.30},
    {"key": "opinion", "label": "輿情負面", "weight": 0.20},
    {"key": "parent_report", "label": "家長回報", "weight": 0.10},
    {"key": "data_quality", "label": "資料完整度", "weight": 0.10},
]

HIGH_THRESHOLD = 80
MEDIUM_THRESHOLD = 60


def level_of(score):
    if score is None:
        return None
    if score >= HIGH_THRESHOLD:
        return "high"
    if score >= MEDIUM_THRESHOLD:
        return "medium"
    return "normal"


def _empty_dimensions():
    return [dict(d, score=None) for d in DIMENSIONS]


def _merge_dimensions(stored):
    """把 DB 裡存的分數套回定案的維度定義上（缺的補 None、多的忽略）。"""
    if not stored:
        return _empty_dimensions()
    if isinstance(stored, str):
        try:
            stored = json.loads(stored)
        except ValueError:
            return _empty_dimensions()
    by_key = {}
    if isinstance(stored, list):
        for item in stored:
            if isinstance(item, dict) and item.get("key"):
                by_key[item["key"]] = item
    elif isinstance(stored, dict):
        by_key = {k: {"score": v} for k, v in stored.items()}

    merged = []
    for d in DIMENSIONS:
        score = (by_key.get(d["key"]) or {}).get("score")
        merged.append(dict(d, score=None if score is None else float(score)))
    return merged


def get_risk(cur, kg_id, identity):
    """GET /api/secure/kindergartens/{id}/risk"""
    county = auth.scoped_county(identity)
    if county is None and not identity["isAdmin"]:
        return error(
            403, "FORBIDDEN", "這個帳號沒有設定縣市（custom:county），請聯絡管理者"
        )

    cur.execute("SELECT id, county FROM kindergarten WHERE id = %s", (kg_id,))
    school = cur.fetchone()
    if not school or (county and school["county"] != county):
        return error(404, "KINDERGARTEN_NOT_FOUND", "查無此幼兒園")

    cur.execute(
        "SELECT total_score, risk_level, dimensions, model_version, is_placeholder, "
        "computed_at FROM risk_score_current WHERE kindergarten_id = %s",
        (kg_id,),
    )
    row = cur.fetchone()

    if not row:
        return respond(
            200,
            {
                "kindergartenId": kg_id,
                "totalScore": None,
                "riskLevel": None,
                "isPlaceholder": True,
                "modelVersion": None,
                "computedAt": None,
                "dimensions": _empty_dimensions(),
            },
        )

    total = None if row["total_score"] is None else float(row["total_score"])
    return respond(
        200,
        {
            "kindergartenId": kg_id,
            "totalScore": total,
            # DB 的 risk_level 只是快取，實際以閾值重算，保證與前端顏色規則一致
            "riskLevel": row["risk_level"] or level_of(total),
            "isPlaceholder": bool(row["is_placeholder"]),
            "modelVersion": row["model_version"],
            "computedAt": iso(row["computed_at"]),
            "dimensions": _merge_dimensions(row["dimensions"]),
        },
    )
