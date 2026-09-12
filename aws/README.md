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
RDS MySQL「my-mysql-db」     ← 資料庫，schema = moe，table = kindergarten
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
| `template.yaml` | CloudFormation 模板，定義所有 AWS 資源 |
| `src/app.py` | Lambda 主程式（路由 + SQL 查詢） |
| `src/requirements.txt` | Lambda 依賴（只有 PyMySQL，純 Python 不需編譯） |
| `build_zip.py` | 把 `build/` 打包成 `lambda.zip` |
| `deploy.ps1` | 一鍵部署腳本 |
| `deploy.config.ps1` | 你的設定與**資料庫密碼**（已 gitignore，不會進版控） |
| `deploy.config.example.ps1` | 給隊友抄的範本 |

## 部署

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

## 測試

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
aws cloudformation delete-stack --stack-name ntpc-kg-api --region us-east-1
```

（S3 artifact bucket 要另外手動清空刪除。RDS 不在這個 stack 裡，不會被刪。）

## 已知的簡化 / 待改進

目前是為了「先跑起來」而做的最小架構，正式版建議補上：

1. **資料庫密碼目前是 Lambda 環境變數。**
   方便但不理想。正式做法是放 Secrets Manager，或改用 RDS IAM 認證。
   注意：Lambda 在沒有 NAT 的 VPC 裡要讀 Secrets Manager，需要額外開
   Interface VPC Endpoint（要收費）。
2. **API 沒有任何身分驗證，任何人拿到網址都能查。** 目前資料是公開資料所以可接受；
   之後若加入財報、家長回報等非公開資料，必須加上 Cognito 或 Lambda Authorizer。
3. **CORS 開放 `*`。** 上線前應改成前端實際網域。
4. **每個 Lambda 冷啟動都要重連 MySQL。** 流量大時可考慮 RDS Proxy 管理連線池。
5. **前端目前只在本機跑。** 之後可放 S3 + CloudFront 做靜態網站。
