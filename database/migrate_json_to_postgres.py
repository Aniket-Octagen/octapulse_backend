"""
migrate_json_to_postgres.py
Reads all JSON files from the database/ directory and seeds a PostgreSQL database.

Usage:
    pip install psycopg2-binary
    python database/migrate_json_to_postgres.py

Environment variables (or edit the CONFIG block below):
    DB_HOST     default: localhost
    DB_PORT     default: 5432
    DB_NAME     default: deskon
    DB_USER     default: postgres
    DB_PASSWORD default: (empty)
"""

import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras  # for execute_values and UUID adapter

psycopg2.extras.register_uuid()

# ── Load .env file manually (no python-dotenv dependency) ─────────────────────

def _load_env(env_path: Path):
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)

_load_env(Path(__file__).parent / ".env")

# ── Config ────────────────────────────────────────────────────────────────────

CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     int(os.getenv("DB_PORT", 5432)),
    "dbname":   os.getenv("DB_NAME", "deskon"),
    "user":     os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
}

DB_DIR = Path(__file__).parent  # database/

# ── Helpers ───────────────────────────────────────────────────────────────────

def load(filename: str):
    path = DB_DIR / filename
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def to_uuid(value):
    """Return a uuid.UUID or None — never a bare string."""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError):
        return None


def parse_ts(value):
    """Parse ISO timestamp string to a timezone-aware datetime, or None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def jsond(value):
    """Serialise a Python object to a JSON string for JSONB columns."""
    return json.dumps(value, ensure_ascii=False)


def log(msg: str):
    print(f"  {msg}")


# ── Migration steps ───────────────────────────────────────────────────────────

def migrate_users(cur):
    rows = load("users.json") or []
    inserted = 0
    for u in rows:
        cur.execute(
            """
            INSERT INTO users (id, email, name, role, password, must_change_password, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (
                to_uuid(u["id"]),
                u["email"],
                u.get("name", ""),
                u.get("role", "user"),
                u.get("password", ""),
                u.get("must_change_password", False),
                parse_ts(u.get("created_at")),
                parse_ts(u.get("updated_at")),
            ),
        )
        inserted += cur.rowcount
    log(f"users: {inserted}/{len(rows)} inserted")


def migrate_subscriptions(cur):
    rows = load("subscriptions.json") or []
    inserted = 0
    for s in rows:
        uid = to_uuid(s.get("user_id"))
        if uid is None:
            continue
        # Check user exists (subscriptions for deleted/unknown users are skipped)
        cur.execute("SELECT 1 FROM users WHERE id = %s", (uid,))
        if cur.fetchone() is None:
            continue
        last_date = s.get("last_upload_date")
        cur.execute(
            """
            INSERT INTO subscriptions (user_id, plan, uploads_today, total_uploads, last_upload_date, daily_limit, is_locked)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO NOTHING
            """,
            (
                uid,
                s.get("plan", "free"),
                s.get("uploads_today", 0),
                s.get("total_uploads", 0),
                last_date or None,
                s.get("daily_limit", 1),
                s.get("is_locked", False),
            ),
        )
        inserted += cur.rowcount
    log(f"subscriptions: {inserted}/{len(rows)} inserted")


