import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { ChartModule } from 'primeng/chart';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { TagModule } from 'primeng/tag';

import { RiskAssessment } from '../../models/report.model';
import { SecureReportService } from '../../services/secure-report.service';

/**
 * 詳細資料 Tab 1：風險評估（UI_SPEC §6.3 Tab 1、API_SPEC §4.8）。
 *
 * 顯示 AI 綜合總分 + 雷達圖（各維度 label 當軸、score 0–100 當值）。
 * 目前後端為 placeholder（isPlaceholder=true），分數可能為 null，
 * null 時雷達圖畫 0 並加註「尚無資料」。維度 key/label 已定案，前端不自訂。
 */
@Component({
  selector: 'app-risk-assessment-tab',
  standalone: true,
  imports: [ChartModule, TagModule, ProgressSpinnerModule],
  templateUrl: './risk-assessment-tab.component.html',
  styleUrl: './risk-assessment-tab.component.scss',
})
export class RiskAssessmentTabComponent {
  /** 幼兒園 id；變更時重新載入 */
  readonly kindergartenId = input.required<number>();

  private secure = inject(SecureReportService);

  readonly loading = signal(false);
  readonly risk = signal<RiskAssessment | null>(null);

  /** 是否有任何維度沒有分數（用來顯示「尚無資料」註記） */
  readonly hasMissingScore = computed(
    () => this.risk()?.dimensions.some((d) => d.score === null) ?? false,
  );

  readonly chartData = computed(() => {
    const r = this.risk();
    if (!r) {
      return null;
    }
    return {
      labels: r.dimensions.map((d) => d.label),
      datasets: [
        {
          label: '風險分數',
          // null 的維度畫 0（並在畫面加註尚無資料）
          data: r.dimensions.map((d) => d.score ?? 0),
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
}
