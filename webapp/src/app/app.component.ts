import { Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';

/**
 * 應用外殼：只放 router-outlet。
 * 各頁面自行套用共用的 HeaderComponent（UI_SPEC §2），
 * 因此這裡不再放全域導覽列。
 */
@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet],
  template: '<router-outlet></router-outlet>',
})
export class AppComponent {}
