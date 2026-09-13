import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { DropdownModule } from 'primeng/dropdown';
import { InputTextModule } from 'primeng/inputtext';
import { TabViewModule } from 'primeng/tabview';
import { TableLazyLoadEvent, TableModule } from 'primeng/table';
import { TagModule } from 'primeng/tag';

import { CaseManagementComponent } from '../../components/case-management/case-management.component';
import { HeaderComponent } from '../../components/header/header.component';
import { SchoolDetailPanelComponent } from '../../components/school-detail-panel/school-detail-panel.component';
import { Kindergarten, KindergartenQuery } from '../../models/kindergarten.model';
import { MeResponse, SecureApiService } from '../../services/secure-api.service';

/**
 * 政府機關資料整合主頁 /admin/dashboard（UI_SPEC §6.2）。
 *
 * 外殼：Header（登入版）+ PrimeNG TabView 兩個平行 Tab：
 *   Tab 1「學校統整」— 學校資料表格（§6.2.1）+ 詳細資料浮動面板（§6.3）
 *   Tab 2「案件處理」— 全縣市家長回報案件總覽（CaseManagementComponent，§6.2.2 / §6.4）
 *
 * 進頁先打 /api/secure/me 取得縣市範圍顯示。資料範圍由後端依 token 鎖定。
 */
@Component({
  selector: 'app-admin-dashboard',
  standalone: true,
  imports: [
    FormsModule,
    HeaderComponent,
    SchoolDetailPanelComponent,
    CaseManagementComponent,
    TabViewModule,
    TableModule,
    ButtonModule,
    TagModule,
    InputTextModule,
    DropdownModule,
  ],
  templateUrl: './admin-dashboard.component.html',
  styleUrl: './admin-dashboard.component.scss',
})
export class AdminDashboardComponent {
  private secure = inject(SecureApiService);

  readonly me = signal<MeResponse | null>(null);

  // ---- Tab 1：學校統整表格 ----
  readonly rows = signal<Kindergarten[]>([]);
  readonly total = signal(0);
  readonly loading = signal(false);
  readonly pageSize = 20;

  // ---- 搜尋 / 篩選 ----
  /** 學校名稱關鍵字（對應 API name，LIKE %關鍵字%） */
  nameQuery = '';
  /** 公私立篩選（對應 API ownership）；null = 全部 */
  ownershipFilter: string | null = null;
  readonly ownershipOptions: { label: string; value: string | null }[] = [
    { label: '全部', value: null },
    { label: '公立', value: '公立' },
    { label: '私立', value: '私立' },
  ];

  /** 記住最後一次 lazy 事件（排序 / 每頁筆數），套用搜尋時沿用 */
  private lastLazyEvent: TableLazyLoadEvent | null = null;

  /** 被選來看詳情的幼兒園；非 null 時開啟風險面板（§6.3） */
  readonly selected = signal<Kindergarten | null>(null);

  constructor() {
    this.secure.me().subscribe({
      next: (me) => this.me.set(me),
      error: () => this.me.set(null),
    });
  }

  /** PrimeNG lazy 載入：換頁 / 排序時只向 API 要那一頁，並帶上搜尋條件 */
  loadRows(event: TableLazyLoadEvent): void {
    this.lastLazyEvent = event;
    this.loading.set(true);
    const page = Math.floor((event.first ?? 0) / (event.rows ?? this.pageSize)) + 1;
    const sortField = (event.sortField as string) || undefined;
    const sortDir: 'asc' | 'desc' | undefined =
      event.sortOrder === 1 ? 'asc' : event.sortOrder === -1 ? 'desc' : undefined;

    const query: KindergartenQuery = {
      page,
      pageSize: event.rows ?? this.pageSize,
      sortBy: sortField,
      sortDir,
    };
    const name = this.nameQuery.trim();
    if (name) {
      query.name = name;
    }
    if (this.ownershipFilter) {
      query.ownership = this.ownershipFilter;
    }

    this.secure.kindergartens(query).subscribe({
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

  /** 套用搜尋 / 篩選：回到第一頁重新查詢（沿用目前排序與每頁筆數） */
  applySearch(): void {
    const ev: TableLazyLoadEvent = {
      ...(this.lastLazyEvent ?? {}),
      first: 0,
      rows: this.lastLazyEvent?.rows ?? this.pageSize,
    };
    this.loadRows(ev);
  }

  /** 清除搜尋條件並重新查詢 */
  clearSearch(): void {
    this.nameQuery = '';
    this.ownershipFilter = null;
    this.applySearch();
  }

  /** 整列文字顏色 class：直接吃 risk_level，不自行判斷分數 */
  rowClass(kg: Kindergarten): string {
    switch (kg.risk_level) {
      case 'high':
        return 'row--risk-high';
      case 'medium':
        return 'row--risk-medium';
      default:
        return '';
    }
  }

  openDetail(kg: Kindergarten): void {
    this.selected.set(kg);
  }

  closeDetail(): void {
    this.selected.set(null);
  }

  displayScore(kg: Kindergarten): string {
    return kg.risk_score === null || kg.risk_score === undefined ? '—' : String(kg.risk_score);
  }
}
