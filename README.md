<h1 align="center">README!!!</h1>

![README!!!](assets/banner.png)

## 簡介

現行教保機構風險評估方案多仰賴人力，且缺乏各項資訊的交叉比對與整合，導致評估效率與完整性較低。針對以上困境，我們欲利用 AI Agent 自動、彙整各項公開資料（其中包括Google Map 評論在內等網路輿論），並與政府內部資料如財報等，整合至單一平臺，方便審查與分析。同時於平臺串接 AI 模型，藉由交叉對比不同學校、追蹤各學校財報的年度變化等方法，綜合所有資料並自動分析教育機構的各類風險指數。最終以可視化方式清楚、簡潔的呈現結果。除此之外，我們亦將在其中加入家長回報系統，藉由這些第一手資料提升準確度與預測效率。

- 作品簡報：*pending*
- 線上 Demo：**https://d17mx0mlb8rctm.cloudfront.net**

## 技術棧

- 前端：Angular 18, PrimeNG 17（`webapp/`）
- 後端：AWS Serverless — API Gateway (HTTP API) + Lambda (Python 3.12) + CloudFormation（`aws/`）
- 前端託管：S3 + CloudFront（全 serverless，無需維護伺服器）
- 資料庫：Amazon RDS for MySQL 8.4（schema `readme`）
- AI：Amazon Bedrock（Claude Haiku 4.5，輿情歸屬判定與摘要）+ Amazon Comprehend（繁中情緒分析）

## 系統架構

```
                    使用者瀏覽器
                    │            │
        靜態檔案     │            │  API 呼叫 (JSON)
                    ▼            ▼
            CloudFront        API Gateway (HTTP API)
                 │                    │
                 ▼                    ▼
        S3「ntpc-kg-web」      Lambda「ntpc-kg-api」
        (Angular 建置產物)      (Python + PyMySQL，位於 RDS 的 VPC 內)
                                      │  MySQL 3306
                                      ▼
                            RDS MySQL「my-mysql-db」
                            readme.kindergarten
                            (104～114 學年度，74,628 列)
```

全部 AWS 資源都由 CloudFormation 定義（`aws/template.yaml`、`aws/web-template.yaml`），
可重建、可交接、可一次刪除。

## 快速開始

```powershell
# 1) 部署後端 API（第一次要先複製 deploy.config.example.ps1 成 deploy.config.ps1 並填密碼）
cd aws
.\deploy.ps1          # 完成後會印出 ApiUrl

# 2) 部署前端到 AWS（S3 + CloudFront）
.\deploy-web.ps1      # 完成後會印出 live demo 網址

# 或 2') 只在本機開發前端
cd ..\webapp
npm install
npm start             # http://localhost:4200
```

細節說明：

- 後端與部署：[`aws/README.md`](aws/README.md)
- 前端：[`webapp/README.md`](webapp/README.md)

## 目前完成度

- [x] 幼兒園清單查詢 API（縣市 / 名稱 LIKE / 公私立 / 分頁 / 排序）
- [x] Angular + PrimeNG 查詢畫面（伺服器端分頁）
- [x] 前後端皆部署於 AWS，具備可公開存取的 live demo
- [x] 幼兒園裁罰紀錄爬取與查詢 API（新北市，`kindergarten_punishment` 表，外鍵串回 `kindergarten`）
- [x] 輿情分析 AI Agent（政府端一鍵啟動；多來源蒐集 + Bedrock 判讀歸屬與摘要 + Comprehend 情緒）
- [ ] 財報、Google Map 評論等資料源整合
- [ ] AI 風險指數分析（輿情維度已由輿情分析寫入，其餘維度仍為 placeholder）
- [ ] 家長回報系統

## 輿情分析 AI Agent

政府人員在幼兒園詳細資料的「輿情分析」Tab 按一個按鈕，系統就會自動蒐集並整理該園的
公開輿情。因為整段分析要數十秒到數分鐘（遠超 API Gateway 的 29 秒上限），所以做成
非同步 job：API 建立工作 → worker Lambda 執行 → 前端輪詢進度。

蒐集的來源，以及每個來源失敗時的處理：

| 來源 | 型態 | 說明 |
|---|---|---|
| 新北市政府電子公布欄 | 官方 OpenAPI | 最穩定，且可核對 |
| 本府裁罰紀錄 | 自有資料庫 | 判讀網路傳聞真偽的事實基準 |
| 家長回報 | 自有資料庫 | 已完成 Email 驗證的第一手資訊 |
| Google News RSS | 新聞搜尋 | 唯一能用園名精準搜到新聞的來源（見下方說明） |
| 中央社 RSS | 官方 RSS | 全台近期議題，靠園名比對才歸屬 |
| 公開網路搜尋 | 可設定端點 | 最不穩定的一環，被擋就記 `blocked` |

