import { DecimalPipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, computed, effect, inject, input, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MessageModule } from 'primeng/message';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { TableModule } from 'primeng/table';
import { TagModule } from 'primeng/tag';

import { KindergartenPunishments, PunishmentRecord } from '../../models/kindergarten.model';
import { KindergartenService } from '../../services/kindergarten.service';

/**
 * 詳細資料 Tab：裁罰紀錄。
 *
 * 資料來自 GET /api/kindergartens/{id}/punishments（`kindergarten_punishment` 表，
 * 原始來源為全國教保資訊網裁罰紀錄查詢）。這是**官方公文紀錄**，不是網路傳聞，
 * 所以跟輿情分析刻意分開呈現：這裡的每一筆都可以憑文號回查。
 *
 * 幾個刻意的做法：
 *   * 不做任何加權或評分。裁罰次數與金額直接如實列出，風險判斷留給承辦人。
 *   * 欄位幾乎都可能是 null（公布欄本身就有缺漏），一律顯示「—」而不是空白，
 *     避免看起來像畫面壞掉。
 *   * 處分當時園名與現名不同時要標出來，改名／改制的園所很常見。
 *   * 目前資料只涵蓋新北市，查無紀錄時必須說明「不代表全國查無」，
 *     否則會被誤讀成已查證清白。
 */
@Component({
  selector: 'app-punishment-record-tab',
  standalone: true,
  imports: [DecimalPipe, MessageModule, ProgressSpinnerModule, TableModule, TagModule],
  templateUrl: './punishment-record-tab.component.html',
  styleUrls: ['./tab-empty.scss', './punishment-record-tab.component.scss'],
})
export class PunishmentRecordTabComponent {
  /** 幼兒園 id；變更時重新載入 */
  readonly kindergartenId = input.required<number>();

  private api = inject(KindergartenService);
  private destroyRef = inject(DestroyRef);

  readonly loading = signal(true);
  readonly data = signal<KindergartenPunishments | null>(null);
  readonly errorMessage = signal<string | null>(null);

  readonly records = computed(() => this.data()?.records ?? []);
  readonly count = computed(() => this.data()?.count ?? 0);
  readonly totalFine = computed(() => this.data()?.totalFine ?? 0);

  /** 有罰鍰金額的筆數；跟總筆數不同（有些處分不含罰鍰） */
  readonly finedCount = computed(
    () => this.records().filter((r) => (r.fine_amount ?? 0) > 0).length,
  );

  constructor() {
    effect(
      () => {
        this.load(this.kindergartenId());
      },
      { allowSignalWrites: true },
    );
  }

  private load(id: number): void {
    this.loading.set(true);
    this.errorMessage.set(null);
    this.api
      .getPunishments(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (data) => {
          this.data.set(data);
          this.loading.set(false);
        },
        error: (err: HttpErrorResponse) => {
          this.data.set(null);
          this.loading.set(false);
          const body = err.error as { message?: string } | null;
          this.errorMessage.set(body?.message || '無法載入裁罰紀錄');
        },
      });
  }

  /** 空值統一顯示「—」，避免看起來像畫面壞掉 */
  orDash(value: string | null | undefined): string {
    const text = (value ?? '').trim();
    return text === '' ? '—' : text;
  }

  /** 處分當時的園名與現名不同才需要提醒承辦人（改名／改制） */
  renamed(record: PunishmentRecord): boolean {
    const then = (record.school_name_at_time ?? '').trim();
    const now = (this.data()?.kindergarten.school_name ?? '').trim();
    return then !== '' && now !== '' && then !== now;
  }
}
