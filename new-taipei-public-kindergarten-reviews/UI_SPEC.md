# UI_SPEC.md — README!!! 教育機構風險評估整合平臺

> 本文件依據需求描述整理而成，供 Agent 自動化開發前端 UI 使用。
> 技術棧：**Angular + PrimeNG**
> 身份驗證：**AWS Cognito**（僅用於 `/admin` 系列頁面）

---

## 0. 專案簡介

- 教保機構風險評估整合平臺，整合公開資料（含 Google Map 評論等網路輿論）與政府內部資料（如財報），並串接 AI 模型進行交叉比對、年度變化追蹤、風險指數分析，最終以可視化方式呈現。
- 本文件僅涵蓋 **UI 部分**的規格與開發項目。

---

## 1. 路由總覽

| 路徑 | 說明 | 存取層級 |
|---|---|---|
| `/` | 首頁（入場動畫 + 回報入口） | 公開 |
| `/report` | 回報表單頁 | 公開 |
| `/report/<TOKEN>` | 回報進度追蹤頁 | 公開（憑 TOKEN） |
| `/admin` | 政府機關登入頁 | 隱藏入口，需直接輸入網址 |
| `/admin/dashboard` | 資料整合主頁面（表格） | 登入後 |

---

## 2. 全域共用元件

### 2.1 Header / Logo Bar

- [x] 建立共用 `HeaderComponent`，可於各頁面重複使用
- [x] 左側顯示 Logo：`img/icon.png`
- [x] 標題文字：「教育機構風險評估整合平臺」
- [x] `/admin/dashboard` 專用版本：最右側加入登出按鈕 `[->`
  - [x] 點擊後清除 Cognito session 並導回 `/admin`

---

## 3. 首頁 `/`（入場動畫 + 回報入口）

### 3.1 入場動畫

- [x] 進入網站（首次載入 `/`）時，全螢幕顯示可點擊的 README!!! 大 Logo，使用者點一下才進入動畫
  - 備註：瀏覽器會擋沒有使用者手勢的音檔自動播放，故改為「點擊 Logo 進入」，該點擊即為播放音效所需的手勢。
- [x] 動畫內容：專案標題「README!!!」
- [x] 動畫播放同時，播放音檔 `audio/readme_intro.mp3`（吶喊 "README!!!" 的聲音）
  - 備註：由點擊 Logo 觸發播放；音檔載入/播放失敗會被 catch，動畫照常進行、不卡住、不整頁報錯。檔案將由開發者手動放入。
- [x] 動畫結束後，過渡至主畫面內容
- [x] Todo：確認動畫觸發時機（每次進入 `/` 皆播放，或僅首次載入播放——依現有描述，預設每次進入 `/` 皆播放）
  - 已依保守假設實作為「每次進入 `/` 皆播放」，程式中標註 TBD 待確認。

### 3.2 主畫面

- [x] 套用共用 `HeaderComponent`（Logo + 標題「教育機構風險評估整合平臺」）
- [x] 畫面中央放置大按鈕，文字：「我要回報」
- [x] 點擊「我要回報」按鈕，導向 `/report`

---

## 4. 回報表單頁 `/report`

### 4.1 表單欄位

- [x] 建立 `ReportFormComponent`
- [x] 欄位 1：姓名（文字輸入）
- [x] 欄位 2：Email（文字輸入，**必填**，需 email 格式驗證）
- [x] 欄位 3：幼兒園（下拉選單 / Autocomplete，串接幼兒園資料庫清單 API）
- [x] 欄位 4：回報事由（多行文字輸入）
- [x] 欄位 5：附件檔案上傳
  - [x] 使用 PrimeNG FileUpload 元件
  - [x] 限制：單一檔案大小 < 5MB
  - [x] 限制：至多上傳 5 個檔案
  - [x] 限制：僅允許圖片格式（accept 設定為圖片 mime type）
  - [x] 超出限制時顯示錯誤提示

### 4.2 送出與驗證碼流程

- [x] 建立「送出」按鈕
- [x] 送出後呼叫後端 API，觸發驗證碼寄送至輸入的 email
- [x] 顯示驗證碼輸入欄位（Modal 或頁面切換）
- [x] 使用者輸入驗證碼並確認後，才將回報資料正式送入政府資料庫（呼叫對應 API）
- [x] 驗證碼輸入錯誤時顯示錯誤提示，並可重新輸入 / 重寄

> 備註：目前串接 mock service（API_SPEC §2 標記「規劃中」）。後端上線後將 `environment.useMockApi` 設為 `false` 即可切換，元件不用改。

### 4.3 送出完成後行為

