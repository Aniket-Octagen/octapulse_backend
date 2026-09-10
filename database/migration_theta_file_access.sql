-- =============================================================================
-- Additive: Theta file ownership / sharing (US1)
-- Safe on production — CREATE TABLE / INDEX IF NOT EXISTS only.
-- =============================================================================

BEGIN;

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

COMMIT;