**單一來源失敗不會中斷整體結果**，每個來源的 success / blocked / unavailable 都會寫進
摘要交代清楚。對政府端來說「哪些來源查得到、哪些被擋」本身就是需要說明的資訊。

蒐集完成後：Bedrock（Claude Haiku 4.5）判斷每筆資料到底是不是指向這一間園所並產生
中文摘要，Comprehend 補上繁中情緒分數，最後算出 0–100 的「關注指數」並寫回風險雷達圖的
輿情維度。

### 這個功能刻意做了哪些限制

- **不自動掃描。** 對外部網站發請求和呼叫 AI 都有成本與禮貌問題，必須是承辦人明確的動作。
  每次啟動都會記錄誰、什麼時候、對哪一園（`opinion_scan_audit`）。
- **分數按「問題類型」計，不按「報導篇數」計。** 一個事件被 10 家媒體報導是 1 個事件，
  不是 10 個問題。每種風險標籤只計一次，取權重最高的那一筆；報導篇數在摘要裡如實另計。
  早期版本把每篇文章相加，結果只要有新聞群聚就直接頂到 100 分，那是在衡量媒體關注度
  而不是風險。
- **官方紀錄不被語氣打折。** 裁罰處分書是公文腔，情緒分析會判成中性，但「被罰了」
  這件事跟語氣無關，所以可核對的官方紀錄用完整權重。
- **同名園所一律標為待人工確認。** 「可歸屬本園」與「待人工確認」在畫面上是兩張分開的表；
  只有前者會計入分數。
- **不算風險總分。** 其他維度還是 placeholder，算總分會給人「已完成評估」的錯覺。
- **分數不是違法機率。** 每個畫面都標示這些資料是需要人工關注的線索，不是已證實的事實，
  不得單獨作為裁處依據。

### robots.txt 的處理（一個明講的例外）

原則是尊重 robots.txt，也因此排除了 `www.bing.com/search`（`Disallow: /search`）。
遇到 401/403/429 或 anti-bot 頁面一律停止該來源並記錄，不換入口、不重放 Cookie、
不繞 CAPTCHA。

**例外只有一個**：`news.google.com/rss/search` 的 robots.txt 對 `*` 是 `Disallow: /`，
但它是唯一能用園名精準搜到新聞的來源。為了驗證這個概念可行，我們小量使用它：

- 每次掃描最多 2 個查詢、每查詢最多 8 筆
- 沿用全域的請求間隔（至少 1.2 秒），不併發
- 只讀 RSS 的標題與連結，**不抓原文**，避免變成內容重製
- 401/403/429 與 anti-bot 頁面照樣立刻停止

例外是**明列在設定裡**而不是把檢查拔掉：`OPINION_ROBOTS_EXEMPT_HOSTS`（預設只有
`news.google.com`），其他來源的 robots 檢查完全不受影響。
要關掉這個來源把 `OPINION_GOOGLE_NEWS` 設成 `0`；要移除例外就清空
`OPINION_ROBOTS_EXEMPT_HOSTS`。正式上線前建議改接有授權的新聞 API。

## 財報風險偵測項目

財報資料清理後，將針對下列項目進行異常分析：

- 人事費異常
- 業務費異常
- 修繕／採購費突然暴增
- 業務發展費異常
- 其他支出異常
- 預算與決算落差
- 年度支出突然大幅變化

PDF 抽取階段先保留人事費、業務費、修繕及採購費、業務發展費、其他支出、預算總額與決算總額等原始欄位。異常判定則需要搭配同一幼兒園的多年度資料，計算年度變化率、預算執行率與各支出項目占比，避免只依單一年度金額誤判。

## 本機 OCR 模式

不使用 Gemini 或 OpenAI 時，可用 Tesseract OCR 處理所有 PDF 的第 5、6 頁：

```bash
python data/finance_pdf_cleaning/scripts/extract_financial_reports.py --ocr
```

OCR 結果會依學年度／幼兒園輸出為 `*_page5_ocr.csv` 與 `*_page6_ocr.csv`。腳本會使用 `chi_tra+eng` 辨識繁體中文與數字，需自行準備 `tessdata/chi_tra.traineddata`（未納入版控）；若 Tesseract 安裝在非預設路徑，可設定 `TESSERACT_CMD`。

## 貢獻者

<a href="https://github.com/seraphforge/Vacant-Education-Bureau/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=seraphforge/Vacant-Education-Bureau" />
</a>

Made with [contrib.rocks](https://contrib.rocks).

## 授權條款
