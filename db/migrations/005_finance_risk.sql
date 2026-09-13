-- 005_finance_risk.sql
-- 財報法遵分析結果（決算書 PDF -> 清理 -> 指標計算的產出，見
-- data/finance_pdf_cleaning/analysis/outputs/risk_scores.csv）。
--
-- 為什麼只存一列（current）而不是逐年明細：
--   風險指數只看最新一個決算年度（113），逐年趨勢已經被壓縮進指標的
--   「歷年 z 分數 / 連續成長年數」裡。要做趨勢圖再另開一張 history 表即可，
--   不影響這裡。
--
-- indicators 是 JSON，因為指標會增減（目前 8 項），換指標不用改 schema：
--   [{key, label, level: GREEN/YELLOW/RED/N/A, levelScore: 0-3, yearZ, peerZ}]
--   levelScore 1=GREEN 2=YELLOW 3=RED 0=無資料，直接對應 CSV 的「等級分數」欄。
--
-- compliance_index 就是 CSV 的「法遵風險指數」（0-25 左右的實務範圍），
-- 換算成風險維度分數的公式寫在 aws/src/risk.py（index * 4，上限 100），
-- 不寫在 SQL 裡，這樣改權重不用重跑 migration。

CREATE TABLE IF NOT EXISTS finance_report_current (
    kindergarten_id  INT          NOT NULL,
    finance_id       VARCHAR(10)  NULL,      -- 來源 CSV 的幼兒園ID，例如 N01
    school_alias     VARCHAR(50)  NULL,      -- 來源 CSV 的園名簡稱，例如 安溪
    fiscal_year      VARCHAR(4)   NOT NULL,  -- 決算年度（民國），目前一律 113
    compliance_index DECIMAL(6,2) NULL,      -- 法遵風險指數
    overall_level    VARCHAR(12)  NULL,      -- 整體風險等級（低風險/中風險/高風險）
    early_warning    VARCHAR(255) NULL,      -- 事件前預警（CSV 原文）
    indicators       JSON         NULL,      -- 見檔頭說明
    metrics          JSON         NULL,      -- 財報頁要顯示的主要數字
    source_file      VARCHAR(120) NULL,
    updated_at       DATETIME     NOT NULL,
    PRIMARY KEY (kindergarten_id),
    KEY idx_compliance_index (compliance_index),
    CONSTRAINT fk_frc_kg FOREIGN KEY (kindergarten_id)
        REFERENCES kindergarten (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
