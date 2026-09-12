# kindergarten-intelligence

台灣幼兒園公開資訊與輿情蒐集系統，優先新北市。Python 3.11+，自行執行、SQLite 與 JSON 全部存本機，輸出 Markdown。無 AWS、雲端資料庫、付費爬蟲或雲端 AI。

**風險分數只代表「需要人工關注程度」，不是違法機率，不是事件已被證實。** 否認、澄清及一般政策文字也可能命中關鍵字。社群、新聞及評論一律 `verified=false`；政府公開資料的 `true` 只表示官方來源可核對，不能推定指控成立。幼兒園同名或多園所文章不會自動歸屬單一園所。

## 安裝與執行

Windows PowerShell（在專案目錄）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\Activate.ps1
python main.py gov
python main.py crawl --city 新北市 --limit 5
python main.py crawl --district 板橋區 --limit 10 --workers 3
python main.py crawl --kindergarten "ABC幼兒園"
python main.py news
python main.py social
python main.py status
python main.py report
python -m pytest -q
```

如 PowerShell 禁止 Activate.ps1，直接以 `.\.venv\Scripts\python.exe main.py ...` 執行。Linux/macOS 使用 `python3 -m venv .venv`、`source .venv/bin/activate`，其餘相同。首次無政府名錄時 crawl/news/social 會先更新名錄。

## 本機檔案

* `data/kindergarten_intelligence.db`：名錄、標準資料、風險標籤、來源執行紀錄。
* `data/kindergarten_keywords.json`：由政府名錄產生的官方名稱及短名、空格／kindergarten 別名。
* `data/raw/`：政府原始公開 JSON 與來源 URL、擷取時間。
* `data/normalized/`：標準化摘要／metadata。新聞及社群不保存完整 HTML 或全文。
* `data/reports/YYYY-MM-DD_HH-MM.md`：累積資料報告；同分鐘重新執行會更新該檔。
* `logs/crawler.log`：時間、來源、query、HTTP status、result count、錯誤；不記錄憑證、Cookie 或 token。

## 來源

* [教育部幼兒園名錄](https://data.gov.tw/dataset/6086)：每次解析現行頁面連結及序列化 metadata 找 JSON/CSV，不寫死學年度下載網址。保留原始全部學年度，SQLite 僅匯入檔案最新學年度。
* [新北市立案幼兒園](https://data.ntpc.gov.tw/datasets/f563b4cd-b850-41f5-9709-b910f2d147e9)：[官方 OpenAPI](https://data.ntpc.gov.tw/openapi/) 分頁，保存原始欄位；以完整官方名稱與縣市合併來源，短名不用於政府園所去重。
* [新北市政府電子公布欄](https://data.ntpc.gov.tw/datasets/EAAC9944-2CCB-4DBB-B616-441128E17A4A)：擷取目前公開公告、依幼兒／教保／托育／兒童／管教／裁罰等關鍵字篩選。保存文號、日期、機關、主旨與附件 URL，不下載附件。
* [CNA 官方 RSS](https://www.cna.com.tw/about/rss.aspx)：社會、地方、生活。僅存標題與最多 300 字 RSS 摘要，保留原始連結；使用者仍應遵守來源授權條件。
* Instagram、Threads、X：只取得未登入公開搜尋索引。網站 blocking 或 robots 限制記 `blocked`，HTTP／解析錯誤記 `unavailable`，未取得結果不代表没有討論。
* 一般新聞／網頁：公開搜尋結果頁；預設 Bing。新聞依網域識別，已支援 CNA、公視、自由、聯合、ETtoday、TVBS、三立、中時、Newtalk、NOWnews、Yahoo。增加官方 RSS 可編輯 `RSS_FEEDS`。

## 流量與設定

預設 `--limit 10`，選擇排序後前 10 間，**不是全市完整查詢**。`--workers` 1–5；HTTP 共用鎖保持逐次發送，workers 只增加處理排程，不倍增對外流量。每次 request 至少 1 秒，尊重更長的 robots crawl-delay；逾時 25 秒、網路或 5xx 最多 3 次指數退避。401/403/429/CAPTCHA 立即停止該 host，無登入、Cookie 重播、代理輪換、CAPTCHA 或限流繞過。

`.env` 可設定 `REQUEST_DELAY`、`REQUEST_TIMEOUT`、`SEARCH_ENDPOINT`、`SEARCH_QUERY_PARAM`、`SEARCH_QUERIES_PER_SCHOOL`、`SEARCH_RESULTS_LIMIT`、`MAX_PAGES`。預設每園每 collector 最多 2 queries、每 query 最多 5 結果；完整 query 模板保存在 collector 中，可提高預算啟用更多。預設 crawl 每園最多 10 次搜尋。共用搜尋入口被封鎖後不切換入口規避；可由使用者設定另一個有權使用的免費公開 endpoint。JSON endpoint 支援 `results: [{title, url, snippet/content}]`，HTML 支援 Bing／DuckDuckGo 結果結構。不要在 endpoint 放 token。

一般網站與 RSS 讀取 robots.txt；暫時失效會保守跳過；404/410 視為沒有規則；成功回傳檔案中的非規則行依 RFC 9309 忽略，但明確拒絕／CAPTCHA 頁仍記 blocked。政府明確提供的 metadata 與資料 OpenAPI 依機器介接契約呼叫，僅允許程式中列出的兩個官方 API 路徑；API 拒絕立即停止，不拿來繞過網頁限制。非公開 IP、無效 URL 不讀取。使用 `truststore` 的作業系統信任庫驗證 TLS，不使用 `verify=False`。預設不追讀社群頁面；`public_metadata()` 提供公開 URL 的 HTML/OpenGraph 讀取能力，仍受相同限制。

## 資料與報告語意

items URL UNIQUE，另以 normalized title/content/幼兒園名稱 SHA256 去重；跨 URL 重複摘要保留第一筆來源。`kindergartens.metadata` 保留政府原欄位與多來源。`items.metadata` 保留公告欄位、feed_url、候選園所及摘要擴充欄位。只根據文字唯一命中才寫入 kindergarten_id；一般幼教新聞及歧義討論仍保存但標記未確認園所。

報告為本機累積資料，清楚列出最近執行範圍；RSS 是全台近期議題，不表示都是本次選取園所。高關注、新聞、社群各節上限 100 筆，完整資料在 SQLite，政府名錄完整列出。單一來源失敗不阻擋報告。crawl_runs 保留全部歷史錯誤，報告显示最近一次各來源／query 狀態。

## 已知限制

規則不判斷真假、否定、語境或司法結果。名稱別名是啟發式，附設、分班、同名園所仍需人工核對。新聞 RSS 只有近期資料，公告是當下公開清單，不能保證歷史完整或名錄即時反映立案狀態。公開搜尋沒有完整性保證，索引可能過期；無法判定帳號現在是否轉私人，明示 private/protected 的結果略過。搜尋 HTML 變動須調整 parser。沒有內建排程常駐程式，可用 Windows 工作排程器／cron 定期執行 `gov`、`news`、`social`。

測試使用隔離 SQLite 與測試 fixture 驗證安全邊界及去重；實際連網驗收由 CLI 執行，資料成果不使用 mock 補充。
