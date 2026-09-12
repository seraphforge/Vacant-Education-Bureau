# API_SPEC.md — README!!! 平臺後端 API 契約

> 給前端開發用的 API 契約。搭配 [`UI_SPEC.md`](UI_SPEC.md) 閱讀。
> **實作狀態**：本文件中標記 `已上線` 的端點現在就能打；標記 `規劃中` 的是後端正在實作的契約，
> 欄位名稱與型別已定案，前端可以直接照這份寫 model 與 mock service，不用等後端完成。

---

## 0. 通用約定

### 0.1 Base URL

```ts
// webapp/src/environments/environment.ts（已存在）
apiBaseUrl: 'https://e86tz73y7h.execute-api.us-east-1.amazonaws.com'
```

### 0.2 認證

| 路徑前綴 | 認證 |
|---|---|
| `/api/...` | 公開，**不要**帶 `Authorization` header |
| `/api/secure/...` | 必須帶 `Authorization: Bearer <Cognito ID Token>` |

現有的 `authInterceptor` 已經處理好這件事（只對含 `/api/secure/` 的 URL 加 header），家長端的新端點都是公開的，不用動 interceptor。

注意必須是 **ID token**（不是 access token），因為 `custom:county` 只出現在 ID token 裡。`AuthService.getIdToken()` 已經是對的。

### 0.3 命名慣例（重要，兩種混用）

- **既有的幼兒園／裁罰端點**：`items` 內是資料庫原始列，欄位為 `snake_case`（`school_name`、`academic_year`…）。維持不變。
- **本次新增的所有端點**（家長回報、風險）：回應為手工組裝的 JSON，欄位一律 **`camelCase`**。
- Query string 參數一律 `camelCase`（`pageSize`、`dateFrom`…），與現況一致。

### 0.4 時間格式

新增端點的所有時間欄位一律為 **UTC ISO 8601 帶 `Z`**：`"2026-09-12T13:20:00Z"`。前端需自行轉台北時間（+8）顯示。

（既有的 `punish_date` 之類仍是 `"2026-09-12"` 純日期字串，不變。）

### 0.5 錯誤回應

所有錯誤統一格式：

```jsonc
{
  "message": "驗證碼不正確",   // 可直接顯示給使用者的中文訊息
  "code": "OTP_INVALID",      // 給程式判斷用的固定字串
  "detail": { "attemptsLeft": 3 }   // 選填，視 code 而定
}
```

HTTP 狀態碼：`400` 參數／狀態錯誤、`401` 未登入或 token 過期、`403` 權限不足、`404` 找不到、`500` 伺服器錯誤。

完整 `code` 清單見 §5。

### 0.6 分頁

所有清單端點統一：

- 請求：`page`（1 起算，預設 1）、`pageSize`（預設 20，上限 100）
- 回應：`{ "items": [...], "total": 123, "page": 1, "pageSize": 20 }`

`total` 是符合條件的總筆數，PrimeNG Table 的 `[totalRecords]` 直接用它，`lazy` 模式。

### 0.7 開發模式（Dev Mode）

AWS SES 目前在 sandbox，只能寄給事先驗證過的信箱。後端因此提供 dev mode：

- `MAIL_MODE=dev` 時，`POST /api/reports/drafts` 與 `.../otp/resend` 的回應會**多一個 `devOtp` 欄位**（6 位數驗證碼），且不會真的寄信。
- `MAIL_MODE=ses` 時 `devOtp` **不存在**，驗證碼只會寄到信箱。

前端請這樣寫：**若 `devOtp` 存在，就把它預填進驗證碼欄位並顯示一個「開發模式」提示**；不存在時走正常流程。這樣 demo 時用任意信箱也走得完，正式模式也不用改程式。

---

## 1. 幼兒園查詢（`已上線`）

回報表單的幼兒園下拉選單、Dashboard 主表格都用這支，不另外開 API。

### GET `/api/kindergartens`

| 參數 | 說明 |
|---|---|
| `county` | 縣市，例：`新北市` |
| `name` | 名稱關鍵字，`LIKE %name%` |
| `ownership` | `公立` / `私立` |
| `page` / `pageSize` | 分頁，`pageSize` 上限 100 |
| `sortBy` / `sortDir` | `id`/`school_name`/`county`/`district`/`ownership`，`asc`/`desc` |

回應（`items` 為 `snake_case`）：

