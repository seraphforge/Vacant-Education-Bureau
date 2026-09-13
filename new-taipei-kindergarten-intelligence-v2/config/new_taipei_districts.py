from typing import Set, Dict

NEW_TAIPEI_DISTRICTS: list[str] = [
    "板橋區", "三重區", "中和區", "永和區", "新莊區",
    "新店區", "樹林區", "鶯歌區", "三峽區", "淡水區",
    "汐止區", "瑞芳區", "土城區", "蘆洲區", "五股區",
    "泰山區", "林口區", "深坑區", "石碇區", "坪林區",
    "三芝區", "石門區", "八里區", "平溪區", "雙溪區",
    "貢寮區", "金山區", "萬里區", "烏來區",
]

NEW_TAIPEI_DISTRICTS_SET: Set[str] = set(NEW_TAIPEI_DISTRICTS)

# Map district name variants to canonical name
DISTRICT_ALIASES: Dict[str, str] = {
    "板橋": "板橋區",
    "三重": "三重區",
    "中和": "中和區",
    "永和": "永和區",
    "新莊": "新莊區",
    "新店": "新店區",
    "樹林": "樹林區",
    "鶯歌": "鶯歌區",
    "三峽": "三峽區",
    "淡水": "淡水區",
    "汐止": "汐止區",
    "瑞芳": "瑞芳區",
    "土城": "土城區",
    "蘆洲": "蘆洲區",
    "五股": "五股區",
    "泰山": "泰山區",
    "林口": "林口區",
    "深坑": "深坑區",
    "石碇": "石碇區",
    "坪林": "坪林區",
    "三芝": "三芝區",
    "石門": "石門區",
    "八里": "八里區",
    "平溪": "平溪區",
    "雙溪": "雙溪區",
    "貢寮": "貢寮區",
    "金山": "金山區",
    "萬里": "萬里區",
    "烏來": "烏來區",
}

def is_valid_district(district: str) -> bool:
    return district in NEW_TAIPEI_DISTRICTS_SET

def normalize_district(district: str) -> str | None:
    if district in NEW_TAIPEI_DISTRICTS_SET:
        return district
    return DISTRICT_ALIASES.get(district)
