"""Batch-extract kindergarten financial fields from PDF reports with a vision LLM."""

from __future__ import annotations

import argparse
import base64
import csv
from io import BytesIO
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pdf2image import convert_from_path
from PIL import Image

FIELDS = [
    "幼兒園名稱",
    "總收入",
    "總支出",
    "人事費執行率",
    "本期稅後餘絀",
    "關係人交易金額",
    "人事費",
    "業務費",
    "修繕及採購費",
    "業務發展費",
    "其他支出",
    "預算總額",
    "決算總額",
]
CSV_COLUMNS = [
    "檔案名稱",
    "來源相對路徑",
    "幼兒園代碼",
    "幼兒園名稱（檔名）",
    "報告學年度",
    *FIELDS,
    "處理狀態",
    "錯誤訊息",
]
PAGE_SECTIONS = {
    3: "會計師查核報告",
    5: "資產負債表",
    6: "收支餘絀表（本年度）",
    7: "收支餘絀表（前一年度）",
    8: "淨值變動表",
    9: "現金流量表",
    10: "財務報表附註、一般概況",
    11: "重大會計政策之彙總說明",
    14: "重要會計項目說明",
    22: "賸餘款執行概況說明、關係人交易",
    23: "關係人交易、質抵押資產、重大承諾事項及或有事項、重大之期後事項、法源依據",
    24: "附表一：收支餘絀表-功能別",
    25: "附表二：經費流用及勻支檢查表",
    26: "附表三：各學年收支預決算比較表",
    27: "附表三：各學年收支預決算比較表",
    28: "附表四：財產清冊",
    29: "附表四：財產清冊",
    30: "附表四：財產清冊",
    31: "附表四：財產清冊",
    32: "附表四：財產清冊",
    33: "附表五：會計師查核附表",
    34: "附表五：會計師查核附表",
    35: "附表五：會計師查核附表",
    36: "附表五：會計師查核附表",
    37: "附表五：會計師查核附表",
    38: "附表五：會計師查核附表",
    39: "附表六：績效考評",
    40: "附表六：績效考評",
    41: "附表七：業務發展準備金提列申請表、提列前後經費分析彙整表",
    42: "附表七：業務發展準備金提列申請表、提列前後經費分析彙整表",
}
PROMPT = """你是台灣幼兒園財報資料抽取專家。請閱讀提供的財報頁面圖片，抽取以下欄位：
幼兒園名稱、總收入、總支出、人事費執行率、本期稅後餘絀、關係人交易金額、
人事費、業務費、修繕及採購費、業務發展費、其他支出、預算總額、決算總額。

請只輸出一個 JSON 物件，不要 Markdown code fence、解釋或其他文字。
JSON 的 key 必須完全使用以下名稱：
幼兒園名稱、總收入、總支出、人事費執行率、本期稅後餘絀、關係人交易金額、
人事費、業務費、修繕及採購費、業務發展費、其他支出、預算總額、決算總額。
找不到或無法判讀的值請填 null。金額保留數字與原始幣別資訊；百分比請保留百分比數值。
如果提供多頁，請綜合所有頁面，只輸出一個物件。
"""
BALANCE_SHEET_PROMPT = """你正在處理幼兒園財報第 5 頁的固定資產負債表。
請逐列讀取表格，輸出 JSON 陣列，不要輸出 Markdown 或說明文字。
每列格式必須是：
{"科目": "表格中的完整科目名稱", "金額": "該科目金額", "欄位": "資產或負債及淨值", "年度": "表格標示的年度或期別"}
保留括號負數、逗號與原始幣別；無法辨識的金額填 null。不要自行計算或四捨五入。
"""
INCOME_STATEMENT_PROMPT = """你正在處理幼兒園財報第 6 頁的收支餘絀表，該頁是報告檔名所代表學年度的本年度資料。
請逐列讀取表格，輸出 JSON 陣列，不要輸出 Markdown 或說明文字。
每列格式必須是：
{"科目": "表格中的完整科目名稱", "金額": "該科目金額", "年度": "報告學年度"}
保留括號負數、逗號與原始幣別；無法辨識的金額填 null。不要自行計算或四捨五入。
"""
CASH_FLOW_PROMPT = """你正在處理幼兒園財報第 9 頁的現金流量表，含本年度與前一年度兩欄。
請逐列讀取表格，輸出 JSON 陣列，不要輸出 Markdown 或說明文字。
每列格式必須是：
{"項目": "表格中的完整項目名稱", "本年度金額": "", "前年度金額": ""}
本年度是報告檔名學年度（左欄，例如 113.8.1~114.7.31），前年度是右欄（例如 112.8.1~113.7.31）。
保留括號負數與逗號；表格中的破折號「-」請填 null；無法辨識填 null。忠實抄錄，不要自行計算。
"""
APPENDIX2_PROMPT = """你正在處理幼兒園財報「附表二：經費流用及勻支檢查表」。
請逐列讀取表格，輸出 JSON 陣列，不要輸出 Markdown 或說明文字。
每列格式必須是：
{"項目": "表格中的完整項目名稱", "預算數": "", "決算數": "", "差異數": "", "差異率": "", "預決算檢查結果": ""}
預算數為 A 欄、決算數為 B 欄、差異數為 B-A 欄、差異率為差異%欄；預決算檢查結果為最後一欄的文字（例如「未超支」）。
保留括號負數與逗號；差異率只保留數字；破折號「-」與空白填 null。忠實抄錄，不要自行計算。
"""
APPENDIX4_PROMPT = """你正在處理幼兒園財報「附表四：財產清冊」的其中一頁。
請逐列讀取表格，輸出 JSON 陣列，不要輸出 Markdown 或說明文字。
每列格式必須是：
{"分類": "", "財產編號": "", "登錄號分號": "", "財產名稱": "", "原始價值": "", "數量": "", "帳面價值": "", "使用年限": "", "購置日期": ""}
分類指表格上方的類別標題（例如「代管財產」「自置財產」），同一類別下的每列都填相同分類；若該頁沒有新標題，分類填 null。
財產名稱若跨兩行請合併為完整名稱。保留金額的逗號；無法辨識的值填 null。忠實抄錄，不要自行計算或補齊。
"""
NOTES_PROMPT = """你正在處理幼兒園財報「財務報表附註」中的段落，圖片可能包含多頁。
請只抽取以下三個主題段落，其餘段落（如賸餘款執行概況、重大之期後事項、法源依據）一律忽略：
1. 關係人交易
2. 質抵押資產
3. 重大承諾事項及或有事項

請輸出 JSON 陣列，不要輸出 Markdown 或說明文字。每個主題一列，格式必須是：
{"章節": "關係人交易", "內容": "該段落的完整文字"}
規則：
- 章節只能是「關係人交易」「質抵押資產」「重大承諾事項及或有事項」三者之一，忠實使用報告中的標題文字。
- 內容請完整抄錄該段落文字，包含子項目編號與敘述；若段落內有小表格，請以「欄位：值」的方式併入內容文字，保留金額的逗號與括號。
- 若某主題在報告中標示為「無」，內容就填「無」。
- 找不到的主題不要輸出該列。不要自行摘要或改寫，忠實抄錄。
"""
OPERATING_PROMPT = """你正在處理幼兒園財報附註的「一般概況」段落（通常在第 10 頁）。
請找出以下營運數字，輸出單一 JSON 物件，不要輸出 Markdown 或說明文字：
{"核定招收人數": null, "實際招收人數": null, "員工人數": null, "教保人員數": null}
對照規則：
- 「核定及實際招收總人數：分別為 X 名及 Y 名」→ 核定招收人數=X、實際招收人數=Y。
- 「員工人數均為 N 人（含教保人員 M 人）」→ 員工人數=N、教保人員數=M。
  若員工人數列出兩個時點（例如截至 114 年及 113 年），請取本報告學年度（較新、較晚日期）的數字。
- 只輸出純數字（去除「名」「人」等單位與逗號）。找不到的欄位填 null。不要自行推算。
"""
APPENDIX3_PROMPT = """你正在處理幼兒園財報的「附表三：各學年收支預決算比較表」。
這張表通常橫跨兩頁圖片，請把兩頁視為同一張表，依表格由上到下逐列讀取，合併輸出。
表格每列的項目名稱有階層（例如「收入」「教保費收入淨額」「教保費收入」為不同層級），
每個項目分別有本年度與前一年度的四個數值：預算數、決算數、決算數與預算數之差異、執行率%。

請輸出 JSON 陣列，不要輸出 Markdown 或說明文字。每列格式必須是：
{"項目": "表格中的完整項目名稱", "本年度預算數": "", "本年度決算數": "", "本年度差異": "", "本年度執行率": "", "前年度預算數": "", "前年度決算數": "", "前年度差異": "", "前年度執行率": ""}
保留括號負數與逗號；表格中的破折號「-」請填 null；無法辨識的值也填 null。
執行率欄位只保留數字（例如 111）。不要自行計算或四捨五入，忠實抄錄表格內容。
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批次抽取幼兒園財報 PDF 欄位")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "raw",
        help="PDF 原始資料夾，預設為 ../raw",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "processed" / "reports" / "report.csv",
        help="主 CSV 輸出路徑，預設為 ../processed/reports/report.csv",
    )
    parser.add_argument(
        "--provider",
        choices=("gemini", "openai"),
        default=os.getenv("LLM_PROVIDER", "gemini"),
        help="使用的模型供應商，預設讀取 LLM_PROVIDER 或 gemini",
    )
    parser.add_argument(
        "--poppler-path",
        type=Path,
        default=os.getenv("POPPLER_PATH") or None,
        help="Windows 若未將 Poppler 加入 PATH，可指定 bin 資料夾",
    )
    parser.add_argument("--dpi", type=int, default=150, help="PDF 轉圖解析度，預設 150")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="每份 PDF 最多送出的頁數；0 表示全部頁面",
    )
    parser.add_argument(
        "--page-batch-size",
        type=int,
        default=1,
        help="每次送給模型的頁數，預設 1；0 表示整份 PDF 一次送出",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=None,
        help="進度記錄檔；省略時使用輸出 CSV 同名的 .checkpoint.json",
    )
    parser.add_argument(
        "--table-output",
        type=Path,
        default=None,
        help="第 5 頁表格輸出目錄或基準 CSV；預設為 ../processed/balance_sheets/",
    )
    parser.add_argument(
        "--validation-output",
        type=Path,
        default=None,
        help="資產負債表驗算輸出目錄；預設為 ../processed/validation/",
    )
    parser.add_argument(
        "--index-only",
        action="store_true",
        help="只建立排除第 5 頁的財報頁碼索引，不呼叫模型",
    )
    parser.add_argument(
        "--ocr",
        action="store_true",
        help="使用本機 Tesseract OCR，不呼叫 Gemini 或 OpenAI",
    )
    parser.add_argument(
        "--appendix3-only",
        action="store_true",
        help="只抽取第 26、27 頁的附表三：各學年收支預決算比較表",
    )
    parser.add_argument(
        "--appendix3-pages",
        default="26,27",
        help="附表三所在頁碼，以逗號分隔，預設 26,27",
    )
    parser.add_argument(
        "--notes-only",
        action="store_true",
        help="只抽取財務報表附註段落（關係人交易、質抵押、重大承諾與或有事項）",
    )
    parser.add_argument(
        "--notes-pages",
        default="22,23",
        help="附註段落所在頁碼，以逗號分隔，預設 22,23",
    )
    parser.add_argument(
        "--cash-flow-only",
        action="store_true",
        help="只抽取現金流量表",
    )
    parser.add_argument(
        "--cash-flow-pages",
        default="9",
        help="現金流量表所在頁碼，預設 9",
    )
    parser.add_argument(
        "--appendix2-only",
        action="store_true",
        help="只抽取附表二：經費流用及勻支檢查表",
    )
    parser.add_argument(
        "--appendix2-pages",
        default="25",
        help="附表二所在頁碼，預設 25",
    )
    parser.add_argument(
        "--appendix4-only",
        action="store_true",
        help="只抽取附表四：財產清冊（多頁逐頁合併）",
    )
    parser.add_argument(
        "--appendix4-pages",
        default="28,32",
        help="附表四頁碼範圍，起訖以逗號分隔，預設 28,32",
    )
    parser.add_argument(
        "--balance-sheet-only",
        action="store_true",
        help="只抽取資產負債表（含加總驗證）",
    )
    parser.add_argument(
        "--balance-sheet-pages",
        default="5",
        help="資產負債表所在頁碼，預設 5",
    )
    parser.add_argument(
        "--income-only",
        action="store_true",
        help="只抽取本年度收支餘絀表（含驗證）",
    )
    parser.add_argument(
        "--income-pages",
        default="6",
        help="收支餘絀表所在頁碼，預設 6",
    )
    parser.add_argument(
        "--income-prev-only",
        action="store_true",
        help="抽取前一年度收支餘絀表（第 7 頁），學年度標為本檔減 1，用以補齊 109 年",
    )
    parser.add_argument(
        "--income-prev-pages",
        default="7",
        help="前一年度收支餘絀表所在頁碼，預設 7",
    )
    parser.add_argument(
        "--operating-only",
        action="store_true",
        help="抽取一般概況營運數字（招收人數、員工數、教保員數），彙整成 operating_long.csv",
    )
    parser.add_argument(
        "--operating-pages",
        default="10",
        help="一般概況所在頁碼，預設 10",
    )
    parser.add_argument("--kindergarten", help="只處理指定幼兒園名稱或代碼")
    parser.add_argument("--school-year", help="只處理指定學年度，例如 113")
    return parser.parse_args()


def clean_json_response(text: str) -> dict[str, Any]:
    """Extract and validate the first JSON object returned by the model."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    if not fenced:
        object_match = re.search(r"\{.*\}", text, re.DOTALL)
        if object_match:
            candidate = object_match.group(0)
    parsed = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise ValueError("模型回傳的 JSON 不是物件")
    return {field: parsed.get(field) for field in FIELDS}


