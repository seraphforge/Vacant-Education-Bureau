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
  1. 已報報
  2. 調查中
  3. 調查完畢
- [x] 節點顯示規則：
  - [x] 已完成 / 當前所在階段：上色
  - [x] 尚未進行的階段：淺灰色
- [x] 特殊階段「不受理」：
  - [x] 預設不顯示，僅在該回報被標記為「不受理」時才顯示
  - [x] 顯示時取代原本「調查中」「調查完畢」的後續節點位置（例如：已報報 → 不受理）
- [x] Todo：確認「不受理」節點的顯示位置規則與連接線樣式（依範例為緊接在「已報報」之後）
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

#### 6.2.1 資料表格

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

### 6.3 詳細資料 Tabbed Floating Panel

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