```jsonc
{
  "items": [
    {
      "id": 1234,
      "academic_year": "114",
      "code": "N01",
      "school_name": "新北市私立○○幼兒園",
      "ownership": "私立",
      "county": "新北市",
      "district": "板橋區",
      "address": "新北市板橋區○○路1號",
      "phone": "02-1234-5678"
    }
  ],
  "total": 6747, "page": 1, "pageSize": 20,
  "academicYear": "", "county": null
}
```

**Autocomplete 用法**：`GET /api/kindergartens?name=<使用者輸入>&pageSize=10`，顯示 `school_name`（副標可放 `county + district`），送出時帶 `id` 當 `kindergartenId`。建議 debounce 300ms、輸入至少 1 個字才打。

### GET `/api/counties`（`已上線`）

```jsonc
{ "items": [ { "county": "新北市", "count": 1234 } ] }
```

### GET `/api/health`（`已上線`）
`{ "ok": true, "total": 6747 }`

---

## 2. 家長回報流程（`規劃中`）

### 2.1 流程總覽

```
使用者填表（姓名/Email/幼兒園/事由）＋選檔案
   │
   │ ① POST /api/reports/drafts                → draftId（此時已寄出驗證碼）
   │
   ├─ 有附件才做：
   │   ② POST /api/reports/drafts/{id}/attachments/presign  → 每檔一組 url + fields
   │   ③ 瀏覽器直接 POST 到 S3（不經過我們的 API）
   │   ④ POST /api/reports/drafts/{id}/attachments          → 登錄附件
   │
   │ ⑤ 顯示驗證碼輸入 Modal
   │    POST /api/reports/drafts/{id}/otp/verify → trackingToken
   │    （可選 POST .../otp/resend 重寄）
   ▼
導向 /report/<trackingToken>
   GET /api/reports/{token} → 追蹤頁資料
```

重點：**案件在驗證成功前不會進入政府端的清單**（後端狀態為 `pending_verification`，所有政府端查詢都排除）。這符合 UI_SPEC 4.2「驗證後才正式送入政府資料庫」的要求。

### 2.2 POST `/api/reports/drafts` — 建立草稿並寄出驗證碼

Request：

```jsonc
{
  "reporterName": "王小明",        // 選填，可傳 null 或省略
  "reporterEmail": "parent@example.com",   // 必填，需 email 格式
  "kindergartenId": 1234,          // 必填，來自 /api/kindergartens 的 id
  "content": "回報事由的內容…"      // 必填，1～5000 字
}
```

Response `201`：

```jsonc
{
  "draftId": 57,
  "reporterEmailMasked": "p*****@example.com",   // 給 Modal 顯示「驗證碼已寄到 p*****@example.com」
  "otpExpiresAt": "2026-09-12T13:30:00Z",        // 倒數計時用
  "devOtp": "482910"                             // 僅 dev mode 出現，見 §0.7
}
```

錯誤：`INVALID_EMAIL`、`CONTENT_REQUIRED`、`CONTENT_TOO_LONG`、`KINDERGARTEN_NOT_FOUND`。

### 2.3 POST `/api/reports/drafts/{draftId}/attachments/presign` — 取得上傳網址

限制（後端會擋，前端也要先擋並顯示錯誤提示）：**最多 5 檔、單檔 < 5MB、僅 `image/*`**。

Request：

```jsonc
{
  "files": [
    { "fileName": "photo1.jpg", "contentType": "image/jpeg", "sizeBytes": 1048576 }
  ]
}
```

Response `200`：

```jsonc
{
  "uploads": [
    {
      "fileName": "photo1.jpg",
      "key": "pending/57/9f2c…-photo1.jpg",   // 之後步驟④要回傳這個
      "url": "https://ntpc-kg-report-attachments-xxx.s3.amazonaws.com",
      "fields": {                              // presigned POST 的必要欄位，原封不動照抄
        "key": "pending/57/9f2c…-photo1.jpg",
        "Content-Type": "image/jpeg",
        "policy": "eyJ…",
        "x-amz-algorithm": "AWS4-HMAC-SHA256",
        "x-amz-credential": "…",
        "x-amz-date": "…",
        "x-amz-signature": "…"
      },
      "expiresAt": "2026-09-12T13:35:00Z"
    }
  ]
}
```

錯誤：`TOO_MANY_FILES`、`FILE_TOO_LARGE`、`UNSUPPORTED_FILE_TYPE`、`DRAFT_NOT_FOUND`、`DRAFT_ALREADY_VERIFIED`。

