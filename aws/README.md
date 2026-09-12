# 後端（AWS Serverless）

## 這是什麼？

一支「查幼兒園資料」的 HTTP API，完全用 AWS 的 Serverless 服務組成，
沒有任何需要自己維護的伺服器。

```
瀏覽器 (Angular)
    │  HTTPS
    ▼
API Gateway (HTTP API)      ← 對外的網址，負責收 request
    │  觸發
    ▼
Lambda「ntpc-kg-api」        ← 我們的 Python 程式，收到請求才啟動、跑完就休眠
    │  MySQL 3306（走 VPC 內網）      │  443（走 VPC endpoint）
    ▼                                 ▼
RDS MySQL「my-mysql-db」          SES（寄驗證碼 / 通知信）
schema = readme                   S3（家長回報附件，presigned URL 直傳）
```

### 三個名詞（給沒有 AWS 經驗的人）

| 名詞 | 一句話解釋 | 為什麼要它 |
|---|---|---|
| **Lambda** | 「一段程式碼」而不是「一台機器」。有人呼叫才執行，按執行時間計費，沒人用就 0 元。 | 不用管作業系統、不用管擴充，流量大自動長出更多份。 |
| **API Gateway** | Lambda 的門面，把 `https://xxx/api/...` 轉成呼叫 Lambda。 | Lambda 本身沒有網址，要靠它才能被瀏覽器呼叫。 |
| **CloudFormation** | 用一份 YAML 描述「我要哪些資源」，AWS 幫你建好。 | 環境可重建、可刪除、可交接，不用在 Console 上點半天。 |

### 為什麼 Lambda 要放進 VPC？

這個 RDS 的 `PubliclyAccessible = false`，也就是**只能從 AWS 內部網路連**
（你自己在本機是靠 bastion EC2 開 SSH tunnel 才連得到）。
所以 Lambda 必須「站在同一個 VPC 裡面」才連得到資料庫。

`template.yaml` 幫你做了兩件事：

1. 建一個 Lambda 專用的 Security Group（防火牆規則群組）。
2. 在 RDS 現有的 Security Group 上，加一條「允許上面那個群組連 3306」的規則。

> 副作用：放進 VPC 的 Lambda 沒有對外網路（因為 default VPC 沒有 NAT Gateway）。
> 家長回報需要寄信，所以 `template.yaml` 另外加了一個
> **SES 的 Interface VPC Endpoint**（`com.amazonaws.us-east-1.email`），
> 讓 Lambda 走內網呼叫 SES，不必為此開 NAT Gateway（約 $7/月 vs $32/月起）。
> 附件用的 S3 不需要，因為 presigned URL 只是本機簽章運算。

### 輿情分析 worker 為什麼需要 NAT Gateway

輿情分析要去讀**別人的網站**（政府公布欄 OpenAPI、新聞 RSS、公開搜尋結果），
這不是呼叫 AWS 服務，沒有 VPC Endpoint 可以用，只能有真正的對外網路。

作法是**只加不改**，完全不動現在會跑的 API：

```
現有 6 個子網（RDS + API Lambda）    新增的兩個子網（只有 worker 在裡面）
0.0.0.0/0 -> Internet Gateway        0.0.0.0/0 -> NAT Gateway
（Lambda 沒有 public IP，等於沒網路）      （真的出得去）
        │                                     │
        └──────────── 同一個 VPC，local 路由互通 ─┘
                              │
                         RDS MySQL
```

- 新增 `172.31.96.0/20`、`172.31.112.0/20` 兩個私有子網（部署當下 VPC 內未使用），
  各自在不同 AZ，共用一張新的 route table 指向 NAT Gateway。
- **沒有修改 main route table**，也沒有改任何現有子網 —— RDS 與 API Lambda 的網路
  跟今天一模一樣。
- NAT Gateway 放在現有子網（已經有 IGW 路由），`deploy.ps1` 自動帶第一個 RDS 子網進去。
- API Lambda 要非同步喚醒 worker，但它沒有對外網路，所以另外加了
  **Lambda 的 Interface VPC Endpoint**（作法與上面的 SES endpoint 相同）。

固定成本大約：NAT Gateway $32/月起 + Lambda endpoint $7/月 + 流量。
比賽期間是幾美金；**Demo 結束後 `aws cloudformation delete-stack` 會一起收掉**。

輿情 worker 的網路設定另外有兩點值得知道：

- `ReservedConcurrentExecutions: 3`：同時最多 3 個掃描。對面是別人的網站，
  這個上限是禮貌也是保護。
- 堆疊 Output 有 `OpinionEgressIp`（NAT 的固定 IP）。如果某個來源開始回 `blocked`，
  它封的就是這個位址。

### 輿情 worker 的可調參數

都是 CloudFormation 參數，不用改程式：