def clean_table_response(text: str) -> list[dict[str, Any]]:
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    else:
        array_match = re.search(r"\[.*\]", candidate, re.DOTALL)
        if array_match:
            candidate = array_match.group(0)
    parsed = json.loads(candidate)
    if not isinstance(parsed, list):
        raise ValueError("資產負債表回傳的 JSON 不是陣列")
    return [row for row in parsed if isinstance(row, dict)]


def report_school_year(pdf_path: Path) -> int:
    match = re.search(r"(?P<year>\d{3})學年度", pdf_path.parent.name)
    match = match or re.search(r"(?P<year>\d{3})學年度", pdf_path.stem)
    if not match:
        raise ValueError(f"檔名沒有學年度：{pdf_path.name}")
    return int(match.group("year"))


def report_source(pdf_path: Path, input_dir: Path) -> dict[str, str]:
    relative_path = pdf_path.relative_to(input_dir)
    match = re.match(
        r"(?P<code>N\d+)(?P<name>.+?)_(?P<year>\d{3})學年度",
        pdf_path.stem,
    )
    if match:
        code = match.group("code")
        kindergarten_name = match.group("name")
    else:
        code = ""
        kindergarten_name = pdf_path.stem
    return {
        "檔案名稱": pdf_path.name,
        "來源相對路徑": str(relative_path),
        "幼兒園代碼": code,
        "幼兒園名稱（檔名）": kindergarten_name,
        "報告學年度": f"{report_school_year(pdf_path)}學年度",
    }


