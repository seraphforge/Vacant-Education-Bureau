/** 對應資料庫 readme.kindergarten 一列 */
export interface Kindergarten {
  id: number;
  academic_year: string;
  code: string;
  school_name: string;
  ownership: string;
  county: string;
  district: string;
  address: string;
  phone: string;
}

/** GET /api/kindergartens 的查詢條件 */
export interface KindergartenQuery {
  county?: string;
  name?: string;
  ownership?: string;
  academicYear?: string;
  page?: number;
  pageSize?: number;
  sortBy?: string;
  sortDir?: 'asc' | 'desc';
}

/** GET /api/kindergartens 的回應 */
export interface KindergartenPage {
  items: Kindergarten[];
  total: number;
  page: number;
  pageSize: number;
  academicYear: string;
  /** 後端實際套用的縣市範圍；受保護端點會回使用者被鎖定的縣市 */
  county?: string | null;
}

export interface CountyOption {
  county: string;
  count: number;
}

export interface AcademicYearOption {
  academic_year: string;
  count: number;
}
