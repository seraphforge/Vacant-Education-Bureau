import { Component, ViewChild, inject, signal } from '@angular/core';
import { FormsModule, NgForm } from '@angular/forms';
import { Router } from '@angular/router';
import { AutoCompleteCompleteEvent, AutoCompleteModule } from 'primeng/autocomplete';
import { ButtonModule } from 'primeng/button';
import { FileUpload, FileUploadModule } from 'primeng/fileupload';
import { InputTextModule } from 'primeng/inputtext';
import { InputTextareaModule } from 'primeng/inputtextarea';
import { MessageModule } from 'primeng/message';
import { MessageService } from 'primeng/api';
import { ToastModule } from 'primeng/toast';

import { HeaderComponent } from '../../components/header/header.component';
import { VerificationCodeComponent } from '../../components/verification-code/verification-code.component';
import { Kindergarten } from '../../models/kindergarten.model';
import { CreateDraftResponse } from '../../models/report.model';
import { KindergartenService } from '../../services/kindergarten.service';
import { ReportService } from '../../services/report.service';

/** 附件限制（UI_SPEC §4.1 / API_SPEC §2.3） */
const MAX_FILES = 5;
const MAX_FILE_SIZE = 5 * 1024 * 1024; // 5MB
const ACCEPT_TYPES = 'image/*';

/**
 * 回報表單頁 /report（UI_SPEC §4、API_SPEC §2）。
 *
 * 流程：填表 → 建立草稿(寄驗證碼) → (有附件才) presign → 直傳 S3 → 登錄附件
 *       → 開驗證碼 Modal → 驗證成功 → 導向 /report/<token>
 */
@Component({
  selector: 'app-report-form',
  standalone: true,
  imports: [
    FormsModule,
    HeaderComponent,
    VerificationCodeComponent,
    AutoCompleteModule,
    InputTextModule,
    InputTextareaModule,
    FileUploadModule,
    ButtonModule,
    MessageModule,
    ToastModule,
  ],
  providers: [MessageService],
  templateUrl: './report-form.component.html',
  styleUrl: './report-form.component.scss',
})
export class ReportFormComponent {
  @ViewChild('fileUpload') fileUpload?: FileUpload;

  private kgSvc = inject(KindergartenService);
  private reportSvc = inject(ReportService);
  private router = inject(Router);
  private messages = inject(MessageService);

  readonly maxFiles = MAX_FILES;
  readonly maxFileSize = MAX_FILE_SIZE;
  readonly acceptTypes = ACCEPT_TYPES;

  // ---- 表單欄位 ----
  reporterName = '';
  reporterEmail = '';
  selectedKindergarten: Kindergarten | null = null;
  content = '';

  /** 已選檔案（自行管理，用 customUpload） */
  readonly selectedFiles = signal<File[]>([]);

  /** autocomplete 建議清單 */
  readonly suggestions = signal<Kindergarten[]>([]);

  readonly submitting = signal(false);

  // ---- 驗證碼 Modal ----
  readonly otpVisible = signal(false);
  readonly draft = signal<CreateDraftResponse | null>(null);

  private searchTimer?: ReturnType<typeof setTimeout>;

  /** 幼兒園 autocomplete：debounce 300ms、至少 1 字（API_SPEC §1） */
  searchKindergarten(event: AutoCompleteCompleteEvent): void {
    const q = (event.query ?? '').trim();
    if (this.searchTimer) {
      clearTimeout(this.searchTimer);
    }
    if (q.length < 1) {
      this.suggestions.set([]);
      return;
    }
    this.searchTimer = setTimeout(() => {
      this.kgSvc.search({ name: q, pageSize: 10 }).subscribe({
        next: (page) => this.suggestions.set(page.items),
        error: () => this.suggestions.set([]),
      });
    }, 300);
  }

  /** p-fileUpload 用 customUpload，改由這裡接收選檔並套用限制 */
  onSelectFiles(event: { currentFiles: File[] }): void {
    const incoming = event.currentFiles ?? [];
    const valid: File[] = [];
    for (const f of incoming) {
      if (!f.type.startsWith('image/')) {
        this.messages.add({
          severity: 'warn',
          summary: '格式不符',
          detail: `${f.name} 不是圖片檔，已略過`,
        });
        continue;
      }
      if (f.size > MAX_FILE_SIZE) {
        this.messages.add({
          severity: 'warn',
          summary: '檔案過大',
          detail: `${f.name} 超過 5MB，已略過`,
        });
        continue;
      }
      valid.push(f);
    }
    // 合併並去重（以 name+size 判斷），再套上限
    const merged = [...this.selectedFiles()];
    for (const f of valid) {
      if (!merged.some((m) => m.name === f.name && m.size === f.size)) {
        merged.push(f);
      }
    }
    if (merged.length > MAX_FILES) {
      this.messages.add({
        severity: 'warn',
        summary: '檔案數量超過上限',
        detail: `最多 ${MAX_FILES} 個檔案，多餘的已略過`,
      });
    }
    this.selectedFiles.set(merged.slice(0, MAX_FILES));
    // 清掉 PrimeNG 內部暫存，避免它自己顯示一份
    this.fileUpload?.clear();
  }

