import { Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { ButtonModule } from 'primeng/button';
import { ProgressSpinnerModule } from 'primeng/progressspinner';

import { HeaderComponent } from '../../components/header/header.component';
import { TrackingResponse } from '../../models/report.model';
import { ReportService } from '../../services/report.service';

/**
 * 回報進度追蹤頁 /report/<TOKEN>（UI_SPEC §5、API_SPEC §3）。
 *
 * 公開頁：不掛 authGuard，也不呼叫任何 /api/secure/ 端點。
 *
 * steps 由後端算好（含「不受理」節點的位置），前端只負責上色顯示：
 *   done    → 已完成，上色
 *   current → 當前，上色 + 強調
 *   pending → 未進行，淺灰
 * 前端不自行推導狀態機（API_SPEC §7.2）。
 */
@Component({
  selector: 'app-report-tracking',
  standalone: true,
  imports: [HeaderComponent, ButtonModule, ProgressSpinnerModule],
  templateUrl: './report-tracking.component.html',
  styleUrl: './report-tracking.component.scss',
})
export class ReportTrackingComponent {
  private route = inject(ActivatedRoute);
  private reportSvc = inject(ReportService);

  readonly loading = signal(true);
  readonly notFound = signal(false);
  readonly data = signal<TrackingResponse | null>(null);

  /** 是否為「不受理」案件（用來調整連接線樣式） */
  readonly isRejected = computed(() => this.data()?.status === 'rejected');

  constructor() {
    const token = this.route.snapshot.paramMap.get('token') ?? '';
    this.load(token);
  }

  private load(token: string): void {
    this.loading.set(true);
    this.notFound.set(false);
    this.reportSvc.getTracking(token).subscribe({
      next: (res) => {
        this.data.set(res);
        this.loading.set(false);
      },
      error: () => {
        // API_SPEC §3：不存在或無權限一律 404 REPORT_NOT_FOUND
        this.notFound.set(true);
        this.loading.set(false);
      },
    });
  }

  /** UTC ISO → 台北時間顯示（+8），API_SPEC §0.4 */
  formatTaipei(iso: string): string {
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
