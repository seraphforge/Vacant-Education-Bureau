import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';

import { AuthService } from '../services/auth.service';

/**
 * 沒登入就導去 /admin（登入頁），並記下原本想去的網址，登入後導回去。
 *
 * 提醒：這只是「介面上的擋」，真正的資料保護在後端
 * （API Gateway 的 JWT authorizer + Lambda 用 token claims 過濾縣市）。
 * 前端 guard 被繞過也拿不到別人的資料。
 */
export const authGuard: CanActivateFn = async (_route, state) => {
  const auth = inject(AuthService);
  const router = inject(Router);

  const token = await auth.getIdToken();
  if (token) {
    return true;
  }

  void router.navigate(['/admin'], { queryParams: { redirect: state.url } });
  return false;
};