### 2.4 步驟③：瀏覽器直傳 S3

**這一步不是打我們的 API**，請注意四個地雷：

1. 用 `FormData`，先 `append` 所有 `fields` 的鍵值，**`file` 必須放在最後一個**。
2. **不要**自己設 `Content-Type` header（要讓瀏覽器產生 multipart boundary）。
3. **不要**帶 `Authorization` header（會被 S3 拒絕）。Angular 的 `authInterceptor` 只對 `/api/secure/` 加 header，所以預設就是對的。
4. 成功的回應是 **HTTP 204，且 body 為空**，不是 200。

```ts
const fd = new FormData();
Object.entries(up.fields).forEach(([k, v]) => fd.append(k, v));
fd.append('file', file);          // 一定放最後
await firstValueFrom(this.http.post(up.url, fd, { responseType: 'text' }));
```

PrimeNG `p-fileUpload` 請用 `customUpload` + `(uploadHandler)` 自己送，不要用內建的 `url` 屬性。

### 2.5 POST `/api/reports/drafts/{draftId}/attachments` — 登錄附件

上傳成功後呼叫，把檔案掛到草稿上。可一次送多筆。

Request：

```jsonc
{
  "files": [
    { "key": "pending/57/9f2c…-photo1.jpg", "fileName": "photo1.jpg",
      "contentType": "image/jpeg", "sizeBytes": 1048576 }
  ]
}
```

Response `201`：

```jsonc
{ "attachments": [ { "id": 91, "fileName": "photo1.jpg", "sizeBytes": 1048576 } ], "count": 1 }
```

### 2.6 POST `/api/reports/drafts/{draftId}/otp/verify` — 驗證並正式成案

Request：`{ "code": "482910" }`

Response `200`：

```jsonc
{
  "caseNo": "R2509-000057",              // 對外案號，可顯示給家長
  "status": "submitted",
  "trackingToken": "kJ8s…43字元",
  "trackingUrl": "/report/kJ8s…43字元"    // 前端直接 router.navigateByUrl 這個值
}
```

錯誤：

| code | HTTP | 前端處理 |
|---|---|---|
| `OTP_INVALID` | 400 | 顯示錯誤、清空輸入框，`detail.attemptsLeft` 為剩餘次數 |
| `OTP_EXPIRED` | 400 | 提示已過期，引導按「重寄」 |
| `OTP_LOCKED` | 400 | 錯太多次已作廢，引導重新填表 |
| `DRAFT_ALREADY_VERIFIED` | 400 | 這張草稿已成案（重複送出），可忽略或導回首頁 |
| `DRAFT_NOT_FOUND` | 404 | 導回 `/report` |

### 2.7 POST `/api/reports/drafts/{draftId}/otp/resend` — 重寄驗證碼

Request：無 body。Response `200`：`{ "otpExpiresAt": "…", "devOtp": "…"? }`

---

## 3. 回報追蹤頁（`規劃中`）

### GET `/api/reports/{token}` — 公開，憑 token

`token` 就是 URL 上的 `/report/<TOKEN>`。錯誤或不存在一律回 `404 REPORT_NOT_FOUND`（不區分「不存在」與「無權限」）。

Response `200`：

```jsonc
{
  "caseNo": "R2509-000057",
  "schoolName": "新北市私立○○幼兒園",
  "submittedAt": "2026-09-12T13:25:00Z",
  "status": "investigating",
  "statusLabel": "調查中",
  "statusReason": null,              // 「不受理」時為理由文字，其餘多為 null
  "steps": [
    { "key": "submitted",     "label": "已報報",   "state": "done" },
    { "key": "investigating", "label": "調查中",   "state": "current" },
    { "key": "closed",        "label": "調查完畢", "state": "pending" }
  ],
  "messages": [
    { "createdAt": "2026-09-13T02:10:00Z",
      "authorDisplay": "新北市教育局 陳承辦",
      "body": "您的案件已受理，將於 7 個工作日內完成初步查核。" }
  ],
  "attachments": [
    { "id": 91, "fileName": "photo1.jpg", "contentType": "image/jpeg",
      "url": "https://…s3…?X-Amz-Signature=…" }   // 短效（15 分鐘）presigned GET
  ]
}
```

**`steps` 是後端算好的完整節點集合，前端不要自己推導狀態機**：

