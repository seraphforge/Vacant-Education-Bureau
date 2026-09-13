import { DecimalPipe } from '@angular/common';
import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { TableModule } from 'primeng/table';
import { TagModule } from 'primeng/tag';

import { FinanceDirection, FinanceIndicator, FinanceReport } from '../../models/report.model';
import { SecureReportService } from '../../services/secure-report.service';

/**
 * 詳細資料 Tab 2：財報（UI_SPEC §6.3 Tab 2、API_SPEC §4.9）。
 *
 * 顯示三件事：
 *  1. **法遵風險指數**（決算書分析的總指標）與它換算進雷達圖的分數。
 *  2. **哪些指標有風險** —— 八項指標的等級（1 正常 / 2 注意 / 3 警示），
 *     等級與換算規則都由後端給，前端只負責上色與排序。
 *  3. 主要財務數字，讓承辦人看得到指標背後的依據。
 *
 * 只有 10 間學校有決算書分析資料，其餘回 hasData=false，
 * 這時要顯示「尚無資料」而不是 0 分。
 */
@Component({
  selector: 'app-financial-report-tab',
  standalone: true,
  imports: [DecimalPipe, TableModule, TagModule, ProgressSpinnerModule],
  templateUrl: './financial-report-tab.component.html',
  styleUrls: ['./financial-report-tab.component.scss', './tab-empty.scss'],
})
export class FinancialReportTabComponent {
  /** 幼兒園 id；變更時重新載入 */
  readonly kindergartenId = input.required<number>();

  private secure = inject(SecureReportService);

  readonly loading = signal(false);
  readonly data = signal<FinanceReport | null>(null);

  /**
   * 有風險的指標（等級 2 注意 / 3 警示）排前面，其次正常，無資料排最後。
   * 同等級時「過高」排在「過低」之前，只有突變的排最後 —— 讓最需要看的排最上面。
   */
  readonly sortedIndicators = computed(() => {
    const order = (i: FinanceIndicator) => (i.levelScore === 0 ? 9 : 4 - i.levelScore);
    const dirOrder = (i: FinanceIndicator) => {
      const codes = i.directions.map((d) => d.code);
      if (codes.includes('HIGH')) return 0;
      if (codes.includes('LOW')) return 1;
      if (codes.includes('SHIFT')) return 2;
      return 3;
    };
    return [...(this.data()?.indicators ?? [])].sort(
      (a, b) => order(a) - order(b) || dirOrder(a) - dirOrder(b),
    );
  });

  readonly flagged = computed(() => this.data()?.flagged ?? []);

  /** 只列出這間園實際出現過的方向，避免圖例講一堆用不到的 */
  readonly usedDirections = computed(() => {
    const used = new Set<string>();
    (this.data()?.indicators ?? []).forEach((i) =>
      i.directions.forEach((d) => used.add(d.code)),
    );
    return (this.data()?.directionLegend ?? []).filter((d) => used.has(d.code));
  });

  /** 財務數字分組（在 component 算好，模板不要放 ?? 這類運算式） */
  readonly metricGroups = computed(() => this.data()?.metrics?.groups ?? []);

  constructor() {
    effect(
      () => {
        const id = this.kindergartenId();
        this.load(id);
      },
      { allowSignalWrites: true },
    );
  }

  private load(id: number): void {
    // 切換園所時先清掉舊資料，否則載入中會短暫顯示上一間的財報
    this.data.set(null);
    this.loading.set(true);
    this.secure.getFinance(id).subscribe({
      next: (r) => {
        this.data.set(r);
        this.loading.set(false);
      },
      error: () => {
        this.data.set(null);
        this.loading.set(false);
      },
    });
  }

  /** 法遵風險指數的顏色：以換算後的風險分數（指數×4）套 65/45 閾值，與雷達圖一致 */
  indexSeverity(): 'danger' | 'warning' | 'success' | 'secondary' {
    const score = this.data()?.riskScore;
    if (score === null || score === undefined) return 'secondary';
    if (score >= 65) return 'danger';
    if (score >= 45) return 'warning';
    return 'success';
  }

  /** 指標等級 -> tag 顏色。3 警示 = 紅、2 注意 = 黃、1 正常 = 綠、0 無資料 = 灰 */
  levelSeverity(i: FinanceIndicator): 'danger' | 'warning' | 'success' | 'secondary' {
    switch (i.levelScore) {
      case 3:
        return 'danger';
      case 2:
        return 'warning';
      case 1:
        return 'success';
      default:
        return 'secondary';
    }
  }

  /** 「等級 2（注意）」這種顯示字串，把 CSV 的等級分數直接攤開給承辦人看 */
  levelText(i: FinanceIndicator): string {
    return i.levelScore === 0 ? '無資料' : `等級 ${i.levelScore}・${i.levelLabel}`;
  }

  /**
   * 「需注意的指標」用的短標籤：指標名稱 + 風險方向。
   * 等級用顏色表示（紅=警示、黃=注意），文字留給方向，才不會擠成一長串。
   */
  flaggedText(i: FinanceIndicator): string {
    return `${i.label}｜${i.directionLabel}`;
  }

  /** 方向圖示；後端沒給就退回一個中性圖示 */
  directionIcon(d: FinanceDirection): string {
    return `pi ${d.icon || 'pi-info-circle'}`;
  }

  /** z 分數顯示：正值代表比基準高（越高越可能異常） */
  z(value: number | null | undefined): string {
    return value === null || value === undefined ? '—' : value.toFixed(2);
  }

  overallSeverity(): 'danger' | 'warning' | 'success' | 'secondary' {
    switch (this.data()?.overallLevel) {
      case '高風險':
        return 'danger';
      case '中風險':
        return 'warning';
      case '低風險':
        return 'success';
      default:
        return 'secondary';
    }
  }
}
