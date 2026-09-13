# AWS 雲端架構圖產生 Prompt（簡報版）

## 先說結論：這個系統沒有主圖看起來那麼複雜

從簡報角度，核心架構其實只有：

```text
Angular 網站（CloudFront + S3）
        ↓
API Gateway → API Lambda → RDS MySQL
                    ├→ SES 寄信
                    ├→ S3 附件
                    └→ 輿情 Worker → 公開資料 / Bedrock / Comprehend

政府人員登入：Cognito
```

先前版本把 IAM、Security Group、Route Table、VPC Endpoint、每一條 API Route、
CloudFormation Stack 等部署細節全部放進同一張圖，因此資訊雖完整，卻不適合投影簡報。

**建議簡報只放 1 張核心架構圖；若需要說明特色，再加 2 張流程圖。**
網路與資安實作細節只有在技術審查時才放入附錄。

---

# Prompt A：一頁式核心架構圖（主要推薦）

請產生一張適合放入正式簡報的 **AWS 雲端架構總覽圖**，主題為：

> 幼兒園資訊查詢、家長回報與政府輿情分析平台

## 圖片規格

- 16:9 橫式簡報比例，建議 1920×1080 或 4K。
- 白色或極淺灰背景，高對比、留白充足。
- 使用 AWS 官方服務圖示，但不要塞入基礎設施細節。
- 所有文字使用繁體中文。
- 標題字大於 32 px，節點文字至少 24 px，箭頭標籤至少 20 px。
- 使用粗線箭頭，不交叉、不繞行；投影時必須能從後排看清楚。
- 每個方塊最多兩行字，不放長段落、程式名稱或完整 API 路徑。
- 將同類服務合併呈現，整張圖不超過 10 個 AWS 服務方塊。
- **不要畫成單一長橫條**：請把元件分成 2～3 排堆疊（上下分層），避免整張圖過寬、
  塞進投影片後字變得太小。核心架構用「使用者端 / 平台 / 資料與 AI 服務」三排；
  流程圖則把步驟折成兩排蛇形排列（例如 6 步拆成 3+3）。
- 輸出 SVG 或高解析 PNG，不要輸出程式碼截圖或 Mermaid 編輯器畫面。

> 已產出的圖片檔（Graphviz 繪製）：
> - `assets/architecture-overview.png`（三排堆疊的核心總覽）
> - `assets/parent-report-flow.png`（兩排蛇形的家長回報流程）
> - `assets/opinion-analysis-flow.png`（兩排蛇形的 AI 輿情分析流程）
>
> 原始 `.dot` 檔在 `assets/diagrams/`，重繪指令：
> `dot -Tpng -Gdpi=150 assets/diagrams/<name>.dot -o assets/<name>.png`

## 版面配置

採用由左至右的三欄式配置：

### 左欄：使用者

畫兩種使用者，但共用同一個瀏覽器入口：

1. `一般家長 / 民眾`
2. `政府承辦人員`

兩者連到 `Angular Web 應用程式`。

### 中欄：平台核心

依序排列以下元件：

1. `Amazon CloudFront + Amazon S3`
   - 副標：`Angular 前端網站`
   - 表示 CloudFront 對外提供網站，S3 儲存靜態檔案。

2. `Amazon API Gateway`
   - 副標：`統一 API 入口`

3. `AWS Lambda`
   - 副標：`查詢、回報與後台業務邏輯`

4. `Amazon RDS for MySQL`
   - 副標：`平台主要資料庫`

請把網站載入與 API 呼叫畫成兩條不同的資料流：

1. `使用者瀏覽器 → CloudFront + S3`，標示 `載入 Angular 網站`。
2. `使用者瀏覽器 → API Gateway → Lambda → RDS MySQL`，第一段標示 `HTTPS API`。

**不要**畫成 `CloudFront → API Gateway`。CloudFront 只提供前端靜態網站；Angular
載入後，是由使用者瀏覽器直接呼叫 API Gateway。