- 一般案件回 3 個節點：`已報報` → `調查中` → `調查完畢`
- 被標記不受理時，回 **2 個節點**：`已報報`(done) → `不受理`(current)，後續節點不出現

`state` 只有三種：`done`（已完成，上色）、`current`（當前，上色＋強調）、`pending`（未進行，淺灰）。這一併解掉 UI_SPEC §7「不受理節點位置」的待確認項。

隱私：這支**不會**回傳回報人姓名與 email。

---

## 4. 政府機關端（`規劃中`，需登入）

所有端點的資料範圍由後端依 token 的 `custom:county` 自動鎖定；`admin` 群組可看全國。前端不需要（也無法）自己指定縣市。跨縣市存取單筆案件會得到 `404`。

### 4.1 GET `/api/secure/me`（`已上線`，會擴充）

現有回應加上 `displayName`：

```jsonc
{
  "username": "ntpc.chen",
  "displayName": "陳承辦",       // 新增，可能為 null
  "county": "新北市",
  "agency": "新北市教育局",
  "groups": ["staff"],
  "isAdmin": false,
  "scope": "新北市"
}
```

進 `/admin/dashboard` 先打這支拿 `county`（對應 UI_SPEC 6.2「先從資料庫讀取登入人員所屬縣市」）。

### 4.2 GET `/api/secure/kindergartens`（`已上線`，會擴充）

參數與公開版相同（`county` 會被忽略／覆寫）。`items` 每列**新增兩個 `snake_case` 欄位**（維持該列風格一致）：

```jsonc
{
  "id": 1234, "school_name": "…", "county": "新北市", "district": "板橋區",
  "address": "…", "phone": "…", "ownership": "私立", "code": "N01", "academic_year": "114",
  "risk_score": 83.5,      // number | null（尚未計算時為 null）
  "risk_level": "high"     // "high" | "medium" | "normal" | null
}
```

`sortBy` 新增可用值 `risk_score`。

**列文字顏色規則**（UI_SPEC 6.2.1）：直接用 `risk_level`，不要自己比大小 —
`high`（分數 ≥ 80）紫色、`medium`（60–79）紅色、`normal`（< 60）預設樣式、`null` 預設樣式。閾值寫在後端，之後調整前端不用改。

### 4.3 GET `/api/secure/reports` — 家長回報清單

| 參數 | 說明 |
|---|---|
| `status` | `submitted`/`investigating`/`closed`/`rejected`，可逗號分隔多選；省略＝全部（永不含未驗證草稿） |
| `kindergartenId` | 限定某園 |
| `q` | 關鍵字，比對回報內容與學校名稱 |
| `dateFrom` / `dateTo` | `YYYY-MM-DD`，比對成案日期 |
| `assignee` | 承辦人 username；傳 `unassigned` 表示只看未指派 |
| `page` / `pageSize` | 分頁 |
| `sortBy` / `sortDir` | `created_at`（預設）/`status`/`school_name`，`desc` 預設 |

Response `200`：

```jsonc
{
  "items": [
    {
      "id": 57,
      "caseNo": "R2509-000057",
      "status": "submitted",
      "statusLabel": "已報報",
      "createdAt": "2026-09-12T13:25:00Z",
      "kindergartenId": 1234,
      "schoolName": "新北市私立○○幼兒園",
      "county": "新北市",
      "district": "板橋區",
      "reporterName": "王小明",                  // 可能為 null
      "reporterEmailMasked": "p*****@example.com", // 清單只給遮蔽版
      "contentExcerpt": "回報事由的前 60 字…",
      "attachmentCount": 2,
      "assignee": null,
      "lastMessageAt": null                      // 最後一次回覆時間，可能為 null
    }
  ],
  "total": 12, "page": 1, "pageSize": 20, "county": "新北市"
}
```

### 4.4 GET `/api/secure/reports/summary` — 各狀態件數

給 tab 上的 badge 用。

```jsonc
{
  "total": 12,
  "byStatus": { "submitted": 5, "investigating": 4, "closed": 2, "rejected": 1 }
}
```

### 4.5 GET `/api/secure/reports/{id}` — 案件詳情

