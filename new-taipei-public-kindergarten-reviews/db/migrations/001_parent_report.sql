-- 001_parent_report.sql
-- 家長回報系統：主表 + 附件 + 訊息串（政府回覆／內部備註／狀態變更稽核）
--
-- 設計備註：
--   * 未驗證的草稿也放在 parent_report，用 status='pending_verification' 隔離，
--     政府端所有查詢一律排除，等同「驗證後才進政府資料庫」。
--   * 時間欄位一律存 UTC naive datetime（RDS 的 time_zone 為 UTC），
--     API 回傳時由程式補上 'Z'。

CREATE TABLE IF NOT EXISTS parent_report (
    id                BIGINT       NOT NULL AUTO_INCREMENT,
    -- pending_verification / submitted / investigating / closed / rejected
    status            VARCHAR(30)  NOT NULL DEFAULT 'pending_verification',

    -- 回報人（PII：清單 API 只回遮蔽版，追蹤 API 完全不回）
    reporter_name     VARCHAR(100) NULL,
    reporter_email    VARCHAR(255) NOT NULL,

    -- 被回報的幼兒園（校名／縣市一律 JOIN kindergarten 取得）
    kindergarten_id   INT          NOT NULL,
    content           TEXT         NOT NULL,

    -- Email 驗證碼；驗證成功後 otp_hash / otp_expires_at 清成 NULL
    otp_hash          CHAR(64)     NULL,
    otp_expires_at    DATETIME     NULL,
    otp_attempts      TINYINT      NOT NULL DEFAULT 0,

    -- 追蹤連結（/report/<tracking_token>）
    tracking_token    CHAR(43)     NULL,
    verified_at       DATETIME     NULL,

    -- 承辦
    assignee          VARCHAR(100) NULL,
    status_reason     VARCHAR(500) NULL,
    status_updated_at DATETIME     NULL,

    created_at        TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
                                   ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_token (tracking_token),
    KEY idx_status_created (status, created_at),
    KEY idx_kg (kindergarten_id),
    CONSTRAINT fk_pr_kg FOREIGN KEY (kindergarten_id) REFERENCES kindergarten (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS parent_report_attachment (
    id            BIGINT       NOT NULL AUTO_INCREMENT,
    report_id     BIGINT       NOT NULL,
    s3_key        VARCHAR(500) NOT NULL,
    original_name VARCHAR(255) NULL,
    content_type  VARCHAR(100) NOT NULL,
    size_bytes    INT          NOT NULL,
    created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_key (s3_key),
    KEY idx_report (report_id),
    CONSTRAINT fk_pra_report FOREIGN KEY (report_id)
        REFERENCES parent_report (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS parent_report_message (
    id                BIGINT       NOT NULL AUTO_INCREMENT,
    report_id         BIGINT       NOT NULL,
    -- reply（家長看得到）/ internal_note（僅內部）/ status_change / system
    kind              VARCHAR(20)  NOT NULL,
    visible_to_parent TINYINT(1)   NOT NULL DEFAULT 1,
    author_type       VARCHAR(20)  NOT NULL DEFAULT 'staff',   -- staff / system
    author_username   VARCHAR(100) NULL,
    author_display    VARCHAR(100) NULL,   -- 顯示給家長的「機關 + 承辦人」
    body              TEXT         NULL,
    from_status       VARCHAR(30)  NULL,
    to_status         VARCHAR(30)  NULL,
    emailed_at        DATETIME     NULL,
    created_at        TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_report_created (report_id, created_at),
    CONSTRAINT fk_prm_report FOREIGN KEY (report_id)
        REFERENCES parent_report (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
