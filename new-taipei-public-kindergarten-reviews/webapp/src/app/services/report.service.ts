import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, delay, firstValueFrom, of, throwError } from 'rxjs';

import { environment } from '../../environments/environment';
import {
  CreateDraftRequest,
  CreateDraftResponse,
  PresignRequest,
  PresignResponse,
  RegisterAttachmentsRequest,
  RegisterAttachmentsResponse,
  ResendOtpResponse,
  TrackingResponse,
  VerifyOtpResponse,
} from '../models/report.model';

/**
 * 家長回報流程 + 追蹤頁（API_SPEC §2、§3）。
 * 這些端點在 API_SPEC 標記「規劃中」，故此 service 用自己的內部旗標 mock
 * 走 mock 實作（回傳與契約對齊的假資料）。後端上線後把 mock 設 false 即可，
 * 元件完全不用改。
 *
 * 註：mock 切換刻意做成各 service 內部獨立旗標，而非單一全域旗標，
 * 這樣某一塊 API 上線時不會影響其他仍在 mock 的功能。
 *
 * 全部走公開路徑（/api/...），不帶 Authorization header。
 */
@Injectable({ providedIn: 'root' })
export class ReportService {
  private http = inject(HttpClient);
  private base = `${environment.apiBaseUrl}/api`;
  /** 家長回報 §2/§3 尚未上線，維持 mock；後端完成後設為 false。 */
  private readonly mock = true;

  /** mock 用：記住每張草稿正確的驗證碼與剩餘嘗試次數 */
  private mockDrafts = new Map<number, { otp: string; attemptsLeft: number; email: string }>();
  private mockDraftSeq = 57;

  // ---------- §2.2 建立草稿並寄出驗證碼 ----------
  createDraft(req: CreateDraftRequest): Observable<CreateDraftResponse> {
    if (this.mock) {
      const draftId = this.mockDraftSeq++;
      const otp = this.randomOtp();
      this.mockDrafts.set(draftId, { otp, attemptsLeft: 3, email: req.reporterEmail });
      const resp: CreateDraftResponse = {
        draftId,
        reporterEmailMasked: this.maskEmail(req.reporterEmail),
        otpExpiresAt: this.isoIn(10 * 60), // 10 分鐘後過期
        devOtp: otp, // 模擬 dev mode：預填驗證碼
      };
      return of(resp).pipe(delay(500));
    }
    return this.http.post<CreateDraftResponse>(`${this.base}/reports/drafts`, req);
  }

  // ---------- §2.3 取得上傳網址 ----------
  presignAttachments(draftId: number, req: PresignRequest): Observable<PresignResponse> {
    if (this.mock) {
      // mock 不做真的 S3 上傳，回傳假的 presigned POST 欄位
      const resp: PresignResponse = {
        uploads: req.files.map((f, i) => ({
          fileName: f.fileName,
          key: `pending/${draftId}/mock-${i}-${f.fileName}`,
          url: 'https://mock-bucket.s3.amazonaws.com',
          fields: {
            key: `pending/${draftId}/mock-${i}-${f.fileName}`,
            'Content-Type': f.contentType,
            policy: 'mock-policy',
            'x-amz-algorithm': 'AWS4-HMAC-SHA256',
            'x-amz-credential': 'mock',
            'x-amz-date': 'mock',
            'x-amz-signature': 'mock',
          },
          expiresAt: this.isoIn(5 * 60),
        })),
      };
      return of(resp).pipe(delay(300));
    }
    return this.http.post<PresignResponse>(
      `${this.base}/reports/drafts/${draftId}/attachments/presign`,
      req,
    );
  }

  /**
   * §2.4 瀏覽器直傳 S3。注意四個地雷：
   *  1. FormData，先 append 所有 fields，file 一定放最後。
   *  2. 不要自己設 Content-Type（讓瀏覽器產 multipart boundary）。
   *  3. 不要帶 Authorization（authInterceptor 只對 /api/secure/ 加，預設就對）。
   *  4. 成功回應是 HTTP 204、body 空。
   */
  async uploadToS3(
    upload: { url: string; fields: Record<string, string> },
    file: File,
  ): Promise<void> {
    if (this.mock) {
      // mock 模式不真的上傳
      await new Promise((r) => setTimeout(r, 200));
      return;
    }
    const fd = new FormData();
    Object.entries(upload.fields).forEach(([k, v]) => fd.append(k, v));
    fd.append('file', file); // 一定放最後
    await firstValueFrom(this.http.post(upload.url, fd, { responseType: 'text' }));
  }

  // ---------- §2.5 登錄附件 ----------
  registerAttachments(
    draftId: number,
    req: RegisterAttachmentsRequest,
  ): Observable<RegisterAttachmentsResponse> {
    if (this.mock) {
      const resp: RegisterAttachmentsResponse = {
        attachments: req.files.map((f, i) => ({
          id: 90 + i,
          fileName: f.fileName,
          sizeBytes: f.sizeBytes,
        })),
        count: req.files.length,
      };
      return of(resp).pipe(delay(200));
    }
    return this.http.post<RegisterAttachmentsResponse>(
      `${this.base}/reports/drafts/${draftId}/attachments`,
      req,
    );
  }

