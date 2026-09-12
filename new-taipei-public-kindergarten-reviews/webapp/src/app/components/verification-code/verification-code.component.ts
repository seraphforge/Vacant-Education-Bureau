import { Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { DialogModule } from 'primeng/dialog';
import { InputOtpModule } from 'primeng/inputotp';
import { MessageModule } from 'primeng/message';

import { CreateDraftResponse } from '../../models/report.model';
import { ReportService } from '../../services/report.service';

/**
 * 驗證碼輸入 Modal（UI_SPEC §4.2、API_SPEC §2.6/§2.7）。
 *
 * 由 ReportFormComponent 在建立草稿成功後開啟，傳入 draft 資訊。
 * 使用者輸入 6 位數驗證碼 → verifyOtp → 成功發出 verified(trackingUrl)。
 * 可重寄（resendOtp）。錯太多次（OTP_LOCKED）發出 locked 事件請家長重填。
 *
 * dev mode：draft.devOtp 存在時預填驗證碼並顯示「開發模式」提示（API_SPEC §0.7）。
 */
@Component({
  selector: 'app-verification-code',
  standalone: true,
  imports: [FormsModule, DialogModule, InputOtpModule, ButtonModule, MessageModule],
  templateUrl: './verification-code.component.html',
  styleUrl: './verification-code.component.scss',
})
export class VerificationCodeComponent {
  /** 由父層以 model 方式雙向綁定顯示狀態 */
  readonly visible = input.required<boolean>();
  /** 建立草稿的回應（含 draftId、遮蔽 email、過期時間、devOtp） */
  readonly draft = input.required<CreateDraftResponse | null>();

  /** 驗證成功，帶出 trackingUrl 供父層導頁 */
  readonly verified = output<string>();
  /** 草稿作廢（OTP_LOCKED），請家長重填 */
  readonly locked = output<void>();
  /** 使用者關閉 Modal */
  readonly closed = output<void>();

  private reportSvc = inject(ReportService);

  readonly code = signal('');
  /** 供 p-inputOtp 的 [(ngModel)] 綁定 */
  get codeModel(): string {
    return this.code();
  }
  set codeModel(v: string) {
    this.code.set(v ?? '');
  }
  onCodeChange(event: { value: string }): void {
    this.code.set(event?.value ?? '');
  }

  readonly submitting = signal(false);
  readonly resending = signal(false);
  readonly errorMsg = signal<string | null>(null);
  readonly attemptsLeft = signal<number | null>(null);
  readonly expiresAt = signal<string | null>(null);

  /** dev mode 提示：devOtp 存在時顯示 */
  readonly devOtp = computed(() => this.draft()?.devOtp ?? null);
  readonly maskedEmail = computed(() => this.draft()?.reporterEmailMasked ?? '');

  constructor() {
    // 草稿變更時（第一次開或重寄）：重置狀態、若有 devOtp 就預填。
    // effect 內要寫 signal 需開 allowSignalWrites。
    effect(
      () => {
        const d = this.draft();
        if (d) {
          this.expiresAt.set(d.otpExpiresAt);
          this.errorMsg.set(null);
          this.attemptsLeft.set(null);
          this.code.set(d.devOtp ?? '');
        }
      },
      { allowSignalWrites: true },
    );
  }

  submit(): void {
    const d = this.draft();
    const c = this.code().trim();
    if (!d || c.length !== 6) {
      this.errorMsg.set('請輸入 6 位數驗證碼');
      return;
    }
    this.submitting.set(true);
    this.errorMsg.set(null);
    this.reportSvc.verifyOtp(d.draftId, c).subscribe({
      next: (res) => {
        this.submitting.set(false);
        this.verified.emit(res.trackingUrl);
      },
      error: (err) => {
        this.submitting.set(false);
        const code = err?.error?.code as string | undefined;
        const message = err?.error?.message as string | undefined;
        this.code.set('');
        if (code === 'OTP_INVALID') {
          const left = err?.error?.detail?.attemptsLeft as number | undefined;
          this.attemptsLeft.set(left ?? null);
          this.errorMsg.set(message ?? '驗證碼不正確');
        } else if (code === 'OTP_EXPIRED') {
          this.errorMsg.set(message ?? '驗證碼已過期，請按「重寄」');
        } else if (code === 'OTP_LOCKED') {
          this.errorMsg.set(message ?? '錯誤次數過多，請重新填表');
          this.locked.emit();
        } else if (code === 'DRAFT_ALREADY_VERIFIED') {
          // 已成案（重複送出）：忽略，關閉即可
          this.errorMsg.set(message ?? '此回報已送出');
        } else {
          this.errorMsg.set(message ?? '驗證失敗，請稍後再試');
        }
      },
    });
  }

  resend(): void {
    const d = this.draft();
    if (!d) {
      return;
    }
    this.resending.set(true);
    this.errorMsg.set(null);
    this.reportSvc.resendOtp(d.draftId).subscribe({
      next: (res) => {
        this.resending.set(false);
        this.expiresAt.set(res.otpExpiresAt);
        this.attemptsLeft.set(null);
        // dev mode 重寄也會回 devOtp，直接預填
        if (res.devOtp) {
          this.code.set(res.devOtp);
        }
      },
      error: () => {
        this.resending.set(false);
        this.errorMsg.set('重寄失敗，請稍後再試');
      },
    });
  }

  onHide(): void {
    this.closed.emit();
  }
}
