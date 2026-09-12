import { Component, inject, input } from '@angular/core';
import { Router } from '@angular/router';
import { ButtonModule } from 'primeng/button';

import { AuthService } from '../../services/auth.service';

/**
 * 共用 Header / Logo Bar（UI_SPEC §2）。
 *
 * 一般版：Logo（img/icon.png）+ 標題「教育機構風險評估整合平臺」。
 * 登入版（loggedIn=true，供 /admin/dashboard 用）：最右側加登出按鈕，
 *   點擊後清除 Cognito session 並導回 /admin。
 */
@Component({
  selector: 'app-header',
  standalone: true,
  imports: [ButtonModule],
  templateUrl: './header.component.html',
  styleUrl: './header.component.scss',
})
export class HeaderComponent {
  /** 登入版：顯示登出按鈕 */
  readonly loggedIn = input(false);

  private auth = inject(AuthService);
  private router = inject(Router);

  onLogout(): void {
    this.auth.logout();
    void this.router.navigate(['/admin']);
  }
}