def report_prefix(pdf_path: Path) -> str:
    """從檔名取出「代碼+園名」前綴，例如 N01安溪；取不到時退回檔名去除副檔名。"""
    match = re.match(r"(?P<prefix>N\d+.+?)_\d{3}學年度", pdf_path.stem)
    return match.group("prefix") if match else pdf_path.stem


def pdf_output_path(base: Path, pdf_path: Path, suffix: str) -> Path:
    output_dir = base.parent if base.suffix.lower() == ".csv" else base
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"{pdf_path.stem}_{suffix}.csv"


def table_output_path(base: Path, pdf_path: Path, table_name: str) -> Path:
    output_dir = school_output_dir(base, pdf_path)
    return output_dir / f"{table_name}.csv"


def school_output_dir(base: Path, pdf_path: Path) -> Path:
    source = re.match(r"N\d+(?P<name>.+?)_\d{3}學年度", pdf_path.stem)
    school_name = source.group("name") if source else pdf_path.stem
    return base / f"{report_school_year(pdf_path)}學年度" / school_name


def page_section(page_number: int, school_year: int) -> str:
    if page_number == 6:
        return f"收支餘絀表（{school_year}學年度，本年度）"
    if page_number == 7:
        return f"收支餘絀表（{school_year - 1}學年度，前一年度）"
    return PAGE_SECTIONS.get(page_number, "未標註章節")


def write_page_index_csv(output: Path, pdf_path: Path, page_count: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    columns = ["檔案名稱", "來源頁碼", "章節", "年度類別", "是否資產負債表"]
    with output.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns)
        writer.writeheader()
        school_year = report_school_year(pdf_path)
        for page_number in range(1, page_count + 1):
            if page_number == 5:
                continue
            year_category = "本年度" if page_number == 6 else "前一年度" if page_number == 7 else ""
            writer.writerow(
                {
                    "檔案名稱": pdf_path.name,
                    "來源頁碼": page_number,
                    "章節": page_section(page_number, school_year),
                    "年度類別": year_category,
                    "是否資產負債表": "否",
                }
            )


def normalize_balance_years(
    pdf_path: Path,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    current_year = report_school_year(pdf_path)
    source_years: list[str] = []
    for row in rows:
        source_year = str(row.get("年度") or "").strip()
        if source_year and source_year not in source_years:
            source_years.append(source_year)

    normalized: list[dict[str, Any]] = []
    for row in rows:
        copied = dict(row)
        source_year = str(row.get("年度") or "").strip()
        position = source_years.index(source_year) if source_year in source_years else 0
        position = min(position, 1)
        copied["年度"] = f"{current_year - position}學年度"
        copied["年度類別"] = "本年度" if position == 0 else "前一年度"
        copied["原始年度標示"] = source_year
        normalized.append(copied)
    return normalized


def image_to_data_url(image: Image.Image, image_format: str = "JPEG") -> str:
    """Convert a PIL image to a data URL accepted by the OpenAI vision API."""
    from io import BytesIO

    buffer = BytesIO()
    image.convert("RGB").save(buffer, format=image_format, quality=90)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def request_openai(images: list[Image.Image], section_prompt: str = "") -> dict[str, Any]:
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    content: list[dict[str, Any]] = [{"type": "text", "text": PROMPT + section_prompt}]
    content.extend(
        {
            "type": "image_url",
            "image_url": {"url": image_to_data_url(image)},
        }
        for image in images
    )
    response = client.chat.completions.create(
        model="gpt-4o",
        temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": content}],
    )
    text = response.choices[0].message.content or ""
    return clean_json_response(text)


def request_gemini(images: list[Image.Image], section_prompt: str = "") -> dict[str, Any]:
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=os.environ["GEMINI_API_KEY"],
        http_options=types.HttpOptions(timeout=120000),
    )
    contents: list[Any] = [PROMPT + section_prompt]
    for image in images:
        buffer = BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=90)
        contents.append(
            types.Part.from_bytes(data=buffer.getvalue(), mime_type="image/jpeg")
        )
    response = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
        contents=contents,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
        ),
    )
    return clean_json_response(response.text or "")


def request_balance_sheet(provider: str, image: Image.Image) -> list[dict[str, Any]]:
    if provider == "openai":
        from openai import OpenAI

        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        response = client.chat.completions.create(
            model="gpt-4o",
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": BALANCE_SHEET_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": image_to_data_url(image)},
                        },
                    ],
                }
            ],
        )
        return clean_table_response(response.choices[0].message.content or "")

    from google import genai
    from google.genai import types

    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=95)
    client = genai.Client(
        api_key=os.environ["GEMINI_API_KEY"],
        http_options=types.HttpOptions(timeout=120000),
    )
    response = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
        contents=[
            BALANCE_SHEET_PROMPT,
            types.Part.from_bytes(data=buffer.getvalue(), mime_type="image/jpeg"),
        ],
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
        ),
    )
    return clean_table_response(response.text or "")


def request_income_statement(provider: str, image: Image.Image) -> list[dict[str, Any]]:
    if provider == "openai":
        from openai import OpenAI

        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        response = client.chat.completions.create(
            model="gpt-4o",
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": INCOME_STATEMENT_PROMPT},
                        {"type": "image_url", "image_url": {"url": image_to_data_url(image)}},
                    ],
                }
            ],
        )
        return clean_table_response(response.choices[0].message.content or "")

    from google import genai
    from google.genai import types

    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=95)
    client = genai.Client(
        api_key=os.environ["GEMINI_API_KEY"],
        http_options=types.HttpOptions(timeout=120000),
    )
    response = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
        contents=[
            INCOME_STATEMENT_PROMPT,
            types.Part.from_bytes(data=buffer.getvalue(), mime_type="image/jpeg"),
        ],
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
        ),
    )
    return clean_table_response(response.text or "")


def request_appendix3(provider: str, images: list[Image.Image]) -> list[dict[str, Any]]:
    """抽取附表三：各學年收支預決算比較表（通常橫跨兩頁）。"""
    if provider == "openai":
        from openai import OpenAI

        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        content: list[dict[str, Any]] = [{"type": "text", "text": APPENDIX3_PROMPT}]
        content.extend(
            {"type": "image_url", "image_url": {"url": image_to_data_url(image)}}
            for image in images
        )
        response = client.chat.completions.create(
            model="gpt-4o",
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": content}],
        )
        return clean_table_response(response.choices[0].message.content or "")

    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=os.environ["GEMINI_API_KEY"],
        http_options=types.HttpOptions(timeout=120000),
    )
    contents: list[Any] = [APPENDIX3_PROMPT]
    for image in images:
        buffer = BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=95)
        contents.append(
            types.Part.from_bytes(data=buffer.getvalue(), mime_type="image/jpeg")
        )
    response = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
        contents=contents,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
        ),
    )
    return clean_table_response(response.text or "")


def write_appendix3_csv(
    output: Path,
    pdf_path: Path,
    rows: list[dict[str, Any]],
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    current_year = report_school_year(pdf_path)
    columns = [
        "檔案名稱",
        "項目",
        f"{current_year}學年度預算數",
        f"{current_year}學年度決算數",
        f"{current_year}學年度決算與預算差異",
        f"{current_year}學年度執行率",
        f"{current_year - 1}學年度預算數",
        f"{current_year - 1}學年度決算數",
        f"{current_year - 1}學年度決算與預算差異",
        f"{current_year - 1}學年度執行率",
    ]
    with output.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "檔案名稱": pdf_path.name,
                    "項目": row.get("項目"),
                    f"{current_year}學年度預算數": row.get("本年度預算數"),
                    f"{current_year}學年度決算數": row.get("本年度決算數"),
                    f"{current_year}學年度決算與預算差異": row.get("本年度差異"),
                    f"{current_year}學年度執行率": row.get("本年度執行率"),
                    f"{current_year - 1}學年度預算數": row.get("前年度預算數"),
                    f"{current_year - 1}學年度決算數": row.get("前年度決算數"),
                    f"{current_year - 1}學年度決算與預算差異": row.get("前年度差異"),
                    f"{current_year - 1}學年度執行率": row.get("前年度執行率"),
                }
            )