| 參數 | 預設 | 說明 |
|---|---|---|
| `OpinionModelId` | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | Bedrock 模型。Anthropic 系列**必須**帶 `us.` inference profile 前綴，裸 model id 會被拒 |
| `OpinionSearchEndpoint` | `https://lite.duckduckgo.com/lite/` | 公開搜尋端點。會遵守 robots，被 anti-bot 擋就記 `blocked` |
| `OpinionGoogleNews` | `1` | 是否查 Google News RSS（唯一能用園名精準搜到新聞的來源）。設 `0` 關閉 |
| `OpinionRobotsExemptHosts` | `news.google.com` | **明列**跳過 robots 檢查的 host。清空即完全移除例外；其他來源不受影響 |
| `WorkerSubnetACidr` / `WorkerSubnetBCidr` | `172.31.96.0/20` / `172.31.112.0/20` | worker 專用子網，必須是 VPC 內未使用的區段 |

`OpinionRobotsExemptHosts` 是一個**刻意的取捨**：`news.google.com` 的 robots.txt
對 `*` 是 `Disallow: /`，我們為了驗證概念小量使用它（每次掃描 2 個查詢 × 8 筆，
只讀標題與連結不抓原文）。專案根目錄的 README 有完整說明。正式上線前建議改接
有授權的新聞 API。

## 檔案

| 檔案 | 用途 |
|---|---|
| `template.yaml` | CloudFormation 模板，定義**後端 API** 資源 |
| `web-template.yaml` | CloudFormation 模板，定義**前端網站託管**資源（S3 + CloudFront） |
| `auth-template.yaml` | CloudFormation 模板，定義**登入**資源（Cognito User Pool） |
| `src/app.py` | Lambda 路由分派 + 幼兒園／裁罰查詢 |
| `src/common.py` | DB 連線、HTTP 回應、body 解析、時間格式（共用工具） |
| `src/auth.py` | JWT claims、縣市範圍控管、`staff_profile` |
| `src/reports.py` | 家長回報（公開端點：草稿／驗證碼／附件／追蹤） |
| `src/reports_admin.py` | 家長回報（政府端：清單／詳情／回覆／狀態變更） |
| `src/mailer.py` | 寄信（SES v2，含不寄信的 dev mode） |
| `src/storage.py` | 附件的 S3 presigned URL |
| `src/risk.py` | 風險指數（目前是 placeholder） |
| `src/opinion.py` | 輿情分析 job API（啟動掃描／查進度／查最新結果） |
| `src/opinion_worker.py` | 輿情分析 worker（多來源蒐集 + Bedrock 判讀 + Comprehend 情緒） |
| `src/requirements.txt` | Lambda 依賴（只有 PyMySQL；boto3 是 runtime 內建） |
| `build_zip.py` | 把 `build/` 打包成 `lambda.zip` |
| `local_test.py` | 本機測試幼兒園／裁罰端點（開 SSH tunnel 連 RDS，不用部署） |
| `local_test_reports.py` | 本機測試家長回報全流程（63 項檢查） |
| `local_test_opinion.py` | 本機測試輿情 worker（`--live` 會連真的來源與 Bedrock／Comprehend） |
| `deploy.ps1` | 一鍵部署**後端** |
| `deploy-web.ps1` | 一鍵部署**前端**（build + 上傳 + 清快取） |
| `deploy-auth.ps1` | 一鍵部署**登入**（Cognito User Pool） |
| `create-user.ps1` | 手動建立一個公務人員帳號 |
| `deploy.config.ps1` | 你的設定與**資料庫密碼**（已 gitignore，不會進版控） |
| `deploy.config.example.ps1` | 給隊友抄的範本 |

專案根目錄另外還有：

| 路徑 | 用途 |
|---|---|
| `API_SPEC.md` | **前後端的 API 契約**（請求／回應格式、錯誤碼、TS 型別） |
| `db/migrations/*.sql` | 資料表 DDL，照檔名順序套用 |
| `tools/migrate.py` | 跑 migration（同樣走 bastion SSH tunnel） |
| `tools/opinion_e2e.py` | 輿情分析端到端測試（真 worker + 真 RDS + 真 Bedrock，不用部署） |
| `tools/seed_risk_placeholder.py` | 灌風險指數的假分數，給前端開發用 |
| `tools/cleanup_test_reports.py` | 刪掉測試用的家長回報資料 |

兩個 stack 是分開的，前端重新部署不會動到 API：

| Stack | 內容 |
|---|---|
| `ntpc-kg-api` | Lambda、API Gateway、Security Group、IAM Role、SES VPC endpoint、附件 S3 bucket |
| `ntpc-kg-api-web` | S3 網站 bucket、CloudFront distribution |
| `ntpc-kg-api-auth` | Cognito User Pool、App Client、admin 群組 |

## 前端託管（Live Demo）

Live demo：**https://d17mx0mlb8rctm.cloudfront.net**

