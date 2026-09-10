-- =============================================================================
-- Deskon Production Migration + Seed
-- Run ONCE against the production PostgreSQL database.
-- All statements are idempotent (IF NOT EXISTS / ON CONFLICT DO NOTHING guards).
-- Consolidates: migration_multitenancy, migration_company_features,
--               migration_company_suspend, migration_inactive_status,
--               migration_company_scoped_tables, migration_monthly_reports_company
-- Includes: Descon data backfill (assigns all pre-tenancy rows to Descon)
-- =============================================================================
 
BEGIN;

-- =============================================================================
-- PART 1 — SCHEMA MIGRATIONS
-- =============================================================================

-- ── 1. Companies table ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS companies (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT        NOT NULL,
    slug         TEXT        UNIQUE NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    features     JSONB       NOT NULL DEFAULT '{}',
    is_suspended BOOLEAN     NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_companies_suspended ON companies(is_suspended);

-- Insert Descon immediately so FK-referencing backfills below can reference it.
-- ON CONFLICT DO NOTHING makes this idempotent if it already exists.
INSERT INTO companies (id, name, slug) VALUES
    ('a1000000-0000-4000-a000-000000000001', 'Descon', 'descon')
ON CONFLICT (slug) DO NOTHING;

-- ── 2. Users — multi-tenant columns ──────────────────────────────────────────
-- Drop old role constraint and replace with extended role set
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check
    CHECK (role IN ('super_admin', 'company_admin', 'admin', 'manager', 'user'));

-- Add company membership (NULL = super_admin, no company scope)
ALTER TABLE users ADD COLUMN IF NOT EXISTS company_id  UUID REFERENCES companies(id) ON DELETE SET NULL;

-- Add approval workflow columns
ALTER TABLE users ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'approved';
ALTER TABLE users ADD COLUMN IF NOT EXISTS approved_by UUID REFERENCES users(id) ON DELETE SET NULL;

-- Drop old status constraint (may have been created without 'inactive')
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_status_check;
ALTER TABLE users ADD CONSTRAINT users_status_check
    CHECK (status IN ('pending', 'approved', 'rejected', 'inactive'));

CREATE INDEX IF NOT EXISTS idx_users_company_id ON users(company_id);
CREATE INDEX IF NOT EXISTS idx_users_status     ON users(status);

-- Grandfather existing users as approved
UPDATE users SET status = 'approved' WHERE status IS NULL OR status NOT IN ('pending','approved','rejected','inactive');

-- Assign existing non-super-admin users to Descon if not already assigned
UPDATE users
    SET company_id = 'a1000000-0000-4000-a000-000000000001'
    WHERE company_id IS NULL AND role != 'super_admin';

-- ── 3. Monthly reports — add company_id ──────────────────────────────────────
ALTER TABLE monthly_reports
    ADD COLUMN IF NOT EXISTS company_id UUID REFERENCES companies(id) ON DELETE CASCADE;

DO $$
BEGIN
    ALTER TABLE monthly_reports DROP CONSTRAINT IF EXISTS monthly_reports_month_year_key;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS monthly_reports_month_year_company_idx
    ON monthly_reports (month, year, company_id);

-- ── 4. Engage monthly summary log — company-scoped ───────────────────────────
-- Table is auto-created by Flask on startup; ALTER is safe on existing DBs.
-- Wrap in DO block so it's a no-op if the table doesn't exist yet.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'engage_monthly_summary_log') THEN
        ALTER TABLE engage_monthly_summary_log ADD COLUMN IF NOT EXISTS company_id UUID;
        ALTER TABLE engage_monthly_summary_log
            DROP CONSTRAINT IF EXISTS engage_monthly_summary_log_month_key;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_monthly_log_month_company
    ON engage_monthly_summary_log(month, COALESCE(company_id::text, ''));

-- ── 5. Add company_id to all shared tables ────────────────────────────────────

-- engage_posts
ALTER TABLE engage_posts ADD COLUMN IF NOT EXISTS company_id UUID;
CREATE INDEX IF NOT EXISTS idx_engage_posts_company ON engage_posts(company_id);

-- engage_groups
ALTER TABLE engage_groups ADD COLUMN IF NOT EXISTS company_id UUID;
CREATE INDEX IF NOT EXISTS idx_engage_groups_company ON engage_groups(company_id);

-- history
ALTER TABLE history ADD COLUMN IF NOT EXISTS company_id UUID;
CREATE INDEX IF NOT EXISTS idx_history_company ON history(company_id);

-- pending_upload_approvals
ALTER TABLE pending_upload_approvals ADD COLUMN IF NOT EXISTS company_id UUID;
CREATE INDEX IF NOT EXISTS idx_pending_approvals_company ON pending_upload_approvals(company_id);

