import { Routes } from '@angular/router';

export const routes: Routes = [
  {
    path: 'kindergartens',
    loadComponent: () =>
      import('./pages/kindergarten-list/kindergarten-list.component').then(
        (m) => m.KindergartenListComponent,
      ),
  },
  { path: '', redirectTo: 'kindergartens', pathMatch: 'full' },
  { path: '**', redirectTo: 'kindergartens' },
];
