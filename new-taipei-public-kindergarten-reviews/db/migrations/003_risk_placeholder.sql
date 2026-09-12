-- 003_risk_placeholder.sql
-- 風險指數。演算法還沒定案，所以：
--   * 各維度放在 dimensions JSON，改指標不用改 schema
--   * risk_level（high/medium/normal）由後端依 80 / 60 閾值算好，前端只做上色
--   * is_placeholder = 1 代表這是假資料，API 會原樣回給前端，讓畫面能標示「示意」

CREATE TABLE IF NOT EXISTS risk_score_current (
    kindergarten_id INT          NOT NULL,
    total_score     DECIMAL(5,2) NULL,
    risk_level      VARCHAR(10)  NULL,   -- high / medium / normal
    dimensions      JSON         NULL,   -- [{key,label,score,weight}]
    model_version   VARCHAR(30)  NULL,
    is_placeholder  TINYINT(1)   NOT NULL DEFAULT 1,
    computed_at     DATETIME     NULL,
    PRIMARY KEY (kindergarten_id),
    KEY idx_score (total_score),
    CONSTRAINT fk_rsc_kg FOREIGN KEY (kindergarten_id)
        REFERENCES kindergarten (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
