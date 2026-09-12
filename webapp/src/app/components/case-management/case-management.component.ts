import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AutoCompleteCompleteEvent, AutoCompleteModule } from 'primeng/autocomplete';
import { ButtonModule } from 'primeng/button';
import { DropdownModule } from 'primeng/dropdown';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { TableLazyLoadEvent, TableModule } from 'primeng/table';
import { TagModule } from 'primeng/tag';

import { ReportCaseDrawerComponent } from '../report-case-drawer/report-case-drawer.component';
import { Kindergarten } from '../../models/kindergarten.model';
import { ReportDetail, ReportListItem, ReportStatus } from '../../models/report.model';
import { KindergartenService } from '../../services/kindergarten.service';
import { ReportListQuery, SecureReportService } from '../../services/secure-report.service';

/** 狀態篩選選項（對應 API_SPEC §4.3 status 參數） */
const STATUS_FILTER_OPTIONS: { label: string; value: ReportStatus | null }[] = [
  { label: '全部狀態', value: null },
  { label: '已通報', value: 'submitted' },
  { label: '調查中', value: 'investigating' },
  { label: '調查完畢', value: 'closed' },
  { label: '不受理', value: 'rejected' },
];

/**
 * 案件處理 Tab（UI_SPEC §6.2.2 / §6.4）。
 *
 * dashboard 第二個平行 Tab，顯示登入者權限範圍（依 token 縣市）內**全部**的家長回報案件，
 * 供行政人員標記處理進度與回覆。與學校統整 Tab 平行、互不隸屬。
 *
 * - 清單：GET /api/secure/reports（伺服器端分頁 lazy）
 * - 篩選：狀態（status）、依學校（kindergartenId，選填）
 * - 點「處理」開啟 ReportCaseDrawerComponent（傳 reportId）
 */
@Component({
  selector: 'app-case-management',
  standalone: true,
  imports: [
    FormsModule,
    TableModule,
    TagModule,
    ButtonModule,
    DropdownModule,
    AutoCompleteModule,
    ProgressSpinnerModule,
    ReportCaseDrawerComponent,
  ],
  templateUrl: './case-management.component.html',
  styleUrl: './case-management.component.scss',
})
export class CaseManagementComponent implements OnInit {
  private secure = inject(SecureReportService);
  private kgSvc = inject(KindergartenService);

  readonly statusFilterOptions = STATUS_FILTER_OPTIONS;

  readonly rows = signal<ReportListItem[]>([]);
  readonly total = signal(0);
  readonly loading = signal(false);
  readonly pageSize = 20;

  // ---- 篩選 ----
  readonly statusFilter = signal<ReportStatus | null>(null);
  /** 依學校篩選（選填）；null = 全縣市 */
  readonly schoolFilter = signal<Kindergarten | null>(null);
  readonly schoolSuggestions = signal<Kindergarten[]>([]);

  /** 目前開啟處理的案件 id；null = Drawer 關閉 */
  readonly openReportId = signal<number | null>(null);

  private lastLazyEvent: TableLazyLoadEvent | null = null;
  private searchTimer?: ReturnType<typeof setTimeout>;

  ngOnInit(): void {
    // 首次載入交給 p-table 的 onLazyLoad（會在初始化時觸發一次）
  }

  loadRows(event: TableLazyLoadEvent): void {
    this.lastLazyEvent = event;
    this.loading.set(true);
    const page = Math.floor((event.first ?? 0) / (event.rows ?? this.pageSize)) + 1;
    const sortField = (event.sortField as string) || undefined;
    const sortDir: 'asc' | 'desc' | undefined =
      event.sortOrder === 1 ? 'asc' : event.sortOrder === -1 ? 'desc' : undefined;

    const query: ReportListQuery = {
      page,
      pageSize: event.rows ?? this.pageSize,
      sortBy: sortField,
      sortDir,
    };
    const status = this.statusFilter();
    if (status) {
      query.status = status;
    }
    const school = this.schoolFilter();
    if (school) {
      query.kindergartenId = school.id;
    }

    this.secure.listReports(query).subscribe({
      next: (res) => {
        this.rows.set(res.items);
        this.total.set(res.total);
        this.loading.set(false);
      },
      error: () => {
        this.rows.set([]);
        this.total.set(0);
        this.loading.set(false);
      },
    });
  }

  /** 篩選改變：回到第一頁重新查詢 */
  applyFilters(): void {
    const ev: TableLazyLoadEvent = {
      ...(this.lastLazyEvent ?? {}),
      first: 0,
      rows: this.lastLazyEvent?.rows ?? this.pageSize,
    };
    this.loadRows(ev);
  }

  // ---- 依學校 autocomplete ----
  searchSchool(event: AutoCompleteCompleteEvent): void {
    const q = (event.query ?? '').trim();
    if (this.searchTimer) {
      clearTimeout(this.searchTimer);
    }
    if (q.length < 1) {
      this.schoolSuggestions.set([]);
      return;
    }
    this.searchTimer = setTimeout(() => {
      this.kgSvc.search({ name: q, pageSize: 10 }).subscribe({
        next: (pageRes) => this.schoolSuggestions.set(pageRes.items),
        error: () => this.schoolSuggestions.set([]),
      });
    }, 300);
  }

  clearSchoolFilter(): void {
    this.schoolFilter.set(null);
    this.applyFilters();
  }

  // ---- Drawer ----
  openCase(item: ReportListItem): void {
    this.openReportId.set(item.id);
  }

  closeCase(): void {
    this.openReportId.set(null);
  }

  /** Drawer 內狀態變更後，同步更新清單那一列 */
  onCaseUpdated(d: ReportDetail): void {
    this.rows.update((items) =>
      items.map((it) =>
        it.id === d.id ? { ...it, status: d.status, statusLabel: d.statusLabel } : it,
      ),
    );
  }

  // ---- helpers ----
  statusSeverity(status: ReportStatus): 'success' | 'info' | 'warning' | 'danger' | 'secondary' {
    switch (status) {
      case 'submitted':
        return 'info';
      case 'investigating':
        return 'warning';
      case 'closed':
        return 'success';
      case 'rejected':
        return 'danger';
      default:
        return 'secondary';
    }
  }

  formatTaipei(iso: string | null): string {
    if (!iso) {
      return '—';
    }
    const d = new Date(iso);
    if (isNaN(d.getTime())) {
      return iso;
    }
    return new Intl.DateTimeFormat('zh-TW', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
      timeZone: 'Asia/Taipei',
    }).format(d);
  }
}
