import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, delay, of } from 'rxjs';

import { environment } from '../../environments/environment';
import {
  CreateMessageRequest,
  PatchReportRequest,
  ReportDetail,
  ReportListItem,
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
 * §4.3～§4.7（回報清單/詳情/回覆/狀態）為「規劃中」，useMockApi=true 時走 mock。
 * §4.8 風險評估目前後端就是 placeholder，這裡的 mock 也標 isPlaceholder=true。
 */
@Injectable({ providedIn: 'root' })
export class SecureReportService {
  private http = inject(HttpClient);
  private base = `${environment.apiBaseUrl}/api/secure`;
  private mock = environment.useMockApi;

  // ---------- §4.3 回報清單 ----------
  listReports(query: ReportListQuery = {}): Observable<ReportListResponse> {
    if (this.mock) {
      return of(this.mockReportList(query)).pipe(delay(300));
    }
    let params = new HttpParams();
    Object.entries(query).forEach(([k, v]) => {
      if (v !== null && v !== undefined && v !== '') params = params.set(k, String(v));
    });
    return this.http.get<ReportListResponse>(`${this.base}/reports`, { params });
  }

  // ---------- §4.4 各狀態件數 ----------
  reportsSummary(): Observable<ReportSummary> {
    if (this.mock) {
      return of<ReportSummary>({
        total: 4,
        byStatus: { submitted: 2, investigating: 1, closed: 1, rejected: 0 },
      }).pipe(delay(200));
    }
    return this.http.get<ReportSummary>(`${this.base}/reports/summary`);
  }

  // ---------- §4.5 案件詳情 ----------
  getReport(id: number): Observable<ReportDetail> {
    if (this.mock) {
      return of(this.mockReportDetail(id)).pipe(delay(300));
    }
    return this.http.get<ReportDetail>(`${this.base}/reports/${id}`);
  }

  // ---------- §4.6 回覆／內部備註 ----------
  addMessage(
    id: number,
    req: CreateMessageRequest,
  ): Observable<{ message: ReportMessage; emailed: boolean }> {
    if (this.mock) {
      const message: ReportMessage = {
        id: Math.floor(Math.random() * 100000),
        kind: req.kind,
        visibleToParent: req.kind === 'reply',
        authorType: 'staff',
        authorUsername: 'ntpc.chen',
        authorDisplay: '新北市教育局 陳承辦',
        body: req.body,
        fromStatus: null,
        toStatus: null,
        emailedAt: req.kind === 'reply' && req.notifyParent ? new Date().toISOString() : null,
        createdAt: new Date().toISOString(),
      };
      return of({ message, emailed: !!message.emailedAt }).pipe(delay(300));
    }
    return this.http.post<{ message: ReportMessage; emailed: boolean }>(
      `${this.base}/reports/${id}/messages`,
      req,
    );
  }

  // ---------- §4.7 變更狀態／指派 ----------
  patchReport(id: number, req: PatchReportRequest): Observable<ReportDetail> {
    if (this.mock) {
      const detail = this.mockReportDetail(id);
      if (req.status) {
        detail.status = req.status;
        detail.statusLabel = this.statusLabel(req.status);
        detail.statusReason = req.statusReason ?? null;
      }
      if (req.assignee !== undefined) detail.assignee = req.assignee;
      return of(detail).pipe(delay(300));
    }
    return this.http.patch<ReportDetail>(`${this.base}/reports/${id}`, req);
  }

  // ---------- §4.8 風險評估 ----------
  getRisk(kindergartenId: number): Observable<RiskAssessment> {
    if (this.mock) {
      return of(this.mockRisk(kindergartenId)).pipe(delay(300));
    }
    return this.http.get<RiskAssessment>(`${this.base}/kindergartens/${kindergartenId}/risk`);
  }

  // ---------- mock helpers ----------
  private statusLabel(s: ReportListItem['status']): string {
    return s === 'submitted'
      ? '已報報'
      : s === 'investigating'
        ? '調查中'
        : s === 'closed'
          ? '調查完畢'
          : '不受理';
  }

  private mockReportList(query: ReportListQuery): ReportListResponse {
    const all: ReportListItem[] = [
      {
        id: 57,
        caseNo: 'R2509-000057',
        status: 'submitted',
        statusLabel: '已報報',
        createdAt: '2026-09-12T13:25:00Z',
        kindergartenId: 1234,
        schoolName: '新北市私立安溪幼兒園',
        county: '新北市',
        district: '板橋區',
        reporterName: '王小明',
        reporterEmailMasked: 'p*****@example.com',
        contentExcerpt: '園內午休環境與師生比疑似不符規定，懇請查核……',
        attachmentCount: 2,
        assignee: null,
        lastMessageAt: null,
      },
      {
        id: 58,
        caseNo: 'R2509-000058',
        status: 'investigating',
        statusLabel: '調查中',
        createdAt: '2026-09-11T02:10:00Z',
        kindergartenId: 1235,
        schoolName: '新北市私立山北幼兒園',
        county: '新北市',
        district: '三峽區',
        reporterName: null,
        reporterEmailMasked: 'a*****@gmail.com',
        contentExcerpt: '收費項目與公告不一致，且未提供正式收據……',
        attachmentCount: 0,
        assignee: 'ntpc.chen',
        lastMessageAt: '2026-09-11T05:00:00Z',
      },
      {
        id: 59,
        caseNo: 'R2509-000059',
        status: 'closed',
        statusLabel: '調查完畢',
        createdAt: '2026-09-05T09:00:00Z',
        kindergartenId: 1236,
        schoolName: '新北市公立幸福幼兒園',
        county: '新北市',
        district: '中和區',
        reporterName: '陳大華',
        reporterEmailMasked: 'c*****@yahoo.com.tw',
        contentExcerpt: '已查核完畢，感謝回報。',
        attachmentCount: 1,
        assignee: 'ntpc.chen',
        lastMessageAt: '2026-09-08T08:00:00Z',
      },
    ];
    const page = query.page ?? 1;
    const pageSize = query.pageSize ?? 20;
    let items = all;
    if (query.status) {
      const wanted = query.status.split(',');
      items = items.filter((i) => wanted.includes(i.status));
    }
    if (query.kindergartenId) {
      items = items.filter((i) => i.kindergartenId === query.kindergartenId);
    }
    return {
      items: items.slice((page - 1) * pageSize, page * pageSize),
      total: items.length,
      page,
      pageSize,
      county: '新北市',
    };
  }

  private mockReportDetail(id: number): ReportDetail {
    return {
      id,
      caseNo: `R2509-${String(id).padStart(6, '0')}`,
      status: 'investigating',
      statusLabel: '調查中',
      statusReason: null,
      createdAt: '2026-09-12T13:20:00Z',
      verifiedAt: '2026-09-12T13:25:00Z',
      statusUpdatedAt: '2026-09-13T02:10:00Z',
      assignee: 'ntpc.chen',
      reporter: { name: '王小明', email: 'parent@example.com' },
      kindergarten: {
        id: 1234,
        schoolName: '新北市私立安溪幼兒園',
        county: '新北市',
        district: '板橋區',
        address: '新北市板橋區文化路一段100號',
        phone: '02-1234-5678',
      },
      content:
        '園內午休環境與師生比疑似不符規定，且部分教具老舊有安全疑慮，懇請教育局派員查核，謝謝。',
      attachments: [
        {
          id: 91,
          fileName: 'photo1.jpg',
          contentType: 'image/jpeg',
          sizeBytes: 1048576,
          url: 'https://picsum.photos/seed/kg91/600/400',
        },
      ],
      messages: [
        {
          id: 301,
          kind: 'status_change',
          visibleToParent: true,
          authorType: 'staff',
          authorUsername: 'ntpc.chen',
          authorDisplay: '新北市教育局 陳承辦',
          body: null,
          fromStatus: 'submitted',
          toStatus: 'investigating',
          emailedAt: null,
          createdAt: '2026-09-13T02:10:00Z',
        },
        {
          id: 302,
          kind: 'reply',
          visibleToParent: true,
          authorType: 'staff',
          authorUsername: 'ntpc.chen',
          authorDisplay: '新北市教育局 陳承辦',
          body: '您的案件已受理，將於 7 個工作日內完成初步查核。',
          fromStatus: null,
          toStatus: null,
          emailedAt: '2026-09-13T02:11:00Z',
          createdAt: '2026-09-13T02:11:00Z',
        },
        {
          id: 303,
          kind: 'internal_note',
          visibleToParent: false,
          authorType: 'staff',
          authorUsername: 'ntpc.chen',
          authorDisplay: '新北市教育局 陳承辦',
          body: '已聯繫該園負責人，預計本週安排實地訪查。',
          fromStatus: null,
          toStatus: null,
          emailedAt: null,
          createdAt: '2026-09-13T03:00:00Z',
        },
      ],
    };
  }

  /**
   * 風險評估 mock。依 kindergartenId 落在三種區間，方便驗證表格顏色標記：
   *   1234 → 83.5 high（紫，≥80）
   *   1235 → 72   medium（紅，60–79）
   *   1236 → 45   normal（預設，<60）
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
