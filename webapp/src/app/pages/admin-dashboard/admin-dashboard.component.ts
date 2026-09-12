import { Component, inject, signal } from '@angular/core';
import { ButtonModule } from 'primeng/button';
import { TableLazyLoadEvent, TableModule } from 'primeng/table';
import { TagModule } from 'primeng/tag';

import { HeaderComponent } from '../../components/header/header.component';
import { SchoolDetailPanelComponent } from '../../components/school-detail-panel/school-detail-panel.component';
import { Kindergarten } from '../../models/kindergarten.model';
import { MeResponse, SecureApiService } from '../../services/secure-api.service';

/**
 * 政府機關資料整合主頁 /admin/dashboard（UI_SPEC §6.2）。
 *
 * - 進頁先打 /api/secure/me 取得登入人員縣市（顯示資料範圍）。
 * - PrimeNG Table 伺服器端分頁載入該範圍的幼兒園（含 risk_score/risk_level）。
 * - 整列文字顏色依 risk_level（UI_SPEC §6.2.1，顏色分級由後端決定，前端不自行比大小）：
 *     high  → 紫色、medium → 紅色、normal/null → 預設。
 * - 「詳細資料」按鈕開啟 Tabbed 浮動面板（Phase 7 的 SchoolDetailPanelComponent）。
 */
@Component({
  selector: 'app-admin-dashboard',
  standalone: true,
  imports: [
    HeaderComponent,
    SchoolDetailPanelComponent,
    TableModule,
    ButtonModule,
    TagModule,
  ],
  templateUrl: './admin-dashboard.component.html',
  styleUrl: './admin-dashboard.component.scss',
})
export class AdminDashboardComponent {
  private secure = inject(SecureApiService);

  readonly me = signal<MeResponse | null>(null);
  readonly rows = signal<Kindergarten[]>([]);
  readonly total = signal(0);
  readonly loading = signal(false);
  readonly pageSize = 20;

  /** 被選來看詳情的幼兒園；非 null 時開啟面板（Phase 7 綁定） */
  readonly selected = signal<Kindergarten | null>(null);

  constructor() {
    this.secure.me().subscribe({
      next: (me) => this.me.set(me),
      error: () => this.me.set(null),
    });
  }

  /** PrimeNG lazy 載入：換頁 / 排序時只向 API 要那一頁 */
  loadRows(event: TableLazyLoadEvent): void {
    this.loading.set(true);
    const page = Math.floor((event.first ?? 0) / (event.rows ?? this.pageSize)) + 1;
    const sortField = (event.sortField as string) || undefined;
    const sortDir: 'asc' | 'desc' | undefined =
      event.sortOrder === 1 ? 'asc' : event.sortOrder === -1 ? 'desc' : undefined;

    this.secure
      .kindergartens({
        page,
        pageSize: event.rows ?? this.pageSize,
        sortBy: sortField,
        sortDir,
      })
      .subscribe({
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
    return kg.risk_score === null || kg.risk_score === undefined
      ? '—'
      : String(kg.risk_score);
  }
}
