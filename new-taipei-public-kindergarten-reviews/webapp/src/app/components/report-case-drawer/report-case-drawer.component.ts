import { Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MessageService } from 'primeng/api';
import { ButtonModule } from 'primeng/button';
import { DropdownModule } from 'primeng/dropdown';
import { InputSwitchModule } from 'primeng/inputswitch';
import { InputTextareaModule } from 'primeng/inputtextarea';
import { MessageModule } from 'primeng/message';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { SelectButtonModule } from 'primeng/selectbutton';
import { SidebarModule } from 'primeng/sidebar';
import { TagModule } from 'primeng/tag';
import { ToastModule } from 'primeng/toast';

import { ReportDetail, ReportStatus } from '../../models/report.model';
import { SecureReportService } from '../../services/secure-report.service';

type MessageKind = 'reply' | 'internal_note';

/** 狀態下拉選項（送出 enum，顯示中文）— 對應 API_SPEC §5.1 */
const STATUS_OPTIONS: { label: string; value: ReportStatus }[] = [
  { label: '已通報', value: 'submitted' },
  { label: '調查中', value: 'investigating' },
  { label: '調查完畢', value: 'closed' },
  { label: '不受理', value: 'rejected' },
];

/**
 * 單筆案件處理 Drawer（UI_SPEC §6.4）。
 *
 * 由案件處理 Tab（CaseManagementComponent）在點選某筆案件時開啟，傳入 reportId。
 * 側邊 Drawer（p-sidebar，右側滑出）內：
 *  - 案件詳情（GET /api/secure/reports/{id}）
 *  - 標記狀態（PATCH，不受理需填理由）
 *  - 回覆家長 / 內部備註（POST messages）
 *  - 明確區分「尚未儲存 / 已儲存」，避免誤以為已送出
 *
 * 狀態或訊息變更成功後，透過 updated 事件通知父層更新清單那一列。
 */
@Component({
  selector: 'app-report-case-drawer',
  standalone: true,
  imports: [
    FormsModule,
    SidebarModule,
    ButtonModule,
    DropdownModule,
    SelectButtonModule,
    InputSwitchModule,
    InputTextareaModule,
    MessageModule,
    TagModule,
    ToastModule,
    ProgressSpinnerModule,
  ],
  providers: [MessageService],
  templateUrl: './report-case-drawer.component.html',
  styleUrl: './report-case-drawer.component.scss',
})
export class ReportCaseDrawerComponent {
  /** 要處理的案件 id；null 表示關閉 Drawer */
  readonly reportId = input.required<number | null>();
  /** 關閉通知父層 */
  readonly closed = output<void>();
  /** 案件狀態被更新時，帶出最新詳情供父層同步清單 */
  readonly updated = output<ReportDetail>();

  private secure = inject(SecureReportService);
  private toast = inject(MessageService);

  readonly statusOptions = STATUS_OPTIONS;
  readonly kindOptions: { label: string; value: MessageKind }[] = [
    { label: '回覆家長', value: 'reply' },
    { label: '內部備註', value: 'internal_note' },
  ];

  readonly visible = signal(false);

  // ---- 詳情 ----
  readonly detailLoading = signal(false);
  readonly detail = signal<ReportDetail | null>(null);

  // ---- 狀態表單（可編輯值） ----
  readonly formStatus = signal<ReportStatus | null>(null);
  readonly formStatusReason = signal('');
  readonly notifyOnStatus = signal(true);
  readonly savingStatus = signal(false);

  // ---- 訊息表單 ----
  readonly messageKind = signal<MessageKind>('reply');
  readonly messageBody = signal('');
  readonly notifyOnReply = signal(true);
  readonly sendingMessage = signal(false);

  /** 狀態表單是否被改動（與伺服器目前值不同）→ 顯示「尚未儲存」 */
  readonly statusDirty = computed(() => {
    const d = this.detail();
    if (!d) {
      return false;
    }
    const reasonChanged =
      this.formStatus() === 'rejected' &&
      (this.formStatusReason().trim() || '') !== (d.statusReason ?? '').trim();
    return this.formStatus() !== d.status || reasonChanged;
  });

  /** 不受理必須填理由 */
  readonly statusReasonRequired = computed(() => this.formStatus() === 'rejected');
  readonly statusReasonMissing = computed(
    () => this.statusReasonRequired() && this.formStatusReason().trim().length === 0,
  );