```jsonc
{
  "id": 57,
  "caseNo": "R2509-000057",
  "status": "investigating",
  "statusLabel": "調查中",
  "statusReason": null,
  "createdAt": "2026-09-12T13:20:00Z",
  "verifiedAt": "2026-09-12T13:25:00Z",
  "statusUpdatedAt": "2026-09-13T02:10:00Z",
  "assignee": "ntpc.chen",
  "reporter": { "name": "王小明", "email": "parent@example.com" },   // 詳情才給完整 email
  "kindergarten": {
    "id": 1234, "schoolName": "新北市私立○○幼兒園", "county": "新北市",
    "district": "板橋區", "address": "…", "phone": "…"
  },
  "content": "完整回報事由…",
  "attachments": [
    { "id": 91, "fileName": "photo1.jpg", "contentType": "image/jpeg",
      "sizeBytes": 1048576, "url": "https://…presigned GET（15 分鐘）…" }
  ],
  "messages": [
    { "id": 301, "kind": "status_change", "visibleToParent": true,
      "authorType": "staff", "authorUsername": "ntpc.chen", "authorDisplay": "新北市教育局 陳承辦",
      "body": null, "fromStatus": "submitted", "toStatus": "investigating",
      "emailedAt": null, "createdAt": "2026-09-13T02:10:00Z" },
    { "id": 302, "kind": "reply", "visibleToParent": true,
      "authorType": "staff", "authorUsername": "ntpc.chen", "authorDisplay": "新北市教育局 陳承辦",
      "body": "您的案件已受理…", "fromStatus": null, "toStatus": null,
      "emailedAt": "2026-09-13T02:11:00Z", "createdAt": "2026-09-13T02:11:00Z" }
  ]
}
```

`messages` 依 `createdAt` 由舊到新。渲染時：

- `kind: "reply"` → 對話氣泡，`emailedAt` 有值就顯示「已 email 通知」
- `kind: "internal_note"` → 標示「內部備註（不對外）」樣式，`visibleToParent` 必為 `false`
- `kind: "status_change"` → 時間軸事件，用 `fromStatus → toStatus` 組文字
- `kind: "system"` → 系統事件（例如成案）

### 4.6 POST `/api/secure/reports/{id}/messages` — 回覆家長／新增內部備註

Request：

```jsonc
{
  "kind": "reply",          // "reply"（家長看得到）| "internal_note"（僅內部）
  "body": "您的案件已受理…",  // 1～2000 字
  "notifyParent": true      // 僅 kind=reply 有效；true 時寄 email 通知家長
}
```

Response `201`：`{ "message": { …同 §4.5 的 message 物件… }, "emailed": true }`

寄給家長的信只含「有新回覆，請點連結查看」與追蹤連結，不夾帶案件內文。`emailed` 為 `false` 表示未寄或寄送失敗（sandbox 未驗證信箱時會是 `false`，但留言仍會存下來，不視為錯誤）。

### 4.7 PATCH `/api/secure/reports/{id}` — 變更狀態／指派

Request（欄位皆可選，至少給一個）：

```jsonc
{
  "status": "rejected",
  "statusReason": "非本局管轄範圍，已移請○○單位處理",   // status=rejected 時必填
  "assignee": "ntpc.chen",     // 傳 null 可取消指派
  "notifyParent": true
}
```

Response `200`：回傳**與 §4.5 完全相同的詳情物件**（前端可直接覆蓋畫面狀態，不用再打一次 GET）。

狀態變更會自動產生一筆 `kind: "status_change"` 的 message。

錯誤：`INVALID_STATUS`、`STATUS_REASON_REQUIRED`、`REPORT_NOT_FOUND`。

### 4.8 GET `/api/secure/kindergartens/{id}/risk` — 風險評估 Tab

**目前是 placeholder**：`isPlaceholder: true`，`totalScore` 與各維度 `score` 可能為 `null`。維度的 `key`／`label` 已定案，之後只會換掉分數。

```jsonc
{
  "kindergartenId": 1234,
  "totalScore": 83.5,          // number | null
  "riskLevel": "high",         // "high" | "medium" | "normal" | null
  "isPlaceholder": true,
  "modelVersion": "placeholder-v0",
  "computedAt": "2026-09-12T00:00:00Z",   // 可能為 null
  "dimensions": [
    { "key": "finance",       "label": "財務異常",   "score": 88, "weight": 0.3 },
    { "key": "compliance",    "label": "裁罰紀錄",   "score": 92, "weight": 0.3 },
    { "key": "opinion",       "label": "輿情負面",   "score": 70, "weight": 0.2 },
    { "key": "parent_report", "label": "家長回報",   "score": 65, "weight": 0.1 },
    { "key": "data_quality",  "label": "資料完整度", "score": 40, "weight": 0.1 }
  ]
}
```

