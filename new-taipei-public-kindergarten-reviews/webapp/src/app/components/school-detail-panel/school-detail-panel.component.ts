import { Component, effect, input, output, signal } from '@angular/core';
import { DialogModule } from 'primeng/dialog';
import { TabViewModule } from 'primeng/tabview';

import { Kindergarten } from '../../models/kindergarten.model';
import { FinancialReportTabComponent } from './financial-report-tab.component';
import { PublicOpinionTabComponent } from './public-opinion-tab.component';
import { RiskAssessmentTabComponent } from './risk-assessment-tab.component';

/**
 * 詳細資料 Tabbed 浮動面板（UI_SPEC §6.3）。
 *
 * PrimeNG Dialog + TabView，開啟時預設顯示第一個 Tab「風險評估」。
 * 三個 Tab：風險評估（雷達圖）、財報（殼）、輿情分析（殼）。
 *
 * 用 [(visible)] 雙向綁定一個內部 signal（visible），而不是把整個 p-dialog
 * 包在 @if 裡並硬綁 [visible]="true"。後者會讓 PrimeNG 內部關閉狀態與外部
 * 輸入不同步，導致叉叉按鈕點了沒反應。
 */
@Component({
  selector: 'app-school-detail-panel',
  standalone: true,
  imports: [
    DialogModule,
    TabViewModule,
    RiskAssessmentTabComponent,
    FinancialReportTabComponent,
    PublicOpinionTabComponent,
  ],
  templateUrl: './school-detail-panel.component.html',
})
export class SchoolDetailPanelComponent {
  /** 要顯示的幼兒園；null 表示關閉 */
  readonly kindergarten = input.required<Kindergarten | null>();
  /** 使用者關閉面板 */
  readonly closed = output<void>();

  /** p-dialog 的 [(visible)] 綁定目標 */
  readonly visible = signal(false);

  constructor() {
    // 父層設定 / 清除 kindergarten 時同步開關 dialog
    effect(
      () => {
        this.visible.set(this.kindergarten() !== null);
      },
      { allowSignalWrites: true },
    );
  }

  /** X 按鈕 / 遮罩點擊會透過 visibleChange 傳來 false */
  onVisibleChange(v: boolean): void {
    this.visible.set(v);
    if (!v) {
      this.closed.emit();
    }
  }

  onHide(): void {
    this.visible.set(false);
    this.closed.emit();
  }
}