def request_notes(provider: str, images: list[Image.Image]) -> list[dict[str, Any]]:
    """抽取財務報表附註段落：關係人交易、質抵押資產、重大承諾事項及或有事項。"""
    if provider == "openai":
        from openai import OpenAI

        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        content: list[dict[str, Any]] = [{"type": "text", "text": NOTES_PROMPT}]
        content.extend(
            {"type": "image_url", "image_url": {"url": image_to_data_url(image)}}
            for image in images
        )
        response = client.chat.completions.create(
            model="gpt-4o",
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": content}],
        )
        return clean_table_response(response.choices[0].message.content or "")

    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=os.environ["GEMINI_API_KEY"],
        http_options=types.HttpOptions(timeout=120000),
    )
    contents: list[Any] = [NOTES_PROMPT]
    for image in images:
        buffer = BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=95)
        contents.append(
            types.Part.from_bytes(data=buffer.getvalue(), mime_type="image/jpeg")
        )
    response = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
        contents=contents,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
        ),
    )
    return clean_table_response(response.text or "")


def _parse_json_object(text: str) -> dict[str, Any]:
    """解析模型回傳的單一 JSON 物件，保留原始鍵（不套用固定 FIELDS）。"""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    if not fenced:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            candidate = match.group(0)
    parsed = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise ValueError("模型回傳的 JSON 不是物件")
    return parsed


def request_operating(provider: str, images: list[Image.Image]) -> dict[str, Any]:
    """抽取一般概況的營運數字，回傳單一 JSON 物件（dict），保留原始鍵。"""
    if provider == "openai":
        from openai import OpenAI

        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        content: list[dict[str, Any]] = [{"type": "text", "text": OPERATING_PROMPT}]
        content.extend(
            {"type": "image_url", "image_url": {"url": image_to_data_url(image)}}
            for image in images
        )
        response = client.chat.completions.create(
            model="gpt-4o",
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": content}],
        )
        return _parse_json_object(response.choices[0].message.content or "")

    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=os.environ["GEMINI_API_KEY"],
        http_options=types.HttpOptions(timeout=120000),
    )
    contents: list[Any] = [OPERATING_PROMPT]
    for image in images:
        buffer = BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=95)
        contents.append(
            types.Part.from_bytes(data=buffer.getvalue(), mime_type="image/jpeg")
        )
    response = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
        contents=contents,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
        ),
    )
    return _parse_json_object(response.text or "")


def request_table(
    provider: str, images: list[Image.Image], prompt: str
) -> list[dict[str, Any]]:
    """通用：送出圖片與 prompt，回傳逐列 JSON 陣列（用於現金流量表、附表二、附表四）。"""
    if provider == "openai":
        from openai import OpenAI

        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        content.extend(
            {"type": "image_url", "image_url": {"url": image_to_data_url(image)}}
            for image in images
        )
        response = client.chat.completions.create(
            model="gpt-4o",
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": content}],
        )
        return clean_table_response(response.choices[0].message.content or "")

    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=os.environ["GEMINI_API_KEY"],
        http_options=types.HttpOptions(timeout=120000),
    )
    contents: list[Any] = [prompt]
    for image in images:
        buffer = BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=95)
        contents.append(
            types.Part.from_bytes(data=buffer.getvalue(), mime_type="image/jpeg")
        )
    response = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
        contents=contents,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
        ),
    )
    return clean_table_response(response.text or "")


def write_cash_flow_csv(output: Path, pdf_path: Path, rows: list[dict[str, Any]]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    current_year = report_school_year(pdf_path)
    columns = ["檔案名稱", "項目", f"{current_year}學年度金額", f"{current_year - 1}學年度金額"]
    with output.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "檔案名稱": pdf_path.name,
                    "項目": row.get("項目"),
                    f"{current_year}學年度金額": row.get("本年度金額"),
                    f"{current_year - 1}學年度金額": row.get("前年度金額"),
                }
            )


def write_appendix2_csv(output: Path, pdf_path: Path, rows: list[dict[str, Any]]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    columns = ["檔案名稱", "報告學年度", "項目", "預算數", "決算數", "差異數", "差異率", "預決算檢查結果"]
    with output.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "檔案名稱": pdf_path.name,
                    "報告學年度": f"{report_school_year(pdf_path)}學年度",
                    "項目": row.get("項目"),
                    "預算數": row.get("預算數"),
                    "決算數": row.get("決算數"),
                    "差異數": row.get("差異數"),
                    "差異率": row.get("差異率"),
                    "預決算檢查結果": row.get("預決算檢查結果"),
                }
            )


def write_appendix4_csv(output: Path, pdf_path: Path, rows: list[dict[str, Any]]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "檔案名稱",
        "分類",
        "財產編號",
        "登錄號分號",
        "財產名稱",
        "原始價值",
        "數量",
        "帳面價值",
        "使用年限",
        "購置日期",
    ]
    with output.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "檔案名稱": pdf_path.name,
                    "分類": row.get("分類"),
                    "財產編號": row.get("財產編號"),
                    "登錄號分號": row.get("登錄號分號"),
                    "財產名稱": row.get("財產名稱"),
                    "原始價值": row.get("原始價值"),
                    "數量": row.get("數量"),
                    "帳面價值": row.get("帳面價值"),
                    "使用年限": row.get("使用年限"),
                    "購置日期": row.get("購置日期"),
                }
            )


def write_notes_csv(
    output: Path,
    pdf_path: Path,
    rows: list[dict[str, Any]],
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    wanted = ["關係人交易", "質抵押資產", "重大承諾事項及或有事項"]
    by_section = {str(row.get("章節") or "").strip(): row.get("內容") for row in rows}
    columns = ["檔案名稱", "報告學年度", "章節", "內容"]
    with output.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns)
        writer.writeheader()
        for section in wanted:
            writer.writerow(
                {
                    "檔案名稱": pdf_path.name,
                    "報告學年度": f"{report_school_year(pdf_path)}學年度",
                    "章節": section,
                    "內容": by_section.get(section),
                }
            )


def write_statement_csv(
    output: Path,
    pdf_path: Path,
    rows: list[dict[str, Any]],
    page: int = 6,
    school_year: int | None = None,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    year = school_year if school_year is not None else report_school_year(pdf_path)
    columns = ["檔案名稱", "頁碼", "報告學年度", "科目", "金額", "原始年度標示"]
    with output.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "檔案名稱": pdf_path.name,
                    "頁碼": page,
                    "報告學年度": f"{year}學年度",
                    "科目": row.get("科目"),
                    "金額": row.get("金額"),
                    "原始年度標示": row.get("年度"),
                }
            )