- [x] 驗證成功後，觸發第二封 email 寄送（內含 `/report/<TOKEN>` 連結）
  - 備註：第二封 email 由後端於驗證成功後寄送；前端僅負責導頁。
- [x] 前端頁面自動導向 `/report/<TOKEN>`

---

## 5. 回報追蹤頁 `/report/<TOKEN>`

- [x] 建立 `ReportTrackingComponent`，依 URL 上的 `<TOKEN>` 取得回報狀態資料
- [x] 以一系列圓形節點呈現處理階段（Stepper / Timeline 樣式，可用 PrimeNG Steps 或自訂元件）
- [x] 預設三個階段，依序為：
  1. 已通報
  2. 調查中
  3. 調查完畢
- [x] 節點顯示規則：
  - [x] 已完成 / 當前所在階段：上色
  - [x] 尚未進行的階段：淺灰色
- [x] 特殊階段「不受理」：
  - [x] 預設不顯示，僅在該回報被標記為「不受理」時才顯示
  - [x] 顯示時取代原本「調查中」「調查完畢」的後續節點位置（例如：已通報 → 不受理）
- [x] Todo：確認「不受理」節點的顯示位置規則與連接線樣式（依範例為緊接在「已通報」之後）
  - 已解決：`steps` 由後端算好（API_SPEC §3），前端直接依 `state`（done/current/pending）上色，不自行推導。

---

## 6. 政府機關用資料整合頁面

### 6.1 登入頁 `/admin`

- [x] 建立 `AdminLoginComponent`
- [x] 公開頁面（首頁等）不提供任何連結入口，僅能直接輸入網址存取
- [x] 套用共用 `HeaderComponent`（Logo + 標題「教育機構風險評估整合平臺」）
- [x] 帳號欄位、密碼欄位、登入按鈕
- [x] 串接 **AWS Cognito** 進行身份驗證
- [x] 登入成功後導向 `/admin/dashboard`
- [x] 登入失敗顯示錯誤訊息

### 6.2 Dashboard `/admin/dashboard`

- [x] 建立 `AdminDashboardComponent`
- [x] 進入頁面時，先從資料庫讀取登入人員所屬縣市
  - 備註：進頁呼叫 `/api/secure/me` 取得 `county`；資料範圍由後端依 token 鎖定。
- [x] 依所屬縣市過濾 / 請求對應資料，僅顯示該縣市資料
- [x] 套用共用 `HeaderComponent` 之登入版（含登出按鈕 `[->`）
- [x] 登出按鈕：清除 Cognito Session，導回 `/admin`

#### 6.2.0 頁面結構：兩個平行的頂層 Tab

- [x] Header 底下用 **PrimeNG `TabView`** 分成兩個**平行、獨立**的功能區塊：
  1. **學校統整**（Tab 1，預設開啟）— 學校資料表格 + 詳細資料浮動面板（見 §6.2.1、§6.3）
  2. **案件處理**（Tab 2）— 全縣市家長回報案件總覽與處理（見 §6.2.2、§6.4）
- [x] 兩個 Tab 互不隸屬：「案件處理」是**全縣市案件總覽**，不綁定任何一間學校；不再放在學校列表列上或學校詳細資料裡。
- [x] `AdminDashboardComponent` 作為外殼（Header + TabView），Tab 內容各自拆成獨立元件。

#### 6.2.1 學校統整 Tab：資料表格

- [x] 使用 PrimeNG Table 建立主表格
- [x] 欄位（由左到右）：
  1. 學校名稱
  2. 風險指數
  3. 縣市
  4. 鄉鎮市區
  5. 地址
  6. 電話
  7. 詳細資料（按鈕欄位）
- [x] 風險指數對應的整列（row）文字顏色規則：
  - [x] 風險指數 ≥ 80：整列文字標記為**紫色**
  - [x] 風險指數 60～79：整列文字標記為**紅色**
  - [x] 風險指數 < 60：預設樣式（無特殊標記）
  - 備註：直接依後端 `risk_level`（high/medium/normal/null）上色，不在前端比大小（API_SPEC §4.2）。
- [x] 「詳細資料」欄位為按鈕，點擊後開啟浮動面板（見 6.3）
  - 備註：學校統整 Tab 的表格列**只有「詳細資料（風險）」一個操作**；案件處理已移到 Tab 2，不再放在此列。

#### 6.2.2 案件處理 Tab：全縣市家長回報總覽

> 對應 API_SPEC §4.3～§4.7（皆已上線）。這是 **dashboard 層級的第二個 Tab**，與學校統整平行，
> 顯示登入者權限範圍內（依 token 縣市）**全部**的家長回報案件，供行政人員標記處理進度與回覆。詳細內容見 §6.4。

