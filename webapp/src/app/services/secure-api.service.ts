import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';
import { KindergartenPage, KindergartenQuery } from '../models/kindergarten.model';

/** GET /api/secure/me 的回應 */
export interface MeResponse {
  username: string;
  county: string | null;
  agency: string | null;
  groups: string[];
  isAdmin: boolean;
  /** 後端算出來的資料範圍描述，例如「新北市」或「全國」 */
  scope: string;
}

/**
 * 需要登入的 API。
 *
 * 所有路徑都在 /api/secure/ 底下，authInterceptor 會自動附上 ID token。
 * 縣市範圍是後端依 token 決定的，這裡送 county 參數對一般人員無效。
 */
@Injectable({ providedIn: 'root' })
export class SecureApiService {
  private http = inject(HttpClient);
  private base = `${environment.apiBaseUrl}/api/secure`;

  /** 我是誰、我能看哪個範圍 */
  me(): Observable<MeResponse> {
    return this.http.get<MeResponse>(`${this.base}/me`);
  }

  /** 範圍內的幼兒園清單（示範 row-level 權限，未來功能照這個模式接） */
  kindergartens(query: KindergartenQuery = {}): Observable<KindergartenPage> {
    let params = new HttpParams();
    Object.entries(query).forEach(([key, value]) => {
      if (value !== null && value !== undefined && value !== '') {
        params = params.set(key, String(value));
      }
    });
    return this.http.get<KindergartenPage>(`${this.base}/kindergartens`, { params });
  }
}
