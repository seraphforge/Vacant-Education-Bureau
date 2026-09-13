/**
 * API_SPEC.md §6 的 TypeScript 型別。
 * 家長回報流程、政府端回報、風險評估共用。
 * 新增端點欄位一律 camelCase、時間為 UTC ISO 8601 帶 Z。
 */

// ---------- 共用 ----------
export type ReportStatus = 'submitted' | 'investigating' | 'closed' | 'rejected';
export type RiskLevel = 'high' | 'medium' | 'normal';

export interface ApiError {
  message: string;
  code: string;
  detail?: Record<string, unknown>;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
}

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
  /** 僅 dev mode 出現（MAIL_MODE=dev）。存在時前端預填驗證碼並顯示「開發模式」提示。 */
  devOtp?: string;
}

export interface PresignRequestFile {
  fileName: string;
  contentType: string;
  sizeBytes: number;
}

export interface PresignRequest {
  files: PresignRequestFile[];
}

export interface PresignedUpload {
  fileName: string;
  key: string;
  url: string;
  /** presigned POST 的必要欄位，原封不動照抄；上傳時 file 必須放最後。 */
  fields: Record<string, string>;
  expiresAt: string;
}

export interface PresignResponse {
  uploads: PresignedUpload[];
}

export interface RegisterAttachmentFile {
  key: string;
  fileName: string;
  contentType: string;
  sizeBytes: number;
}

export interface RegisterAttachmentsRequest {
  files: RegisterAttachmentFile[];
}

export interface RegisterAttachmentsResponse {
  attachments: { id: number; fileName: string; sizeBytes: number }[];
  count: number;
}

export interface VerifyOtpResponse {
  caseNo: string;
  status: ReportStatus;
  trackingToken: string;
  /** 前端可直接 router.navigateByUrl 這個值，例如 /report/<token> */
  trackingUrl: string;
}

export interface ResendOtpResponse {
  otpExpiresAt: string;
  devOtp?: string;
}

// ---------- 追蹤頁 ----------
export interface TrackingStep {
  key: string;
  label: string;
  state: 'done' | 'current' | 'pending';
}

export interface TrackingMessage {
  createdAt: string;
  authorDisplay: string;
  body: string;
}

export interface TrackingAttachment {
  id: number;
  fileName: string;
  contentType: string;
  url: string;
}

export interface TrackingResponse {
  caseNo: string;
  schoolName: string;
  submittedAt: string;
  status: ReportStatus;
  statusLabel: string;
  statusReason: string | null;
  steps: TrackingStep[];
  messages: TrackingMessage[];
  attachments: TrackingAttachment[];
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

export interface ReportListResponse extends Page<ReportListItem> {
  county: string | null;
}

export interface ReportSummary {
  total: number;
  byStatus: Record<ReportStatus, number>;
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
    id: number;
    schoolName: string;
    county: string;
    district: string | null;
    address: string | null;
    phone: string | null;
  };
  content: string;
  attachments: {
    id: number;
    fileName: string;
    contentType: string;
    sizeBytes: number;
    url: string;
  }[];
  messages: ReportMessage[];
}

export interface CreateMessageRequest {
  kind: 'reply' | 'internal_note';
  body: string;
  notifyParent?: boolean;
}

export interface PatchReportRequest {
  status?: ReportStatus;
  statusReason?: string | null;
  assignee?: string | null;
  notifyParent?: boolean;
}

// ---------- 風險評估 ----------
/**
 * 雷達圖的一個軸。
 *
 * `weight` 是**有效權重**（缺資料的維度權重會被平均分配給其他維度，
 * 所以它不一定等於 `baseWeight`）；`score === null` 代表這個維度沒有資料，
 * 不計分，畫面要標示「尚無資料」而不是畫 0。
 */
export interface RiskDimension {
  key: 'finance' | 'parent_report' | 'opinion' | 'compliance' | string;
  label: string;
  score: number | null;
  weight: number;
  baseWeight?: number;
  /** 後端給的說明（例如「尚有 2 件未結案回報」），直接顯示，不要自己編 */
  detail?: {
    note?: string;
    complianceIndex?: number | null;
    fiscalYear?: string | null;
    overallLevel?: string | null;
    openCount?: number;
    totalCount?: number;
    attentionScore?: number | null;
    scannedAt?: string | null;
    recordCount?: number;
    totalFine?: number;
  };
}

export interface RiskAssessment {
  kindergartenId: number;
  schoolName?: string;
  /** 加權平均（0–100）；四個維度全都沒資料時才會是 null */
  totalScore: number | null;
  riskLevel: RiskLevel | null;
  /** 正式演算法上線後一律 false，保留欄位是為了相容 */
  isPlaceholder: boolean;
  modelVersion: string | null;
  computedAt: string | null;
  dimensions: RiskDimension[];
  disclaimer?: string;
}