  removeFile(index: number): void {
    const arr = [...this.selectedFiles()];
    arr.splice(index, 1);
    this.selectedFiles.set(arr);
  }

  formatSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  async submit(form: NgForm): Promise<void> {
    if (this.submitting()) {
      return;
    }
    // 基本驗證（Email 必填 + 格式、幼兒園、事由）
    if (form.invalid || !this.selectedKindergarten || !this.content.trim()) {
      Object.values(form.controls).forEach((c) => c.markAsTouched());
      this.messages.add({
        severity: 'error',
        summary: '欄位未完成',
        detail: '請確認 Email、幼兒園與回報事由皆已填寫',
      });
      return;
    }

    this.submitting.set(true);
    try {
      // ① 建立草稿（寄驗證碼）
      const draft = await this.firstValue(
        this.reportSvc.createDraft({
          reporterName: this.reporterName.trim() || null,
          reporterEmail: this.reporterEmail.trim(),
          kindergartenId: this.selectedKindergarten.id,
          content: this.content.trim(),
        }),
      );

      // ②③④ 有附件才上傳
      const files = this.selectedFiles();
      if (files.length > 0) {
        await this.uploadAttachments(draft.draftId, files);
      }

      // ⑤ 開驗證碼 Modal
      this.draft.set(draft);
      this.otpVisible.set(true);
    } catch (err: unknown) {
      this.handleSubmitError(err);
    } finally {
      this.submitting.set(false);
    }
  }

  private async uploadAttachments(draftId: number, files: File[]): Promise<void> {
    // ② 取得每檔的 presigned POST
    const presign = await this.firstValue(
      this.reportSvc.presignAttachments(draftId, {
        files: files.map((f) => ({
          fileName: f.name,
          contentType: f.type,
          sizeBytes: f.size,
        })),
      }),
    );

    // ③ 逐檔直傳 S3
    for (let i = 0; i < presign.uploads.length; i++) {
      const up = presign.uploads[i];
      const file = files[i];
      await this.reportSvc.uploadToS3(up, file);
    }

    // ④ 登錄附件
    await this.firstValue(
      this.reportSvc.registerAttachments(draftId, {
        files: presign.uploads.map((up, i) => ({
          key: up.key,
          fileName: files[i].name,
          contentType: files[i].type,
          sizeBytes: files[i].size,
        })),
      }),
    );
  }

  private handleSubmitError(err: unknown): void {
    const e = err as { error?: { code?: string; message?: string } };
    const code = e?.error?.code;
    const map: Record<string, string> = {
      INVALID_EMAIL: 'Email 格式錯誤',
      CONTENT_REQUIRED: '請填寫回報事由',
      CONTENT_TOO_LONG: '回報事由超過 5000 字',
      KINDERGARTEN_NOT_FOUND: '找不到所選幼兒園，請重新選擇',
      TOO_MANY_FILES: '附件超過 5 個',
      FILE_TOO_LARGE: '有附件超過 5MB',
      UNSUPPORTED_FILE_TYPE: '附件僅接受圖片格式',
    };
    this.messages.add({
      severity: 'error',
      summary: '送出失敗',
      detail: (code && map[code]) || e?.error?.message || '送出時發生錯誤，請稍後再試',
    });
  }

  // ---- 驗證碼 Modal 事件 ----
  onVerified(trackingUrl: string): void {
    this.otpVisible.set(false);
    void this.router.navigateByUrl(trackingUrl);
  }

  onLocked(): void {
    // 草稿作廢：關閉 Modal、清掉 draft，讓家長重填
    this.otpVisible.set(false);
    this.draft.set(null);
    this.messages.add({
      severity: 'warn',
      summary: '驗證失敗',
      detail: '錯誤次數過多，請重新送出表單',
    });
  }

  onOtpClosed(): void {
    this.otpVisible.set(false);
  }

  private firstValue<T>(obs: import('rxjs').Observable<T>): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const sub = obs.subscribe({
        next: (v) => {
          resolve(v);
          sub.unsubscribe();
        },
        error: (e) => reject(e),
      });
    });
  }
}
