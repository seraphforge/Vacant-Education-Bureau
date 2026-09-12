import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { CardModule } from 'primeng/card';
import { DropdownModule } from 'primeng/dropdown';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { TableModule, TableLazyLoadEvent } from 'primeng/table';
import { TagModule } from 'primeng/tag';

import { Kindergarten } from '../../models/kindergarten.model';
import { KindergartenService } from '../../services/kindergarten.service';

interface Option {
  label: string;
  value: string;
}

@Component({
  selector: 'app-kindergarten-list',
  standalone: true,
  imports: [
    FormsModule,
    ButtonModule,
    CardModule,
    DropdownModule,
    InputTextModule,
    MessageModule,
    TableModule,
    TagModule,
  ],
  templateUrl: './kindergarten-list.component.html',
  styleUrl: './kindergarten-list.component.scss',
})
export class KindergartenListComponent implements OnInit {
  private api = inject(KindergartenService);

  // 查詢條件
  county = '';
  name = '';
  ownership = '';
  academicYear = '';

  // 下拉選單資料
  countyOptions: Option[] = [{ label: '全部縣市', value: '' }];
  yearOptions: Option[] = [];
  ownershipOptions: Option[] = [
    { label: '全部', value: '' },
    { label: '公立', value: '公立' },
    { label: '私立', value: '私立' },
  ];

  // 表格資料
  rows: Kindergarten[] = [];
  total = 0;
  pageSize = 20;
  first = 0;
  loading = false;
  errorMessage = '';

  ngOnInit(): void {
    this.api.getCounties().subscribe({
      next: (res) => {
        this.countyOptions = [
          { label: '全部縣市', value: '' },
          ...res.items.map((c) => ({ label: `${c.county} (${c.count})`, value: c.county })),
        ];
      },
      error: () => (this.errorMessage = '無法載入縣市清單，請確認後端 API 是否正常。'),
    });

    this.api.getAcademicYears().subscribe({
      next: (res) => {
        this.yearOptions = res.items.map((y) => ({
          label: `${y.academic_year} 學年度`,
          value: y.academic_year,
        }));
        // 預設選最新學年度
        if (!this.academicYear && this.yearOptions.length) {
          this.academicYear = this.yearOptions[0].value;
          this.load();
        }
      },
      error: () => (this.errorMessage = '無法載入學年度清單，請確認後端 API 是否正常。'),
    });
  }

  /** 按下查詢：回到第一頁再重新載入 */
  onSearch(): void {
    this.first = 0;
    this.load();
  }

  onReset(): void {
    this.county = '';
    this.name = '';
    this.ownership = '';
    this.academicYear = this.yearOptions[0]?.value ?? '';
    this.onSearch();
  }

  /** p-table 換頁 / 排序時觸發 */
  onLazyLoad(event: TableLazyLoadEvent): void {
    this.first = event.first ?? 0;
    this.pageSize = event.rows ?? this.pageSize;
    const sortField = typeof event.sortField === 'string' ? event.sortField : undefined;
    this.load(sortField, event.sortOrder === -1 ? 'desc' : 'asc');
  }

  private load(sortBy?: string, sortDir: 'asc' | 'desc' = 'asc'): void {
    this.loading = true;
    this.errorMessage = '';
    this.api
      .search({
        county: this.county,
        name: this.name.trim(),
        ownership: this.ownership,
        academicYear: this.academicYear,
        page: Math.floor(this.first / this.pageSize) + 1,
        pageSize: this.pageSize,
        sortBy,
        sortDir,
      })
      .subscribe({
        next: (res) => {
          this.rows = res.items;
          this.total = res.total;
          this.loading = false;
        },
        error: (err) => {
          this.rows = [];
          this.total = 0;
          this.loading = false;
          this.errorMessage = `查詢失敗：${err.status ?? ''} ${err.message ?? ''}`;
        },
      });
  }
}