def write_statement_validation_csv(
    output: Path,
    pdf_path: Path,
    rows: list[dict[str, Any]],
) -> None:
    checks: list[dict[str, Any]] = []
    invalid_rows = [
        str(row.get("科目") or "")
        for row in rows
        if row.get("金額") not in (None, "") and money_to_int(row.get("金額")) is None
    ]
    checks.append(
        {
            "檔案名稱": pdf_path.name,
            "檢查項目": "所有抽取金額可解析",
            "左方": len(rows) - len(invalid_rows),
            "右方": len(rows),
            "結果": "通過" if not invalid_rows else "不一致",
            "說明": "；".join(invalid_rows),
        }
    )
    amount_values = {
        str(row.get("科目") or ""): money_to_int(row.get("金額"))
        for row in rows
    }
    total_keys = [key for key in amount_values if "餘絀" in key or "結餘" in key]
    if total_keys:
        checks.append(
            {
                "檔案名稱": pdf_path.name,
                "檢查項目": "收支餘絀表已抽取總餘絀／結餘欄位",
                "左方": len(total_keys),
                "右方": 1,
                "結果": "通過",
                "說明": "、".join(total_keys),
            }
        )
    income_key = next((key for key in amount_values if "收入合計" in key or key == "收入"), None)
    expense_key = next((key for key in amount_values if "支出合計" in key or key == "支出"), None)
    surplus_key = next(
        (key for key in amount_values if "本期餘絀" in key or "本期結餘" in key),
        None,
    )
    if income_key and expense_key and surplus_key:
        calculated = (amount_values[income_key] or 0) - (amount_values[expense_key] or 0)
        reported = amount_values[surplus_key]
        checks.append(
            {
                "檔案名稱": pdf_path.name,
                "檢查項目": "收入合計-支出合計=本期餘絀",
                "左方": calculated,
                "右方": reported,
                "結果": "通過" if calculated == reported else "不一致",
                "說明": f"{income_key}；{expense_key}；{surplus_key}",
            }
        )
    columns = ["檔案名稱", "檢查項目", "左方", "右方", "結果", "說明"]
    with output.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(checks)


def extract_with_tesseract(image: Image.Image) -> str:
    import pytesseract

    tesseract_path = os.getenv(
        "TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    )
    tessdata_dir = os.getenv(
        "TESSDATA_DIR",
        str(Path(__file__).resolve().parents[1] / "tessdata"),
    )
    pytesseract.pytesseract.tesseract_cmd = tesseract_path
    os.environ["TESSDATA_PREFIX"] = tessdata_dir
    config = "--psm 6"
    return pytesseract.image_to_string(image, lang="chi_tra+eng", config=config)


def write_ocr_csv(output: Path, pdf_path: Path, page_number: int, text: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=["檔案名稱", "來源頁碼", "行號", "OCR文字"],
        )
        writer.writeheader()
        for line_number, line in enumerate(text.splitlines(), start=1):
            if line.strip():
                writer.writerow(
                    {
                        "檔案名稱": pdf_path.name,
                        "來源頁碼": page_number,
                        "行號": line_number,
                        "OCR文字": line.strip(),
                    }
                )


def extract_ocr_pages_only(
    pdf_path: Path,
    dpi: int,
    poppler_path: Path | None,
    output_dir: Path,
) -> None:
    convert_options: dict[str, Any] = {"dpi": dpi, "fmt": "jpeg"}
    if poppler_path:
        convert_options["poppler_path"] = str(poppler_path)
    images = convert_from_path(str(pdf_path), **convert_options)
    if len(images) < 6:
        raise ValueError("PDF 少於 6 頁，無法 OCR 第 5、6 頁")
    for page_number in (5, 6):
        print(f"  OCR 第 {page_number} 頁")
        text = extract_with_tesseract(images[page_number - 1])
        write_ocr_csv(
            output_dir / f"{pdf_path.stem}_page{page_number}_ocr.csv",
            pdf_path,
            page_number,
            text,
        )


def extract_appendix3_only(
    pdf_path: Path,
    provider: str,
    dpi: int,
    poppler_path: Path | None,
    pages: list[int],
    output: Path,
) -> None:
    """只抽取附表三：各學年收支預決算比較表（預設第 26、27 頁）。"""
    convert_options: dict[str, Any] = {"dpi": dpi, "fmt": "jpeg"}
    if poppler_path:
        convert_options["poppler_path"] = str(poppler_path)
    images = convert_from_path(
        str(pdf_path),
        first_page=min(pages),
        last_page=max(pages),
        **convert_options,
    )
    if not images:
        raise ValueError(f"PDF 沒有第 {pages} 頁，無法抽取附表三")
    print(f"  抽取附表三（第 {'、'.join(str(p) for p in pages)} 頁）")
    rows = request_appendix3(provider, images)
    write_appendix3_csv(output, pdf_path, rows)


def extract_notes_only(
    pdf_path: Path,
    provider: str,
    dpi: int,
    poppler_path: Path | None,
    pages: list[int],
    output: Path,
) -> None:
    """只抽取財務報表附註段落（關係人交易、質抵押、重大承諾與或有事項，預設第 22、23 頁）。"""
    convert_options: dict[str, Any] = {"dpi": dpi, "fmt": "jpeg"}
    if poppler_path:
        convert_options["poppler_path"] = str(poppler_path)
    images = convert_from_path(
        str(pdf_path),
        first_page=min(pages),
        last_page=max(pages),
        **convert_options,
    )
    if not images:
        raise ValueError(f"PDF 沒有第 {pages} 頁，無法抽取附註段落")
    print(f"  抽取附註段落（第 {'、'.join(str(p) for p in pages)} 頁）")
    rows = request_notes(provider, images)
    write_notes_csv(output, pdf_path, rows)


def _convert_pages(
    pdf_path: Path, dpi: int, poppler_path: Path | None, pages: list[int]
) -> list[Image.Image]:
    convert_options: dict[str, Any] = {"dpi": dpi, "fmt": "jpeg"}
    if poppler_path:
        convert_options["poppler_path"] = str(poppler_path)
    return convert_from_path(
        str(pdf_path),
        first_page=min(pages),
        last_page=max(pages),
        **convert_options,
    )


def extract_cash_flow_only(
    pdf_path: Path,
    provider: str,
    dpi: int,
    poppler_path: Path | None,
    pages: list[int],
    output: Path,
) -> None:
    """只抽取現金流量表（預設第 9 頁）。"""
    images = _convert_pages(pdf_path, dpi, poppler_path, pages)
    if not images:
        raise ValueError(f"PDF 沒有第 {pages} 頁，無法抽取現金流量表")
    print(f"  抽取現金流量表（第 {'、'.join(str(p) for p in pages)} 頁）")
    rows = request_table(provider, images, CASH_FLOW_PROMPT)
    write_cash_flow_csv(output, pdf_path, rows)


def extract_appendix2_only(
    pdf_path: Path,
    provider: str,
    dpi: int,
    poppler_path: Path | None,
    pages: list[int],
    output: Path,
) -> None:
    """只抽取附表二：經費流用及勻支檢查表（預設第 25 頁）。"""
    images = _convert_pages(pdf_path, dpi, poppler_path, pages)
    if not images:
        raise ValueError(f"PDF 沒有第 {pages} 頁，無法抽取附表二")
    print(f"  抽取附表二（第 {'、'.join(str(p) for p in pages)} 頁）")
    rows = request_table(provider, images, APPENDIX2_PROMPT)
    write_appendix2_csv(output, pdf_path, rows)


def extract_appendix4_only(
    pdf_path: Path,
    provider: str,
    dpi: int,
    poppler_path: Path | None,
    pages: list[int],
    output: Path,
) -> None:
    """只抽取附表四：財產清冊（預設第 28~32 頁），逐頁抽取後合併，分類向下填充。"""
    all_rows: list[dict[str, Any]] = []
    last_category: Any = None
    for page in range(min(pages), max(pages) + 1):
        images = _convert_pages(pdf_path, dpi, poppler_path, [page])
        if not images:
            continue
        print(f"  抽取附表四第 {page} 頁")
        rows = request_table(provider, images, APPENDIX4_PROMPT)
        for row in rows:
            category = str(row.get("分類") or "").strip()
            if category:
                last_category = category
            else:
                row["分類"] = last_category
            all_rows.append(row)
    if not all_rows:
        raise ValueError(f"PDF 第 {pages} 頁沒有可抽取的財產清冊資料")
    write_appendix4_csv(output, pdf_path, all_rows)


def write_balance_sheet_csv(
    output: Path,
    pdf_path: Path,
    rows: list[dict[str, Any]],
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = normalize_balance_years(pdf_path, rows)
    columns = ["檔案名稱", "頁碼", "科目", "金額", "欄位", "年度", "年度類別", "原始年度標示"]
    expected_header = ",".join(columns)
    file_exists = False
    if output.exists() and output.stat().st_size > 0:
        with output.open(encoding="utf-8-sig") as existing_file:
            file_exists = existing_file.readline().strip() == expected_header
    mode = "a" if file_exists else "w"
    with output.open(mode, newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns)
        if not file_exists:
            writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "檔案名稱": pdf_path.name,
                    "頁碼": 5,
                    "科目": row.get("科目"),
                    "金額": row.get("金額"),
                    "欄位": row.get("欄位"),
                    "年度": row.get("年度"),
                    "年度類別": row.get("年度類別"),
                    "原始年度標示": row.get("原始年度標示"),
                }
            )


