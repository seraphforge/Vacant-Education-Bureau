import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, delay, of } from 'rxjs';

import { environment } from '../../environments/environment';
import { Kindergarten, KindergartenPage, KindergartenQuery } from '../models/kindergarten.model';

/** GET /api/secure/me 的回應（API_SPEC §4.1） */
export interface MeResponse {
  username: string;
  /** 承辦人顯示名稱，可能為 null */
  displayName?: string | null;
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
 *
 * /api/secure/me 與 /api/secure/kindergartens 在 API_SPEC 標記「已上線（會擴充）」，
 * 但 risk_score / risk_level 尚未計算。為了能在後端補完前驗證表格顏色標記，
 * useMockApi=true 時 kindergartens() 回傳涵蓋三種風險區間的假資料。
 */
@Injectable({ providedIn: 'root' })
export class SecureApiService {
  private http = inject(HttpClient);
  private base = `${environment.apiBaseUrl}/api/secure`;
  private mock = environment.useMockApi;

  /** 我是誰、我能看哪個範圍 */
  me(): Observable<MeResponse> {
    if (this.mock) {
      return of<MeResponse>({
        username: 'ntpc.chen',
        displayName: '陳承辦',
        county: '新北市',
        agency: '新北市教育局',
        groups: ['staff'],
        isAdmin: false,
        scope: '新北市',
      }).pipe(delay(200));
    }
    return this.http.get<MeResponse>(`${this.base}/me`);
  }

  /** 範圍內的幼兒園清單（含 risk_score / risk_level） */
  kindergartens(query: KindergartenQuery = {}): Observable<KindergartenPage> {
    if (this.mock) {
      return of(this.mockKindergartens(query)).pipe(delay(300));
    }
    let params = new HttpParams();
    Object.entries(query).forEach(([key, value]) => {
      if (value !== null && value !== undefined && value !== '') {
        params = params.set(key, String(value));
      }
    });
    return this.http.get<KindergartenPage>(`${this.base}/kindergartens`, { params });
  }

  /**
   * mock 幼兒園清單。刻意涵蓋三種風險區間以驗證整列文字顏色（UI_SPEC §6.2.1）：
   *   high  ≥ 80  → 紫色
   *   medium 60–79 → 紅色
   *   normal < 60  → 預設
   *   null（未計算） → 預設
   */
  private mockKindergartens(query: KindergartenQuery): KindergartenPage {
    const all: Kindergarten[] = [
      {
        id: 1234,
        academic_year: '114',
        code: 'N01',
        school_name: '新北市私立安溪幼兒園',
        ownership: '私立',
        county: '新北市',
        district: '板橋區',
        address: '新北市板橋區文化路一段100號',
        phone: '02-1234-5678',
        risk_score: 83.5,
        risk_level: 'high',
      },
      {
        id: 1235,
        academic_year: '114',
        code: 'N02',
        school_name: '新北市私立山北幼兒園',
        ownership: '私立',
        county: '新北市',
        district: '三峽區',
        address: '新北市三峽區大學路50號',
        phone: '02-2345-6789',
        risk_score: 72,
        risk_level: 'medium',
      },
      {
        id: 1236,
        academic_year: '114',
        code: 'N03',
        school_name: '新北市公立幸福幼兒園',
        ownership: '公立',
        county: '新北市',
        district: '中和區',
        address: '新北市中和區中山路二段200號',
        phone: '02-3456-7890',
        risk_score: 45,
        risk_level: 'normal',
      },
      {
        id: 1237,
        academic_year: '114',
        code: 'N04',
        school_name: '新北市私立晨光幼兒園',
        ownership: '私立',
        county: '新北市',
        district: '新莊區',
        address: '新北市新莊區中正路300號',
        phone: '02-4567-8901',
        risk_score: null,
        risk_level: null,
      },
    ];
    const page = query.page ?? 1;
    const pageSize = query.pageSize ?? 20;
    let items = all;
    if (query.name) {
      items = items.filter((i) => i.school_name.includes(query.name as string));
    }
    if (query.ownership) {
      items = items.filter((i) => i.ownership === query.ownership);
    }
    return {
      items: items.slice((page - 1) * pageSize, page * pageSize),
      total: items.length,
      page,
      pageSize,
      academicYear: '114',
      county: '新北市',
    };
  }
}