雷達圖直接用 `dimensions` 的 `label` 當軸、`score`（0–100）當值。`score` 為 `null` 時建議畫 0 並加註「尚無資料」。

### 4.9 輿情分析（政府端一鍵啟動，非同步）

分析要跑數十秒到數分鐘，超過 API Gateway 的 29 秒上限，所以是 **job 模式**：
`POST` 建立工作 → API Lambda 非同步 invoke worker Lambda → 前端輪詢進度。

實作：`aws/src/opinion.py`（API）、`aws/src/opinion_worker.py`（分析）、`db/migrations/004_opinion.sql`。

#### 4.9.1 `GET /api/secure/kindergartens/{id}/opinion`

開 Tab 時呼叫，回最後一次**成功完成**的結果。從沒掃過回 `hasData: false`。

```jsonc
{
  "kindergartenId": 1234,
  "schoolName": "新北市私立快樂幼兒園",
  // 免責說明由後端統一提供，前端直接顯示，不要自己改寫
  "disclaimer": "本頁資料由 AI 自動蒐集公開網路資訊產生，僅代表「需要人工關注的程度」…",
  "cooldownMinutes": 10,
  "hasData": true,
  "job": { /* 最近一次的 job，可能還在跑或失敗，見 4.9.3 */ },
  "resultJobId": 7,
  "resultAt": "2026-09-13T01:40:00Z",
  "summary": "多行純文字摘要（AI 產生 + 程式補上來源狀態）",
  "opinionScore": 23.5,          // 0–100，可能為 0；null 代表還沒算過
  "items": [ /* 見 4.9.4 */ ]
}
```

#### 4.9.2 `POST /api/secure/kindergartens/{id}/opinion/scans`

沒有 request body。回應：

| 狀態碼 | 情況 |
|---|---|
| `202` | 已建立 job 並成功喚醒 worker |
| `200` | 已經有 job 在跑，回那個 job 並帶 `reused: true`（不重複發動） |
| `429` | 冷卻期內（`code: SCAN_COOLDOWN`，`detail.retryAfterSeconds`、`detail.lastJobId`） |
| `404` | 幼兒園不存在，**或不在這個帳號的縣市權限內**（故意不區分，避免洩漏存在性） |
| `500` | 無法喚醒 worker；job 會被標成 `failed` 並附 `error` |

#### 4.9.3 `GET /api/secure/kindergartens/{id}/opinion/scans/{jobId}`

輪詢用（前端每 4 秒一次）。`status` 為 `done` 時 `items` 會一起回來。

```jsonc
{
  "jobId": 7,
  "kindergartenId": 1234,
  "status": "analyzing",        // queued | searching | analyzing | done | failed
  "statusLabel": "AI 正在整理與判讀",   // 文字由後端定案，前端不要自己翻譯
  "requestedAt": "2026-09-13T01:38:00Z",
  "startedAt": "2026-09-13T01:38:01Z",
  "finishedAt": null,
  "requestedBy": "staff01",
  "queryCount": 12,             // 各來源實際取得的原始筆數合計
  "itemCount": 8,
  "confirmedCount": 2,          // attribution = confirmed
  "negativeCount": 1,           // confirmed 且負面分數 >= 0.5
  "opinionScore": null,
  "summary": null,
  "searchProvider": "http",     // http | http(blocked)
  "modelId": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
  "error": null,
  "disclaimer": "…"
}
```

#### 4.9.4 `items[]`

UI_SPEC Tab 3 要求的欄位是「文字內容 / 來源 / 日期」，這裡另外提供情緒與風險標籤。

```jsonc
{
  "id": 91,
  "title": "行政裁罰（新北教前字第…號）：違反幼兒教育及照顧法第 …",
  // internal:// 開頭的是本府內部資料（裁罰紀錄／家長回報），前端不要當連結
  "url": "https://…",
  "source": "教保服務機構裁罰紀錄",
  "sourceType": "gov",          // news | social | gov | web | report
  "publishedAt": "2026-05-20",  // 來源沒寫就是 null，不要猜
  "snippet": "只保留摘要片段，不存全文",
  "sentiment": "NEGATIVE",      // Comprehend DetectSentiment（zh-TW）；可能為 null
  "negativeScore": 0.9891,
  "riskTags": ["行政裁罰"],
  // 同名園所很常見，所以 ambiguous 是常態而非例外
  "attribution": "confirmed",   // confirmed | ambiguous | unrelated
  "attributionLabel": "已比對到本園全名",
  "confidence": 1.0,
  // 只代表「來源可核對」（官方公開資料或本府自有紀錄），不代表指控成立
  "verified": true
}
```