// ---------- 財報法遵分析 ----------
/** 指標等級。GREEN/YELLOW/RED 對應 levelScore 1/2/3，N/A 對應 0（缺欄位） */
export type FinanceIndicatorLevel = 'GREEN' | 'YELLOW' | 'RED' | 'N/A';

export interface FinanceIndicator {
  key: string;
  label: string;
  level: FinanceIndicatorLevel;
  /** 0 = 無資料、1 = 正常、2 = 注意、3 = 警示 */
  levelScore: 0 | 1 | 2 | 3;
  levelLabel: string;
  /** 跟自己歷年比的 z 分數 */
  yearZ: number | null;
  /** 跟同業比的 z 分數 */
  peerZ: number | null;
}

export interface FinanceMetricItem {
  label: string;
  value: number | null;
  /** 元 / % / 倍 / 人 / 空字串 */
  unit: string;
}

export interface FinanceMetricGroup {
  label: string;
  items: FinanceMetricItem[];
}

/** GET /api/secure/kindergartens/{id}/finance */
export interface FinanceReport {
  kindergartenId: number;
  schoolName: string;
  /** false = 這間園沒有決算書分析資料（只有 10 間有），前端要顯示「尚無資料」 */
  hasData: boolean;
  disclaimer: string;
  scoreFormula: string;
  indicators: FinanceIndicator[];
  financeId?: string | null;
  alias?: string | null;
  /** 決算年度（民國），目前一律 113 */
  fiscalYear?: string;
  /** 法遵風險指數 */
  complianceIndex?: number | null;
  /** 整體風險等級（低風險／中風險／高風險） */
  overallLevel?: string | null;
  earlyWarning?: string | null;
  /** 換算進雷達圖 finance 軸的分數（指數 × 4，上限 100） */
  riskScore?: number | null;
  riskWeight?: number;
  /** 等級 >= 2（注意／警示）的指標 */
  flagged?: FinanceIndicator[];
  metrics?: { groups: FinanceMetricGroup[] };
  sourceFile?: string | null;
  updatedAt?: string | null;
}

// ---------- 輿情分析 ----------
/** 掃描工作的狀態。後端提供 statusLabel，前端不要自己翻譯。 */
export type OpinionScanStatus = 'queued' | 'searching' | 'analyzing' | 'done' | 'failed';

/**
 * 這筆資料能不能歸屬到這一間幼兒園。
 * 同名園所很常見，所以 ambiguous 是常態而非例外，UI 必須把它跟 confirmed 分開顯示。
 */
export type OpinionAttribution = 'confirmed' | 'ambiguous' | 'unrelated';

export interface OpinionItem {
  id: number;
  title: string;
  /** internal:// 開頭的是本府內部資料（裁罰紀錄／家長回報），不是可點的外部連結 */
  url: string;
  source: string | null;
  sourceType: 'news' | 'social' | 'gov' | 'web' | 'report' | string;
  publishedAt: string | null;
  snippet: string | null;
  sentiment: 'POSITIVE' | 'NEGATIVE' | 'NEUTRAL' | 'MIXED' | null;
  negativeScore: number | null;
  riskTags: string[];
  attribution: OpinionAttribution;
  attributionLabel: string;
  confidence: number | null;
  /** 只代表來源可核對（官方公開資料），不代表指控成立 */
  verified: boolean;
}

export interface OpinionScanJob {
  jobId: number;
  kindergartenId: number;
  schoolName?: string;
  status: OpinionScanStatus;
  statusLabel: string;
  requestedAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  requestedBy: string | null;
  queryCount: number;
  itemCount: number;
  confirmedCount: number;
  negativeCount: number;
  opinionScore: number | null;
  summary: string | null;
  searchProvider: string | null;
  modelId: string | null;
  error: string | null;
  /** 已經有一個 job 在跑，後端直接把那個 job 回來 */
  reused?: boolean;
  disclaimer?: string;
  items?: OpinionItem[];
}

/** GET /api/secure/kindergartens/{id}/opinion */
export interface OpinionLatest {
  kindergartenId: number;
  schoolName: string;
  /** 免責說明由後端統一提供，前端直接顯示，不要自己編 */
  disclaimer: string;
  cooldownMinutes: number;
  hasData: boolean;
  /** 最近一次的 job（可能還在跑或失敗） */
  job: OpinionScanJob | null;
  /** 最後一次成功完成的結果 */
  resultJobId?: number;
  resultAt?: string | null;
  summary?: string | null;
  opinionScore?: number | null;
  items: OpinionItem[];
}
