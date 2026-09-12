import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { ButtonModule } from 'primeng/button';
import { CardModule } from 'primeng/card';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { PasswordModule } from 'primeng/password';

import { AuthService } from '../../services/auth.service';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [
    FormsModule,
    RouterLink,
    ButtonModule,
    CardModule,
    InputTextModule,
    MessageModule,
    PasswordModule,
  ],
  templateUrl: './login.component.html',
  styleUrl: './login.component.scss',
})
export class LoginComponent {
  private auth = inject(AuthService);
  private router = inject(Router);

  username = '';
  password = '';
  loading = false;
  errorMessage = '';

  onSubmit(): void {
    if (!this.username || !this.password) {
      this.errorMessage = '請輸入帳號與密碼';
      return;
    }
    this.loading = true;
    this.errorMessage = '';

    this.auth.login(this.username.trim(), this.password).subscribe({
      next: () => {
        this.loading = false;
        const redirect =
          new URLSearchParams(window.location.search).get('redirect') ?? '/dashboard';
        void this.router.navigateByUrl(redirect);
      },
      error: (err: Error) => {
        this.loading = false;
        this.errorMessage = this.friendlyError(err);
      },
    });
  }

  /** Cognito 的錯誤訊息是英文的，轉成看得懂的中文 */
  private friendlyError(err: Error & { code?: string }): string {
    switch (err.code) {
      case 'NotAuthorizedException':
        return '帳號或密碼錯誤';
      case 'UserNotFoundException':
        return '找不到這個帳號';
      case 'PasswordResetRequiredException':
        return '此帳號需要重設密碼，請聯絡管理者';
      case 'TooManyRequestsException':
        return '嘗試次數過多，請稍後再試';
      default:
        return err.message || '登入失敗';
    }
  }
}
