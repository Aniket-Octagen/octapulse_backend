-- =============================================================================
-- Deskon — Full Database Setup (for new developers)
-- =============================================================================
-- Run once against a fresh PostgreSQL database:
--   psql $DATABASE_URL -f database/setup.sql
--
-- All statements are idempotent (IF NOT EXISTS / ON CONFLICT DO NOTHING).
-- Tables are ordered by foreign-key dependency — run the file top-to-bottom.
--
-- Default credentials created by the seed section at the bottom:
--   Super Admin : superadmin@thetaai.ai  / SuperAdmin@123!
--   Demo Admin  : admin@demo.thetaai.ai  / Demo@Admin2026!
--   Demo User   : user@demo.thetaai.ai   / Demo@User2026!
--   (Flask hashes plaintext passwords automatically on first startup.)
-- =============================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- =============================================================================
-- COMPANIES  (no upstream FKs — create first)
-- =============================================================================

CREATE TABLE IF NOT EXISTS companies (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT        NOT NULL,
    slug         TEXT        UNIQUE NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    features     JSONB       NOT NULL DEFAULT '{}',
    is_suspended BOOLEAN     NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_companies_suspended ON companies(is_suspended);

-- =============================================================================
-- USERS
-- =============================================================================

CREATE TABLE IF NOT EXISTS users (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email                TEXT        UNIQUE NOT NULL,
    name                 TEXT        NOT NULL,
    role                 TEXT        NOT NULL
                             CHECK (role IN ('super_admin','company_admin','admin','manager','user')),
    password             TEXT        NOT NULL,
    must_change_password BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ,
    -- multi-tenancy columns
    company_id           UUID        REFERENCES companies(id) ON DELETE SET NULL,
    status               TEXT        NOT NULL DEFAULT 'approved'
                             CHECK (status IN ('pending','approved','rejected','inactive')),
    approved_by          UUID        REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_users_company_id ON users(company_id);
CREATE INDEX IF NOT EXISTS idx_users_status     ON users(status);

-- =============================================================================
-- AUTH & SESSIONS
-- =============================================================================

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token      TEXT        PRIMARY KEY,
    user_id    UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

-- =============================================================================
-- SUBSCRIPTIONS
-- =============================================================================

CREATE TABLE IF NOT EXISTS subscriptions (
    user_id          UUID    PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    plan             TEXT    NOT NULL DEFAULT 'free',
    uploads_today    INTEGER NOT NULL DEFAULT 0,
    total_uploads    INTEGER NOT NULL DEFAULT 0,
    last_upload_date DATE,
    daily_limit      INTEGER NOT NULL DEFAULT 1,
    is_locked        BOOLEAN NOT NULL DEFAULT FALSE
);

-- =============================================================================
-- NOTIFICATIONS
-- =============================================================================

CREATE TABLE IF NOT EXISTS notifications (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      TEXT        NOT NULL,
    message    TEXT        NOT NULL,
    type       TEXT        NOT NULL,
    metadata   JSONB       NOT NULL DEFAULT '{}',
    read       BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notifications_user_id  ON notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_created  ON notifications(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_metadata ON notifications USING GIN(metadata);

-- =============================================================================
-- PUSH SUBSCRIPTIONS  (web push notification endpoints per user)
-- =============================================================================

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id         SERIAL      PRIMARY KEY,
    user_id    UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint   TEXT        NOT NULL,
    p256dh     TEXT        NOT NULL,
    auth       TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, endpoint)
);

-- =============================================================================
-- FILE PROCESSING HISTORY
-- =============================================================================

CREATE TABLE IF NOT EXISTS history (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID        REFERENCES users(id) ON DELETE SET NULL,
    company_id      UUID        REFERENCES companies(id) ON DELETE CASCADE,
    filename        TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'completed',
    total_sheets    INTEGER     NOT NULL DEFAULT 0,
    success_count   INTEGER     NOT NULL DEFAULT 0,
    error_count     INTEGER     NOT NULL DEFAULT 0,
    detected_sheets JSONB       NOT NULL DEFAULT '[]',
    results         JSONB       NOT NULL DEFAULT '[]',
    processed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_history_user_id ON history(user_id);
CREATE INDEX IF NOT EXISTS idx_history_company  ON history(company_id);

-- =============================================================================
-- PENDING UPLOAD APPROVALS
-- =============================================================================

CREATE TABLE IF NOT EXISTS pending_upload_approvals (
    approval_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id           UUID        NOT NULL,
    upload_filename  TEXT        NOT NULL,
    upload_path      TEXT        NOT NULL,
    user_id          UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    company_id       UUID        REFERENCES companies(id) ON DELETE CASCADE,
    user_name        TEXT        NOT NULL DEFAULT '',
    status           TEXT        NOT NULL DEFAULT 'pending',
    detected_changes JSONB       NOT NULL DEFAULT '[]',
    submitted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_approvals_user_id ON pending_upload_approvals(user_id);
CREATE INDEX IF NOT EXISTS idx_approvals_status  ON pending_upload_approvals(status);
CREATE INDEX IF NOT EXISTS idx_pending_approvals_company ON pending_upload_approvals(company_id);

-- =============================================================================
-- THETA FILE ACCESS (library ownership / sharing)
-- =============================================================================

CREATE TABLE IF NOT EXISTS theta_file_access (
    file_id         UUID        PRIMARY KEY,
    company_id      UUID        NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    filename        TEXT        NOT NULL,
    uploaded_by     UUID        REFERENCES users(id) ON DELETE SET NULL,
    visibility      TEXT        NOT NULL DEFAULT 'restricted'
                        CHECK (visibility IN ('restricted', 'company', 'anyone')),
    link_token      TEXT        NOT NULL UNIQUE,
    link_permission TEXT        NOT NULL DEFAULT 'viewer'
                        CHECK (link_permission IN ('viewer', 'editor')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_theta_file_access_company ON theta_file_access(company_id);
CREATE INDEX IF NOT EXISTS idx_theta_file_access_uploaded_by ON theta_file_access(uploaded_by);

CREATE TABLE IF NOT EXISTS theta_file_shares (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id    UUID        NOT NULL REFERENCES theta_file_access(file_id) ON DELETE CASCADE,
    user_id    UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    permission TEXT        NOT NULL DEFAULT 'viewer'
                   CHECK (permission IN ('viewer', 'editor')),
    shared_by  UUID        REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (file_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_theta_file_shares_user ON theta_file_shares(user_id);
CREATE INDEX IF NOT EXISTS idx_theta_file_shares_file ON theta_file_shares(file_id);

CREATE TABLE IF NOT EXISTS theta_file_share_invites (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id    UUID        NOT NULL REFERENCES theta_file_access(file_id) ON DELETE CASCADE,
    email      TEXT        NOT NULL,
    permission TEXT        NOT NULL DEFAULT 'viewer'
                   CHECK (permission IN ('viewer', 'editor')),
    invited_by UUID        REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_theta_file_share_invites_file_email
    ON theta_file_share_invites (file_id, lower(email));

-- =============================================================================
-- BASE FILE VERSIONING
-- =============================================================================

CREATE TABLE IF NOT EXISTS base_file_config (
    id         SERIAL      PRIMARY KEY,
    filename   TEXT        NOT NULL,
    sheet_name TEXT        NOT NULL DEFAULT '',
    is_active  BOOLEAN     NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    company_id UUID        REFERENCES companies(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_base_file_config_company ON base_file_config(company_id);

CREATE TABLE IF NOT EXISTS base_file_versions (
    version_id          TEXT        PRIMARY KEY,
    stage               TEXT        NOT NULL,
    base_filename       TEXT        NOT NULL,
    snapshot_rel_path   TEXT        NOT NULL,
    snapshot_abs_path   TEXT        NOT NULL,
    snapshot_size_bytes BIGINT,
    merge_summary       JSONB       NOT NULL DEFAULT '{}',
    context             JSONB       NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    company_id          UUID        REFERENCES companies(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_versions_base_filename ON base_file_versions(base_filename);
CREATE INDEX IF NOT EXISTS idx_versions_created       ON base_file_versions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_base_file_versions_company ON base_file_versions(company_id);

-- =============================================================================
-- UPDATE CHAIN  (audit trail for base-file merges)
-- =============================================================================

CREATE TABLE IF NOT EXISTS update_chain (
    index         SERIAL      PRIMARY KEY,
    filename      TEXT        NOT NULL,
    source_upload TEXT        NOT NULL DEFAULT '',
    job_id        UUID,
    approved_by   TEXT        NOT NULL DEFAULT 'auto',
    status        TEXT        NOT NULL DEFAULT 'approved',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    company_id    UUID        REFERENCES companies(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_update_chain_company ON update_chain(company_id);

-- =============================================================================
-- MONTHLY REPORTS
-- =============================================================================

CREATE TABLE IF NOT EXISTS monthly_reports (
    id         SERIAL      PRIMARY KEY,
    month      TEXT        NOT NULL,
    year       INTEGER     NOT NULL DEFAULT 2026,
    data       JSONB       NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    company_id UUID        REFERENCES companies(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS monthly_reports_month_year_company_idx
    ON monthly_reports (month, year, company_id);

-- =============================================================================
-- AI CHAT HISTORY
-- =============================================================================

CREATE TABLE IF NOT EXISTS chat_history (
    id           SERIAL      PRIMARY KEY,
    user_id      UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    user_name    TEXT        NOT NULL DEFAULT '',
    user_role    TEXT        NOT NULL DEFAULT 'user',
    route        TEXT        NOT NULL DEFAULT '/api/chat',
    message      TEXT        NOT NULL,
    response     TEXT        NOT NULL,
    model        TEXT        NOT NULL DEFAULT '',
    context_info TEXT        NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_history_user_id ON chat_history(user_id);
CREATE INDEX IF NOT EXISTS idx_chat_history_created  ON chat_history(created_at DESC);

-- =============================================================================
-- AI CACHES
-- =============================================================================

CREATE TABLE IF NOT EXISTS ai_response_cache (
    id           SERIAL      PRIMARY KEY,
    cache_key    TEXT        NOT NULL,
    user_id      UUID        REFERENCES users(id) ON DELETE SET NULL,
    company_id   UUID        REFERENCES companies(id) ON DELETE CASCADE,
    question     TEXT        NOT NULL,
    response     TEXT        NOT NULL,
    context_hash TEXT        NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_cache_company_key
    ON ai_response_cache(COALESCE(company_id::text, ''), cache_key);
CREATE INDEX IF NOT EXISTS idx_ai_cache_company ON ai_response_cache(company_id);

CREATE TABLE IF NOT EXISTS intelligence_insight_cache (
    id         SERIAL      PRIMARY KEY,
    cache_key  TEXT        NOT NULL,
    data_hash  TEXT        NOT NULL DEFAULT '',
    section    TEXT        NOT NULL DEFAULT '',
    title      TEXT        NOT NULL DEFAULT '',
    insight    JSONB       NOT NULL DEFAULT '{}',
    company_id UUID        REFERENCES companies(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_insight_cache_company_key
    ON intelligence_insight_cache(COALESCE(company_id::text, ''), cache_key);
CREATE INDEX IF NOT EXISTS idx_insight_cache_company ON intelligence_insight_cache(company_id);

-- =============================================================================
-- WHAT-IF CACHES
-- =============================================================================

CREATE TABLE IF NOT EXISTS whatif_predecessor_successor_cache (
    id           SERIAL      PRIMARY KEY,
    cache_key    TEXT        NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source       JSONB       NOT NULL DEFAULT '{}',
    dependencies JSONB       NOT NULL DEFAULT '[]',
    company_id   UUID        REFERENCES companies(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_whatif_pred_succ_company_key
    ON whatif_predecessor_successor_cache(COALESCE(company_id::text, ''), cache_key);
CREATE INDEX IF NOT EXISTS idx_whatif_pred_succ_company
    ON whatif_predecessor_successor_cache(company_id);

CREATE TABLE IF NOT EXISTS whatif_project_update_summary (
    id           SERIAL      PRIMARY KEY,
    cache_key    TEXT        NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source       JSONB       NOT NULL DEFAULT '{}',
    updates      JSONB       NOT NULL DEFAULT '[]',
    company_id   UUID        REFERENCES companies(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_whatif_proj_summary_company_key
    ON whatif_project_update_summary(COALESCE(company_id::text, ''), cache_key);
CREATE INDEX IF NOT EXISTS idx_whatif_proj_summary_company
    ON whatif_project_update_summary(company_id);

CREATE TABLE IF NOT EXISTS whatif_claude_responses (
    id         SERIAL      PRIMARY KEY,
    session_id TEXT,
    user_id    UUID        REFERENCES users(id) ON DELETE SET NULL,
    company_id UUID        REFERENCES companies(id) ON DELETE CASCADE,
    question   TEXT,
    response   TEXT,
    context    JSONB       NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_whatif_claude_company ON whatif_claude_responses(company_id);

CREATE TABLE IF NOT EXISTS whatif_critical_dashboard_data (
    id         SERIAL      PRIMARY KEY,
    cache_key  TEXT        NOT NULL DEFAULT 'default',
    data       JSONB       NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    company_id UUID        REFERENCES companies(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_whatif_critical_company_key
    ON whatif_critical_dashboard_data(COALESCE(company_id::text, ''), cache_key);
CREATE INDEX IF NOT EXISTS idx_whatif_critical_company ON whatif_critical_dashboard_data(company_id);

CREATE TABLE IF NOT EXISTS whatif_realtime_data (
    id         SERIAL      PRIMARY KEY,
    cache_key  TEXT        NOT NULL DEFAULT 'default',
    data       JSONB       NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    company_id UUID        REFERENCES companies(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_whatif_realtime_company_key
    ON whatif_realtime_data(COALESCE(company_id::text, ''), cache_key);
CREATE INDEX IF NOT EXISTS idx_whatif_realtime_company ON whatif_realtime_data(company_id);

-- =============================================================================
-- DEVIATIONS  (uses TEXT for user_id/company_id — no FK constraints by design)
-- =============================================================================

CREATE TABLE IF NOT EXISTS deviations (
    id                        SERIAL      PRIMARY KEY,
    sheet                     TEXT,
    flag                      TEXT,
    severity                  TEXT,
    description               TEXT,
    row_data                  JSONB,
    detected_at               TEXT,
    review_status             TEXT        NOT NULL DEFAULT 'Pending',
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

-- =============================================================================
-- ACTIVITY LOG  (uses TEXT for user_id/company_id — cross-cutting audit trail)
-- =============================================================================

CREATE TABLE IF NOT EXISTS activity_log (
    id          SERIAL      PRIMARY KEY,
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

-- =============================================================================
-- ENGAGE (social / collaboration)
-- =============================================================================

CREATE TABLE IF NOT EXISTS engage_groups (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name       TEXT        NOT NULL,
    members    JSONB       NOT NULL DEFAULT '[]',
    company_id UUID        REFERENCES companies(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_engage_groups_company ON engage_groups(company_id);

CREATE TABLE IF NOT EXISTS engage_posts (
    id         TEXT        PRIMARY KEY,
    user_id    UUID        REFERENCES users(id) ON DELETE SET NULL,
    company_id UUID        REFERENCES companies(id) ON DELETE CASCADE,
    user_name  TEXT        NOT NULL DEFAULT '',
    user_email TEXT        NOT NULL DEFAULT '',
    content    TEXT        NOT NULL DEFAULT '',
    image_url  TEXT        NOT NULL DEFAULT '',
    group_id   TEXT        NOT NULL DEFAULT '',
    source     TEXT        NOT NULL DEFAULT 'manual',
    likes      JSONB       NOT NULL DEFAULT '[]',
    comments   JSONB       NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_engage_posts_user_id  ON engage_posts(user_id);
CREATE INDEX IF NOT EXISTS idx_engage_posts_group_id ON engage_posts(group_id);
CREATE INDEX IF NOT EXISTS idx_engage_posts_company  ON engage_posts(company_id);

CREATE TABLE IF NOT EXISTS engage_monthly_summary_log (
    id         SERIAL      PRIMARY KEY,
    month      TEXT        NOT NULL,
    company_id UUID        REFERENCES companies(id) ON DELETE CASCADE,
    post_id    TEXT        NOT NULL,
    posted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_monthly_log_month_company
    ON engage_monthly_summary_log(month, COALESCE(company_id::text, ''));

-- =============================================================================
-- PPTX SLIDE SECTIONS CACHE
-- =============================================================================

CREATE TABLE IF NOT EXISTS pptx_slide_sections (
    id          SERIAL      PRIMARY KEY,
    section_key TEXT        NOT NULL,
    data        JSONB       NOT NULL DEFAULT '{}',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    company_id  UUID        REFERENCES companies(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_pptx_sections_company_key
    ON pptx_slide_sections(COALESCE(company_id::text, ''), section_key);
CREATE INDEX IF NOT EXISTS idx_pptx_sections_company ON pptx_slide_sections(company_id);

-- =============================================================================
-- RECOVERY NARRATIVE
-- =============================================================================

CREATE TABLE IF NOT EXISTS recovery_narrative (
    id         SERIAL      PRIMARY KEY,
    company_id UUID        REFERENCES companies(id) ON DELETE CASCADE,
    data       JSONB       NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_recovery_narrative_company
    ON recovery_narrative(COALESCE(company_id::text, ''));
CREATE INDEX IF NOT EXISTS idx_recovery_narrative_company ON recovery_narrative(company_id);

-- =============================================================================
-- REPORT BUILDER
-- =============================================================================

CREATE TABLE IF NOT EXISTS report_builder_catalog (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    label        TEXT        NOT NULL,
    description  TEXT        NOT NULL DEFAULT '',
    type         TEXT        NOT NULL DEFAULT 'kpi_card',
    data_key     TEXT        NOT NULL,
    unit         TEXT        NOT NULL DEFAULT '',
    is_suggested BOOLEAN     NOT NULL DEFAULT FALSE,
    sort_order   INTEGER     NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS report_builder_config (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID        UNIQUE REFERENCES companies(id) ON DELETE CASCADE,
    layout     JSONB       NOT NULL DEFAULT '[]',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- THETA SHEETS (live spreadsheet editor)
-- =============================================================================

CREATE TABLE IF NOT EXISTS theta_sheets (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id  UUID        NOT NULL UNIQUE REFERENCES companies(id) ON DELETE CASCADE,
    name        TEXT        NOT NULL DEFAULT 'Theta Sheets',
    data        JSONB       NOT NULL DEFAULT '{"sheets":[]}',
    version     INTEGER     NOT NULL DEFAULT 1,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_theta_sheets_company ON theta_sheets(company_id);

-- =============================================================================
-- SEED DATA
-- =============================================================================

-- ── Companies ─────────────────────────────────────────────────────────────────

INSERT INTO companies (id, name, slug) VALUES
    ('a1000000-0000-4000-a000-000000000001', 'Descon',       'descon'),
    ('b2000000-0000-4000-b000-000000000002', 'Demo Company', 'demo')
ON CONFLICT (slug) DO NOTHING;

-- ── Super Admin (no company scope) ───────────────────────────────────────────

INSERT INTO users (email, name, role, password, status, company_id, must_change_password)
VALUES (
    'superadmin@thetaai.ai',
    'Super Admin',
    'super_admin',
    'SuperAdmin@123!',
    'approved',
    NULL,
    TRUE
)
ON CONFLICT (email) DO NOTHING;

-- ── Demo Company Users ────────────────────────────────────────────────────────

INSERT INTO users (email, name, role, password, status, company_id, must_change_password)
SELECT u.email, u.name, u.role, u.password, u.status, c.id, u.must_change_password
FROM (VALUES
    ('admin@demo.thetaai.ai', 'Demo Admin', 'admin', 'Demo@Admin2026!', 'approved', TRUE),
    ('user@demo.thetaai.ai',  'Demo User',  'user',  'Demo@User2026!',  'approved', TRUE)
) AS u(email, name, role, password, status, must_change_password)
CROSS JOIN (SELECT id FROM companies WHERE slug = 'demo') c
ON CONFLICT (email) DO NOTHING;

-- ── Demo Subscriptions ────────────────────────────────────────────────────────

INSERT INTO subscriptions (user_id, plan, daily_limit)
SELECT id, 'pro', 10
FROM users
WHERE email IN ('admin@demo.thetaai.ai', 'user@demo.thetaai.ai')
ON CONFLICT (user_id) DO NOTHING;

-- ── Report Builder Catalog ────────────────────────────────────────────────────

INSERT INTO report_builder_catalog (label, description, type, data_key, unit, is_suggested, sort_order)
VALUES
    ('Total Activities',    'Total number of project activities tracked this month',   'kpi_card',  'totalActivities',   '',  TRUE,  0),
    ('On-Time Activities',  'Activities completed or progressing on schedule',          'kpi_card',  'onTime',            '',  TRUE,  1),
    ('Late Activities',     'Activities running behind planned schedule',               'kpi_card',  'onLate',            '',  TRUE,  2),
    ('Milestones Achieved', 'Milestones marked as achieved this period',               'kpi_card',  'milestoneAchieved', '',  TRUE,  3),
    ('Not Started',         'Activities not yet started',                              'kpi_card',  'notStarted',        '',  FALSE, 4),
    ('Early Activities',    'Activities that started ahead of schedule',               'kpi_card',  'onEarly',           '',  FALSE, 5),
    ('Avg Planned Duration','Average planned duration across all activities',          'kpi_card',  'avgPlannedDuration','d', FALSE, 6),
    ('Max Planned Duration','Longest single planned activity duration',                'kpi_card',  'maxPlannedDuration','d', FALSE, 7),
    ('S-Curve Overview',    'Planned vs actual progress by discipline (bar chart)',    'bar_chart', 'scurves',           '%', TRUE,  8),
    ('In Progress',         'Activities currently in progress',                        'kpi_card',  'inProgress',        '',  FALSE, 9),
    ('On Plan (Duration)',  'Activities whose actual duration matches plan',            'kpi_card',  'onPlan',            '',  FALSE, 10)
ON CONFLICT DO NOTHING;

-- ── Demo Monthly Reports ──────────────────────────────────────────────────────

INSERT INTO monthly_reports (month, year, data, company_id)
SELECT 'February', 2026, '{
    "totalActivities": 1850, "onTime": 1210, "onLate": 390, "onEarly": 182,
    "notStarted": 68, "milestoneAchieved": 12, "durationMissing": 148,
    "avgPlannedDuration": 42, "maxPlannedDuration": 280,
    "scurves": {
        "homeOffice":    { "actual": 62.4, "planned": 68.0 },
        "manufacturing": { "actual": 44.1, "planned": 50.0 },
        "construction":  { "actual": 18.3, "planned": 22.0 },
        "projectMgmt":   { "actual": 70.2, "planned": 74.0 },
        "commissioning": { "actual":  2.1, "planned":  3.0 }
    }
}'::jsonb, id FROM companies WHERE slug = 'demo'
ON CONFLICT (month, year, company_id) DO NOTHING;

INSERT INTO monthly_reports (month, year, data, company_id)
SELECT 'March', 2026, '{
    "totalActivities": 1850, "onTime": 1174, "onLate": 432, "onEarly": 176,
    "notStarted": 68, "milestoneAchieved": 14, "durationMissing": 142,
    "avgPlannedDuration": 42, "maxPlannedDuration": 280,
    "scurves": {
        "homeOffice":    { "actual": 71.8, "planned": 78.0 },
        "manufacturing": { "actual": 54.6, "planned": 62.0 },
        "construction":  { "actual": 26.9, "planned": 34.0 },
        "projectMgmt":   { "actual": 77.4, "planned": 81.0 },
        "commissioning": { "actual":  3.8, "planned":  5.5 }
    }
}'::jsonb, id FROM companies WHERE slug = 'demo'
ON CONFLICT (month, year, company_id) DO NOTHING;

INSERT INTO monthly_reports (month, year, data, company_id)
SELECT 'April', 2026, '{
    "totalActivities": 1850, "onTime": 1098, "onLate": 518, "onEarly": 164,
    "notStarted": 70, "milestoneAchieved": 15, "durationMissing": 139,
    "avgPlannedDuration": 42, "maxPlannedDuration": 280,
    "scurves": {
        "homeOffice":    { "actual": 79.2, "planned": 88.0 },
        "manufacturing": { "actual": 63.4, "planned": 74.0 },
        "construction":  { "actual": 34.7, "planned": 47.0 },
        "projectMgmt":   { "actual": 83.1, "planned": 88.0 },
        "commissioning": { "actual":  6.2, "planned":  9.0 }
    }
}'::jsonb, id FROM companies WHERE slug = 'demo'
ON CONFLICT (month, year, company_id) DO NOTHING;

COMMIT;

-- =============================================================================
-- AFTER RUNNING THIS FILE:
-- 1. Copy .env.example → .env and fill in DATABASE_URL, SECRET_KEY, etc.
-- 2. Start Flask:  python app.py
--    Flask will auto-migrate any remaining tables on first startup.
-- 3. Log in as Super Admin to create Descon users:
--       superadmin@thetaai.ai / SuperAdmin@123!
-- 4. Upload a Descon EPC tracker XLSX via the UI to populate the Knowledgebase.
-- =============================================================================
