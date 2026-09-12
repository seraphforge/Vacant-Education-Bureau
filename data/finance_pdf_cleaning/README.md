# 財報 PDF 資料清理區

此資料夾用於整理、清理、驗證與後續分析財報 PDF 檔案，方便後續建立資料抽取流程與模型輸入。

## 目錄說明

- raw/: 原始下載的財報 PDF 檔案
- processed/: 已清理，或已拆分後的輸出資料
- scripts/: PDF 清理、解析、欄位抽取與資料處理腳本
- docs/: 作業規範、處理紀錄與欄位定義

## 輸出資料夾

輸出會依「學年度／幼兒園」分層，例如：

```text
processed/
└─ 113學年度/
	└─ 安溪/
		├─ 資產負債表.csv
		├─ 資產負債表加總驗證.csv
		├─ 113學年度收支餘絀表.csv
		└─ 113學年度收支餘絀表驗證.csv
```

所有 PDF 的合併主報告仍輸出至 `processed/reports/report.csv`；checkpoint 則輸出至 `processed/checkpoints/`。

## 建議流程

1. 將原始財報 PDF 放入 raw/
2. 依年度/機構/檔案類型命名
3. 執行清理腳本，輸出到 processed/
4. 記錄處理結果與欄位定義於 docs/
5. 後續分析與模型訓練可直接讀取 processed/

## 命名建議

範例：
- 2025-XX-幼兒園-決算書.pdf
- 2025-XX-幼兒園-財務報表.pdf

請保持一致命名規則，避免後續資料整合時出錯。

## PDF 批次抽取

安裝依賴：

```bash
pip install -r requirements.txt
```

在專案根目錄建立 `.env`，擇一設定 API 金鑰與模型供應商：

```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=你的_Gemini_API_Key
# 免費層優先使用；也可省略此行
GEMINI_MODEL=gemini-3.5-flash-lite
```

或使用 OpenAI：

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=你的_OpenAI_API_Key
```

執行批次處理：

```bash
python data/finance_pdf_cleaning/scripts/extract_financial_reports.py
```

預設會掃描 `raw/` 內所有 PDF，並將結果輸出至 `report.csv`。也可以指定路徑：

```bash
python data/finance_pdf_cleaning/scripts/extract_financial_reports.py --input-dir data/finance_pdf_cleaning/raw --output data/finance_pdf_cleaning/report.csv --provider openai
```

免費 Gemini 測試時可限制每份 PDF 頁數，避免一次請求過大：

```bash
python data/finance_pdf_cleaning/scripts/extract_financial_reports.py --max-pages 5
```

完整財報預設每 1 頁分批送給模型，再合併成一列結果；每次 API 請求最多等待 120 秒：

```bash
python data/finance_pdf_cleaning/scripts/extract_financial_reports.py --page-batch-size 1
```

每批完成後會自動記錄進度至與 CSV 同名的 `.checkpoint.json`。若中途停止，重新執行相同指令即可從上次完成的頁面繼續，不會重跑已完成頁面。

第 5 頁固定先以資產負債表專用格式抽取，輸出至 `資產負債表.csv`；第 6 頁固定以本年度收支餘絀表格式抽取，輸出至 `<學年度>學年度收支餘絀表.csv`。一般財報欄位抽取會跳過第 5 頁，避免表格數字誤配。可用 `--table-output` 自訂表格 CSV 輸出目錄。

抽取第 5 頁後會自動產生 `資產負債表加總驗證.csv`，驗證資產總計、負債總額、餘絀總額，以及負債加餘絀是否相等；抽取第 6 頁後會產生 `<學年度>學年度收支餘絀表驗證.csv`。`結果` 欄為 `通過` 或 `不一致`。

`--page-batch-size 0` 可恢復整份 PDF 一次送出，但不建議在免費 API 使用。

### 其他常用參數

- `--kindergarten`：只處理指定幼兒園名稱或代碼（例如 `安溪` 或 `N01`）。
- `--school-year`：只處理指定學年度（例如 `113`）。
- `--index-only`：只建立排除第 5 頁的財報頁碼索引，不呼叫模型。
- `--ocr`：改用本機 Tesseract OCR 抽取第 5、6 頁，不呼叫 Gemini 或 OpenAI。
- `--validation-output`：自訂驗算 CSV 的輸出目錄。
- `--appendix3-only`：只抽取附表三「各學年收支預決算比較表」（跨兩頁），逐列輸出項目與本年度／前一年度的預算數、決算數、差異、執行率，存成 `附表三_各學年收支預決算比較表.csv`。
- `--appendix3-pages`：附表三所在頁碼，以逗號分隔，預設 `26,27`；若某園頁碼不同可自行指定。
- `--notes-only`：只抽取財務報表附註的三個段落（關係人交易、質抵押資產、重大承諾事項及或有事項），逐段輸出章節與內容，存成 `<前綴>_財務報表附註_關係人交易質抵押重大承諾.csv`。
- `--notes-pages`：附註段落所在頁碼，以逗號分隔，預設 `22,23`。

所有表格 CSV 皆以「代碼＋園名」前綴命名，例如 `N01安溪_資產負債表.csv`、`N02山北_附表三_各學年收支預決算比較表.csv`。

抽取附表三範例（指定園所與學年度）：

```bash
python data/finance_pdf_cleaning/scripts/extract_financial_reports.py --appendix3-only --school-year 113 --kindergarten N01
```

Windows 若未將 Poppler 加入 PATH，請另外安裝 Poppler，並執行時指定：

```bash
python data/finance_pdf_cleaning/scripts/extract_financial_reports.py --poppler-path "C:\\Program Files\\poppler\\Library\\bin"
```