```
瀏覽器
   │  HTTPS
   ▼
CloudFront (CDN)          ← 對外的 https 網址，自帶憑證
   │  只有它能讀
   ▼
S3 bucket「ntpc-kg-web-<帳號ID>」  ← 放 ng build 產出的 index.html / js / css
```

部署：

```powershell
cd aws
.\deploy-web.ps1
```

腳本會做：建 stack → `ng build --configuration production` →
`aws s3 sync` 到 bucket → `create-invalidation` 清 CloudFront 快取 → 印出網址。
之後改了前端程式，重跑同一行就更新。

### 為什麼要 CloudFront，不能直接開 S3 靜態網站？

S3 自己的靜態網站功能**只有 http、沒有 https**，而且要把 bucket 設成公開。
加上 CloudFront 後：

- 免費拿到 `https://xxx.cloudfront.net` 憑證（評審點連結不會出現安全警告）
- S3 bucket 保持完全私有，只透過 **OAC（Origin Access Control）** 讓 CloudFront 讀
- CDN 快取，從台灣連過來也不會慢（`PriceClass_200` 含亞洲節點）

### SPA 路由的關鍵設定

Angular 的 `/kindergartens` 這種路徑在 S3 上**不存在對應的檔案**，
直接連會拿到 403/404。所以模板裡設了 `CustomErrorResponses`：
把 403 和 404 都改成回傳 `200` + `/index.html`，讓 Angular 自己接手路由。

### 快取策略

| 檔案 | Cache-Control | 原因 |
|---|---|---|
| `main-XXXX.js`、`styles-XXXX.css` | `max-age=31536000, immutable` | 檔名帶 hash，內容變檔名就變，可以永久快取 |
| `index.html` | `no-cache` | 一定要每次重新抓，否則使用者會拿到舊 HTML 指向已刪掉的舊 js |

## 部署（後端）

第一次：

```powershell
cd aws
Copy-Item deploy.config.example.ps1 deploy.config.ps1   # 然後填入 DbPassword
.\deploy.ps1
```

之後改了 `src/app.py`，重跑同一行就會更新：

```powershell
.\deploy.ps1
```

`deploy.ps1` 做的事：

1. 用 AWS CLI 查出 RDS 的 VPC / 子網 / Security Group（不用手填 ID）
2. `pip install` 依賴 + 複製 `src/*.py` → 打包成 `lambda.zip`
   （注意是**平鋪複製**，所以 Lambda 的模組都要放在 `src/` 根層，不能用子目錄）
3. 上傳到 S3 bucket `ntpc-kg-artifacts-<你的帳號ID>`（沒有就自動建）
4. `aws cloudformation deploy` 建立或更新 stack `ntpc-kg-api`
5. 印出 API 網址

第一次部署（或改了 `db/migrations/`）之後別忘了建表：

```powershell
python ..\tools\migrate.py
```

## API

Base URL：部署完成後由 `deploy.ps1` 印出（目前為
`https://e86tz73y7h.execute-api.us-east-1.amazonaws.com`）。

| Method | Path | 登入 | 說明 |
|---|---|---|---|
| GET | `/api/health` | 免 | 健康檢查，會真的 ping 一次 DB。回 `{"ok":true,"total":6747}` |
| GET | `/api/counties` | 免 | 縣市清單 |
| GET | `/api/academic-years` | 免 | 學年度清單（目前只有 114） |
| GET | `/api/kindergartens` | 免 | 幼兒園主查詢 |
| GET | `/api/punishments` | 免 | 裁罰紀錄查詢（縣市/鄉鎮/名稱/日期/罰鍰 + 分頁） |
| GET | `/api/kindergartens/{id}/punishments` | 免 | 單一幼兒園的裁罰紀錄 |
| POST | `/api/reports/drafts` | 免 | 建立回報草稿 + 寄出 Email 驗證碼 |
| POST | `/api/reports/drafts/{id}/attachments/presign` | 免 | 取得 S3 直傳網址 |
| POST | `/api/reports/drafts/{id}/attachments` | 免 | 登錄已上傳的附件 |
| POST | `/api/reports/drafts/{id}/otp/verify` | 免 | 驗證碼正確才正式成案 |
| POST | `/api/reports/drafts/{id}/otp/resend` | 免 | 重寄驗證碼 |
| GET | `/api/reports/{token}` | 免 | 回報進度追蹤（憑 token，不含個資） |
| GET | `/api/secure/me` | **要** | 我是誰 / 我能看哪個範圍 |
| GET | `/api/secure/kindergartens` | **要** | 同主查詢，但縣市鎖在權限內，並帶出風險指數 |
| GET | `/api/secure/punishments` | **要** | 同裁罰查詢，但縣市鎖在權限範圍內 |
| GET | `/api/secure/reports` | **要** | 家長回報清單（依縣市過濾） |
| GET | `/api/secure/reports/summary` | **要** | 各狀態件數（dashboard badge） |
| GET | `/api/secure/reports/{id}` | **要** | 案件詳情（含附件與訊息串） |
| POST | `/api/secure/reports/{id}/messages` | **要** | 回覆家長 / 內部備註 |
| PATCH | `/api/secure/reports/{id}` | **要** | 變更狀態 / 指派承辦 |
| GET | `/api/secure/kindergartens/{id}/risk` | **要** | 風險評估（placeholder） |

