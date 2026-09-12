import { Component, input } from '@angular/core';

/**
 * 詳細資料 Tab 3：輿情分析（UI_SPEC §6.3 Tab 3）。
 *
 * UI_SPEC 列出的欄位為：文字內容 / 來源 / 日期。
 * 但 API_SPEC §4.9 明確指出輿情分析 Tab 的 API 尚未定案，
 * 因此先做空狀態殼（「資料整合中」），不先綁定假欄位，避免之後對不上。
 *
 * TBD（待確認）：輿情資料 API 欄位（文字內容/來源/日期）與來源。API 定案後補表格。
 */
@Component({
  selector: 'app-public-opinion-tab',
  standalone: true,
  template: `
    <div class="tab-empty">
      <i class="pi pi-comments"></i>
      <h3>輿情分析資料整合中</h3>
      <p>將整合 Google Map 評論等網路輿論，完成後會以表格呈現（文字內容、來源、日期）。</p>
    </div>
  `,
  styleUrl: './tab-empty.scss',
})
export class PublicOpinionTabComponent {
  /** 幼兒園 id（API 定案後會用來查詢） */
  readonly kindergartenId = input.required<number>();
}
