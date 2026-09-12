import { Injectable, computed, signal } from '@angular/core';
import {
  AuthenticationDetails,
  CognitoUser,
  CognitoUserPool,
  CognitoUserSession,
} from 'amazon-cognito-identity-js';
import { Observable, from } from 'rxjs';

import { environment } from '../../environments/environment';

/** 從 ID token 解出來的使用者資訊 */
export interface CurrentUser {
  username: string;
  county: string;
  agency: string;
  groups: string[];
  isAdmin: boolean;
}

/**
 * Cognito 登入。
 *
 * 用 amazon-cognito-identity-js 直接對 User Pool 認證（SRP 流程，
 * 密碼不會以明文送出）。登入成功後 SDK 會把 token 存進 localStorage，
 * 重新整理頁面仍然是登入狀態。
 *
 * 注意這裡一律使用 **ID token**，不是 access token：
 * custom:county 這類自訂屬性只會出現在 ID token 裡。
 */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private pool = new CognitoUserPool({
    UserPoolId: environment.cognitoUserPoolId,
    ClientId: environment.cognitoClientId,
  });

  /** 目前登入者；null = 未登入 */
  readonly user = signal<CurrentUser | null>(null);
  readonly isLoggedIn = computed(() => this.user() !== null);

  constructor() {
    // 頁面重新載入時，把先前存的 session 還原回來
    void this.restoreSession();
  }

  login(username: string, password: string): Observable<CurrentUser> {
    const cognitoUser = new CognitoUser({ Username: username, Pool: this.pool });
    const details = new AuthenticationDetails({ Username: username, Password: password });

    return from(
      new Promise<CurrentUser>((resolve, reject) => {
        cognitoUser.authenticateUser(details, {
          onSuccess: (session) => {
            const user = this.toCurrentUser(session);
            this.user.set(user);
            resolve(user);
          },
          onFailure: (err) => reject(err),
          // 管理者建立帳號時若沒有加 --permanent，第一次登入會走到這裡。
          // create-user.ps1 已經設成永久密碼，所以正常不會發生。
          newPasswordRequired: () =>
            reject(new Error('此帳號需要重設密碼，請聯絡管理者重新開通')),
        });
      }),
    );
  }

  logout(): void {
    this.pool.getCurrentUser()?.signOut();
    this.user.set(null);
  }

  /** 取得有效的 ID token 給 HTTP interceptor 用；過期會自動用 refresh token 換新的 */
  getIdToken(): Promise<string | null> {
    return new Promise((resolve) => {
      const cognitoUser = this.pool.getCurrentUser();
      if (!cognitoUser) {
        resolve(null);
        return;
      }
      cognitoUser.getSession((err: Error | null, session: CognitoUserSession | null) => {
        if (err || !session?.isValid()) {
          resolve(null);
          return;
        }
        resolve(session.getIdToken().getJwtToken());
      });
    });
  }

  private restoreSession(): Promise<void> {
    return new Promise((resolve) => {
      const cognitoUser = this.pool.getCurrentUser();
      if (!cognitoUser) {
        resolve();
        return;
      }
      cognitoUser.getSession((err: Error | null, session: CognitoUserSession | null) => {
        if (!err && session?.isValid()) {
          this.user.set(this.toCurrentUser(session));
        }
        resolve();
      });
    });
  }

  private toCurrentUser(session: CognitoUserSession): CurrentUser {
    const claims = session.getIdToken().payload as Record<string, unknown>;
    const groups = (claims['cognito:groups'] as string[] | undefined) ?? [];
    return {
      username: (claims['cognito:username'] as string) ?? '',
      county: (claims['custom:county'] as string) ?? '',
      agency: (claims['custom:agency'] as string) ?? '',
      groups,
      isAdmin: groups.includes('admin'),
    };
  }
}
