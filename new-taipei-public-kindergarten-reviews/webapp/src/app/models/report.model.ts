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
export interface RiskDimension {
  key: string;
  label: string;
  score: number | null;
  weight: number;
}

export interface RiskAssessment {
  kindergartenId: number;
  totalScore: number | null;
  riskLevel: RiskLevel | null;
  isPlaceholder: boolean;
  modelVersion: string | null;
  computedAt: string | null;
  dimensions: RiskDimension[];
}
