import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { ChartModule } from 'primeng/chart';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { TableModule } from 'primeng/table';
import { TagModule } from 'primeng/tag';

import { RiskAssessment, RiskDimension } from '../../models/report.model';
import { SecureReportService } from '../../services/secure-report.service';

/**
 * 詳細資料 Tab 1：風險評估（UI_SPEC §6.3 Tab 1、API_SPEC §4.8）。
 *
 * 四個維度（後端定案，前端不自訂）：財務法遵 / 家長回報 / 輿情關注 / 裁罰紀錄。
 * 總分是加權平均（0–100），後端每次讀取都即時重算。
 *
 * 缺資料的維度 `score` 是 null，**權重會被後端平均分配給其他維度**，
 * 所以這裡把它畫成 0 但一定要另外標示「尚無資料（不計分）」，
 * 否則承辦人會把「沒查過」誤讀成「沒問題」。
 */
@Component({
  selector: 'app-risk-assessment-tab',
  standalone: true,
  imports: [ChartModule, TagModule, TableModule, ProgressSpinnerModule],
  templateUrl: './risk-assessment-tab.component.html',
  styleUrl: './risk-assessment-tab.component.scss',
})
export class RiskAssessmentTabComponent {
  /** 幼兒園 id；變更時重新載入 */
  readonly kindergartenId = input.required<number>();

  private secure = inject(SecureReportService);

  readonly loading = signal(false);
  readonly risk = signal<RiskAssessment | null>(null);

  readonly dimensions = computed<RiskDimension[]>(() => this.risk()?.dimensions ?? []);

  /** 沒有資料、不計分的維度（畫面要明確標示，不能當成 0 分） */
  readonly missingDimensions = computed(() =>
    this.dimensions().filter((d) => d.score === null),
  );

  readonly chartData = computed(() => {
    const dims = this.dimensions();
    if (!dims.length) {
      return null;
    }
    return {
      labels: dims.map((d) => d.label),
      datasets: [
        {
          label: '風險分數',
          // null 的維度畫 0（並在下方列出「尚無資料」）
          data: dims.map((d) => d.score ?? 0),
          backgroundColor: 'rgba(124, 58, 237, 0.2)',
          borderColor: 'rgba(124, 58, 237, 0.9)',
          pointBackgroundColor: 'rgba(124, 58, 237, 1)',
        },
      ],
    };
  });

  readonly chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
    },
    scales: {
      r: {
        min: 0,
        max: 100,
        ticks: { stepSize: 20, backdropColor: 'transparent' },
        pointLabels: { font: { size: 13 } },
      },
    },
  };

  /** 風險等級對應中文與 tag severity */
  readonly levelLabel = computed(() => {
    switch (this.risk()?.riskLevel) {
      case 'high':
        return '高風險';
      case 'medium':
        return '中風險';
      case 'normal':
        return '一般';
      default:
        return '未計算';
    }
  });

  constructor() {
    // effect 內 load() 會同步 set(loading)，需開 allowSignalWrites
    effect(
      () => {
        const id = this.kindergartenId();
        this.load(id);
      },
      { allowSignalWrites: true },
    );
  }

  private load(id: number): void {
    this.loading.set(true);
    this.secure.getRisk(id).subscribe({
      next: (r) => {
        this.risk.set(r);
        this.loading.set(false);
      },
      error: () => {
        this.risk.set(null);
        this.loading.set(false);
      },
    });
  }

  displayTotal(): string {
    const t = this.risk()?.totalScore;
    return t === null || t === undefined ? '—' : String(t);
  }

  /** 維度分數的顯示值；null 代表沒有資料 */
  dimScore(d: RiskDimension): string {
    return d.score === null ? '尚無資料' : String(d.score);
  }

  dimSeverity(d: RiskDimension): 'danger' | 'warning' | 'success' | 'secondary' {
    if (d.score === null) return 'secondary';
    if (d.score >= 65) return 'danger';
    if (d.score >= 45) return 'warning';
    return 'success';
  }

  /** 有效權重（後端已把缺資料的權重分配掉）；不計分時顯示 0 */
  dimWeight(d: RiskDimension): string {
    return d.weight.toFixed(2);
  }
}
