import { Routes } from '@angular/router';

import { authGuard } from './guards/auth.guard';

export const routes: Routes = [
  // 公開：不需登入
  {
    path: 'kindergartens',
    loadComponent: () =>
      import('./pages/kindergarten-list/kindergarten-list.component').then(
        (m) => m.KindergartenListComponent,
      ),
  },
  {
    path: 'login',
    loadComponent: () => import('./pages/login/login.component').then((m) => m.LoginComponent),
  },

  // 需登入：家長回報 / 財報 / 風險分析都會掛在這底下
  {
    path: 'dashboard',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./pages/dashboard/dashboard.component').then((m) => m.DashboardComponent),
  },

  { path: '', redirectTo: 'kindergartens', pathMatch: 'full' },
  { path: '**', redirectTo: 'kindergartens' },
];