-- base_file_config
ALTER TABLE base_file_config ADD COLUMN IF NOT EXISTS company_id UUID;
CREATE INDEX IF NOT EXISTS idx_base_file_config_company ON base_file_config(company_id);

-- base_file_versions
ALTER TABLE base_file_versions ADD COLUMN IF NOT EXISTS company_id UUID;
CREATE INDEX IF NOT EXISTS idx_base_file_versions_company ON base_file_versions(company_id);

-- update_chain
ALTER TABLE update_chain ADD COLUMN IF NOT EXISTS company_id UUID;
CREATE INDEX IF NOT EXISTS idx_update_chain_company ON update_chain(company_id);

-- ai_response_cache
ALTER TABLE ai_response_cache ADD COLUMN IF NOT EXISTS company_id UUID;
ALTER TABLE ai_response_cache DROP CONSTRAINT IF EXISTS ai_response_cache_cache_key_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_cache_company_key
    ON ai_response_cache(COALESCE(company_id::text, ''), cache_key);
CREATE INDEX IF NOT EXISTS idx_ai_cache_company ON ai_response_cache(company_id);

-- intelligence_insight_cache
ALTER TABLE intelligence_insight_cache ADD COLUMN IF NOT EXISTS company_id UUID;
ALTER TABLE intelligence_insight_cache DROP CONSTRAINT IF EXISTS intelligence_insight_cache_cache_key_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_insight_cache_company_key
    ON intelligence_insight_cache(COALESCE(company_id::text, ''), cache_key);
CREATE INDEX IF NOT EXISTS idx_insight_cache_company ON intelligence_insight_cache(company_id);

-- whatif_predecessor_successor_cache
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'whatif_predecessor_successor_cache' AND column_name = 'company_id'
    ) THEN
        ALTER TABLE whatif_predecessor_successor_cache ADD COLUMN company_id UUID;
        ALTER TABLE whatif_predecessor_successor_cache ADD COLUMN id SERIAL;
        ALTER TABLE whatif_predecessor_successor_cache
            DROP CONSTRAINT IF EXISTS whatif_predecessor_successor_cache_pkey;
        ALTER TABLE whatif_predecessor_successor_cache ADD PRIMARY KEY (id);
        CREATE UNIQUE INDEX uq_whatif_pred_succ_company_key
            ON whatif_predecessor_successor_cache(COALESCE(company_id::text, ''), cache_key);
        CREATE INDEX idx_whatif_pred_succ_company
            ON whatif_predecessor_successor_cache(company_id);
    END IF;
END $$;

-- whatif_project_update_summary
ALTER TABLE whatif_project_update_summary ADD COLUMN IF NOT EXISTS company_id UUID;
ALTER TABLE whatif_project_update_summary
    DROP CONSTRAINT IF EXISTS whatif_project_update_summary_cache_key_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_whatif_proj_summary_company_key
    ON whatif_project_update_summary(COALESCE(company_id::text, ''), cache_key);
CREATE INDEX IF NOT EXISTS idx_whatif_proj_summary_company
    ON whatif_project_update_summary(company_id);

-- whatif_claude_responses
ALTER TABLE whatif_claude_responses ADD COLUMN IF NOT EXISTS company_id UUID;
CREATE INDEX IF NOT EXISTS idx_whatif_claude_company ON whatif_claude_responses(company_id);

-- whatif_critical_dashboard_data
ALTER TABLE whatif_critical_dashboard_data ADD COLUMN IF NOT EXISTS company_id UUID;
ALTER TABLE whatif_critical_dashboard_data
    DROP CONSTRAINT IF EXISTS whatif_critical_dashboard_data_cache_key_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_whatif_critical_company_key
    ON whatif_critical_dashboard_data(COALESCE(company_id::text, ''), cache_key);
CREATE INDEX IF NOT EXISTS idx_whatif_critical_company ON whatif_critical_dashboard_data(company_id);

-- whatif_realtime_data
ALTER TABLE whatif_realtime_data ADD COLUMN IF NOT EXISTS company_id UUID;
ALTER TABLE whatif_realtime_data
    DROP CONSTRAINT IF EXISTS whatif_realtime_data_cache_key_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_whatif_realtime_company_key
    ON whatif_realtime_data(COALESCE(company_id::text, ''), cache_key);
CREATE INDEX IF NOT EXISTS idx_whatif_realtime_company ON whatif_realtime_data(company_id);

-- pptx_slide_sections
ALTER TABLE pptx_slide_sections ADD COLUMN IF NOT EXISTS company_id UUID;
ALTER TABLE pptx_slide_sections
    DROP CONSTRAINT IF EXISTS pptx_slide_sections_section_key_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_pptx_sections_company_key
    ON pptx_slide_sections(COALESCE(company_id::text, ''), section_key);
