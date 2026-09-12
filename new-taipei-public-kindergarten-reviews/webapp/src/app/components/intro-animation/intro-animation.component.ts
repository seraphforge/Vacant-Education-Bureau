import { Component, ElementRef, OnDestroy, output, signal, viewChild } from '@angular/core';

/**
 * 入場動畫（UI_SPEC §3.1）。
 *
 * 兩階段：
 *  1. idle：全螢幕顯示可點擊的 README!!! 大 Logo，等待使用者點一下進入。
 *  2. playing：點擊後才開始動畫，同時播放音檔 audio/readme_intro.mp3。
 *     （瀏覽器會擋沒有使用者手勢的自動播放，所以改由這個點擊手勢觸發播放。）
 *  動畫播完後發出 finished 事件，由 HomeComponent 過渡到主畫面。
 *
 * 音檔載入/播放失敗（檔案還沒放進去等）一律 catch，動畫照常進行、不卡住、不整頁報錯。
 *
 * TBD（UI_SPEC §7 待確認）：動畫觸發時機。
 * 目前依規格採「每次進入 / 皆播放」；若改為僅首次載入播放，
 * 只需在 HomeComponent 加一個 sessionStorage 旗標判斷即可，本元件不用改。
 */
@Component({
  selector: 'app-intro-animation',
  standalone: true,
  templateUrl: './intro-animation.component.html',
  styleUrl: './intro-animation.component.scss',
})
export class IntroAnimationComponent implements OnDestroy {
  /** 動畫播畢通知父層過渡到主畫面 */
  readonly finished = output<void>();

  /** 是否已點擊進入、開始播放動畫 */
  readonly started = signal(false);
  /** 觸發淡出的旗標 */
  readonly leaving = signal(false);

  private readonly audioRef = viewChild<ElementRef<HTMLAudioElement>>('introAudio');

  /** 動畫總時長（ms）。與 SCSS 的 keyframe 時間對齊。 */
  private readonly durationMs = 2600;
  /** 淡出過場時長（ms），需與 SCSS .leaving 的 transition 對齊。 */
  private readonly fadeMs = 500;

  private timers: ReturnType<typeof setTimeout>[] = [];

  ngOnDestroy(): void {
    this.timers.forEach((t) => clearTimeout(t));
    this.timers = [];
  }

  /** 使用者點擊 Logo 進入：開始動畫並播放音效（此點擊即為播放所需的使用者手勢） */
  enter(): void {
    if (this.started()) {
      return;
    }
    this.started.set(true);
    this.playAudio();

    // 動畫播完 → 淡出 → 通知父層
    this.timers.push(
      setTimeout(() => {
        this.leaving.set(true);
        this.timers.push(setTimeout(() => this.finished.emit(), this.fadeMs));
      }, this.durationMs),
    );
  }

  private playAudio(): void {
    const audio = this.audioRef()?.nativeElement;
    if (!audio) {
      return;
    }
    try {
      const p = audio.play();
      // play() 回傳的 Promise 若被拒絕（缺檔等）一定要 catch，避免 console 噴錯。
      if (p && typeof p.then === 'function') {
        p.catch(() => {
          /* 音檔缺檔或播放失敗：靜默略過，動畫照常進行 */
        });
      }
    } catch {
      /* 極端情況（元素狀態異常）也吞掉，不影響動畫 */
    }
  }

  /** 音檔載入錯誤（例如檔案尚未放入）事件，同樣靜默處理 */
  onAudioError(): void {
    // 不做事，僅避免預設行為干擾；動畫不受影響。
  }
}
