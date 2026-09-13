import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';
import {
  CreateMessageRequest,
  FinanceReport,
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
 * 政府端（需登入）的家長回報、風險評估與財報 API（API_SPEC §4）。
 * 全部在 /api/secure/ 底下，authInterceptor 會自動附上 ID token。
 *
 * 全部端點都已上線，**沒有任何 mock**。風險指數由後端在讀取時即時重算
 * （財務法遵／家長回報／輿情關注／裁罰紀錄四維度加權）。
 */
@Injectable({ providedIn: 'root' })
export class SecureReportService {
  private http = inject(HttpClient);
  private base = `${environment.apiBaseUrl}/api/secure`;

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

  // ---------- §4.8 風險評估（四維度加權，後端讀取時即時重算）----------
  getRisk(kindergartenId: number): Observable<RiskAssessment> {
    return this.http.get<RiskAssessment>(`${this.base}/kindergartens/${kindergartenId}/risk`);
  }

  // ---------- §4.9 財報法遵分析（113 決算年度）----------
  /** 只有 10 間學校有決算書分析資料，其餘會回 hasData=false。 */
  getFinance(kindergartenId: number): Observable<FinanceReport> {
    return this.http.get<FinanceReport>(`${this.base}/kindergartens/${kindergartenId}/finance`);
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
}
