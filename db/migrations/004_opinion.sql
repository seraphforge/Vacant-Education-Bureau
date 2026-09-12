-- 004_opinion.sql
-- 輿情分析（政府端一鍵啟動的非同步掃描）。
--
-- 為什麼是 job 表而不是直接算：
--   web search + 抓取 + LLM 摘要要數十秒到數分鐘，遠超 API Gateway 的 29 秒上限，
--   所以 API 只負責建立 job 並非同步叫醒 worker，前端輪詢 job 狀態。
--
-- 資料語意（重要，UI 必須照樣標示）：
--   * opinion_item 一律是「需要人工關注的線索」，不是已證實的事實。
--   * verified 只代表「來源是可核對的官方公開資料」，不代表指控成立；
--     新聞／社群／評論一律 0。
--   * attribution 記錄這筆資料到底能不能歸屬到這間園所：
--     confirmed = 文中出現唯一可比對的園所全名
--     ambiguous = 同名或多園所候選，需人工判斷
--     unrelated = 只是一般幼教議題，保留但不計分
--   只有 attribution='confirmed' 的項目才會納入 opinion_score。

CREATE TABLE IF NOT EXISTS opinion_scan_job (
    id                 INT AUTO_INCREMENT NOT NULL,
    kindergarten_id    INT          NOT NULL,
    status             VARCHAR(20)  NOT NULL DEFAULT 'queued',  -- queued/searching/analyzing/done/failed
    requested_by       VARCHAR(64)  NULL,     -- cognito sub，用來查是誰按的
    requested_username VARCHAR(128) NULL,
    requested_county   VARCHAR(20)  NULL,     -- 按下時的權限範圍，事後稽核用
    requested_at       DATETIME     NOT NULL,
    started_at         DATETIME     NULL,
    finished_at        DATETIME     NULL,
    query_count        SMALLINT     NOT NULL DEFAULT 0,   -- 實際發出的搜尋次數
    item_count         SMALLINT     NOT NULL DEFAULT 0,
    confirmed_count    SMALLINT     NOT NULL DEFAULT 0,   -- attribution='confirmed'
    negative_count     SMALLINT     NOT NULL DEFAULT 0,
    opinion_score      DECIMAL(5,2) NULL,     -- 0-100，餵給 risk 雷達圖的 opinion 維度
    summary            TEXT         NULL,     -- LLM 產生的中文摘要（含免責語）
    search_provider    VARCHAR(20)  NULL,     -- http / agentcore / mock
    model_id           VARCHAR(80)  NULL,
    error              VARCHAR(255) NULL,
    PRIMARY KEY (id),
    KEY idx_kg_requested (kindergarten_id, requested_at),
    KEY idx_status (status),
    CONSTRAINT fk_osj_kg FOREIGN KEY (kindergarten_id)
        REFERENCES kindergarten (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS opinion_item (
    id              INT AUTO_INCREMENT NOT NULL,
    job_id          INT           NOT NULL,
    kindergarten_id INT           NOT NULL,
    title           VARCHAR(300)  NOT NULL,
    url             VARCHAR(1000) NOT NULL,
    url_hash        CHAR(64)      NOT NULL,   -- SHA256(url)，因為 VARCHAR(1000) 不能直接當 UNIQUE
    source          VARCHAR(80)   NULL,       -- 網域或媒體名稱
    source_type     VARCHAR(20)   NOT NULL DEFAULT 'web',  -- news/social/gov/web/review
    published_at    DATE          NULL,       -- 來源沒寫就是 NULL，不要猜
    snippet         TEXT          NULL,       -- 只存摘要片段，不存全文
    sentiment       VARCHAR(10)   NULL,       -- POSITIVE/NEGATIVE/NEUTRAL/MIXED（Comprehend）
    negative_score  DECIMAL(5,4)  NULL,
    risk_tags       VARCHAR(200)  NULL,       -- 逗號分隔，例如「管教爭議,衛生」
    attribution     VARCHAR(12)   NOT NULL DEFAULT 'ambiguous',  -- confirmed/ambiguous/unrelated
    confidence      DECIMAL(4,3)  NULL,       -- LLM 對歸屬判定的信心
    verified        TINYINT(1)    NOT NULL DEFAULT 0,
    created_at      DATETIME      NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_job_url (job_id, url_hash),
    KEY idx_kg_created (kindergarten_id, created_at),
    CONSTRAINT fk_oi_job FOREIGN KEY (job_id)
        REFERENCES opinion_scan_job (id) ON DELETE CASCADE,
    CONSTRAINT fk_oi_kg FOREIGN KEY (kindergarten_id)
        REFERENCES kindergarten (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 稽核：政府端對特定園所啟動輿情掃描是會被檢視的行為，誰在什麼時候做了什麼要留痕。
CREATE TABLE IF NOT EXISTS opinion_scan_audit (
    id              INT AUTO_INCREMENT NOT NULL,
    job_id          INT          NULL,
    kindergarten_id INT          NOT NULL,
    actor_sub       VARCHAR(64)  NULL,
    actor_username  VARCHAR(128) NULL,
    actor_county    VARCHAR(20)  NULL,
    action          VARCHAR(30)  NOT NULL,   -- scan_requested / scan_rejected / scan_finished
    detail          VARCHAR(255) NULL,
    created_at      DATETIME     NOT NULL,
    PRIMARY KEY (id),
    KEY idx_kg_created (kindergarten_id, created_at),
    KEY idx_actor (actor_sub)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
