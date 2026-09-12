# 實際連網驗收紀錄

驗收日期：2026-09-12（Asia/Taipei）。實際環境：Windows、Python 3.13.14；專案支援 Python 3.11+。已建立 `.venv` 並安裝 `requirements.txt`，所有抓取由本機 Python 執行。

## 已執行

```text
python main.py gov
python main.py crawl --city 新北市 --limit 5
python main.py news --limit 1
python main.py social --limit 1 --workers 3
python main.py status
python main.py report
python -m pytest -q
```

測試結果：**20 passed**。Windows Store Python 需由可正常啟動該 interpreter 的執行環境運行；本次以 `.venv/Scripts/python.exe` 執行上述命令。

## 成功來源

| 來源 | 取得／匯入結果 |
| --- | --- |
| 教育部 metadata API → 動態 JSON 資源 | 最新 114 學年度 6,747 筆 |
| 新北市幼兒園 OpenAPI | 1,111 筆；已實測分頁 |
| 新北市電子公布欄 OpenAPI | 8 筆主旨命中公告 |
| CNA 社會 RSS | 2 筆符合條件的新聞摘要 |
| CNA 地方 RSS | 成功讀取，0 筆符合條件 |
| CNA 生活 RSS | 成功讀取，0 筆符合條件 |

政府名錄按縣市與完整官方名稱合併後，SQLite 共 **6,762 筆**，其中新北市 **1,123 筆**。跨來源名錄更新時間不同，聯集紀錄數不是即時有效立案數；不強行刪除只出現在其中一個来源的園所。

## 阻擋及開發過程問題

* Instagram／Threads／X、一般 Web、新聞搜尋：**blocked — Bing robots.txt disallows URL**。未送出被禁止的搜尋頁請求，未登入任何社群，也未嘗試換代理或繞過。這不代表 Instagram／Threads／X 本站都直接回傳封鎖；實際限制發生在公開搜尋入口。
* data.gov.tw 網頁 robots 路徑曾回 HTTP 500；最終使用其正式 metadata API 成功取得現行下載 URL。
* 新北站 Windows/Python TLS 相容性問題：以 truststore 使用作業系統信任庫解決，保留憑證驗證。政府網站 robots 路徑有拒絕頁，正式公開資料 API 本身可正常介接。
* FeedBurner robots 路徑回 200 非規則 HTML；依可解析規則處理，正式 RSS 全部成功回傳。

## 最終統計與檔案

```text
kindergartens: 6762
ntpc kindergartens: 1123
items: 10
government announcements: 8
news: 2
instagram: 0
threads: 0
x: 0
database size: 9,916,416 bytes
```

SQLite：`data/kindergarten_intelligence.db`。

交付報告：`data/reports/2026-09-12_12-31.md`。

原始政府資料：`data/raw/`；標準化紀錄：`data/normalized/`；搜尋字典：`data/kindergarten_keywords.json`；執行日誌：`logs/crawler.log`。

開發階段 SQLite 另存 `data/development-validation-backup.db`，保留早期錯誤及來源調整前的實測紀錄；正式 DB 以最終版本重新匯入。重跑 gov 時 8 筆公告新增數為 0，確認沒有重複增加。全部成果均來自真實公開來源，沒有使用 mock 填入交付 DB。

## 新增程式檔案

* `app/config.py`、各 package `__init__.py`
* `collectors/base.py`
* `collectors/government/{moe_kindergarten,ntpc_kindergarten,ntpc_announcements}.py`
* `collectors/social/{instagram,threads,x}.py`
* `collectors/news/{rss,news_search}.py`
* `collectors/web/generic.py`
* `analysis/{keyword_filter,risk_analyzer,normalizer,deduplicator}.py`
* `database/{db,repository}.py`
* `reports/markdown.py`
* `tests/test_core.py`、`pytest.ini`
* `main.py`、`diagnose_sources.py`、`requirements.txt`、`.env.example`、`.gitignore`、`README.md`、本紀錄

風險分數僅代表人工關注程度，不是違法機率或已證實事件；公開來源是否有資料、搜尋是否可用，均不能當作園所安全與否的判定。