### 上方：政府登入

在 `政府承辦人員` 與 `API Gateway` 上方放置：

- `Amazon Cognito`
- 副標：`政府人員登入與縣市權限`

連線：

- `政府承辦人員瀏覽器 → Cognito`，標示 `登入`。
- `Cognito → 政府承辦人員瀏覽器`，標示 `ID Token`。
- `政府承辦人員瀏覽器 → API Gateway`，標示 `HTTPS API + Bearer ID Token`。
- `Cognito ⇢ API Gateway` 使用虛線關係，標示 `JWT issuer / audience 驗證依據`；
  這不是請求資料流，不要畫成 Cognito 主動把 Token 傳給 API Gateway。

一般家長不需要經過 Cognito。

### 右欄：延伸服務

從 `AWS Lambda` 分出兩條簡單支線：

1. `Amazon SES`
   - 副標：`Email 驗證碼`

2. `Amazon S3`
   - 副標：`家長回報附件`

附件另加一條從 `一般家長 / 民眾` 直接到附件 S3 的箭頭，標示：

`預簽網址直接上傳`

從 `AWS Lambda` 再連到：

3. `輿情分析 Worker（AWS Lambda）`
   - 副標：`非同步分析`

Worker 往右連到一個合併方塊：

4. `公開網路資訊 + Amazon Bedrock + Amazon Comprehend`
   - 副標：`資料蒐集、AI 摘要、情緒分析`

Worker 另以回程箭頭連回 RDS，標示 `儲存分析結果`。

## 視覺分組

只使用三個淡色背景區塊，不要畫大量巢狀框：

- `使用者端`
- `AWS Serverless 平台`
- `資料與 AI 服務`

可以在 AWS 平台區塊角落小字標示 `Region: us-east-1`，但不要在主圖畫
Availability Zone、Subnet、Route Table 或 Security Group。

## 圖下方重點標語

圖底部放三個短句，字體需清楚：

- `Serverless：依需求自動擴展`
- `權限控管：政府帳號依縣市授權`
- `AI 輔助：公開資訊僅作為人工複核線索`

## 主圖禁止出現的內容

為確保簡報清晰，請不要在這張主圖加入：

- IAM Role 或 IAM Policy
- CloudWatch Log Group
- Security Group 與連接埠規則
- VPC Endpoint
- NAT Gateway、Elastic IP、Internet Gateway
- Route Table、Subnet CIDR、Availability Zone
- CloudFormation Stack 或 logical resource name
- Lambda 記憶體、timeout、runtime
- 完整 API 路徑或路由清單
- S3 bucket 的完整帳號化名稱
- 程式檔名、handler、環境變數
- 每個服務的部署參數
- SQS 或 DynamoDB（本專案沒有使用）

主圖要讓非技術觀眾在 **10 秒內理解**：

> 網站由 AWS Serverless 架構提供，Lambda 連接主要資料庫，Cognito 管理政府登入，
> 並透過 SES、S3 與 AI Worker 完成家長回報及輿情分析。

---

# Prompt B：家長回報流程圖（可選，第 2 頁）

若簡報需要介紹「家長回報」功能，另外產生一張流程圖，不要塞回主架構圖。

## 圖片規格

- 16:9 橫式、白底、高對比、繁體中文。
- 使用 5 個大步驟，由左至右排列。
- 每步驟最多兩行說明，搭配 AWS 圖示。
- 節點文字至少 26 px，步驟編號必須醒目。

## 流程內容

1. `家長填寫回報`
   - 經 API Gateway 與 Lambda 建立草稿。

2. `Email 驗證`
   - Lambda 使用 Amazon SES 寄送 OTP 驗證碼。

3. `附件直接上傳`
   - Lambda 產生預簽網址。
   - 家長瀏覽器直接上傳至私有 Amazon S3，不經 API Gateway。

