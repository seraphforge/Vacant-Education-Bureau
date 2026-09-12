import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, delay, of } from 'rxjs';

import { environment } from '../../environments/environment';
import {
  CreateMessageRequest,
  OpinionLatest,
  OpinionScanJob,
  PatchReportRequest,
  ReportDetail,
  ReportListResponse,
  ReportMessage,
  ReportSummary,
  RiskAssessment,
} from '../models/report.model';

/** GET /api/secure/reports 的查詢條件（API_SPEC §4.3） */
export interface ReportListQuery {
  status?: string;
  kindergartenId?: number;
  q?: string;
  dateFrom?: string;
  dateTo?: string;
  assignee?: string;
  page?: number;
  pageSize?: number;
  sortBy?: string;
  sortDir?: 'asc' | 'desc';
}

/**
 * 政府端（需登入）的家長回報與風險評估 API（API_SPEC §4）。
 * 全部在 /api/secure/ 底下，authInterceptor 會自動附上 ID token。
 *
 * 上線範圍（本次決定）：
 *  - §4.3～§4.7（回報清單/件數/詳情/回覆/狀態變更）**已上線，呼叫真實 API**。
 *  - §4.8 風險評估後端尚未完成，**維持 mock**（用獨立旗標 riskUseMock 控制，
 *    避免之後風險 API 上線時動到已正常運作的回報功能）。
 */
@Injectable({ providedIn: 'root' })
export class SecureReportService {
  private http = inject(HttpClient);
  private base = `${environment.apiBaseUrl}/api/secure`;

  /** 僅風險評估（§4.8）仍走 mock；回報相關端點一律真實 API。 */
  private readonly riskUseMock = true;

  // ---------- §4.3 回報清單 ----------
  listReports(query: ReportListQuery = {}): Observable<ReportListResponse> {
    let params = new HttpParams();
    Object.entries(query).forEach(([k, v]) => {
      if (v !== null && v !== undefined && v !== '') params = params.set(k, String(v));
    });
    return this.http.get<ReportListResponse>(`${this.base}/reports`, { params });
  }

  // ---------- §4.4 各狀態件數 ----------
  reportsSummary(): Observable<ReportSummary> {
    return this.http.get<ReportSummary>(`${this.base}/reports/summary`);
  }

  // ---------- §4.5 案件詳情 ----------
  getReport(id: number): Observable<ReportDetail> {
    return this.http.get<ReportDetail>(`${this.base}/reports/${id}`);
  }

  // ---------- §4.6 回覆／內部備註 ----------
  addMessage(
    id: number,
    req: CreateMessageRequest,
  ): Observable<{ message: ReportMessage; emailed: boolean }> {
    return this.http.post<{ message: ReportMessage; emailed: boolean }>(
      `${this.base}/reports/${id}/messages`,
      req,
    );
  }

  // ---------- §4.7 變更狀態／指派 ----------
  patchReport(id: number, req: PatchReportRequest): Observable<ReportDetail> {
    return this.http.patch<ReportDetail>(`${this.base}/reports/${id}`, req);
  }

  // ---------- §4.8 風險評估（仍為 mock，後端完成後把 riskUseMock 設 false） ----------
  getRisk(kindergartenId: number): Observable<RiskAssessment> {
    if (this.riskUseMock) {
      return of(this.mockRisk(kindergartenId)).pipe(delay(300));
    }
    return this.http.get<RiskAssessment>(`${this.base}/kindergartens/${kindergartenId}/risk`);
  }

  // ---------- 輿情分析（真實 API；分析在 worker Lambda，前端輪詢） ----------
  /** 開 Tab 時取最新一次結果。從沒掃過會回 hasData=false。 */
  getOpinion(kindergartenId: number): Observable<OpinionLatest> {
    return this.http.get<OpinionLatest>(`${this.base}/kindergartens/${kindergartenId}/opinion`);
  }

  /**
   * 啟動一次掃描。回 202 + jobId；已經有 job 在跑會回 200 並帶 reused=true。
   * 冷卻期內會回 429（code=SCAN_COOLDOWN，detail.retryAfterSeconds）。
   */
  startOpinionScan(kindergartenId: number): Observable<OpinionScanJob> {
    return this.http.post<OpinionScanJob>(
      `${this.base}/kindergartens/${kindergartenId}/opinion/scans`,
      {},
    );
  }

  /** 輪詢進度。status=done 時 items 會一起回來。 */
  getOpinionScan(kindergartenId: number, jobId: number): Observable<OpinionScanJob> {
    return this.http.get<OpinionScanJob>(
      `${this.base}/kindergartens/${kindergartenId}/opinion/scans/${jobId}`,
    );
  }

  /**
   * 風險評估 mock。依 kindergartenId 落在三種區間，方便驗證雷達圖 / 顏色：
   *   1234 → 83.5 high、1235 → 72 medium、1236 → 45 normal。
   * 維度 key/label 依 API_SPEC §4.8 定案值，分數為 placeholder。
   */
  private mockRisk(kindergartenId: number): RiskAssessment {
    const table: Record<number, { total: number; level: RiskAssessment['riskLevel'] }> = {
      1234: { total: 83.5, level: 'high' },
      1235: { total: 72, level: 'medium' },
      1236: { total: 45, level: 'normal' },
    };
    const meta = table[kindergartenId] ?? { total: 83.5, level: 'high' as const };
    return {
      kindergartenId,
      totalScore: meta.total,
      riskLevel: meta.level,
      isPlaceholder: true,
      modelVersion: 'placeholder-v0',
      computedAt: '2026-09-12T00:00:00Z',
      dimensions: [
        { key: 'finance', label: '財務異常', score: 88, weight: 0.3 },
        { key: 'compliance', label: '裁罰紀錄', score: 92, weight: 0.3 },
        { key: 'opinion', label: '輿情負面', score: 70, weight: 0.2 },
        { key: 'parent_report', label: '家長回報', score: 65, weight: 0.1 },
        { key: 'data_quality', label: '資料完整度', score: 40, weight: 0.1 },
      ],
    };
  }
}
