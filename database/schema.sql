-- Deskon PostgreSQL Schema
-- Run this first before the migration script

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ─── Monthly Reports ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS monthly_reports (
    id         SERIAL PRIMARY KEY,
    month      TEXT NOT NULL,        -- e.g. 'February'
    year       INTEGER NOT NULL DEFAULT 2026,
    data       JSONB NOT NULL,       -- full KPI payload matching History.jsx shape
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (month, year)
);

-- ─── Activity Log ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS activity_log (
    id          SERIAL PRIMARY KEY,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id     TEXT,
    user_name   TEXT,
    user_role   TEXT,
    company_id  TEXT,
    action_type TEXT        NOT NULL,
    entity_type TEXT,
    entity_id   TEXT,
    description TEXT,
    source      TEXT        NOT NULL DEFAULT 'web',
    level       TEXT        NOT NULL DEFAULT 'user',
    metadata    JSONB,
    ip_address  TEXT,
    session_id  TEXT
);

CREATE INDEX IF NOT EXISTS idx_al_user    ON activity_log(user_id);
CREATE INDEX IF NOT EXISTS idx_al_company ON activity_log(company_id);
CREATE INDEX IF NOT EXISTS idx_al_action  ON activity_log(action_type);
CREATE INDEX IF NOT EXISTS idx_al_ts      ON activity_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_al_level   ON activity_log(level);
CREATE INDEX IF NOT EXISTS idx_al_source  ON activity_log(source);

-- ─── Deviations ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS deviations (
    id                        SERIAL PRIMARY KEY,
    sheet                     TEXT,
    flag                      TEXT,
    severity                  TEXT,
    description               TEXT,
    row_data                  JSONB,
    detected_at               TEXT,
    review_status             TEXT NOT NULL DEFAULT 'Pending',
    review_reason             TEXT,
    reason_type               TEXT,
    user_id                   TEXT,
    company_id                TEXT,
    reviewed_at               TEXT,
    reviewed_by_user_id       TEXT,
    admin_comment             TEXT,
    last_reminder_notified_at TEXT,
    filename                  TEXT,
    job_id                    TEXT,
    expires_at                TIMESTAMPTZ,
    auto_locked_at            TIMESTAMPTZ,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dev_company ON deviations(company_id);
CREATE INDEX IF NOT EXISTS idx_dev_user    ON deviations(user_id);
CREATE INDEX IF NOT EXISTS idx_dev_status  ON deviations(review_status);

-- ─── Core Auth ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email                TEXT UNIQUE NOT NULL,
    name                 TEXT NOT NULL,
    role                 TEXT NOT NULL CHECK (role IN ('admin', 'manager', 'user')),
    password             TEXT NOT NULL,
    must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ
);

