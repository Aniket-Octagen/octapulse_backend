-- =============================================================================
-- Additive: ensure deleting a user removes their theta_file_shares rows.
-- Safe on production — drops/recreates FK constraints only; no row data changes.
-- Flask also applies this on startup via init_theta_file_access_tables().
-- =============================================================================

BEGIN;

ALTER TABLE theta_file_shares
    DROP CONSTRAINT IF EXISTS theta_file_shares_user_id_fkey;
ALTER TABLE theta_file_shares
    ADD CONSTRAINT theta_file_shares_user_id_fkey
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE theta_file_shares
    DROP CONSTRAINT IF EXISTS theta_file_shares_shared_by_fkey;
ALTER TABLE theta_file_shares
    ADD CONSTRAINT theta_file_shares_shared_by_fkey
    FOREIGN KEY (shared_by) REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE theta_file_access
    DROP CONSTRAINT IF EXISTS theta_file_access_uploaded_by_fkey;
ALTER TABLE theta_file_access
    ADD CONSTRAINT theta_file_access_uploaded_by_fkey
    FOREIGN KEY (uploaded_by) REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE theta_file_share_invites
    DROP CONSTRAINT IF EXISTS theta_file_share_invites_invited_by_fkey;
ALTER TABLE theta_file_share_invites
    ADD CONSTRAINT theta_file_share_invites_invited_by_fkey
    FOREIGN KEY (invited_by) REFERENCES users(id) ON DELETE SET NULL;

COMMIT;