**資料語意（UI 必須照樣呈現，不可省略）**

- 每一筆都是「需要人工關注的線索」，不是已證實的事實，不得單獨作為裁處依據。
- `confirmed` 與 `ambiguous` 必須分開顯示，不要混在同一張表。
- 只有 `attribution = confirmed` 的項目會計入 `opinionScore`；沒有任何可歸屬線索時分數是 `0`（查過而且沒查到，本身是有意義的資訊），不是 `null`。
- **`opinionScore` 按「問題類型」計，不按「報導篇數」計。** 每種風險標籤只計一次（取權重最高的那一筆），所以同一個事件被 10 家媒體報導不會讓分數變成 10 倍。`itemCount` 會如實反映筆數，兩者不要互推。
- 官方可核對的紀錄（`verified: true`）用完整權重，不受情緒分數折扣——裁罰處分書是公文腔，`sentiment` 常常是 `NEUTRAL`，但「被罰了」跟語氣無關。
- 完成後 worker 會把分數寫回 `risk_score_current` 的 `opinion` 維度（§4.8 的雷達圖會跟著亮），但**不會**去算 `totalScore`——其他維度還是 placeholder，算總分會給人「已完成評估」的錯覺。

### 4.10 尚未提供

**財報 Tab（UI_SPEC 6.3 Tab 2）的 API 還沒定案**，欄位仍在討論中。這個 Tab 請先做出殼（Tab 標題 + 空狀態提示「資料整合中」），不要先自訂欄位，避免之後對不上。

---

## 5. 列舉值與錯誤碼

### 5.1 案件狀態

| `status` | `statusLabel` | 說明 |
|---|---|---|
| `submitted` | 已報報 | 驗證完成、已進政府端 |
| `investigating` | 調查中 | |
| `closed` | 調查完畢 | 終態 |
| `rejected` | 不受理 | 終態，`statusReason` 必有值 |

`pending_verification`（未驗證草稿）**不會出現在任何回應**中，前端不需處理。

### 5.2 風險等級

`high`（≥80，紫）、`medium`（60–79，紅）、`normal`（<60，預設）、`null`（未計算，預設）。

### 5.3 錯誤碼

| code | HTTP | 意義 |
|---|---|---|
| `INVALID_EMAIL` | 400 | Email 格式錯誤 |
| `CONTENT_REQUIRED` / `CONTENT_TOO_LONG` | 400 | 回報事由空白／超過 5000 字 |
| `KINDERGARTEN_NOT_FOUND` | 400 | `kindergartenId` 不存在 |
| `TOO_MANY_FILES` | 400 | 超過 5 個檔案 |
| `FILE_TOO_LARGE` | 400 | 單檔超過 5MB |
| `UNSUPPORTED_FILE_TYPE` | 400 | 非圖片格式 |
| `DRAFT_NOT_FOUND` | 404 | 草稿不存在或已過期 |
| `DRAFT_ALREADY_VERIFIED` | 400 | 草稿已成案 |
| `OTP_INVALID` | 400 | 驗證碼錯誤，`detail.attemptsLeft` |
| `OTP_EXPIRED` | 400 | 驗證碼逾時 |
| `OTP_LOCKED` | 400 | 錯誤次數過多，草稿作廢 |
| `REPORT_NOT_FOUND` | 404 | token／案件不存在或不在權限範圍 |
| `INVALID_STATUS` | 400 | 狀態值不合法 |
| `STATUS_REASON_REQUIRED` | 400 | 不受理未填理由 |
| `UNAUTHORIZED` | 401 | 未帶 token 或 token 過期 → 導回 `/admin` |
| `FORBIDDEN` | 403 | 帳號未設定縣市，需請管理者處理 |
| `INTERNAL_ERROR` | 500 | 顯示通用錯誤訊息 |

---

## 6. TypeScript 型別（可直接放進 `webapp/src/app/models/`）