  readonly canSaveStatus = computed(
    () => this.statusDirty() && !this.statusReasonMissing() && !this.savingStatus(),
  );

  readonly canSendMessage = computed(
    () => this.messageBody().trim().length > 0 && !this.sendingMessage(),
  );

  constructor() {
    effect(
      () => {
        const id = this.reportId();
        this.visible.set(id !== null);
        if (id !== null) {
          this.resetForms();
          this.loadDetail(id);
        }
      },
      { allowSignalWrites: true },
    );
  }

  private loadDetail(id: number): void {
    this.detail.set(null);
    this.detailLoading.set(true);
    this.secure.getReport(id).subscribe({
      next: (d) => {
        this.applyDetail(d);
        this.detailLoading.set(false);
      },
      error: () => {
        this.detailLoading.set(false);
        this.toast.add({ severity: 'error', summary: '載入失敗', detail: '無法載入案件詳情' });
      },
    });
  }

  /** 把伺服器詳情套進表單，並清掉 dirty（表單值 = 伺服器值） */
  private applyDetail(d: ReportDetail): void {
    this.detail.set(d);
    this.formStatus.set(d.status);
    this.formStatusReason.set(d.statusReason ?? '');
    this.notifyOnStatus.set(true);
    this.messageKind.set('reply');
    this.messageBody.set('');
    this.notifyOnReply.set(true);
  }

  // ---------- A. 儲存狀態 ----------
  saveStatus(): void {
    const d = this.detail();
    const status = this.formStatus();
    if (!d || !status || !this.canSaveStatus()) {
      return;
    }
    if (status === 'rejected' && this.formStatusReason().trim().length === 0) {
      this.toast.add({ severity: 'warn', summary: '需要理由', detail: '不受理必須填寫理由' });
      return;
    }
    this.savingStatus.set(true);
    this.secure
      .patchReport(d.id, {
        status,
        statusReason: status === 'rejected' ? this.formStatusReason().trim() : null,
        notifyParent: this.notifyOnStatus(),
      })
      .subscribe({
        next: (updated) => {
          this.applyDetail(updated); // 覆蓋畫面、清 dirty
          this.updated.emit(updated); // 通知父層同步清單
          this.savingStatus.set(false);
          this.toast.add({ severity: 'success', summary: '已儲存', detail: '處理進度已更新' });
        },
        error: (err: unknown) => {
          this.savingStatus.set(false);
          this.toast.add({
            severity: 'error',
            summary: '儲存失敗',
            detail: this.apiErr(err) ?? '更新狀態時發生錯誤',
          });
        },
      });
  }

  // ---------- B. 送出訊息 ----------
  sendMessage(): void {
    const d = this.detail();
    const body = this.messageBody().trim();
    if (!d || body.length === 0 || !this.canSendMessage()) {
      return;
    }
    const kind = this.messageKind();
    this.sendingMessage.set(true);
    this.secure
      .addMessage(d.id, {
        kind,
        body,
        notifyParent: kind === 'reply' ? this.notifyOnReply() : undefined,
      })
      .subscribe({
        next: (res) => {
          const cur = this.detail();
          if (cur) {
            this.detail.set({ ...cur, messages: [...cur.messages, res.message] });
          }
          this.messageBody.set('');
          this.sendingMessage.set(false);
          const emailed = kind === 'reply' && res.emailed;
          this.toast.add({
            severity: 'success',
            summary: kind === 'reply' ? '已送出回覆' : '已儲存內部備註',
            detail:
              kind === 'reply'
                ? emailed
                  ? '回覆已送出，並已 email 通知家長'
                  : '回覆已送出'
                : '內部備註已儲存（不會通知家長）',
          });
        },
        error: (err: unknown) => {
          this.sendingMessage.set(false);
          this.toast.add({
            severity: 'error',
            summary: '送出失敗',
            detail: this.apiErr(err) ?? '送出訊息時發生錯誤',
          });
        },
      });
  }

  // ---------- 關閉 ----------
  onVisibleChange(v: boolean): void {
    this.visible.set(v);
    if (!v) {
      this.closed.emit();
    }
  }

  private resetForms(): void {
    this.formStatus.set(null);
    this.formStatusReason.set('');
    this.messageKind.set('reply');
    this.messageBody.set('');
  }

  // ---------- helpers ----------
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

  private apiErr(err: unknown): string | null {
    const e = err as { error?: { message?: string } };
    return e?.error?.message ?? null;
  }
}
