import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';
import {
  AcademicYearOption,
  CountyOption,
  KindergartenPage,
  KindergartenPunishments,
  KindergartenQuery,
} from '../models/kindergarten.model';

/** 負責跟後端 API 溝通，畫面元件只呼叫這裡的方法 */
@Injectable({ providedIn: 'root' })
export class KindergartenService {
  private http = inject(HttpClient);
  private base = environment.apiBaseUrl;

  /** 縣市清單（含各縣市資料筆數） */
  getCounties(): Observable<{ items: CountyOption[] }> {
    return this.http.get<{ items: CountyOption[] }>(`${this.base}/api/counties`);
  }

  /** 學年度清單 */
  getAcademicYears(): Observable<{ items: AcademicYearOption[] }> {
    return this.http.get<{ items: AcademicYearOption[] }>(`${this.base}/api/academic-years`);
  }

  /** 依條件查詢幼兒園（伺服器端分頁） */
  search(query: KindergartenQuery): Observable<KindergartenPage> {
    let params = new HttpParams();
    Object.entries(query).forEach(([key, value]) => {
      if (value !== null && value !== undefined && value !== '') {
        params = params.set(key, String(value));
      }
    });
    return this.http.get<KindergartenPage>(`${this.base}/api/kindergartens`, { params });
  }

  /**
   * 單一幼兒園的裁罰紀錄（含罰鍰總額）。
   *
   * 這是公開端點，authInterceptor 不會帶 token（裁罰紀錄本身就是公開資訊），
   * 只是目前僅在政府端的詳細資料面板使用。
   */
  getPunishments(kindergartenId: number): Observable<KindergartenPunishments> {
    return this.http.get<KindergartenPunishments>(
      `${this.base}/api/kindergartens/${kindergartenId}/punishments`,
    );
  }
}