- [x] 建立 `CaseManagementComponent` 作為 Tab 2 的內容元件
- [x] 進入 Tab 時呼叫 `GET /api/secure/reports`（不帶 `kindergartenId`＝全縣市）載入案件清單
- [x] 以 PrimeNG Table 呈現案件清單，欄位：案號、學校名稱、狀態、成案時間、內容摘要、附件數、（操作：處理）
- [x] 提供**狀態篩選**（`submitted`/`investigating`/`closed`/`rejected`，對應 §4.3 `status` 參數）
- [x] 提供**依學校篩選**（選填）：§4.3 支援 `kindergantenId`（實為 `kindergartenId`）過濾，故加一個「限定某園」的篩選；不選則顯示全縣市
- [x] 點「處理」開啟案件詳情與操作（沿用 §6.4 的 Drawer 設計：狀態標記 + reply/internal_note + 未儲存/已儲存）

### 6.3 詳細資料 Tabbed Floating Panel（學校統整 Tab 內）

- [x] 使用 PrimeNG OverlayPanel / Dialog 建立可浮動的 Tabbed 面板
- [x] 面板內建立三個 Tab：
  1. 風險評估
  2. 財報
  3. 輿情分析
- [x] 面板開啟時，預設顯示第一個 Tab「風險評估」

#### Tab 1：風險評估

- [x] 顯示 AI 綜合各項資料評比的總分數
- [x] 建立雷達圖（Radar Chart），針對各項指標繪製（可用 PrimeNG Chart / Chart.js）
- [x] Todo：確認雷達圖各項指標名稱與資料來源欄位（規格未列出具體指標項目，待補）
  - 已解決：維度 `key`/`label` 由後端定案（API_SPEC §4.8）：財務異常、裁罰紀錄、輿情負面、家長回報、資料完整度。目前分數為 placeholder。

#### Tab 2：財報

- [x] 以表格呈現財報資料
  - 備註：API_SPEC §4.9 財報 API 尚未定案，先做空狀態殼（「資料整合中」），不預先自訂欄位。
- [ ] Todo：欄位項目待定，需向需求方確認後補上欄位設定（**API 定案後補**）

#### Tab 3：輿情分析

- [x] 以表格呈現輿情資料
  - 備註：API_SPEC §4.9 輿情 API 尚未定案，先做空狀態殼（「資料整合中」）。
- [ ] 欄位（**API 定案後補**）：
  1. 文字內容
  2. 來源
  3. 日期

### 6.4 案件處理 Tab：處理進度標記與回覆（家長回報案件）

> 對應 API_SPEC §4.3～§4.7（皆已上線）。此功能位於 dashboard 的**案件處理 Tab（§6.2.2）**，
> 讓登入的行政人員檢視全縣市家長回報案件、標記處理進度、回覆家長或新增內部備註；
> 家長端會在 `/report/<TOKEN>` 追蹤頁看到進度與對外回覆。

#### 6.4.1 呈現方式與理由（設計決定）

- [x] **提升為 dashboard 層級的第二個 Tab「案件處理」**（與學校統整平行），不再是學校列表列上的按鈕、
      也不放進 §6.3 的「詳細資料」浮動面板。理由：§6.3 面板呈現的是「學校整體風險資料」（風險/財報/輿情），
      是唯讀分析視角、資料層級是「一間學校」；而案件處理是「全縣市家長回報案件的處理作業（讀 + 寫）」，
      層級是「單一案件」、且不隸屬任一學校，兩者性質不同，故獨立成 Tab。
- [x] Tab 2 內用 **案件清單表格（§6.2.2）＋ 點「處理」開啟案件詳情與操作面板**。
- [x] 案件詳情與操作面板沿用 **PrimeNG `p-sidebar`（右側 Drawer）** 呈現。選擇 Drawer 的理由：
  1. 行政人員「一邊看案件清單、一邊處理」，Drawer 從側邊滑出、可快速開關，不離開清單情境。
  2. 案件內容 + 訊息串 + 操作表單資訊量較大，Drawer 的可捲動高度比 Dialog 更適合。
  3. 與既有 PrimeNG 慣例一致，不引入新的 UI library。

#### 6.4.2 案件詳情 Drawer 內容

- [x] 由 `CaseManagementComponent`（Tab 2）在點選案件時開啟 `ReportCaseDrawerComponent`
- [x] 案件清單來源見 §6.2.2（`GET /api/secure/reports`，可依狀態 / 學校篩選）
- [x] 案件詳情呼叫 `GET /api/secure/reports/{id}`，顯示：
  - [x] 狀態與目前處理階段
  - [x] 回報人姓名 / email、回報內容、附件
  - [x] 訊息串（`messages`）：依 `kind` 區分對外回覆 / 內部備註 / 狀態變更 / 系統事件