4. `驗證成功，正式成案`
   - 案件與附件資料寫入 Amazon RDS for MySQL。

5. `追蹤與政府處理`
   - 家長以追蹤連結查看進度。
   - 政府人員登入後回覆及更新案件狀態。

圖底部以醒目註記標示：

> Email 驗證降低匿名濫用；附件透過預簽網址直接上傳，不公開 S3 Bucket。

這張圖不要加入 IAM、VPC、NAT、資料表名稱或 API 路徑。

---

# Prompt C：AI 輿情分析流程圖（可選，第 3 頁）

若簡報需要介紹「輿情分析」特色，另外產生一張流程圖。

## 圖片規格

- 16:9 橫式、白底、高對比、繁體中文。
- 使用 6 個大步驟，由左至右排列。
- 將「一般 API」與「背景 Worker」用不同顏色區分。
- 節點文字至少 26 px，不放程式實作細節。

## 流程內容

1. `政府人員啟動分析`
2. `API 建立分析工作`
   - 工作狀態寫入 Amazon RDS for MySQL。
3. `非同步啟動 Lambda Worker`
   - API 不等待分析完成，立即回傳工作編號。
4. `蒐集公開資訊`
   - 政府公開資料、新聞 RSS、公開搜尋結果。
5. `AI 輔助分析`
   - Amazon Bedrock：內容歸屬判讀與摘要。
   - Amazon Comprehend：情緒分析。
6. `結果寫回 RDS，前端輪詢顯示`

圖中要清楚畫出：

- `API Lambda → 非同步 Worker`
- `Worker → 公開資訊 / Bedrock / Comprehend`
- `Worker → RDS`
- `前端 → API → RDS` 的進度查詢回路

圖底部放置醒目聲明：

> AI 結果僅是「需要人工關注的線索」，不是違法事實，也不得單獨作為裁處依據。

這張圖不要列出來源網址、模型完整 ID、robots.txt 設定、冷卻秒數、Lambda timeout、
資料表名稱或 IAM 權限。

---

# Prompt D：技術網路附錄（僅技術審查時使用）

只有當聽眾是 AWS 架構師、資安或維運人員時，才額外產生這一頁。不要放進一般提案簡報。

請畫一張簡化的 VPC 網路圖，只呈現下列關係：

- `API Lambda` 位於既有 VPC 子網內，能連線私有 `RDS MySQL:3306`，但沒有公網出口。
- API Lambda 透過 `SES Interface VPC Endpoint` 呼叫 Amazon SES。
- API Lambda 透過 `Lambda Interface VPC Endpoint` 非同步啟動 Opinion Worker。
- `Opinion Worker` 位於兩個私有 Worker Subnet，透過 `NAT Gateway` 對外存取公開來源、
  Amazon Bedrock 與 Amazon Comprehend。
- API Lambda 與 Worker 都能連 RDS。
- 家長瀏覽器使用預簽網址直接存取 Attachment S3；簽署預簽網址不需要 S3 VPC Endpoint。

即使是技術附錄，也只畫到「VPC、兩類 Lambda、RDS、兩個 VPC Endpoint、NAT、S3、
外部服務」層級，不要展開 IAM Policy、每條 Security Group rule 或 CloudFormation 資源 ID。

---

# 建議簡報頁面安排

1. **系統架構總覽**：使用 Prompt A，一張圖講清楚平台全貌。
2. **家長回報如何運作**：需要強調民眾服務時使用 Prompt B。
3. **AI 輿情分析如何運作**：需要強調創新亮點時使用 Prompt C。
4. **AWS 網路與資安設計**：只有技術問答或附錄才使用 Prompt D。

一般 5～10 分鐘簡報只需要 **Prompt A + Prompt C**；如果家長回報是展示重點，則使用
**Prompt A + Prompt B + Prompt C**。不要嘗試將四張圖合併成一張。