> 完整的 request / response 欄位、錯誤碼與 TypeScript 型別在
> 專案根目錄的 [`API_SPEC.md`](../API_SPEC.md)，前端請以那份為準。

`/api/kindergartens` 的查詢參數：

| 參數 | 預設 | 說明 |
|---|---|---|
| `county` | 全部 | 縣市名稱，例如 `新北市` |
| `name` | 全部 | 園所名稱關鍵字，用 `LIKE %關鍵字%` |
| `ownership` | 全部 | `公立` 或 `私立` |
| `academicYear` | 全部 | 選填，例如 `114`。目前資料只有 114，此參數僅為相容保留 |
| `page` | 1 | 第幾頁 |
| `pageSize` | 20 | 每頁筆數，上限 100 |
| `sortBy` | `id` | 只接受白名單欄位 |
| `sortDir` | `asc` | `asc` / `desc` |

`/api/punishments` 的查詢參數：

| 參數 | 預設 | 說明 |
|---|---|---|
| `county` | 全部 | 縣市名稱，例如 `新北市` |
| `district` | 全部 | 鄉鎮市區，例如 `板橋區` |
| `name` | 全部 | 園所名稱關鍵字，用 `LIKE %關鍵字%` |
| `hasFine` | `false` | `true` 只回有罰鍰金額者 |
| `dateFrom` | 無 | 處分日期起，`YYYY-MM-DD` |
| `dateTo` | 無 | 處分日期迄，`YYYY-MM-DD` |
| `page` | 1 | 第幾頁 |
| `pageSize` | 20 | 每頁筆數，上限 100 |
| `sortBy` | `punish_date` | 白名單：`punish_date`/`fine_amount`/`school_name`/`district`/`id` |
| `sortDir` | `desc` | `asc` / `desc` |

回應：

```json
{
  "items": [
    {
      "id": 68000,
      "academic_year": "114",
      "code": "011K02",
      "school_name": "新北市私立溫特爾幼兒園",
      "ownership": "私立",
      "county": "新北市",
      "district": "三峽區",
      "address": "[237]新北市三峽區龍埔里5鄰三樹路336號1、2、3樓",
      "phone": "(02)26718181"
    }
  ],
  "total": 1108,
  "page": 1,
  "pageSize": 20,
  "academicYear": null
}
```

`/api/punishments` 回應（`totalFine` 為符合條件的罰鍰總額；已停業/查無的學校
`kindergarten_id`、`address` 會是 `null`）：

```json
{
  "items": [
    {
      "id": 123,
      "kindergarten_id": 68078,
      "county": "新北市",
      "district": "板橋區",
      "school_name": "新北市私立福音幼兒園",
      "ownership": "私立",
      "op_status": "正常",
      "punish_date": "2024-06-13",
      "school_name_at_time": "新北市私立福音幼兒園",
      "doc_no": "新北府教幼字第11311207455號",
      "legal_basis": "幼兒教育及照顧法 第52條…",
      "violated_rule": "…",
      "person": "負責人：○○○",
      "content": "罰鍰：600,000 元",
      "fine_amount": 600000,
      "address": "[220]新北市板橋區福丘里4鄰民族路8號2樓",
      "phone": "(02)29518767"
    }
  ],
  "total": 328,
  "totalFine": 24721000,
  "page": 1,
  "pageSize": 20,
  "county": "新北市"
}
```

`/api/kindergartens/{id}/punishments` 回應：

```json
{
  "kindergarten": {
    "id": 68078, "school_name": "新北市私立福音幼兒園",
    "county": "新北市", "district": "板橋區",
    "address": "[220]…", "phone": "(02)…"
  },
  "records": [ { "punish_date": "2024-06-13", "doc_no": "…", "fine_amount": 600000, "content": "罰鍰：600,000 元", "…": "…" } ],
  "count": 5,
  "totalFine": 1020000
}
```

### 資料小知識

- `county` 原始值長得像 `[01]新北市`、`[33]臺北市`、`[40]臺北市`。
  同一個縣市有多組代碼，所以 API 一律用 `]` 之後的部分當顯示名稱並合併。
- **kindergarten 表現在只保留最新學年度（114）一份，共 6,747 列、等同「一校一列」。**
  （原本 104～114 共 11 個學年度、74,628 列已刪除，只留 114。）所以查詢
  不再需要帶學年度，`id` 也可直接當作「學校身分」使用。