##### A. 標記處理進度（狀態）

- [x] 提供狀態選擇（對應追蹤頁四個階段）：`submitted`(已通報)、`investigating`(調查中)、`closed`(調查完畢)、`rejected`(不受理)
  - 備註：送出的是 enum 值，顯示用後端回傳的 `statusLabel`（沿用家長端「已通報」用字）。
- [x] 選 `rejected`(不受理) 時，**強制要求填寫理由** `statusReason`（對應 API_SPEC §4.7 `STATUS_REASON_REQUIRED`）
- [x] 提供「是否 email 通知家長」開關（`notifyParent`）
- [x] 呼叫 `PATCH /api/secure/reports/{id}` 儲存；回應為完整詳情物件，直接覆蓋畫面狀態

##### B. 回覆家長 / 內部備註

- [x] 呼叫 `POST /api/secure/reports/{id}/messages`，支援兩種 `kind`：
  - `reply`：**對外回覆，家長在追蹤頁看得到**
  - `internal_note`：**內部備註，不對外顯示**
- [x] **兩種模式用明顯 UI 區隔**，避免誤把內部備註當對外回覆送出：
  - [x] 以切換（Radio / SelectButton）選擇模式
  - [x] 對外回覆（reply）採藍色主色 + `pi pi-send` 圖示，並顯示「家長看得到」提示
  - [x] 內部備註（internal_note）採灰/黃警示色 + `pi pi-lock` 圖示，並顯示「僅內部可見，不會通知家長」提示
  - [x] 送出按鈕文字隨模式變化（「送出回覆」 vs 「儲存內部備註」）
- [x] `reply` 模式提供 `notifyParent` 開關（`internal_note` 不顯示此開關）

##### C. 尚未修改 vs 已儲存狀態（避免誤會已送出）

- [x] 狀態下拉、理由、訊息輸入框只要與伺服器目前值不同，就顯示「尚未儲存」標示（dirty 標記 / 未儲存徽章）
- [x] 成功呼叫 API 後，更新畫面為最新值並清除 dirty 標記、顯示「已儲存」提示（Toast）
- [x] 訊息送出成功後清空輸入框並把新訊息加入訊息串
- [x] 儲存過程中按鈕顯示 loading，避免重複送出

#### 6.4.3 API 上線範圍決定（本次）

- [x] `/api/secure/me`、`/api/secure/kindergartens`（§4.1 / §4.2）與回報相關端點 §4.3～§4.7 **皆已上線，改呼叫真實 API**
- [x] 風險評估 §4.8 **維持 mock**，直到後端完成
- [x] 為避免風險 API 上線時又要動到已正常運作的回報功能，mock 切換改為**各 service 內部獨立旗標**，不再用單一全域 `useMockApi`

---

## 7. 待確認事項彙整（Open Items）

- [ ] 入場動畫是否僅首次載入播放，或每次進入 `/` 皆播放
- [ ] 「不受理」節點於追蹤頁 Stepper 中的顯示位置與連接線規則
- [ ] 「風險評估」Tab 雷達圖之各項指標名稱與對應資料欄位
- [ ] 「財報」Tab 表格欄位定義

---

## 8. 元件清單總覽（供 Agent 拆分開發任務）

- [x] `HeaderComponent`（共用，含一般版 / 登入版）
- [x] `IntroAnimationComponent`（入場動畫 + 音效播放）
- [x] `HomeComponent`（首頁）
- [x] `ReportFormComponent`（回報表單）
- [x] `VerificationCodeComponent`（驗證碼輸入，可為 Modal）
- [x] `ReportTrackingComponent`（回報進度追蹤，含 Stepper）
- [x] `AdminLoginComponent`（登入頁，串接 Cognito）
- [x] `AdminDashboardComponent`（主表格頁面）
- [x] `SchoolDetailPanelComponent`（Tabbed Floating Panel）
  - [x] `RiskAssessmentTabComponent`（風險評估 + 雷達圖）
  - [x] `FinancialReportTabComponent`（財報表格；殼，待 API）
  - [x] `PublicOpinionTabComponent`（輿情分析表格；殼，待 API）
- [x] `AdminDashboardComponent` 外殼改為兩個平行頂層 Tab（學校統整 / 案件處理；見 §6.2.0）
  - [x] `CaseManagementComponent`（案件處理 Tab：全縣市案件清單 + 篩選；見 §6.2.2）
  - [x] `ReportCaseDrawerComponent`（單筆案件處理進度標記與回覆，側邊 Drawer；見 §6.4）