```ts
// ---------- 共用 ----------
export type ReportStatus = 'submitted' | 'investigating' | 'closed' | 'rejected';
export type RiskLevel = 'high' | 'medium' | 'normal';
export interface ApiError { message: string; code: string; detail?: Record<string, unknown>; }
export interface Page<T> { items: T[]; total: number; page: number; pageSize: number; }

// ---------- 家長端 ----------
export interface CreateDraftRequest {
  reporterName?: string | null;
  reporterEmail: string;
  kindergartenId: number;
  content: string;
}
export interface CreateDraftResponse {
  draftId: number;
  reporterEmailMasked: string;
  otpExpiresAt: string;
  devOtp?: string;            // 僅 dev mode
}
export interface PresignRequest {
  files: { fileName: string; contentType: string; sizeBytes: number }[];
}
export interface PresignedUpload {
  fileName: string;
  key: string;
  url: string;
  fields: Record<string, string>;
  expiresAt: string;
}
export interface VerifyOtpResponse {
  caseNo: string;
  status: ReportStatus;
  trackingToken: string;
  trackingUrl: string;
}
export interface TrackingStep {
  key: string;
  label: string;
  state: 'done' | 'current' | 'pending';
}
export interface TrackingResponse {
  caseNo: string;
  schoolName: string;
  submittedAt: string;
  status: ReportStatus;
  statusLabel: string;
  statusReason: string | null;
  steps: TrackingStep[];
  messages: { createdAt: string; authorDisplay: string; body: string }[];
  attachments: { id: number; fileName: string; contentType: string; url: string }[];
}

// ---------- 政府端 ----------
export interface ReportListItem {
  id: number;
  caseNo: string;
  status: ReportStatus;
  statusLabel: string;
  createdAt: string;
  kindergartenId: number;
  schoolName: string;
  county: string;
  district: string | null;
  reporterName: string | null;
  reporterEmailMasked: string;
  contentExcerpt: string;
  attachmentCount: number;
  assignee: string | null;
  lastMessageAt: string | null;
}
export interface ReportMessage {
  id: number;
  kind: 'reply' | 'internal_note' | 'status_change' | 'system';
  visibleToParent: boolean;
  authorType: 'staff' | 'system';
  authorUsername: string | null;
  authorDisplay: string | null;
  body: string | null;
  fromStatus: ReportStatus | null;
  toStatus: ReportStatus | null;
  emailedAt: string | null;
  createdAt: string;
}
export interface ReportDetail {
  id: number;
  caseNo: string;
  status: ReportStatus;
  statusLabel: string;
  statusReason: string | null;
  createdAt: string;
  verifiedAt: string | null;
  statusUpdatedAt: string | null;
  assignee: string | null;
  reporter: { name: string | null; email: string };
  kindergarten: {
    id: number; schoolName: string; county: string;
    district: string | null; address: string | null; phone: string | null;
  };
  content: string;
  attachments: {
    id: number; fileName: string; contentType: string; sizeBytes: number; url: string;
  }[];
  messages: ReportMessage[];
}
export interface RiskAssessment {
  kindergartenId: number;
  totalScore: number | null;
  riskLevel: RiskLevel | null;
  isPlaceholder: boolean;
  modelVersion: string | null;
  computedAt: string | null;
  dimensions: { key: string; label: string; score: number | null; weight: number }[];
}
```

---

## 7. 給前端 agent 的注意事項

1. **可以立刻開始**：`規劃中` 的端點請照上面型別寫 service，並用回傳固定假資料的 mock 實作先接畫面；後端上線後只換 service 內部實作，元件不用動。
2. **不要自己算業務規則**：`steps`（追蹤節點）、`statusLabel`（狀態中文）、`riskLevel`（顏色分級）、`caseNo`（案號格式）都由後端給，前端只負責顯示。
3. **附件上傳的 4 個地雷**見 §2.4，特別是「`file` 放最後」與「成功是 204」。
4. **S3 CORS**：後端會把 `http://localhost:4200` 與 CloudFront 網域都加進 bucket 的允許來源。若上傳出現 CORS 錯誤，先回報，不要改成走我們的 API 中轉（API Gateway 有 10MB 上限，5 檔 × 5MB 會爆）。
5. **`/report/<TOKEN>` 是公開頁面**，不能掛 `authGuard`，也不要在該頁呼叫任何 `/api/secure/` 端點。
6. **PII**：回報人 email 不要寫進 console.log，清單畫面只顯示後端給的遮蔽版 `reporterEmailMasked`。
7. 契約若需要調整（缺欄位、型別不順手），直接提出來改這份文件，不要在前端硬轉。
