import { HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { from, switchMap } from 'rxjs';

import { AuthService } from '../services/auth.service';

/**
 * 只對 /api/secure/ 的請求加上 Authorization header。
 *
 * 公開端點（例如 /api/kindergartens）刻意不帶 token，
 * 這樣即使沒登入也能正常查詢。
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  if (!req.url.includes('/api/secure/')) {
    return next(req);
  }

  const auth = inject(AuthService);
  return from(auth.getIdToken()).pipe(
    switchMap((token) =>
      next(
        token
          ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } })
          : req,
      ),
    ),
  );
};
