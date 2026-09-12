-- 002_staff_profile.sql
-- 公務人員檔案。UI_SPEC 6.2「進入頁面時先從資料庫讀取登入人員所屬縣市」用這張表。
--
-- 注意權限邊界：縣市「權限判定」仍以 Cognito ID token 的 custom:county 為準
-- （由 Cognito 簽章保證、API Gateway 先驗過），這張表只負責
--   1) 顯示用的 display_name（回覆家長時的署名）
--   2) token 沒帶 county 時的 fallback
-- 不要把授權判斷下放到這張可被 SQL 改動的表。

CREATE TABLE IF NOT EXISTS staff_profile (
    cognito_sub   CHAR(36)     NOT NULL,
    username      VARCHAR(100) NOT NULL,
    display_name  VARCHAR(100) NULL,
    county        VARCHAR(30)  NULL,
    agency        VARCHAR(100) NULL,
    role          VARCHAR(20)  NOT NULL DEFAULT 'staff',   -- staff / admin
    last_login_at DATETIME     NULL,
    created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
                               ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (cognito_sub),
    UNIQUE KEY uq_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
