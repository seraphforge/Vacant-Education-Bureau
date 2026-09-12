import { Routes } from '@angular/router';

import { authGuard } from './guards/auth.guard';

/**
 * 路由對照 UI_SPEC.md §1：
 *   /                  首頁（入場動畫 + 回報入口）      公開
 *   /report            回報表單頁                        公開
 *   /report/<TOKEN>    回報進度追蹤頁                    公開（憑 TOKEN）
 *   /admin             政府機關登入頁                    隱藏入口
 *   /admin/dashboard   資料整合主頁面                    登入後
 */
export const routes: Routes = [
  {
    path: '',
    loadComponent: () => import('./pages/home/home.component').then((m) => m.HomeComponent),
  },
  {
    path: 'report',
    loadComponent: () =>
      import('./pages/report-form/report-form.component').then((m) => m.ReportFormComponent),
  },
  {
    // 公開追蹤頁：不掛 authGuard，也不呼叫任何 /api/secure/ 端點（API_SPEC §7.5）
    path: 'report/:token',
    loadComponent: () =>
      import('./pages/report-tracking/report-tracking.component').then(
        (m) => m.ReportTrackingComponent,
      ),
  },
  {
    // 隱藏入口：公開頁面不提供連結，僅能直接輸入網址
    path: 'admin',
    loadComponent: () =>
      import('./pages/admin-login/admin-login.component').then((m) => m.AdminLoginComponent),
  },
  {
    path: 'admin/dashboard',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./pages/admin-dashboard/admin-dashboard.component').then(
        (m) => m.AdminDashboardComponent,
      ),
  },

  { path: '**', redirectTo: '' },
];
