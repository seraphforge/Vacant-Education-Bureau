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

import { Kindergarten } from '../../models/kindergarten.model';
import {
  ReportDetail,
  ReportListItem,
  ReportStatus,
} from '../../models/report.model';
import { SecureReportService } from '../../services/secure-report.service';

type MessageKind = 'reply' | 'internal_note';

/** 狀態下拉選項（送出 enum，顯示中文）— 對應 API_SPEC §5.1 */
const STATUS_OPTIONS: { label: string; value: ReportStatus }[] = [
  { label: '已報報', value: 'submitted' },
  { label: '調查中', value: 'investigating' },
  { label: '調查完畢', value: 'closed' },
  { label: '不受理', value: 'rejected' },
];

/**
 * 行政人員處理進度標記與回覆（UI_SPEC §6.4）。
 *
 * 側邊 Drawer（p-sidebar，右側滑出）：
 *  1. 依 kindergartenId 列出該園家長回報案件（GET /api/secure/reports）
 *  2. 選一筆看詳情（GET /api/secure/reports/{id}）
 *  3. 標記狀態（PATCH，不受理需填理由）、回覆家長 / 內部備註（POST messages）
 *  4. 明確區分「尚未儲存 / 已儲存」，避免誤以為已送出
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
  /** 要處理的幼兒園；null 表示關閉 Drawer */
  readonly kindergarten = input.required<Kindergarten | null>();
  /** 關閉通知父層 */
  readonly closed = output<void>();

  private secure = inject(SecureReportService);
  private toast = inject(MessageService);

  readonly statusOptions = STATUS_OPTIONS;
  readonly kindOptions: { label: string; value: MessageKind }[] = [
    { label: '回覆家長', value: 'reply' },
    { label: '內部備註', value: 'internal_note' },
  ];

  readonly visible = signal(false);

  // ---- 清單 ----
  readonly listLoading = signal(false);
  readonly reports = signal<ReportListItem[]>([]);

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
        const kg = this.kindergarten();
        this.visible.set(kg !== null);
        if (kg) {
          this.resetAll();
          this.loadList(kg.id);
        }
      },
      { allowSignalWrites: true },
    );
  }

  // ---------- 清單 ----------
  private loadList(kindergartenId: number): void {
    this.listLoading.set(true);
    this.secure.listReports({ kindergartenId, pageSize: 100 }).subscribe({
      next: (res) => {
        this.reports.set(res.items);
        this.listLoading.set(false);
      },
      error: () => {
        this.reports.set([]);
        this.listLoading.set(false);
        this.toast.add({ severity: 'error', summary: '載入失敗', detail: '無法載入回報清單' });
      },
    });
  }

  openCase(item: ReportListItem): void {
    this.loadDetail(item.id);
  }

  backToList(): void {
    this.detail.set(null);
  }

  private loadDetail(id: number): void {
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
    // 重置訊息輸入
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
          this.syncListItem(updated);
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
        // notifyParent 僅 reply 有效
        notifyParent: kind === 'reply' ? this.notifyOnReply() : undefined,
      })
      .subscribe({
        next: (res) => {
          // 把新訊息加進詳情的訊息串
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

  /** 詳情狀態更新後，同步更新左側清單那一列 */
  private syncListItem(d: ReportDetail): void {
    this.reports.update((items) =>
      items.map((it) =>
        it.id === d.id ? { ...it, status: d.status, statusLabel: d.statusLabel } : it,
      ),
    );
  }

  // ---------- 關閉 ----------
  onVisibleChange(v: boolean): void {
    this.visible.set(v);
    if (!v) {
      this.closed.emit();
    }
  }

  private resetAll(): void {
    this.reports.set([]);
    this.detail.set(null);
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
