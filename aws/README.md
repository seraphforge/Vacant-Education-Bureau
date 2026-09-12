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
    │  MySQL 3306（走 VPC 內網）
    ▼
RDS MySQL「my-mysql-db」     ← 資料庫，schema = readme，table = kindergarten
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
> 目前只需要連資料庫，所以沒差。之後如果 Lambda 需要呼叫外部 API 或
> Bedrock，要再加 NAT Gateway 或 VPC Endpoint。

## 檔案

| 檔案 | 用途 |
|---|---|
| `template.yaml` | CloudFormation 模板，定義**後端 API** 資源 |
| `web-template.yaml` | CloudFormation 模板，定義**前端網站託管**資源（S3 + CloudFront） |
| `src/app.py` | Lambda 主程式（路由 + SQL 查詢） |
| `src/requirements.txt` | Lambda 依賴（只有 PyMySQL，純 Python 不需編譯） |
| `build_zip.py` | 把 `build/` 打包成 `lambda.zip` |
| `local_test.py` | 本機測試 handler（開 SSH tunnel 連 RDS，不用部署） |
| `deploy.ps1` | 一鍵部署**後端** |
| `deploy-web.ps1` | 一鍵部署**前端**（build + 上傳 + 清快取） |
| `deploy.config.ps1` | 你的設定與**資料庫密碼**（已 gitignore，不會進版控） |
| `deploy.config.example.ps1` | 給隊友抄的範本 |

兩個 stack 是分開的，前端重新部署不會動到 API：

| Stack | 內容 |
|---|---|
| `ntpc-kg-api` | Lambda、API Gateway、Security Group、IAM Role |
| `ntpc-kg-api-web` | S3 網站 bucket、CloudFront distribution |

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
3. 上傳到 S3 bucket `ntpc-kg-artifacts-<你的帳號ID>`（沒有就自動建）
4. `aws cloudformation deploy` 建立或更新 stack `ntpc-kg-api`
5. 印出 API 網址

## API

Base URL：部署完成後由 `deploy.ps1` 印出（目前為
`https://e86tz73y7h.execute-api.us-east-1.amazonaws.com`）。

| Method | Path | 說明 |
|---|---|---|
| GET | `/api/health` | 健康檢查，會真的 ping 一次 DB。回 `{"ok":true,"total":74628}` |
| GET | `/api/counties` | 縣市清單（已把 `[01]` 這種前綴去掉並合併同名縣市） |
| GET | `/api/academic-years` | 學年度清單（114 → 104） |
| GET | `/api/kindergartens` | 主查詢 |

`/api/kindergartens` 的查詢參數：

| 參數 | 預設 | 說明 |
|---|---|---|
| `county` | 全部 | 縣市名稱，例如 `新北市` |
| `name` | 全部 | 園所名稱關鍵字，用 `LIKE %關鍵字%` |
| `ownership` | 全部 | `公立` 或 `私立` |
| `academicYear` | 資料庫最新學年度（114） | 例如 `113` |
| `page` | 1 | 第幾頁 |
| `pageSize` | 20 | 每頁筆數，上限 100 |
| `sortBy` | `id` | 只接受白名單欄位 |
| `sortDir` | `asc` | `asc` / `desc` |

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
  "total": 6747,
  "page": 1,
  "pageSize": 20,
  "academicYear": "114"
}
```

### 資料小知識

- `county` 原始值長得像 `[01]新北市`、`[33]臺北市`、`[40]臺北市`。
  同一個縣市有多組代碼，所以 API 一律用 `]` 之後的部分當顯示名稱並合併。
- 每所幼兒園**每個學年度都有一列**（104～114 共 11 個學年度、74,628 列）。
  所以查詢一定要帶學年度，否則同一間學校會出現 11 次。預設用最新學年度。

## 本機測試（不用部署）

改完 `src/app.py` 後，可以先在本機驗證再部署：

```powershell
cd aws
python local_test.py
```

它會自己開 SSH tunnel（透過 bastion EC2）連到私有的 RDS，把 `app.handler`
當普通 Python function 呼叫，跑過所有端點並印出結果，還會 `EXPLAIN` 確認索引有生效。
帳密從 `deploy.config.ps1` 讀，腳本本身不含密碼。

## 測試（已部署的 API）

```powershell
$base = "https://e86tz73y7h.execute-api.us-east-1.amazonaws.com"
curl "$base/api/health"
curl "$base/api/counties"
curl "$base/api/kindergartens?county=新北市&name=非營利&pageSize=5"
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
```

（RDS 不在這些 stack 裡，不會被刪。Lambda 程式碼的 artifact bucket
`ntpc-kg-artifacts-<帳號ID>` 也要另外手動清。）

## 已知的簡化 / 待改進

目前是為了「先跑起來」而做的最小架構，正式版建議補上：

1. **資料庫密碼目前是 Lambda 環境變數。**
   方便但不理想。正式做法是放 Secrets Manager，或改用 RDS IAM 認證。
   注意：Lambda 在沒有 NAT 的 VPC 裡要讀 Secrets Manager，需要額外開
   Interface VPC Endpoint（要收費）。
2. **API 沒有任何身分驗證，任何人拿到網址都能查。** 目前資料是公開資料所以可接受；
   之後若加入財報、家長回報等非公開資料，必須加上 Cognito 或 Lambda Authorizer。
3. **CORS 開放 `*`。** 現在同時要讓 CloudFront 網址和本機 `localhost:4200` 都能呼叫，
   所以先開放。正式上線應改成只允許 CloudFront 網域。
4. **每個 Lambda 冷啟動都要重連 MySQL。** 流量大時可考慮 RDS Proxy 管理連線池。
5. **沒有自訂網域。** 目前用 `*.cloudfront.net`。若要 `xxx.example.com`，
   需要 Route 53 + ACM 憑證（憑證必須簽在 us-east-1，剛好我們就在這個 region）。