-- ─── AI Chat History ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS chat_history (
    id           SERIAL PRIMARY KEY,
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    user_name    TEXT NOT NULL DEFAULT '',
    user_role    TEXT NOT NULL DEFAULT 'user',
    route        TEXT NOT NULL DEFAULT '/api/chat',
    message      TEXT NOT NULL,
    response     TEXT NOT NULL,
    model        TEXT NOT NULL DEFAULT '',
    context_info TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_history_user_id ON chat_history(user_id);
CREATE INDEX IF NOT EXISTS idx_chat_history_created ON chat_history(created_at DESC);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token      TEXT PRIMARY KEY,
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

-- ─── Subscriptions ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS subscriptions (
    user_id          UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    plan             TEXT NOT NULL DEFAULT 'free',
    uploads_today    INTEGER NOT NULL DEFAULT 0,
    total_uploads    INTEGER NOT NULL DEFAULT 0,
    last_upload_date DATE,
    daily_limit      INTEGER NOT NULL DEFAULT 1,
    is_locked        BOOLEAN NOT NULL DEFAULT FALSE
);

-- ─── Notifications ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS notifications (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      TEXT NOT NULL,
    message    TEXT NOT NULL,
    type       TEXT NOT NULL,
    metadata   JSONB NOT NULL DEFAULT '{}',
    read       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notifications_user_id  ON notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_created  ON notifications(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_metadata ON notifications USING GIN(metadata);

-- ─── File Processing History ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS history (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID REFERENCES users(id) ON DELETE SET NULL,
    filename       TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'completed',
    total_sheets   INTEGER NOT NULL DEFAULT 0,
    success_count  INTEGER NOT NULL DEFAULT 0,
    error_count    INTEGER NOT NULL DEFAULT 0,
    detected_sheets JSONB NOT NULL DEFAULT '[]',
    results        JSONB NOT NULL DEFAULT '[]',
    processed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_history_user_id ON history(user_id);

-- ─── Pending Upload Approvals ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pending_upload_approvals (
    approval_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id           UUID NOT NULL,
    upload_filename  TEXT NOT NULL,
    upload_path      TEXT NOT NULL,
    user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    user_name        TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'pending',
    detected_changes JSONB NOT NULL DEFAULT '[]',
    submitted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_approvals_user_id ON pending_upload_approvals(user_id);
CREATE INDEX IF NOT EXISTS idx_approvals_status  ON pending_upload_approvals(status);

-- ─── Base File Versioning ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS base_file_config (
    id         SERIAL PRIMARY KEY,
    filename   TEXT NOT NULL,
    sheet_name TEXT NOT NULL DEFAULT '',
    is_active  BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS base_file_versions (
    version_id          TEXT PRIMARY KEY,
    stage               TEXT NOT NULL,
    base_filename       TEXT NOT NULL,
    snapshot_rel_path   TEXT NOT NULL,
    snapshot_abs_path   TEXT NOT NULL,
    snapshot_size_bytes BIGINT,
    merge_summary       JSONB NOT NULL DEFAULT '{}',
    context             JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_versions_base_filename ON base_file_versions(base_filename);
CREATE INDEX IF NOT EXISTS idx_versions_created       ON base_file_versions(created_at DESC);

-- ─── Update Chain ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS update_chain (
    index         SERIAL PRIMARY KEY,
    filename      TEXT NOT NULL,
    source_upload TEXT NOT NULL DEFAULT '',
    job_id        UUID,
    approved_by   TEXT NOT NULL DEFAULT 'auto',
    status        TEXT NOT NULL DEFAULT 'approved',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── AI Caches ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ai_response_cache (
    id           SERIAL PRIMARY KEY,
    cache_key    TEXT UNIQUE NOT NULL,
    user_id      UUID REFERENCES users(id) ON DELETE SET NULL,
    question     TEXT NOT NULL,
    response     TEXT NOT NULL,
    context_hash TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS intelligence_insight_cache (
    id         SERIAL PRIMARY KEY,
    cache_key  TEXT UNIQUE NOT NULL,
    data_hash  TEXT NOT NULL DEFAULT '',
    section    TEXT NOT NULL DEFAULT '',
    title      TEXT NOT NULL DEFAULT '',
    insight    JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS whatif_predecessor_successor_cache (
    cache_key    TEXT PRIMARY KEY,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source       JSONB NOT NULL DEFAULT '{}',
    dependencies JSONB NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS whatif_project_update_summary (
    id           SERIAL PRIMARY KEY,
    cache_key    TEXT UNIQUE NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source       JSONB NOT NULL DEFAULT '{}',
    updates      JSONB NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS whatif_claude_responses (
    id         SERIAL PRIMARY KEY,
    session_id TEXT,
    user_id    UUID REFERENCES users(id) ON DELETE SET NULL,
    question   TEXT,
    response   TEXT,
    context    JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS whatif_critical_dashboard_data (
    id         SERIAL PRIMARY KEY,
    cache_key  TEXT UNIQUE NOT NULL DEFAULT 'default',
    data       JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS whatif_realtime_data (
    id         SERIAL PRIMARY KEY,
    cache_key  TEXT UNIQUE NOT NULL DEFAULT 'default',
    data       JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── Groups ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS engage_groups (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       TEXT NOT NULL,
    members    JSONB NOT NULL DEFAULT '[]',  -- stores member_ids list
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS engage_posts (
    id         TEXT PRIMARY KEY,
    user_id    UUID REFERENCES users(id) ON DELETE SET NULL,
    user_name  TEXT NOT NULL DEFAULT '',
    user_email TEXT NOT NULL DEFAULT '',
    content    TEXT NOT NULL DEFAULT '',
    image_url  TEXT NOT NULL DEFAULT '',
    group_id   TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT 'manual',
    likes      JSONB NOT NULL DEFAULT '[]',
    comments   JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_engage_posts_user_id  ON engage_posts(user_id);
CREATE INDEX IF NOT EXISTS idx_engage_posts_group_id ON engage_posts(group_id);

-- ─── PPTX ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pptx_slide_sections (
    id          SERIAL PRIMARY KEY,
    section_key TEXT UNIQUE NOT NULL,
    data        JSONB NOT NULL DEFAULT '{}',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
