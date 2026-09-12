import { Component, input } from '@angular/core';

/**
 * 詳細資料 Tab 2：財報（UI_SPEC §6.3 Tab 2）。
 *
 * API_SPEC §4.9：財報 Tab 的 API 尚未定案，欄位仍在討論。
 * 依指示先做出殼（Tab 標題 + 空狀態提示「資料整合中」），
 * 不預先自訂欄位，避免之後對不上。
 *
 * TBD（待確認）：財報表格欄位定義（UI_SPEC §7）。後端 API 定案後再補表格。
 */
@Component({
  selector: 'app-financial-report-tab',
  standalone: true,
  template: `
    <div class="tab-empty">
      <i class="pi pi-chart-bar"></i>
      <h3>財報資料整合中</h3>
      <p>此區的財報欄位與資料來源尚在確認，完成後會於此以表格呈現。</p>
    </div>
  `,
  styleUrl: './tab-empty.scss',
})
export class FinancialReportTabComponent {
  /** 幼兒園 id（API 定案後會用來查詢） */
  readonly kindergartenId = input.required<number>();
}