CREATE INDEX IF NOT EXISTS idx_pptx_sections_company ON pptx_slide_sections(company_id);

-- ── 6. Recovery narrative table ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS recovery_narrative (
    id         SERIAL      PRIMARY KEY,
    company_id UUID,
    data       JSONB       NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_recovery_narrative_company
    ON recovery_narrative(COALESCE(company_id::text, ''));
CREATE INDEX IF NOT EXISTS idx_recovery_narrative_company ON recovery_narrative(company_id);

-- ── 7. Backfill existing data → Descon ───────────────────────────────────────
-- All rows that existed before multi-tenancy belong to Descon.
-- The WHERE company_id IS NULL guard makes this idempotent.

UPDATE engage_posts               SET company_id = 'a1000000-0000-4000-a000-000000000001' WHERE company_id IS NULL;
UPDATE engage_groups              SET company_id = 'a1000000-0000-4000-a000-000000000001' WHERE company_id IS NULL;
UPDATE history                    SET company_id = 'a1000000-0000-4000-a000-000000000001' WHERE company_id IS NULL;
UPDATE pending_upload_approvals   SET company_id = 'a1000000-0000-4000-a000-000000000001' WHERE company_id IS NULL;
UPDATE base_file_config           SET company_id = 'a1000000-0000-4000-a000-000000000001' WHERE company_id IS NULL;
UPDATE base_file_versions         SET company_id = 'a1000000-0000-4000-a000-000000000001' WHERE company_id IS NULL;
UPDATE update_chain               SET company_id = 'a1000000-0000-4000-a000-000000000001' WHERE company_id IS NULL;
UPDATE monthly_reports            SET company_id = 'a1000000-0000-4000-a000-000000000001' WHERE company_id IS NULL;
UPDATE recovery_narrative         SET company_id = 'a1000000-0000-4000-a000-000000000001' WHERE company_id IS NULL;
UPDATE whatif_claude_responses    SET company_id = 'a1000000-0000-4000-a000-000000000001' WHERE company_id IS NULL;

-- Cache tables: assign to Descon so existing cached results remain usable.
-- (Alternatively these could be truncated since they auto-regenerate.)
UPDATE ai_response_cache                  SET company_id = 'a1000000-0000-4000-a000-000000000001' WHERE company_id IS NULL;
UPDATE intelligence_insight_cache         SET company_id = 'a1000000-0000-4000-a000-000000000001' WHERE company_id IS NULL;
UPDATE whatif_predecessor_successor_cache SET company_id = 'a1000000-0000-4000-a000-000000000001' WHERE company_id IS NULL;
UPDATE whatif_project_update_summary      SET company_id = 'a1000000-0000-4000-a000-000000000001' WHERE company_id IS NULL;
UPDATE whatif_critical_dashboard_data     SET company_id = 'a1000000-0000-4000-a000-000000000001' WHERE company_id IS NULL;
UPDATE whatif_realtime_data               SET company_id = 'a1000000-0000-4000-a000-000000000001' WHERE company_id IS NULL;
UPDATE pptx_slide_sections                SET company_id = 'a1000000-0000-4000-a000-000000000001' WHERE company_id IS NULL;

-- deviations table (if company_id column exists but rows are unassigned)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'deviations' AND column_name = 'company_id'
    ) THEN
        EXECUTE 'UPDATE deviations SET company_id = ''a1000000-0000-4000-a000-000000000001'' WHERE company_id IS NULL';
    END IF;
END $$;

-- engage_monthly_summary_log (if it already existed before this migration)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'engage_monthly_summary_log' AND column_name = 'company_id'
    ) THEN
        EXECUTE 'UPDATE engage_monthly_summary_log SET company_id = ''a1000000-0000-4000-a000-000000000001'' WHERE company_id IS NULL';
    END IF;
END $$;


-- =============================================================================
-- PART 2 — SEED DATA
-- =============================================================================

-- ── 7. Seed: Companies ────────────────────────────────────────────────────────
-- Fixed UUIDs so this seed is idempotent and can be cross-referenced below.

INSERT INTO companies (id, name, slug) VALUES
    ('a1000000-0000-4000-a000-000000000001', 'Descon',       'descon'),
    ('b2000000-0000-4000-b000-000000000002', 'Demo Company', 'demo')
ON CONFLICT (slug) DO NOTHING;

-- ── 8. Seed: Super Admin ──────────────────────────────────────────────────────
-- Plaintext password — Flask will hash it automatically on first startup.
-- Change on first login (must_change_password = TRUE).
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

-- ── 9. Seed: Demo Company Users ───────────────────────────────────────────────
-- Plaintext passwords — Flask hashes them automatically on first startup.
-- Uses subquery to resolve company_id by slug — works with any demo UUID.
INSERT INTO users (email, name, role, password, status, company_id, must_change_password)
SELECT
    u.email, u.name, u.role, u.password, u.status, c.id, u.must_change_password
