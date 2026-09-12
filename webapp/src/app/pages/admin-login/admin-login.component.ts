import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule, NgForm } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { ButtonModule } from 'primeng/button';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { PasswordModule } from 'primeng/password';
import { ProgressSpinnerModule } from 'primeng/progressspinner';

import { HeaderComponent } from '../../components/header/header.component';
import { AuthService } from '../../services/auth.service';

/**
 * 政府機關登入頁 /admin（UI_SPEC §6.1）。
 *
 * 隱藏入口：公開頁面不提供任何連結，僅能直接輸入網址。
 * 帳號 / 密碼 → AWS Cognito 認證（AuthService）。
 * 成功導向 /admin/dashboard（或 authGuard 帶來的 redirect 目的地）。
 */
@Component({
  selector: 'app-admin-login',
  standalone: true,
  imports: [
    FormsModule,
    HeaderComponent,
    InputTextModule,
    PasswordModule,
    ButtonModule,
    MessageModule,
    ProgressSpinnerModule,
  ],
  templateUrl: './admin-login.component.html',
  styleUrl: './admin-login.component.scss',
})
export class AdminLoginComponent implements OnInit {
  private auth = inject(AuthService);
  private router = inject(Router);
  private route = inject(ActivatedRoute);

  username = '';
  password = '';

  readonly submitting = signal(false);
  readonly errorMsg = signal<string | null>(null);
  /** 檢查既有 session 期間先不顯示登入表單，避免閃一下 */
  readonly checkingSession = signal(true);

  ngOnInit(): void {
    // 已登入（有有效 token）就直接導向 dashboard，不用再登入一次
    void this.auth.getIdToken().then((token) => {
      if (token) {
        const redirect = this.route.snapshot.queryParamMap.get('redirect');
        void this.router.navigateByUrl(redirect || '/admin/dashboard');
      } else {
        this.checkingSession.set(false);
      }
    });
  }

  submit(form: NgForm): void {
    if (form.invalid) {
      Object.values(form.controls).forEach((c) => c.markAsTouched());
      return;
    }
    this.submitting.set(true);
    this.errorMsg.set(null);

    this.auth.login(this.username.trim(), this.password).subscribe({
      next: () => {
        this.submitting.set(false);
        const redirect = this.route.snapshot.queryParamMap.get('redirect');
        void this.router.navigateByUrl(redirect || '/admin/dashboard');
      },
      error: (err: unknown) => {
        this.submitting.set(false);
        this.errorMsg.set(this.mapError(err));
      },
    });
  }

  private mapError(err: unknown): string {
    const e = err as { code?: string; message?: string };
    // Cognito 常見錯誤碼轉中文
    switch (e?.code) {
      case 'NotAuthorizedException':
        return '帳號或密碼錯誤';
      case 'UserNotFoundException':
        return '找不到此帳號';
      case 'UserNotConfirmedException':
        return '此帳號尚未啟用，請聯絡管理者';
      case 'PasswordResetRequiredException':
        return '此帳號需要重設密碼，請聯絡管理者';
      case 'TooManyRequestsException':
      case 'LimitExceededException':
        return '嘗試次數過多，請稍後再試';
      default:
        return e?.message || '登入失敗，請稍後再試';
    }
  }
}