def money_to_int(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("$", "")
    if text in {"", "-", "--"}:
        return 0
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("() ")
    try:
        amount = int(float(text))
    except ValueError:
        return None
    return -amount if negative else amount


def write_balance_sheet_validation_csv(
    output: Path,
    pdf_path: Path,
    rows: list[dict[str, Any]],
) -> None:
    rows = normalize_balance_years(pdf_path, rows)
    grouped: dict[tuple[str, str], dict[str, int]] = {}
    for row in rows:
        amount = money_to_int(row.get("金額"))
        year = str(row.get("年度") or "")
        section = str(row.get("欄位") or "")
        subject = str(row.get("科目") or "")
        if amount is None:
            continue
        grouped.setdefault((year, section), {})[subject] = amount

    checks: list[dict[str, Any]] = []
    for (year, section), values in grouped.items():
        if section == "資產":
            checks.append(
                {
                    "檔案名稱": pdf_path.name,
                    "年度": year,
                    "檢查項目": "流動資產合計+非流動資產合計=資產總計",
                    "左方": (values.get("流動資產合計") or 0)
                    + (values.get("非流動資產合計") or 0),
                    "右方": values.get("資產總計"),
                }
            )
        if section == "負債及淨值":
            equity = values.get("餘絀總額")
            checks.extend(
                [
                    {
                        "檔案名稱": pdf_path.name,
                        "年度": year,
                        "檢查項目": "流動負債合計+非流動負債合計=負債總額",
                        "左方": (values.get("流動負債合計") or 0)
                        + (values.get("非流動負債合計") or 0),
                        "右方": values.get("負債總額"),
                    },
                    {
                        "檔案名稱": pdf_path.name,
                        "年度": year,
                        "檢查項目": "累積餘絀+本期餘絀=餘絀總額",
                        "左方": (values.get("累積餘絀") or 0)
                        + (values.get("本期餘絀") or 0),
                        "右方": equity,
                    },
                    {
                        "檔案名稱": pdf_path.name,
                        "年度": year,
                        "檢查項目": "負債總額+餘絀總額=負債及餘絀總計",
                        "左方": (values.get("負債總額") or 0) + (equity or 0),
                        "右方": values.get("負債及餘絀總計"),
                    },
                ]
            )

    columns = ["檔案名稱", "年度", "年度類別", "檢查項目", "左方", "右方", "結果"]
    expected_header = ",".join(columns)
    file_exists = False
    if output.exists() and output.stat().st_size > 0:
        with output.open(encoding="utf-8-sig") as existing_file:
            file_exists = existing_file.readline().strip() == expected_header
    mode = "a" if file_exists else "w"
    with output.open(mode, newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns)
        if not file_exists:
            writer.writeheader()
        for check in checks:
            check["結果"] = "通過" if check["左方"] == check["右方"] else "不一致"
            check["年度類別"] = "本年度" if check["年度"] == f"{report_school_year(pdf_path)}學年度" else "前一年度"
            writer.writerow(check)


def extract_balance_sheet_only(
    pdf_path: Path,
    provider: str,
    dpi: int,
    poppler_path: Path | None,
    pages: list[int],
    output: Path,
    validation_output: Path,
) -> None:
    """只抽取資產負債表（預設第 5 頁），並產生加總驗證。"""
    images = _convert_pages(pdf_path, dpi, poppler_path, pages)
    if not images:
        raise ValueError(f"PDF 沒有第 {pages} 頁，無法抽取資產負債表")
    print(f"  抽取資產負債表（第 {'、'.join(str(p) for p in pages)} 頁）")
    rows = request_balance_sheet(provider, images[0])
    write_balance_sheet_csv(output, pdf_path, rows)
    write_balance_sheet_validation_csv(validation_output, pdf_path, rows)


def extract_income_only(
    pdf_path: Path,
    provider: str,
    dpi: int,
    poppler_path: Path | None,
    pages: list[int],
    output: Path,
    validation_output: Path,
) -> None:
    """只抽取本年度收支餘絀表（預設第 6 頁），並產生驗證。"""
    images = _convert_pages(pdf_path, dpi, poppler_path, pages)
    if not images:
        raise ValueError(f"PDF 沒有第 {pages} 頁，無法抽取收支餘絀表")
    print(f"  抽取收支餘絀表（第 {'、'.join(str(p) for p in pages)} 頁）")
    rows = request_income_statement(provider, images[0])
    write_statement_csv(output, pdf_path, rows)
    write_statement_validation_csv(validation_output, pdf_path, rows)


def extract_income_prev_only(
    pdf_path: Path,
    provider: str,
    dpi: int,
    poppler_path: Path | None,
    pages: list[int],
    output: Path,
    validation_output: Path,
) -> None:
    """抽取前一年度收支餘絀表（預設第 7 頁），學年度標為本檔學年度減 1。

    用途：110 學年度 PDF 的第 7 頁即為 109 學年度收支餘絀，用以補齊 109 年資料。
    """
    images = _convert_pages(pdf_path, dpi, poppler_path, pages)
    if not images:
        raise ValueError(f"PDF 沒有第 {pages} 頁，無法抽取前一年度收支餘絀表")
    prev_year = report_school_year(pdf_path) - 1
    page = min(pages)
    print(f"  抽取前一年度收支餘絀表（第 {page} 頁，標記為 {prev_year} 學年度）")
    rows = request_income_statement(provider, images[0])
    write_statement_csv(output, pdf_path, rows, page=page, school_year=prev_year)
    write_statement_validation_csv(validation_output, pdf_path, rows)


def _num(value) -> float | None:
    if value is None:
        return None
    s = str(value).strip().replace(",", "").replace("名", "").replace("人", "")
    if s in ("", "-", "None", "null"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def extract_operating_only(
    pdf_path: Path,
    provider: str,
    dpi: int,
    poppler_path: Path | None,
    pages: list[int],
    long_output: Path,
) -> None:
    """抽取一般概況營運數字（第 10 頁），append 進共用長表 operating_long.csv。

    長表欄位：園代碼, 園名, 學年度, 指標, 數值, 資料類別
    指標包含：核定招收人數、實際招收人數、員工人數、教保人員數、學生人數（=實際招收人數）。
    """
    images = _convert_pages(pdf_path, dpi, poppler_path, pages)
    if not images:
        raise ValueError(f"PDF 沒有第 {pages} 頁，無法抽取一般概況")
    page = min(pages)
    print(f"  抽取一般概況營運數字（第 {page} 頁）")
    data = request_operating(provider, images)

    code = report_prefix(pdf_path)[:3] if report_prefix(pdf_path)[:1] == "N" else None
    m = re.match(r"(N\d+)(.+?)_", pdf_path.name)
    code = m.group(1) if m else None
    name = m.group(2) if m else pdf_path.stem
    year = report_school_year(pdf_path)

    approved = _num(data.get("核定招收人數"))
    actual = _num(data.get("實際招收人數"))
    staff = _num(data.get("員工人數"))
    teachers = _num(data.get("教保人員數"))

    metrics = {
        "核定招收人數": approved,
        "實際招收人數": actual,
        "學生人數": actual,  # 以實際招收人數作為學生人數，供每生/師生比計算
        "員工人數": staff,
        "教保人員數": teachers,
    }

    long_output.parent.mkdir(parents=True, exist_ok=True)
    columns = ["園代碼", "園名", "學年度", "指標", "數值", "資料類別"]
    existing: list[dict[str, Any]] = []
    if long_output.exists():
        with long_output.open("r", encoding="utf-8-sig", newline="") as f:
            existing = list(csv.DictReader(f))
    # 移除同園同年舊資料（可重跑覆蓋）
    existing = [
        r
        for r in existing
        if not (r.get("園代碼") == code and str(r.get("學年度")) == str(year))
    ]
    for metric, value in metrics.items():
        existing.append(
            {
                "園代碼": code,
                "園名": name,
                "學年度": year,
                "指標": metric,
                "數值": "" if value is None else value,
                "資料類別": "營運",
            }
        )
    existing.sort(key=lambda r: (r.get("園代碼") or "", str(r.get("學年度")), r.get("指標") or ""))
    with long_output.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(existing)


def extract_pdf(
    pdf_path: Path,
    provider: str,
    dpi: int,
    poppler_path: Path | None,
    max_pages: int,
    page_batch_size: int,
    checkpoint_file: Path,
    checkpoint_key: str,
    table_output: Path,
    validation_output: Path,
    statement_output: Path,
    statement_validation_output: Path,
    page_index_output: Path,
    index_only: bool,
) -> dict[str, Any]:
    convert_options: dict[str, Any] = {"dpi": dpi, "fmt": "jpeg"}
    if poppler_path:
        convert_options["poppler_path"] = str(poppler_path)
    images = convert_from_path(str(pdf_path), **convert_options)
    if not images:
        raise ValueError("PDF 沒有可轉換的頁面")
    if max_pages > 0:
        images = images[:max_pages]
    write_page_index_csv(page_index_output, pdf_path, len(images))
    if index_only:
        return {field: None for field in FIELDS}
    if page_batch_size <= 0:
        page_batch_size = len(images)

    checkpoint: dict[str, Any] = {}
    if checkpoint_file.exists():
        with checkpoint_file.open(encoding="utf-8") as file:
            checkpoint = json.load(file)
    saved = checkpoint.get(checkpoint_key, {})
    merged: dict[str, Any] = {
        field: saved.get("merged", {}).get(field) for field in FIELDS
    }
    start_page = int(saved.get("next_page", 0))
    if saved.get("completed"):
        return merged

    if len(images) >= 5 and not saved.get("balance_sheet_completed"):
        print("  先處理第 5 頁固定資產負債表")
        balance_rows = request_balance_sheet(provider, images[4])
        write_balance_sheet_csv(table_output, pdf_path, balance_rows)
        write_balance_sheet_validation_csv(validation_output, pdf_path, balance_rows)
        saved["balance_sheet_completed"] = True
        checkpoint[checkpoint_key] = {
            "next_page": start_page,
            "completed": False,
            "balance_sheet_completed": True,
            "statement_completed": bool(saved.get("statement_completed")),
            "merged": merged,
        }
        checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
        with checkpoint_file.open("w", encoding="utf-8") as file:
            json.dump(checkpoint, file, ensure_ascii=False, indent=2)

    if len(images) >= 6 and not saved.get("statement_completed"):
        print("  處理第 6 頁本年度收支餘絀表")
        statement_rows = request_income_statement(provider, images[5])
        write_statement_csv(statement_output, pdf_path, statement_rows)
        write_statement_validation_csv(
            statement_validation_output, pdf_path, statement_rows
        )
        saved["statement_completed"] = True
        checkpoint[checkpoint_key] = {
            "next_page": start_page,
            "completed": False,
            "balance_sheet_completed": bool(saved.get("balance_sheet_completed")),
            "statement_completed": True,
            "merged": merged,
        }
        checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
        with checkpoint_file.open("w", encoding="utf-8") as file:
            json.dump(checkpoint, file, ensure_ascii=False, indent=2)

    page_indexes = [index for index in range(len(images)) if index != 4]
    for page_position in range(start_page, len(page_indexes), page_batch_size):
        batch_indexes = page_indexes[page_position:page_position + page_batch_size]
        batch_start = batch_indexes[0]
        batch_end = batch_indexes[-1] + 1
        school_year = report_school_year(pdf_path)
        sections = sorted({page_section(index + 1, school_year) for index in batch_indexes})
        section_prompt = "\n本批頁面章節對照：" + "、".join(sections)
        print(f"  頁面 {batch_start + 1}-{batch_end}/{len(images)}：{'、'.join(sections)}")
        if provider == "openai":
            result = request_openai([images[index] for index in batch_indexes], section_prompt)
        else:
            result = request_gemini([images[index] for index in batch_indexes], section_prompt)
        for field in FIELDS:
            value = result.get(field)
            if value is not None and value != "":
                merged[field] = value
        checkpoint[checkpoint_key] = {
            "next_page": page_position + len(batch_indexes),
            "completed": page_position + len(batch_indexes) >= len(page_indexes),
            "balance_sheet_completed": True,
            "statement_completed": True,
            "merged": merged,
        }
        checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
        with checkpoint_file.open("w", encoding="utf-8") as file:
            json.dump(checkpoint, file, ensure_ascii=False, indent=2)
    return merged


def ensure_api_key(provider: str) -> None:
    variable = "OPENAI_API_KEY" if provider == "openai" else "GEMINI_API_KEY"
    if not os.getenv(variable):
        raise RuntimeError(f"找不到環境變數 {variable}")


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parents[2]
    load_dotenv(project_root / ".env")
    load_dotenv(script_dir.parent / ".env")
    args = parse_args()
    data_root = Path(__file__).resolve().parents[1]
    checkpoint_file = args.checkpoint or data_root / "processed" / "checkpoints" / "report.checkpoint.json"
    table_output_base = args.table_output or data_root / "processed"
    validation_output_base = args.validation_output or data_root / "processed"
    page_index_base = data_root / "processed" / "page_indexes"
    if args.provider not in ("gemini", "openai"):
        raise ValueError("provider 必須是 gemini 或 openai")
    if not args.index_only and not args.ocr:
        ensure_api_key(args.provider)
    appendix3_pages = [int(p) for p in str(args.appendix3_pages).split(",") if p.strip()]
    notes_pages = [int(p) for p in str(args.notes_pages).split(",") if p.strip()]
    cash_flow_pages = [int(p) for p in str(args.cash_flow_pages).split(",") if p.strip()]
    appendix2_pages = [int(p) for p in str(args.appendix2_pages).split(",") if p.strip()]
    appendix4_pages = [int(p) for p in str(args.appendix4_pages).split(",") if p.strip()]
    balance_sheet_pages = [int(p) for p in str(args.balance_sheet_pages).split(",") if p.strip()]
    income_pages = [int(p) for p in str(args.income_pages).split(",") if p.strip()]
    income_prev_pages = [int(p) for p in str(args.income_prev_pages).split(",") if p.strip()]
    operating_pages = [int(p) for p in str(args.operating_pages).split(",") if p.strip()]
    operating_long_output = (
        Path(__file__).resolve().parents[1] / "analysis" / "outputs" / "operating_long.csv"
    )
    pdf_files = sorted(args.input_dir.rglob("*.pdf"))
    if args.kindergarten:
        query = args.kindergarten.casefold()
        pdf_files = [
            pdf
            for pdf in pdf_files
            if query in pdf.stem.casefold() or query in pdf.parent.name.casefold()
        ]
    if args.school_year:
        school_year = args.school_year.removesuffix("學年度")
        pdf_files = [
            pdf for pdf in pdf_files if str(report_school_year(pdf)) == school_year
        ]
    if not pdf_files:
        print(f"找不到 PDF：{args.input_dir}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for index, pdf_path in enumerate(pdf_files, start=1):
        print(f"[{index}/{len(pdf_files)}] 處理 {pdf_path.name}")
        row: dict[str, Any] = {column: None for column in CSV_COLUMNS}
        row.update(report_source(pdf_path, args.input_dir))
        source_dir = school_output_dir(table_output_base, pdf_path)
        prefix = report_prefix(pdf_path)
        school_year = report_school_year(pdf_path)
        table_output = table_output_path(table_output_base, pdf_path, f"{prefix}_資產負債表")
        validation_output = table_output_path(
            validation_output_base, pdf_path, f"{prefix}_資產負債表加總驗證"
        )
        statement_output = table_output_path(
            table_output_base, pdf_path, f"{prefix}_{school_year}學年度收支餘絀表"
        )
        statement_validation_output = table_output_path(
            validation_output_base,
            pdf_path,
            f"{prefix}_{school_year}學年度收支餘絀表驗證",
        )
        page_index_output = pdf_output_path(
            school_output_dir(page_index_base, pdf_path), pdf_path, "page_index"
        )
        appendix3_output = table_output_path(
            table_output_base,
            pdf_path,
            f"{prefix}_附表三_各學年收支預決算比較表",
        )
        notes_output = table_output_path(
            table_output_base,
            pdf_path,
            f"{prefix}_財務報表附註_關係人交易質抵押重大承諾",
        )
        cash_flow_output = table_output_path(
            table_output_base, pdf_path, f"{prefix}_現金流量表"
        )
        appendix2_output = table_output_path(
            table_output_base, pdf_path, f"{prefix}_附表二_經費流用及勻支檢查表"
        )
        appendix4_output = table_output_path(
            table_output_base, pdf_path, f"{prefix}_附表四_財產清冊"
        )
        # 前一年度收支餘絀（第 7 頁）輸出至「前一學年度」資料夾
        prev_year = school_year - 1
        prev_school_dir = school_output_dir(table_output_base, pdf_path).parent.parent / f"{prev_year}學年度" / school_output_dir(table_output_base, pdf_path).name
        income_prev_output = prev_school_dir / f"{prefix}_{prev_year}學年度收支餘絀表.csv"
        income_prev_validation_output = (
            school_output_dir(validation_output_base, pdf_path).parent.parent
            / f"{prev_year}學年度"
            / school_output_dir(validation_output_base, pdf_path).name
            / f"{prefix}_{prev_year}學年度收支餘絀表驗證.csv"
        )
        try:
            if args.operating_only:
                extract_operating_only(
                    pdf_path,
                    args.provider,
                    args.dpi,
                    args.poppler_path,
                    operating_pages,
                    operating_long_output,
                )
            elif args.income_prev_only:
                extract_income_prev_only(
                    pdf_path,
                    args.provider,
                    args.dpi,
                    args.poppler_path,
                    income_prev_pages,
                    income_prev_output,
                    income_prev_validation_output,
                )
            elif args.balance_sheet_only:
                extract_balance_sheet_only(
                    pdf_path,
                    args.provider,
                    args.dpi,
                    args.poppler_path,
                    balance_sheet_pages,
                    table_output,
                    validation_output,
                )
            elif args.income_only:
                extract_income_only(
                    pdf_path,
                    args.provider,
                    args.dpi,
                    args.poppler_path,
                    income_pages,
                    statement_output,
                    statement_validation_output,
                )
            elif args.cash_flow_only:
                extract_cash_flow_only(
                    pdf_path,
                    args.provider,
                    args.dpi,
                    args.poppler_path,
                    cash_flow_pages,
                    cash_flow_output,
                )
            elif args.appendix2_only:
                extract_appendix2_only(
                    pdf_path,
                    args.provider,
                    args.dpi,
                    args.poppler_path,
                    appendix2_pages,
                    appendix2_output,
                )
            elif args.appendix4_only:
                extract_appendix4_only(
                    pdf_path,
                    args.provider,
                    args.dpi,
                    args.poppler_path,
                    appendix4_pages,
                    appendix4_output,
                )
            elif args.notes_only:
                extract_notes_only(
                    pdf_path,
                    args.provider,
                    args.dpi,
                    args.poppler_path,
                    notes_pages,
                    notes_output,
                )
            elif args.appendix3_only:
                extract_appendix3_only(
                    pdf_path,
                    args.provider,
                    args.dpi,
                    args.poppler_path,
                    appendix3_pages,
                    appendix3_output,
                )
            elif args.ocr:
                extract_ocr_pages_only(
                    pdf_path,
                    args.dpi,
                    args.poppler_path,
                    source_dir,
                )
            else:
                row.update(
                    extract_pdf(
                        pdf_path,
                        args.provider,
                        args.dpi,
                        args.poppler_path,
                        args.max_pages,
                        args.page_batch_size,
                        checkpoint_file,
                        str(pdf_path.relative_to(args.input_dir)),
                        table_output,
                        validation_output,
                        statement_output,
                        statement_validation_output,
                        page_index_output,
                        args.index_only,
                    )
                )
            row["處理狀態"] = "成功"
            row["錯誤訊息"] = ""
        except Exception as error:  # Keep processing the remaining PDFs.
            row["處理狀態"] = "失敗"
            row["錯誤訊息"] = str(error)
            print(f"  失敗：{error}", file=sys.stderr)
        rows.append(row)
        table_only_mode = (
            args.appendix3_only
            or args.notes_only
            or args.cash_flow_only
            or args.appendix2_only
            or args.appendix4_only
            or args.balance_sheet_only
            or args.income_only
            or args.income_prev_only
            or args.operating_only
        )
        if not args.ocr and not args.index_only and not table_only_mode:
            report_output = school_output_dir(data_root / "processed", pdf_path) / f"{prefix}_財報摘要.csv"
            with report_output.open("w", newline="", encoding="utf-8-sig") as report_file:
                writer = csv.DictWriter(report_file, fieldnames=CSV_COLUMNS)
                writer.writeheader()
                writer.writerow(row)

    if (
        args.cash_flow_only
        or args.appendix2_only
        or args.appendix4_only
        or args.balance_sheet_only
        or args.income_only
        or args.income_prev_only
        or args.operating_only
    ):
        print(f"完成指定表格抽取，共 {len(rows)} 份 PDF")
    elif args.notes_only:
        print(f"完成附註段落抽取，共 {len(rows)} 份 PDF")
    elif args.appendix3_only:
        print(f"完成附表三抽取，共 {len(rows)} 份 PDF")
    elif not args.index_only and not args.ocr:
        with args.output.open("w", newline="", encoding="utf-8-sig") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        print(f"完成：{args.output}，共 {len(rows)} 份 PDF")
    elif args.index_only:
        print(f"完成頁碼索引，共 {len(rows)} 份 PDF")
    else:
        print(f"完成指定財報表，共 {len(rows)} 份 PDF")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