FROM (VALUES
    ('admin@demo.thetaai.ai', 'Demo Admin', 'admin', 'Demo@Admin2026!', 'approved', TRUE),
    ('user@demo.thetaai.ai',  'Demo User',  'user',  'Demo@User2026!',  'approved', TRUE)
) AS u(email, name, role, password, status, must_change_password)
CROSS JOIN (SELECT id FROM companies WHERE slug = 'demo') c
ON CONFLICT (email) DO NOTHING;

-- ── 10. Seed: Demo Subscriptions ──────────────────────────────────────────────
INSERT INTO subscriptions (user_id, plan, daily_limit)
SELECT id, 'pro', 10
FROM users
WHERE email IN ('admin@demo.thetaai.ai', 'user@demo.thetaai.ai')
ON CONFLICT (user_id) DO NOTHING;

-- ── 11. Seed: Demo Monthly Reports ────────────────────────────────────────────
-- Three months of realistic EPC demo data for the Demo Company.

INSERT INTO monthly_reports (month, year, data, company_id)
SELECT 'February', 2026,
    '{
        "totalActivities": 1850,
        "onTime": 1210,
        "onLate": 390,
        "onEarly": 182,
        "notStarted": 68,
        "milestoneAchieved": 12,
        "durationMissing": 148,
        "avgPlannedDuration": 42,
        "maxPlannedDuration": 280,
        "scurves": {
            "homeOffice":    { "actual": 62.4, "planned": 68.0 },
            "manufacturing": { "actual": 44.1, "planned": 50.0 },
            "construction":  { "actual": 18.3, "planned": 22.0 },
            "projectMgmt":   { "actual": 70.2, "planned": 74.0 },
            "commissioning": { "actual":  2.1, "planned":  3.0 }
        }
    }'::jsonb,
    id
FROM companies WHERE slug = 'demo'
ON CONFLICT (month, year, company_id) DO NOTHING;

INSERT INTO monthly_reports (month, year, data, company_id)
SELECT 'March', 2026,
    '{
        "totalActivities": 1850,
        "onTime": 1174,
        "onLate": 432,
        "onEarly": 176,
        "notStarted": 68,
        "milestoneAchieved": 14,
        "durationMissing": 142,
        "avgPlannedDuration": 42,
        "maxPlannedDuration": 280,
        "scurves": {
            "homeOffice":    { "actual": 71.8, "planned": 78.0 },
            "manufacturing": { "actual": 54.6, "planned": 62.0 },
            "construction":  { "actual": 26.9, "planned": 34.0 },
            "projectMgmt":   { "actual": 77.4, "planned": 81.0 },
            "commissioning": { "actual":  3.8, "planned":  5.5 }
        }
    }'::jsonb,
    id
FROM companies WHERE slug = 'demo'
ON CONFLICT (month, year, company_id) DO NOTHING;

INSERT INTO monthly_reports (month, year, data, company_id)
SELECT 'April', 2026,
    '{
        "totalActivities": 1850,
        "onTime": 1098,
        "onLate": 518,
        "onEarly": 164,
        "notStarted": 70,
        "milestoneAchieved": 15,
        "durationMissing": 139,
        "avgPlannedDuration": 42,
        "maxPlannedDuration": 280,
        "scurves": {
            "homeOffice":    { "actual": 79.2, "planned": 88.0 },
            "manufacturing": { "actual": 63.4, "planned": 74.0 },
            "construction":  { "actual": 34.7, "planned": 47.0 },
            "projectMgmt":   { "actual": 83.1, "planned": 88.0 },
            "commissioning": { "actual":  6.2, "planned":  9.0 }
        }
    }'::jsonb,
    id
FROM companies WHERE slug = 'demo'
ON CONFLICT (month, year, company_id) DO NOTHING;

COMMIT;

-- =============================================================================
-- POST-MIGRATION CHECKLIST
-- =============================================================================
-- 1. Run this script: psql $DATABASE_URL -f database/production_migration.sql
-- 2. Restart Flask — init functions will auto-migrate any remaining tables.
-- 3. Super admin login:   superadmin@thetaai.ai / SuperAdmin@123!
--    → Change password immediately on first login.
-- 4. Demo admin login:    admin@demo.thetaai.ai / Demo@Admin2026!
--    Demo user login:     user@demo.thetaai.ai  / Demo@User2026!
-- 5. Set SECRET_KEY env var to a strong random string in production .env
--    (Flask will warn loudly on startup if it is missing).
-- 6. After Flask restart, upload a demo EPC tracker XLSX for the Demo Company
--    to populate the Knowledgebase folder (b2000000-…/demo_…/).
-- =============================================================================
