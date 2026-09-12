import { Component, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { ButtonModule } from 'primeng/button';

import { HeaderComponent } from '../../components/header/header.component';
import { IntroAnimationComponent } from '../../components/intro-animation/intro-animation.component';

/**
 * 首頁 /（UI_SPEC §3）。
 *
 * 先播入場動畫（IntroAnimationComponent），結束後過渡到主畫面：
 * 共用 Header + 中央大按鈕「我要回報」→ 導向 /report。
 *
 * TBD（UI_SPEC §7）：動畫觸發時機。目前採保守假設「每次進入 / 皆播放」，
 * 與 IntroAnimationComponent 的註解一致。
 */
@Component({
  selector: 'app-home',
  standalone: true,
  imports: [HeaderComponent, IntroAnimationComponent, ButtonModule],
  templateUrl: './home.component.html',
  styleUrl: './home.component.scss',
})
export class HomeComponent {
  private router = inject(Router);

  /** 入場動畫是否仍在播放 */
  readonly showIntro = signal(true);

  onIntroFinished(): void {
    this.showIntro.set(false);
  }

  goReport(): void {
    void this.router.navigate(['/report']);
  }
}
