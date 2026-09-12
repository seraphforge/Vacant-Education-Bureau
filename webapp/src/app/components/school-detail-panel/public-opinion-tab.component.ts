import { NgTemplateOutlet } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, computed, effect, inject, input, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ButtonModule } from 'primeng/button';
import { MessageModule } from 'primeng/message';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { TableModule } from 'primeng/table';
import { TagModule } from 'primeng/tag';
import { Subscription, interval } from 'rxjs';

import { OpinionItem, OpinionLatest, OpinionScanJob } from '../../models/report.model';
import { SecureReportService } from '../../services/secure-report.service';

/**
 * 詳細資料 Tab 3：輿情分析（UI_SPEC §6.3 Tab 3）。
 *
 * 政府承辦人按一個按鈕，後端 worker 去蒐集公開資訊、用 Bedrock 判讀歸屬並摘要，
 * 前端輪詢進度後把結果列成表格。
 *
 * 為什麼要輪詢：整段分析要數十秒到數分鐘，遠超 API Gateway 的 29 秒上限，
 * 所以是 job 模式（POST 建立 -> GET 查進度）。
 *
 * 免責標示的規則（不要改成自己的文案）：
 *   * disclaimer 由後端提供，直接顯示。
 *   * 「可歸屬本園」與「待確認」一定要分開呈現，同名園所很常見。
 *   * verified 標籤只代表來源可核對，不代表指控成立，所以文字寫「來源可核對」。
 */
@Component({
  selector: 'app-public-opinion-tab',
  standalone: true,
  imports: [
    NgTemplateOutlet,
    ButtonModule,
    MessageModule,
    ProgressSpinnerModule,
    TableModule,
    TagModule,
  ],
  templateUrl: './public-opinion-tab.component.html',
  styleUrls: ['./tab-empty.scss', './public-opinion-tab.component.scss'],
})
export class PublicOpinionTabComponent {
  /** 幼兒園 id；變更時重新載入 */
  readonly kindergartenId = input.required<number>();

  private secure = inject(SecureReportService);
  private destroyRef = inject(DestroyRef);

  readonly loading = signal(true);
  readonly starting = signal(false);
  readonly data = signal<OpinionLatest | null>(null);
  readonly job = signal<OpinionScanJob | null>(null);
  readonly errorMessage = signal<string | null>(null);

  private poller: Subscription | null = null;

  /** 掃描中（含排隊）時要鎖住按鈕並顯示進度 */
  readonly running = computed(() => {
    const status = this.job()?.status;
    return status === 'queued' || status === 'searching' || status === 'analyzing';
  });

  readonly items = computed(() => this.data()?.items ?? []);

  /** 進度說明。順便交代「為什麼慢」——蒐集刻意放慢是規範不是效能問題。 */
  readonly progressText = computed(
    () =>
      `${this.job()?.statusLabel ?? '處理中'}…（每 4 秒更新一次；` +
      `蒐集會刻意放慢速度並遵守來源網站的 robots 規範）`,
  );

  /** 能明確歸屬到本園的項目；這是承辦人真正要看的 */
  readonly confirmedItems = computed(() =>
    this.items().filter((i) => i.attribution === 'confirmed'),
  );

  /** 同名或無法確認歸屬的，分開顯示避免誤會 */
  readonly ambiguousItems = computed(() =>
    this.items().filter((i) => i.attribution === 'ambiguous'),
  );

  constructor() {
    effect(
      () => {
        const id = this.kindergartenId();
        this.stopPolling();
        this.load(id);
      },
      { allowSignalWrites: true },
    );
    // 面板關閉時把輪詢收掉，不要在背景一直打 API
    this.destroyRef.onDestroy(() => this.stopPolling());
  }

  private load(id: number): void {
    this.loading.set(true);
    this.errorMessage.set(null);
    this.secure
      .getOpinion(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (data) => {
          this.data.set(data);
          this.job.set(data.job);
          this.loading.set(false);
          // 重新開啟面板時掃描可能還在跑，接著輪詢
          if (this.running()) {
            this.startPolling();
          }
        },
        error: (err: HttpErrorResponse) => {
          this.data.set(null);
          this.job.set(null);
          this.loading.set(false);
          this.errorMessage.set(this.messageOf(err, '無法載入輿情資料'));
        },
      });
  }

  startScan(): void {
    if (this.running() || this.starting()) {
      return;
    }
    this.starting.set(true);
    this.errorMessage.set(null);
    this.secure
      .startOpinionScan(this.kindergartenId())
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (job) => {
          this.job.set(job);
          this.starting.set(false);
          this.startPolling();
        },
        error: (err: HttpErrorResponse) => {
          this.starting.set(false);
          this.errorMessage.set(this.messageOf(err, '無法啟動輿情分析'));
        },
      });
  }

  private startPolling(): void {
    this.stopPolling();
    this.poller = interval(4000)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => this.pollOnce());
  }

  private stopPolling(): void {
    this.poller?.unsubscribe();
    this.poller = null;
  }

  private pollOnce(): void {
    const current = this.job();
    if (!current) {
      this.stopPolling();
      return;
    }
    this.secure.getOpinionScan(this.kindergartenId(), current.jobId).subscribe({
      next: (job) => {
        this.job.set(job);
        if (job.status === 'done') {
          this.stopPolling();
          // 完成後重新取一次，讓摘要與表格都換成這次的結果
          this.load(this.kindergartenId());
        } else if (job.status === 'failed') {
          this.stopPolling();
          this.errorMessage.set(job.error || '分析失敗');
        }
      },
      error: () => {
        // 單次輪詢失敗（例如短暫 5xx）不中斷，下一次再試
      },
    });
  }

  private messageOf(err: HttpErrorResponse, fallback: string): string {
    const body = err.error as { message?: string; code?: string } | null;
    if (body?.code === 'SCAN_COOLDOWN') {
      return body.message || '剛剛才掃描過，請稍後再試';
    }
    return body?.message || fallback;
  }

  /** internal:// 是本府內部資料，沒有可點的外部連結 */
  isExternal(item: OpinionItem): boolean {
    return item.url.startsWith('http://') || item.url.startsWith('https://');
  }

  sourceTypeLabel(item: OpinionItem): string {
    switch (item.sourceType) {
      case 'gov':
        return '政府公開資料';
      case 'news':
        return '新聞';
      case 'social':
        return '社群';
      case 'report':
        return '家長回報';
      default:
        return '網路';
    }
  }

  sentimentSeverity(item: OpinionItem): 'danger' | 'warning' | 'info' | 'success' {
    switch (item.sentiment) {
      case 'NEGATIVE':
        return 'danger';
      case 'MIXED':
        return 'warning';
      case 'POSITIVE':
        return 'success';
      default:
        return 'info';
    }
  }

  sentimentLabel(item: OpinionItem): string {
    switch (item.sentiment) {
      case 'NEGATIVE':
        return '負面';
      case 'POSITIVE':
        return '正面';
      case 'MIXED':
        return '褒貶並存';
      case 'NEUTRAL':
        return '中性';
      default:
        return '未判定';
    }
  }

  /** 關注指數只分三段上色，避免看起來像精算過的分數 */
  scoreSeverity(): 'danger' | 'warning' | 'success' {
    const score = this.data()?.opinionScore ?? 0;
    if (score >= 60) {
      return 'danger';
    }
    return score >= 30 ? 'warning' : 'success';
  }
}