- **裁罰紀錄放在 `kindergarten_punishment` 表**，來源為
  [全國教保資訊網—裁罰紀錄查詢](https://ap.ece.moe.edu.tw/webecems/punishSearch.aspx)
  （目前只爬新北市，共 184 所學校、354 筆紀錄）。透過外鍵
  `kindergarten_id → kindergarten(id)`（`ON DELETE SET NULL`）串接；
  已停業（`廢止設立許可`）或查無對應的學校，`kindergarten_id` 為 `NULL`。
  爬蟲與載入腳本在專案根目錄的 `scraper/`（`scrape.py` 爬取、`load_db.py` 建表載入）。
- **家長回報相關的 5 張表**（`parent_report`、`parent_report_attachment`、
  `parent_report_message`、`staff_profile`、`risk_score_current`）由
  `db/migrations/*.sql` 定義，用 `python tools/migrate.py` 套用，
  已套用的版本記在 `schema_migration` 表。詳見下面「家長回報系統」。

## 家長回報系統

家長在公開頁面填表 → 收 Email 驗證碼 → 驗證通過才正式成案 → 政府人員在
`/admin/dashboard` 看到清單、回覆家長、變更狀態；家長憑一組 token 連結追蹤進度。

```
家長（公開頁面）                                     政府人員（需登入）
   │ 填表 + 選圖片                                        │
   ▼                                                      │
POST /api/reports/drafts ──► parent_report                │
   │  status = pending_verification（政府端看不到）        │
   │                                                      │
   │ presign ──► 瀏覽器直傳 S3（不經過 API Gateway）      │
   │                                                      │
   ▼ 輸入信中的 6 位數驗證碼                              │
POST .../otp/verify ──► status = submitted ───────────────┤ GET /api/secure/reports
   │  產生 tracking_token、寄出追蹤連結                    │ GET /api/secure/reports/{id}
   ▼                                                      │ POST .../messages（回覆）
GET /api/reports/{token} ◄────────────────────────────────┤ PATCH（調查中/完畢/不受理）
   顯示 steps、政府回覆、自己的附件                        │
```

### 資料表

| 表 | 用途 |
|---|---|
| `parent_report` | 主表。未驗證的草稿也在這裡，用 `status='pending_verification'` 隔離 |
| `parent_report_attachment` | 附件（只存 S3 key 與 metadata，檔案本身在 S3） |
| `parent_report_message` | 訊息串：政府回覆 / 內部備註 / 狀態變更稽核，一張表全包 |
| `staff_profile` | 公務人員檔案（登入時自動建檔，提供回覆時的署名） |
| `risk_score_current` | 風險指數（目前是 placeholder 假分數） |

DDL 在 `db/migrations/`，用這行套用（會自己開 SSH tunnel）：

```powershell
python tools/migrate.py           # 套用尚未執行的 migration
python tools/migrate.py --status  # 只看狀態
```

已套用的檔案記在 `schema_migration` 表；每個檔案都寫成 `CREATE TABLE IF NOT EXISTS`，
重跑是安全的。

### 幾個刻意的設計

**未驗證的草稿放在同一張表。** 用 `status` 隔離而不是另開一張 pending 表，
政府端每一支查詢都排除 `pending_verification`，效果等同「驗證後才進政府資料庫」，
但少一張表、少一次搬資料。

**驗證碼不存明碼。** 只存 `HMAC-SHA256(code, OTP_PEPPER)`，pepper 由
CloudFormation 的 NoEcho 參數帶入。錯 5 次草稿作廢，TTL 10 分鐘。

**時間一律 UTC naive。** RDS 的 `time_zone` 是 UTC（`tools/migrate.py` 每次跑都會
順手印出來確認），程式端用 `datetime.utcnow()`，只有輸出 JSON 時才補上 `Z`。
前端負責轉台北時間。

**追蹤 token 是能力憑證（capability）。** `secrets.token_urlsafe(32)`，
任何人拿到連結就能看該案進度，所以追蹤 API **不回傳回報人姓名與 email**，
政府端清單也只給遮蔽版（`p*****@example.com`），完整 email 只出現在單筆詳情。

**跨縣市一律回 404。** 政府人員讀到別縣市的案號時回 404 而不是 403，
避免洩漏「這個案號存在」。縣市判定用 token 的 `custom:county`，不看前端參數。

### 寄信：SES v2 + PrivateLink

Lambda 在沒有 NAT 的 VPC 裡，本來連不到 SES。解法是加一個 interface VPC endpoint：

```
Lambda（VPC 內）──443──► com.amazonaws.us-east-1.email（endpoint ENI）──► SES
```

`PrivateDnsEnabled: true`，所以 boto3 不用任何特殊設定，照平常呼叫就會走內網。
費用約 $7/月，比 NAT Gateway（約 $32/月起）便宜。

**目前 SES 還在 sandbox**（`aws sesv2 get-account` 的 `ProductionAccessEnabled: false`），
限制是：每天 200 封、每秒 1 封，而且**收件人也必須是已驗證的地址**。所以：

```powershell
# 把要當「家長」的信箱加進已驗證清單，然後去該信箱點確認連結
aws sesv2 create-email-identity --email-identity you@example.com --region us-east-1
aws sesv2 get-email-identity   --email-identity you@example.com --region us-east-1
```

`MAIL_MODE` 有兩種（在 `deploy.config.ps1` 設定）：

| 值 | 行為 |
|---|---|
| `dev`（預設） | 不寄信，驗證碼印到 CloudWatch log，並**直接回在 API response 的 `devOtp`** |
| `ses` | 真的寄。`MailFrom` 必須是已驗證地址，否則 deploy.ps1 會直接擋下來 |

寄信失敗（例如收件人未驗證）只會寫 log 並回 `emailed: false`，**不會讓整個回報流程失敗**。
demo 時建議先留在 `dev`，這樣任何信箱都能走完流程。

### 附件：presigned POST 直傳 S3

為什麼不讓瀏覽器把檔案 POST 給我們的 API：API Gateway payload 上限 10MB，
而規格允許 5 檔 × 5MB = 25MB，一定會爆。所以：

1. 前端呼叫 `.../attachments/presign`，Lambda 回一組 presigned POST（url + fields）
2. 瀏覽器直接 POST 到 S3（`file` 欄位放最後，成功是 **204**）
3. 前端呼叫 `.../attachments` 把 key 登錄到 DB

policy 裡帶了 `content-length-range 1..5MB` 與 `Content-Type` 條件，
所以 5MB 上限是**由 S3 強制**的，不是靠前端自律。
物件 key 用 `reports/{draftId}/{uuid}.{ext}`，不採用使用者檔名（避免路徑穿越）；
登錄時會檢查 key 前綴，防止把別人的檔案掛到自己的案件上。

bucket（`ntpc-kg-report-attachments-<帳號ID>`）完全私有，
家長與公務人員看附件都是靠 Lambda 簽出來的 15 分鐘短效 GET 連結。

> 簽名是純本機的雜湊運算，**不需要對外網路**，所以不用為 S3 另開 VPC endpoint。
> 之後若要用 `HeadObject` 驗證檔案真的存在，得補一個 S3 Gateway Endpoint（免費）。

**附件必須在驗證碼送出前登錄完成**，成案之後再打附件端點會得到
`DRAFT_ALREADY_VERIFIED`。

### 風險指數是 placeholder

演算法還沒定案。目前的作法讓前端可以先把畫面做完：

- 5 個維度的 `key` / `label` / `weight` 已定案（`src/risk.py` 的 `DIMENSIONS`），
  分數存在 `risk_score_current.dimensions`（JSON），換指標不用改 schema
- `risk_level` 的 80 / 60 閾值寫在後端，前端只依 `riskLevel` 上色
- API 一律回 `isPlaceholder: true`，畫面可以標示「示意資料」

灌假分數（分數由 id 雜湊決定所以每次一樣，且刻意涵蓋紫／紅／預設三種）：

```powershell
python tools/seed_risk_placeholder.py            # 只灌新北市（1108 間）
python tools/seed_risk_placeholder.py --all      # 全國
python tools/seed_risk_placeholder.py --clear    # 清掉假資料
```

接上真模型後，只要有東西去寫 `risk_score_current`，API 與前端都不用改。

### 測試

```powershell
cd aws
python local_test_reports.py                 # 63 項檢查，不寄信、不部署
python local_test_reports.py --bucket ntpc-kg-report-attachments-135989901461   # 連 presign 一起測
python local_test_reports.py --keep          # 保留測試資料以便手動看
```

涵蓋：驗證碼錯誤／逾時／重複驗證、未驗證草稿不出現在政府端、跨縣市讀取回 404、
家長看不到內部備註、不受理只有兩個節點、附件 key 綁定檢查、風險排序與公開端點不外洩風險欄位。

測完的資料清理：

```powershell
python tools/cleanup_test_reports.py         # 刪 *@example.com 的案件
aws s3 rm s3://ntpc-kg-report-attachments-135989901461/reports/ --recursive
```

## 登入機制（Cognito）

給公務人員用的帳號驗證。**幼兒園查詢（`/api/kindergartens`）維持公開，不需登入**；
只有 `/api/secure/*` 這些新功能要驗證。

```
Angular 登入 ──→ Cognito User Pool ──→ 取得 ID token（內含 custom:county）
                                                │
Angular 呼叫 API 時帶 Authorization: Bearer ────┘
        ▼
API Gateway JWT Authorizer   ← 驗簽章 / 驗過期，不通過直接 401（Lambda 不會被叫到）
        ▼
Lambda：從已驗證的 claims 取 county，強制加進 WHERE
        ▼
RDS
```

### 為什麼用 API Gateway 內建的 JWT Authorizer

因為我們的 Lambda 在**沒有 NAT 的 VPC 裡，連不到外網**。
如果要在 Lambda 內自己驗 JWT，得去下載 Cognito 的 JWKS 公鑰 —— 會連不出去，
得多開一個 NAT Gateway（要錢）。交給 API Gateway 驗就完全避開這個問題，
而且 Lambda 收到的 claims 是保證驗過的，可以直接信任。

### 權限模型

| 帳號類型 | 設定 | 可見範圍 |
|---|---|---|
| 縣市人員 | `custom:county = 新北市` | 只有新北市 |
| 主管機關 | 加入 `admin` 群組 | 全國，也可指定單一縣市檢視 |

關鍵在 `auth.py` 的 `scoped_county()`：**一般人員送來的 `county` 參數會被完全忽略**，
一律用 token 裡的值。所以改網址、改 DevTools 都拿不到別的縣市資料。
前端的 route guard 只是介面體驗，不是安全機制。

`staff_profile` 這張表只負責「顯示用的名稱」與 token 沒帶 county 時的 fallback，
**授權判斷不會下放到可被 SQL 改動的表**。

### 建立帳號

帳號無法自行註冊（`AllowAdminCreateUserOnly = true`），一律由管理者建立：

```powershell
cd aws
.\deploy-auth.ps1                                          # 第一次：建立 User Pool

.\create-user.ps1 -Username ntpc_staff -County 新北市 -Agency "新北市政府教育局"
.\create-user.ps1 -Username moe_admin  -Admin -Agency "教育部"   # 看全國
```

沒帶 `-Password` 會自動產一組並印出來。腳本會用 `--permanent` 設定密碼，
避免首次登入卡在 `NEW_PASSWORD_REQUIRED` 挑戰。

建好 User Pool 後要重跑一次 `.\deploy.ps1`，API 才會掛上 authorizer
（`deploy.ps1` 會自動偵測 auth stack 是否存在，輸出的 `AuthEnabled` 會變成 `yes`）。

### 三個踩過的坑

**1. custom attribute 是一次性決定的。** Cognito 的自訂屬性建立後不能改名、不能刪除，
而修改 `auth-template.yaml` 的 `Schema` 會**替換整個 User Pool、所有帳號消失**。
目前開了 `custom:county` 和 `custom:agency`，要加欄位前先想清楚。
相對地「群組」隨時可以加，所以角色類的需求優先用群組。

**2. 一定要用 ID token，不能用 access token。**
`custom:county` 只會出現在 ID token 裡，access token 沒有。
模板裡 authorizer 的 `Audience` 設成 app client id，效果剛好是只接受 ID token。

**3. 受保護路由不能用 `ANY`。**
`ANY /api/secure/{proxy+}` 會連瀏覽器的 CORS 預檢（`OPTIONS`）一起吃掉，
而預檢請求不帶 Authorization header，會被 authorizer 判 401，
結果所有跨網域呼叫全部失敗。所以要改成明列 `GET` / `POST` / `PUT` / `PATCH` / `DELETE`，
把 `OPTIONS` 留給 API Gateway 的 `CorsConfiguration` 自動回應。
（加家長回報的狀態變更時就踩到這點：新增 method 要同時補 route 與 `CorsConfiguration`
的 `AllowMethods`，少一邊就會 404 或被 CORS 擋。）

### 新增受保護功能的作法

照 `app.py` 的 `handle_secure_kindergartens()` 或 `reports_admin.py` 的樣子寫：

```python
def list_something(cur, qs, identity):
    county = auth.scoped_county(identity)
    if county is None and not identity["isAdmin"]:
        return error(403, "FORBIDDEN", "此帳號未設定縣市")
    # 之後把 county 加進 WHERE 即可
```

然後在 `app.py` 的 `route_secure()` 加一行。因為 `/api/secure/{proxy+}` 已經涵蓋
所有子路徑，**新增受保護端點不需要動 CloudFormation**；反之公開端點是逐條列出的，
一定要在 `template.yaml` 明確加一條 route（這是刻意的，避免不小心把東西弄成公開）。

前端加在 `SecureApiService` 並掛到 `/dashboard` 底下的子路由。

## 本機測試（不用部署）

改完 `src/*.py` 後，可以先在本機驗證再部署：

```powershell
cd aws
python local_test.py           # 幼兒園 / 裁罰 / 縣市 端點
python local_test_reports.py   # 家長回報全流程 + 政府端 + 風險（63 項檢查）
```

它們會自己開 SSH tunnel（透過 bastion EC2）連到私有的 RDS，把 `app.handler`
當普通 Python function 呼叫，跑過所有端點並印出結果，`local_test.py` 還會
`EXPLAIN` 確認索引有生效。帳密從 `deploy.config.ps1` 讀，腳本本身不含密碼。

> `local_test_reports.py` 會偽造 Cognito claims（不需要真的登入），
> 也會測「別縣市的帳號讀不到本縣市案件」這種權限邊界。

## 測試（已部署的 API）

```powershell
$base = "https://e86tz73y7h.execute-api.us-east-1.amazonaws.com"
curl "$base/api/health"
curl "$base/api/counties"
curl "$base/api/kindergartens?county=新北市&name=非營利&pageSize=5"
curl "$base/api/punishments?county=新北市&hasFine=true&sortBy=fine_amount&sortDir=desc&pageSize=5"
curl "$base/api/punishments?county=新北市&district=板橋區&dateFrom=2025-01-01"
curl "$base/api/kindergartens/68078/punishments"

# 家長回報（dev mode 下回應會直接帶 devOtp，可以一路手動走完）
curl -X POST "$base/api/reports/drafts" -H "Content-Type: application/json" `
  -d '{\"reporterEmail\":\"you@example.com\",\"kindergartenId\":67882,\"content\":\"測試\"}'
curl -X POST "$base/api/reports/drafts/1/otp/verify" -H "Content-Type: application/json" `
  -d '{\"code\":\"123456\"}'
curl "$base/api/reports/<trackingToken>"
```

看 Lambda 的錯誤訊息：

```powershell
aws logs tail /aws/lambda/ntpc-kg-api --region us-east-1 --follow
```

## 拆掉全部資源

```powershell
# 前端（要先清空 bucket，否則 S3 bucket 刪不掉）
aws s3 rm s3://ntpc-kg-web-135989901461 --recursive
aws cloudformation delete-stack --stack-name ntpc-kg-api-web --region us-east-1

# 後端
aws cloudformation delete-stack --stack-name ntpc-kg-api --region us-east-1

# 登入（注意：會連帳號一起刪掉）
aws cloudformation delete-stack --stack-name ntpc-kg-api-auth --region us-east-1
```

（RDS 不在這些 stack 裡，不會被刪。Lambda 程式碼的 artifact bucket
`ntpc-kg-artifacts-<帳號ID>` 也要另外手動清。
家長回報的附件 bucket `ntpc-kg-report-attachments-<帳號ID>` 設了
`DeletionPolicy: Retain`，**刪 stack 不會刪掉裡面的檔案**，這是刻意的：
附件是案件證據。要真的清掉得手動 `aws s3 rb --force`。
資料表也不會被刪，因為 RDS 不屬於任何 stack。）

## 已知的簡化 / 待改進

目前是為了「先跑起來」而做的最小架構，正式版建議補上：

1. **資料庫密碼與 OTP pepper 目前是 Lambda 環境變數。**
   方便但不理想。正式做法是放 Secrets Manager，或改用 RDS IAM 認證。
   注意：Lambda 在沒有 NAT 的 VPC 裡要讀 Secrets Manager，需要額外開
   Interface VPC Endpoint（要收費）。
2. **公開端點沒有身分驗證。** `/api/kindergartens` 是刻意公開的（教育部公開資料）。
   家長回報的公開端點靠 Email 驗證碼把關：沒通過驗證的內容不會進政府端清單。
3. **家長回報沒有濫用防制。** demo 階段刻意省略：沒有 IP／Email 的頻率限制、
   沒有 captcha、重寄驗證碼沒有次數與間隔限制。正式上線至少要補
   「同 Email／同 IP 每小時件數上限」與 API Gateway throttling。
   （驗證碼本身仍有 10 分鐘 TTL 與錯 5 次作廢。）
4. **附件沒有病毒掃描，也沒有驗證檔案真的上傳成功。** 前端說有就登錄。
   正式版可在登錄時 `HeadObject` 確認（需要 S3 Gateway Endpoint），
   並考慮加掃毒（例如 S3 事件觸發另一支 Lambda）。
5. **SES 還在 sandbox。** 只能寄給已驗證的信箱、每天 200 封。要對真實家長開放
   必須先申請 production access。
6. **CORS 開放 `*`**（API Gateway 與附件 bucket 都是）。現在同時要讓 CloudFront
   網址和本機 `localhost:4200` 都能呼叫，所以先開放。正式上線應改成只允許
   CloudFront 網域（附件 bucket 用 `UploadAllowedOrigins` 參數就能改）。
7. **風險指數是假資料。** 見上面「風險指數是 placeholder」。
8. **每個 Lambda 冷啟動都要重連 MySQL。** 流量大時可考慮 RDS Proxy 管理連線池。
9. **沒有自訂網域。** 目前用 `*.cloudfront.net`。若要 `xxx.example.com`，
   需要 Route 53 + ACM 憑證（憑證必須簽在 us-east-1，剛好我們就在這個 region）。
10. **Cognito 沒有開 MFA、也沒有密碼過期政策。** 正式給公務機關用應該補上。
11. **沒有真正的刪除／保留政策。** 家長回報含個資，正式版需要訂保存年限與
    刪除流程（例如結案 N 年後自動清除 email 與附件）。