def migrate_notifications(cur):
    rows = load("notifications.json") or []
    inserted = 0
    for n in rows:
        nid = to_uuid(n.get("id"))
        uid = to_uuid(n.get("user_id"))
        if nid is None or uid is None:
            continue
        cur.execute("SELECT 1 FROM users WHERE id = %s", (uid,))
        if cur.fetchone() is None:
            continue
        cur.execute(
            """
            INSERT INTO notifications (id, user_id, title, message, type, metadata, read, created_at)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (
                nid,
                uid,
                n.get("title", ""),
                n.get("message", ""),
                n.get("type", "info"),
                jsond(n.get("metadata", {})),
                n.get("read", False),
                parse_ts(n.get("created_at")),
            ),
        )
        inserted += cur.rowcount
    log(f"notifications: {inserted}/{len(rows)} inserted")


def migrate_history(cur):
    rows = load("history.json") or []
    inserted = 0
    for h in rows:
        hid = to_uuid(h.get("id"))
        uid = to_uuid(h.get("user_id"))
        if hid is None:
            continue
        if uid is not None:
            cur.execute("SELECT 1 FROM users WHERE id = %s", (uid,))
            if cur.fetchone() is None:
                uid = None  # store with NULL user_id rather than fail
        cur.execute(
            """
            INSERT INTO history (id, user_id, filename, status, total_sheets, success_count, error_count, detected_sheets, results, processed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (
                hid,
                uid,
                h.get("filename", ""),
                h.get("status", "completed"),
                h.get("total_sheets", 0),
                h.get("success_count", 0),
                h.get("error_count", 0),
                jsond(h.get("detected_sheets", [])),
                jsond(h.get("results", [])),
                parse_ts(h.get("processed_at")),
            ),
        )
        inserted += cur.rowcount
    log(f"history: {inserted}/{len(rows)} inserted")


def migrate_pending_upload_approvals(cur):
    rows = load("pending_upload_approvals.json") or []
    inserted = 0
    for a in rows:
        aid = to_uuid(a.get("approval_id"))
        uid = to_uuid(a.get("user_id"))
        jid = to_uuid(a.get("job_id"))
        if aid is None or uid is None:
            continue
        cur.execute(
            """
            INSERT INTO pending_upload_approvals
                (approval_id, job_id, upload_filename, upload_path, user_id, user_name, status, detected_changes, submitted_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (approval_id) DO NOTHING
            """,
            (
                aid,
                jid,
                a.get("upload_filename", ""),
                a.get("upload_path", ""),
                uid,
                a.get("user_name", ""),
                a.get("status", "pending"),
                jsond(a.get("detected_changes", [])),
                parse_ts(a.get("submitted_at")),
            ),
        )
        inserted += cur.rowcount
    log(f"pending_upload_approvals: {inserted}/{len(rows)} inserted")


def migrate_base_file_config(cur):
    cfg = load("base_file_config.json") or {}
    if not cfg:
        log("base_file_config: empty, skipping")
        return
    cur.execute(
        """
        INSERT INTO base_file_config (filename, sheet_name, is_active, updated_at)
        VALUES (%s, %s, %s, %s)
        """,
        (
            cfg.get("filename", ""),
            cfg.get("sheet_name", ""),
            cfg.get("is_active", True),
            datetime.now(tz=timezone.utc),
        ),
    )
    log(f"base_file_config: 1 row inserted")


def migrate_base_file_versions(cur):
    rows = load("base_file_versions.json") or []
    inserted = 0
    for v in rows:
        vid = v.get("version_id")
        if not vid:
            continue
        cur.execute(
            """
            INSERT INTO base_file_versions
                (version_id, stage, base_filename, snapshot_rel_path, snapshot_abs_path, snapshot_size_bytes, merge_summary, context, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
            ON CONFLICT (version_id) DO NOTHING
            """,
            (
                vid,
                v.get("stage", ""),
                v.get("base_filename", ""),
                v.get("snapshot_rel_path", ""),
                v.get("snapshot_abs_path", ""),
                v.get("snapshot_size_bytes"),
                jsond(v.get("merge_summary", {})),
                jsond(v.get("context", {})),
                parse_ts(v.get("created_at")),
            ),
        )
        inserted += cur.rowcount
    log(f"base_file_versions: {inserted}/{len(rows)} inserted")


def migrate_update_chain(cur):
    rows = load("update_chain.json") or []
    inserted = 0
    for u in rows:
        jid = to_uuid(u.get("job_id"))
        cur.execute(
            """
            INSERT INTO update_chain (filename, source_upload, job_id, approved_by, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                u.get("filename", ""),
                u.get("source_upload", ""),
                jid,
                u.get("approved_by", "auto"),
                u.get("status", "approved"),
                parse_ts(u.get("created_at")),
            ),
        )
        inserted += cur.rowcount
    log(f"update_chain: {inserted}/{len(rows)} inserted")


def migrate_ai_response_cache(cur):
    rows = load("ai_response_cache.json") or []
    inserted = 0
    for r in rows:
        key = r.get("cache_key")
        if not key:
            continue
        uid = to_uuid(r.get("user_id"))
        cur.execute(
            """
            INSERT INTO ai_response_cache (cache_key, user_id, question, response, context_hash, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (cache_key) DO NOTHING
            """,
            (
                key,
                uid,
                r.get("question", ""),
                r.get("response", ""),
                r.get("context_hash", ""),
                parse_ts(r.get("created_at")),
            ),
        )
        inserted += cur.rowcount
    log(f"ai_response_cache: {inserted}/{len(rows)} inserted")


def migrate_intelligence_insight_cache(cur):
    rows = load("intelligence_insight_cache.json") or []
    inserted = 0
    for r in rows:
        key = r.get("cache_key")
        if not key:
            continue
        cur.execute(
            """
            INSERT INTO intelligence_insight_cache (cache_key, data_hash, section, title, insight, created_at)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (cache_key) DO NOTHING
            """,
            (
                key,
                r.get("data_hash", ""),
                r.get("section", ""),
                r.get("title", ""),
                jsond(r.get("insight", {})),
                parse_ts(r.get("created_at")),
            ),
        )
        inserted += cur.rowcount
    log(f"intelligence_insight_cache: {inserted}/{len(rows)} inserted")


def migrate_whatif_predecessor_successor_cache(cur):
    data = load("whatif_predecessor_successor_cache.json") or {}
    if not data:
        log("whatif_predecessor_successor_cache: empty, skipping")
        return
    inserted = 0
    for cache_key, entry in data.items():
        cur.execute(
            """
            INSERT INTO whatif_predecessor_successor_cache (cache_key, generated_at, source, dependencies)
            VALUES (%s, %s, %s::jsonb, %s::jsonb)
            ON CONFLICT (cache_key) DO NOTHING
            """,
            (
                cache_key,
                parse_ts(entry.get("generated_at")),
                jsond(entry.get("source", {})),
                jsond(entry.get("dependencies", [])),
            ),
        )
        inserted += cur.rowcount
    log(f"whatif_predecessor_successor_cache: {inserted} inserted")


def migrate_whatif_project_update_summary(cur):
    data = load("whatif_project_update_summary.json") or {}
    if not data:
        log("whatif_project_update_summary: empty, skipping")
        return
    inserted = 0
    for cache_key, entry in data.items():
        cur.execute(
            """
            INSERT INTO whatif_project_update_summary (cache_key, generated_at, source, updates)
            VALUES (%s, %s, %s::jsonb, %s::jsonb)
            ON CONFLICT (cache_key) DO NOTHING
            """,
            (
                cache_key,
                parse_ts(entry.get("generated_at")),
                jsond(entry.get("source", {})),
                jsond(entry.get("updates", [])),
            ),
        )
        inserted += cur.rowcount
    log(f"whatif_project_update_summary: {inserted} inserted")


def migrate_whatif_claude_responses(cur):
    rows = load("whatif_claude_responses.json") or []
    inserted = 0
    for r in rows:
        uid = to_uuid(r.get("user_id"))
        cur.execute(
            """
            INSERT INTO whatif_claude_responses (session_id, user_id, question, response, context, created_at)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                r.get("session_id"),
                uid,
                r.get("question"),
                r.get("response"),
                jsond(r.get("context", {})),
                parse_ts(r.get("created_at")),
            ),
        )
        inserted += cur.rowcount
    log(f"whatif_claude_responses: {inserted}/{len(rows)} inserted")


def migrate_password_reset_tokens(cur):
    rows = load("password_reset_tokens.json") or []
    inserted = 0
    for t in rows:
        token = t.get("token")
        uid = to_uuid(t.get("user_id"))
        if not token or uid is None:
            continue
        cur.execute("SELECT 1 FROM users WHERE id = %s", (uid,))
        if cur.fetchone() is None:
            continue
        cur.execute(
            """
            INSERT INTO password_reset_tokens (token, user_id, created_at, expires_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (token) DO NOTHING
            """,
            (
                token,
                uid,
                parse_ts(t.get("created_at")),
                parse_ts(t.get("expires_at")) or datetime.now(tz=timezone.utc),
            ),
        )
        inserted += cur.rowcount
    log(f"password_reset_tokens: {inserted}/{len(rows)} inserted")


def migrate_engage_groups(cur):
    rows = load("engage_groups.json") or []
    inserted = 0
    for g in rows:
        gid = to_uuid(g.get("id"))
        if gid is None:
            gid = uuid.uuid4()
        cur.execute(
            """
            INSERT INTO engage_groups (id, name, members, created_at)
            VALUES (%s, %s, %s::jsonb, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (
                gid,
                g.get("name", ""),
                jsond(g.get("members", [])),
                parse_ts(g.get("created_at")),
            ),
        )
        inserted += cur.rowcount
    log(f"engage_groups: {inserted}/{len(rows)} inserted")


def migrate_pptx_slide_sections(cur):
    data = load("pptx_slide_sections.json") or {}
    if not data:
        log("pptx_slide_sections: empty, skipping")
        return
    inserted = 0
    for section_key, section_data in data.items():
        cur.execute(
            """
            INSERT INTO pptx_slide_sections (section_key, data, updated_at)
            VALUES (%s, %s::jsonb, %s)
            ON CONFLICT (section_key) DO NOTHING
            """,
            (
                section_key,
                jsond(section_data),
                datetime.now(tz=timezone.utc),
            ),
        )
        inserted += cur.rowcount
    log(f"pptx_slide_sections: {inserted} inserted")


def migrate_activity_log(cur):
    """
    Migrate activity_log from database/activity_log.db (SQLite) into PostgreSQL.
    Skips if the file doesn't exist or was already migrated.
    """
    sqlite_path = DB_DIR / "activity_log.db"
    migrated_path = DB_DIR / "activity_log.db.migrated"

    if migrated_path.exists() and not sqlite_path.exists():
        log("activity_log: already migrated (activity_log.db.migrated exists), skipping")
        return
    if not sqlite_path.exists():
        log("activity_log: no SQLite file found, skipping")
        return

    try:
        sc = sqlite3.connect(str(sqlite_path))
        sc.row_factory = sqlite3.Row
        rows = sc.execute("SELECT * FROM activity_log").fetchall()
        sc.close()
    except Exception as e:
        log(f"activity_log: SQLite open error — {e}")
        return

    if not rows:
        log("activity_log: SQLite table is empty, skipping")
        return

    inserted = 0
    for r in rows:
        d = dict(r)
        meta = d.get("metadata")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (ValueError, TypeError):
                meta = None

        cur.execute(
            """
            INSERT INTO activity_log
                (timestamp, user_id, user_name, user_role, company_id,
                 action_type, entity_type, entity_id, description,
                 source, level, metadata, ip_address, session_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                parse_ts(d.get("timestamp")) or datetime.now(tz=timezone.utc),
                d.get("user_id"),
                d.get("user_name"),
                d.get("user_role"),
                d.get("company_id"),
                d.get("action_type", ""),
                d.get("entity_type"),
                d.get("entity_id"),
                d.get("description"),
                d.get("source", "web"),
                d.get("level", "user"),
                jsond(meta) if meta is not None else None,
                d.get("ip_address"),
                d.get("session_id"),
            ),
        )
        inserted += cur.rowcount

    log(f"activity_log: {inserted}/{len(rows)} migrated from SQLite")


def migrate_deviations(cur):
    """
    Migrate deviations from database/deviations.db (SQLite) into PostgreSQL.
    If the SQLite file doesn't exist (already migrated or never used), skips silently.
    """
    sqlite_path = DB_DIR / "deviations.db"
    migrated_path = DB_DIR / "deviations.db.migrated"

    if migrated_path.exists() and not sqlite_path.exists():
        log("deviations: already migrated (deviations.db.migrated exists), skipping")
        return

    if not sqlite_path.exists():
        log("deviations: no SQLite file found, skipping")
        return

    try:
        sc = sqlite3.connect(str(sqlite_path))
        sc.row_factory = sqlite3.Row
        rows = sc.execute("SELECT * FROM deviations").fetchall()
        sc.close()
    except Exception as e:
        log(f"deviations: SQLite open error — {e}")
        return

    if not rows:
        log("deviations: SQLite table is empty, skipping")
        return

    inserted = 0
    for r in rows:
        d = dict(r)
        row_data = d.get("row_data")
        if isinstance(row_data, str):
            try:
                row_data = json.loads(row_data)
            except (ValueError, TypeError):
                row_data = {}
        elif row_data is None:
            row_data = {}

        cur.execute(
            """
            INSERT INTO deviations
                (sheet, flag, severity, description, row_data,
                 detected_at, review_status, review_reason, reason_type,
                 user_id, company_id, reviewed_at, reviewed_by_user_id,
                 admin_comment, last_reminder_notified_at, filename, job_id)
            VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                d.get("sheet", ""),
                d.get("flag", ""),
                d.get("severity", ""),
                d.get("description", ""),
                jsond(row_data),
                d.get("detected_at", ""),
                d.get("review_status", "Pending"),
                d.get("review_reason", ""),
                d.get("reason_type", ""),
                d.get("user_id"),
                d.get("company_id"),
                d.get("reviewed_at"),
                d.get("reviewed_by_user_id"),
                d.get("admin_comment", ""),
                d.get("last_reminder_notified_at"),
                d.get("filename", ""),
                d.get("job_id", ""),
            ),
        )
        inserted += cur.rowcount

    log(f"deviations: {inserted}/{len(rows)} migrated from SQLite")


# ── Entry point ───────────────────────────────────────────────────────────────

def run():
    print(f"\nConnecting to PostgreSQL at {CONFIG['host']}:{CONFIG['port']}/{CONFIG['dbname']} ...")
    try:
        conn = psycopg2.connect(**CONFIG)
    except psycopg2.OperationalError as e:
        print(f"\nERROR: Could not connect — {e}")
        print("\nCheck your connection settings and that the database exists:")
        print(f"  createdb -U {CONFIG['user']} {CONFIG['dbname']}")
        sys.exit(1)

    conn.autocommit = False
    cur = conn.cursor()

    # Apply schema
    schema_path = DB_DIR / "schema.sql"
    print(f"\nApplying schema from {schema_path} ...")
    with open(schema_path, encoding="utf-8") as f:
        cur.execute(f.read())

    print("\nMigrating data ...\n")

    steps = [
        migrate_users,
        migrate_subscriptions,
        migrate_notifications,
        migrate_history,
        migrate_pending_upload_approvals,
        migrate_base_file_config,
        migrate_base_file_versions,
        migrate_update_chain,
        migrate_ai_response_cache,
        migrate_intelligence_insight_cache,
        migrate_whatif_predecessor_successor_cache,
        migrate_whatif_project_update_summary,
        migrate_whatif_claude_responses,
        migrate_password_reset_tokens,
        migrate_engage_groups,
        migrate_pptx_slide_sections,
        migrate_activity_log,   # reads from activity_log.db (SQLite)
        migrate_deviations,     # reads from deviations.db (SQLite)
    ]

    failed = False
    for step in steps:
        try:
            step(cur)
        except Exception as e:
            print(f"\n  ERROR in {step.__name__}: {e}")
            conn.rollback()
            failed = True
            break

    if not failed:
        conn.commit()
        print("\nDone. All data committed successfully.")
    else:
        print("\nMigration rolled back due to error above.")

    cur.close()
    conn.close()


if __name__ == "__main__":
    run()
