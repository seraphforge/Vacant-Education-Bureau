import { Component, OnInit, inject } from '@angular/core';
import { ButtonModule } from 'primeng/button';
import { CardModule } from 'primeng/card';
import { MessageModule } from 'primeng/message';
import { TagModule } from 'primeng/tag';

import { AuthService } from '../../services/auth.service';
import { MeResponse, SecureApiService } from '../../services/secure-api.service';

/** 未來功能的入口，UI 還在設計中 */
interface FeatureCard {
  icon: string;
  title: string;
  description: string;
}

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CardModule, ButtonModule, MessageModule, TagModule],
  templateUrl: './dashboard.component.html',
  styleUrl: './dashboard.component.scss',
})
export class DashboardComponent implements OnInit {
  private api = inject(SecureApiService);
  private auth = inject(AuthService);

  me: MeResponse | null = null;
  scopedTotal: number | null = null;
  loading = true;
  errorMessage = '';

  readonly features: FeatureCard[] = [
    {
      icon: 'pi pi-comments',
      title: '家長回報',
      description: '接收並檢視轄內幼兒園的家長第一手回報',
    },
    {
      icon: 'pi pi-chart-line',
      title: '財報分析',
      description: '追蹤各園所財務報表的年度變化與異常訊號',
    },
    {
      icon: 'pi pi-exclamation-triangle',
      title: '風險指數',
      description: '綜合公開輿論、財報與回報資料的 AI 風險評估',
    },
  ];

  ngOnInit(): void {
    // 兩支都是受保護端點，用來確認 token 真的通得過後端驗證
    this.api.me().subscribe({
      next: (res) => {
        this.me = res;
        this.loading = false;
      },
      error: (err) => {
        this.loading = false;
        this.errorMessage = `無法取得身分資訊：${err.status ?? ''} ${err.message ?? ''}`;
      },
    });

    this.api.kindergartens({ pageSize: 1 }).subscribe({
      next: (res) => (this.scopedTotal = res.total),
      error: () => (this.scopedTotal = null),
    });
  }

  get username(): string {
    return this.auth.user()?.username ?? '';
  }
}
