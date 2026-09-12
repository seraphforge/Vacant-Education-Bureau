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
| `auth-template.yaml` | CloudFormation 模板，定義**登入**資源（Cognito User Pool） |
| `src/app.py` | Lambda 主程式（路由 + SQL 查詢） |
| `src/requirements.txt` | Lambda 依賴（只有 PyMySQL，純 Python 不需編譯） |
| `build_zip.py` | 把 `build/` 打包成 `lambda.zip` |
| `local_test.py` | 本機測試 handler（開 SSH tunnel 連 RDS，不用部署） |
| `deploy.ps1` | 一鍵部署**後端** |
| `deploy-web.ps1` | 一鍵部署**前端**（build + 上傳 + 清快取） |
| `deploy-auth.ps1` | 一鍵部署**登入**（Cognito User Pool） |
| `create-user.ps1` | 手動建立一個公務人員帳號 |
| `deploy.config.ps1` | 你的設定與**資料庫密碼**（已 gitignore，不會進版控） |
| `deploy.config.example.ps1` | 給隊友抄的範本 |

兩個 stack 是分開的，前端重新部署不會動到 API：

| Stack | 內容 |
|---|---|
| `ntpc-kg-api` | Lambda、API Gateway、Security Group、IAM Role |
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
3. 上傳到 S3 bucket `ntpc-kg-artifacts-<你的帳號ID>`（沒有就自動建）
4. `aws cloudformation deploy` 建立或更新 stack `ntpc-kg-api`
5. 印出 API 網址

## API

Base URL：部署完成後由 `deploy.ps1` 印出（目前為
`https://e86tz73y7h.execute-api.us-east-1.amazonaws.com`）。

| Method | Path | 登入 | 說明 |
|---|---|---|---|
| GET | `/api/health` | 免 | 健康檢查，會真的 ping 一次 DB。回 `{"ok":true,"total":74628}` |
| GET | `/api/counties` | 免 | 縣市清單（22 組） |
| GET | `/api/academic-years` | 免 | 學年度清單（114 → 104） |
| GET | `/api/kindergartens` | 免 | 主查詢 |
| GET | `/api/secure/me` | **要** | 我是誰 / 我能看哪個範圍 |
| GET | `/api/secure/kindergartens` | **要** | 同主查詢，但縣市鎖在權限範圍內 |

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

關鍵在 `app.py` 的 `scoped_county()`：**一般人員送來的 `county` 參數會被完全忽略**，
一律用 token 裡的值。所以改網址、改 DevTools 都拿不到別的縣市資料。
前端的 route guard 只是介面體驗，不是安全機制。

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
結果所有跨網域呼叫全部失敗。所以要改成明列 `GET` / `POST` / `PUT` / `DELETE`，
把 `OPTIONS` 留給 API Gateway 的 `CorsConfiguration` 自動回應。

### 新增受保護功能的作法

`app.py` 裡照 `handle_secure_kindergartens()` 的樣子寫：

```python
def handle_secure_reports(cur, qs, identity):
    county = scoped_county(identity, qs.get("county", ""))
    if county is None and not identity["isAdmin"]:
        return respond(403, {"message": "此帳號未設定縣市"})
    # 之後把 county 加進 WHERE 即可
```

然後在 `handler()` 的 `/api/secure/` 區塊加一行路由。前端加在
`SecureApiService` 並掛到 `/dashboard` 底下的子路由。

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

# 登入（注意：會連帳號一起刪掉）
aws cloudformation delete-stack --stack-name ntpc-kg-api-auth --region us-east-1
```

（RDS 不在這些 stack 裡，不會被刪。Lambda 程式碼的 artifact bucket
`ntpc-kg-artifacts-<帳號ID>` 也要另外手動清。）

## 已知的簡化 / 待改進

目前是為了「先跑起來」而做的最小架構，正式版建議補上：

1. **資料庫密碼目前是 Lambda 環境變數。**
   方便但不理想。正式做法是放 Secrets Manager，或改用 RDS IAM 認證。
   注意：Lambda 在沒有 NAT 的 VPC 裡要讀 Secrets Manager，需要額外開
   Interface VPC Endpoint（要收費）。
2. **公開端點沒有任何身分驗證，任何人拿到網址都能查。** `/api/kindergartens`
   是刻意公開的（教育部公開資料，且要給評審直接看）。非公開資料一律放
   `/api/secure/*`。
3. **公開端點沒有速率限制。** 可以在 API Gateway 加 throttling 或 usage plan。
4. **CORS 開放 `*`。** 現在同時要讓 CloudFront 網址和本機 `localhost:4200` 都能呼叫，
   所以先開放。正式上線應改成只允許 CloudFront 網域。
5. **每個 Lambda 冷啟動都要重連 MySQL。** 流量大時可考慮 RDS Proxy 管理連線池。
6. **沒有自訂網域。** 目前用 `*.cloudfront.net`。若要 `xxx.example.com`，
   需要 Route 53 + ACM 憑證（憑證必須簽在 us-east-1，剛好我們就在這個 region）。
7. **Cognito 沒有開 MFA、也沒有密碼過期政策。** 正式給公務機關用應該補上。