  // ---------- §2.6 驗證並正式成案 ----------
  verifyOtp(draftId: number, code: string): Observable<VerifyOtpResponse> {
    if (this.mock) {
      const draft = this.mockDrafts.get(draftId);
      if (!draft) {
        return throwError(() => ({
          error: { message: '草稿不存在', code: 'DRAFT_NOT_FOUND' },
          status: 404,
        }));
      }
      if (code !== draft.otp) {
        draft.attemptsLeft -= 1;
        if (draft.attemptsLeft <= 0) {
          return throwError(() => ({
            error: { message: '錯誤次數過多，草稿已作廢，請重新填表', code: 'OTP_LOCKED' },
            status: 400,
          })).pipe(delay(300));
        }
        return throwError(() => ({
          error: {
            message: '驗證碼不正確',
            code: 'OTP_INVALID',
            detail: { attemptsLeft: draft.attemptsLeft },
          },
          status: 400,
        })).pipe(delay(300));
      }
      const token = this.randomToken();
      const caseNo = `R2509-${String(draftId).padStart(6, '0')}`;
      // 把驗證成功的草稿掛到 mock 追蹤資料
      this.mockTracking.set(token, this.buildMockTracking(caseNo));
      const resp: VerifyOtpResponse = {
        caseNo,
        status: 'submitted',
        trackingToken: token,
        trackingUrl: `/report/${token}`,
      };
      return of(resp).pipe(delay(400));
    }
    return this.http.post<VerifyOtpResponse>(`${this.base}/reports/drafts/${draftId}/otp/verify`, {
      code,
    });
  }

  // ---------- §2.7 重寄驗證碼 ----------
  resendOtp(draftId: number): Observable<ResendOtpResponse> {
    if (this.mock) {
      const draft = this.mockDrafts.get(draftId);
      const otp = this.randomOtp();
      if (draft) {
        draft.otp = otp;
        draft.attemptsLeft = 3;
      }
      return of<ResendOtpResponse>({ otpExpiresAt: this.isoIn(10 * 60), devOtp: otp }).pipe(
        delay(400),
      );
    }
    return this.http.post<ResendOtpResponse>(
      `${this.base}/reports/drafts/${draftId}/otp/resend`,
      {},
    );
  }

  // ---------- §3 追蹤頁 ----------
  private mockTracking = new Map<string, TrackingResponse>();

  getTracking(token: string): Observable<TrackingResponse> {
    if (this.mock) {
      const existing = this.mockTracking.get(token);
      // 找不到就給一個「調查中」的範例，方便直接開網址看畫面
      const resp = existing ?? this.buildMockTracking('R2509-000042', 'investigating');
      return of(resp).pipe(delay(400));
    }
    return this.http.get<TrackingResponse>(`${this.base}/reports/${token}`);
  }

  // ---------- mock helpers ----------
  private buildMockTracking(
    caseNo: string,
    status: TrackingResponse['status'] = 'submitted',
  ): TrackingResponse {
    const steps: TrackingResponse['steps'] =
      status === 'rejected'
        ? [
            { key: 'submitted', label: '已通報', state: 'done' },
            { key: 'rejected', label: '不受理', state: 'current' },
          ]
        : [
            { key: 'submitted', label: '已通報', state: status === 'submitted' ? 'current' : 'done' },
            {
              key: 'investigating',
              label: '調查中',
              state:
                status === 'investigating' ? 'current' : status === 'closed' ? 'done' : 'pending',
            },
            {
              key: 'closed',
              label: '調查完畢',
              state: status === 'closed' ? 'current' : 'pending',
            },
          ];
    return {
      caseNo,
      schoolName: '新北市私立安溪幼兒園',
      submittedAt: this.isoIn(-3600),
      status,
      statusLabel:
        status === 'submitted'
          ? '已通報'
          : status === 'investigating'
            ? '調查中'
            : status === 'closed'
              ? '調查完畢'
              : '不受理',
      statusReason: status === 'rejected' ? '非本局管轄範圍，已移請相關單位處理' : null,
      steps,
      messages:
        status === 'submitted'
          ? []
          : [
              {
                createdAt: this.isoIn(-1800),
                authorDisplay: '新北市教育局 陳承辦',
                body: '您的案件已受理，將於 7 個工作日內完成初步查核。',
              },
            ],
      attachments: [],
    };
  }

  private randomOtp(): string {
    return String(Math.floor(100000 + Math.random() * 900000));
  }

  private randomToken(): string {
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    let out = '';
    for (let i = 0; i < 43; i++) out += chars[Math.floor(Math.random() * chars.length)];
    return out;
  }

  private isoIn(seconds: number): string {
    return new Date(Date.now() + seconds * 1000).toISOString().replace(/\.\d{3}Z$/, 'Z');
  }

  private maskEmail(email: string): string {
    const [local, domain] = email.split('@');
    if (!domain) return email;
    const head = local.slice(0, 1);
    return `${head}*****@${domain}`;
  }
}
