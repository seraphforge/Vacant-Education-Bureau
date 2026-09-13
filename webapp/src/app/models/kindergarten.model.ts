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
  /**
   * 以下兩欄僅由 /api/secure/kindergartens 回傳（API_SPEC §4.2），
   * 公開端點不會有。尚未計算時為 null。
   */
  risk_score?: number | null;
  risk_level?: 'high' | 'medium' | 'normal' | null;
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

/**
 * 對應 readme.kindergarten_punishment 一列（單一幼兒園查詢回傳的欄位）。
 *
 * 來源為全國教保資訊網「裁罰紀錄查詢」，欄位幾乎都可能是 null
 * （公布欄本身就有缺漏），所以前端一律要處理空值。
 */
export interface PunishmentRecord {
  id: number;
  /** 處分日期，YYYY-MM-DD */
  punish_date: string | null;
  /** 處分當時的園名，可能與現名不同（改名／改制） */
  school_name_at_time: string | null;
  doc_no: string | null;
  legal_basis: string | null;
  violated_rule: string | null;
  person: string | null;
  content: string | null;
  /** 罰鍰金額（元）；非罰鍰類處分為 null */
  fine_amount: number | null;
}

/** GET /api/kindergartens/{id}/punishments 的回應 */
export interface KindergartenPunishments {
  kindergarten: {
    id: number;
    school_name: string;
    county: string;
    district: string;
    address: string;
    phone: string;
  };
  records: PunishmentRecord[];
  count: number;
  /** 這些紀錄的罰鍰總額 */
  totalFine: number;
}

export interface CountyOption {
  county: string;
  count: number;
}

export interface AcademicYearOption {
  academic_year: string;
  count: number;
}
