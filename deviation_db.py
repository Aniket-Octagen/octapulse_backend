"""
deviation_db.py
PostgreSQL backend for deviations (migrated from database/deviations.db SQLite).
All public function signatures are unchanged so app.py imports continue to work.
"""

import json
import os
from contextlib import contextmanager
from pathlib import Path

import psycopg2
import psycopg2.extras

from db_postgres import get_pool, _j

_BASE_DIR   = Path(__file__).parent
_SQLITE_DB  = _BASE_DIR / 'database' / 'deviations.db'


# ── Connection ─────────────────────────────────────────────────────────────────

@contextmanager
def _conn():
    pool = get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


# ── Schema + startup ───────────────────────────────────────────────────────────

def init_deviations_db():
    """Create the deviations table if it does not exist, then migrate from SQLite."""
    # Step 1 — create table + indexes (one transaction)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
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
                    created_at                TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_dev_company ON deviations(company_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_dev_user    ON deviations(user_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_dev_status  ON deviations(review_status)")

    # Step 2 — add missing columns, each in its own transaction so one failure
    # doesn't roll back the others.
    _missing_cols = [
        ('expires_at',     'TIMESTAMPTZ'),
        ('auto_locked_at', 'TIMESTAMPTZ'),
        ('created_at',     'TIMESTAMPTZ DEFAULT NOW()'),
    ]
    for col, definition in _missing_cols:
        try:
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name='deviations' AND column_name=%s",
                        (col,),
                    )
                    if not cur.fetchone():
                        cur.execute(f"ALTER TABLE deviations ADD COLUMN {col} {definition}")
                        print(f"[deviation_db] Added column: {col}")
        except Exception as e:
            print(f"[deviation_db] Could not add column {col} (non-fatal): {e}")

    _migrate_from_sqlite()
    _reset_id_sequence()
    count = _get_row_count()
    print(f"[deviation_db] PostgreSQL ready — {count} deviation(s)")


def _get_row_count():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM deviations")
            return cur.fetchone()[0]


def _reset_id_sequence():
    """Reset the deviations_id_seq to max(id)+1 so new IDs start just above the highest existing row."""
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT setval('deviations_id_seq', COALESCE(MAX(id), 0) + 1, false) FROM deviations"
                )
                new_val = cur.fetchone()[0]
        print(f"[deviation_db] Sequence reset — next id will be {new_val}")
    except Exception as e:
        print(f"[deviation_db] Sequence reset skipped (non-fatal): {e}")


def _migrate_from_sqlite():
    """
    One-time migration: copy all rows from deviations.db (SQLite) into PostgreSQL,
    then rename the file so it won't be imported again.
    """
    if not _SQLITE_DB.exists():
        return
    if _get_row_count() > 0:
        return  # already populated — skip

    try:
        import sqlite3
        sc = sqlite3.connect(str(_SQLITE_DB))
        sc.row_factory = sqlite3.Row
        rows = sc.execute("SELECT * FROM deviations").fetchall()
        sc.close()
    except Exception as e:
        print(f"[deviation_db] SQLite open failed (non-fatal): {e}")
        return

    if not rows:
        _SQLITE_DB.rename(str(_SQLITE_DB) + '.migrated')
        return

    print(f"[deviation_db] Migrating {len(rows)} deviation(s) from SQLite …")
    migrated = 0
    for r in rows:
        try:
            d = dict(r)
            # row_data was stored as a JSON string in SQLite — parse it
            rd = d.get('row_data')
            if isinstance(rd, str):
                try:
                    d['row_data'] = json.loads(rd)
                except (ValueError, TypeError):
                    d['row_data'] = {}
            insert_deviation(d)
            migrated += 1
        except Exception as e:
            print(f"[deviation_db] Skip row id={r['id']}: {e}")

    _SQLITE_DB.rename(str(_SQLITE_DB) + '.migrated')
    print(f"[deviation_db] Migrated {migrated}/{len(rows)}. SQLite renamed to deviations.db.migrated")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _row_to_dict(row, description):
    cols = [d[0] for d in description]
    d = dict(zip(cols, row))
    if d.get('row_data') is None:
        d['row_data'] = {}
    return d


def _parse_row_data(val):
    """Accept dict, list, JSON string, or None — always return a Python object."""
    if val is None:
        return {}
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (ValueError, TypeError):
        return {}


def _insert_params(dev: dict):
    import os
    expiry_days = os.getenv('DEVIATION_EXPIRY_DAYS', '20')  # keep as str for || text concat in SQL
    detected = dev.get('detected_at') or ''
    return (
        dev.get('sheet', ''),
        dev.get('flag', ''),
        dev.get('severity', ''),
        dev.get('description', ''),
        _j(_parse_row_data(dev.get('row_data', {}))),
        detected,
        dev.get('review_status', 'Pending'),
        dev.get('review_reason', ''),
        dev.get('reason_type', ''),
        dev.get('user_id'),
        dev.get('company_id'),
        dev.get('reviewed_at'),
        dev.get('reviewed_by_user_id'),
        dev.get('admin_comment', ''),
        dev.get('last_reminder_notified_at'),
        dev.get('filename', ''),
        dev.get('job_id', ''),
        expiry_days,
    )


# ── Read operations ────────────────────────────────────────────────────────────

def get_all_deviations(company_id=None, user_id=None):
    clauses, params = [], []
    if company_id is not None:
        clauses.append("company_id = %s")
        params.append(str(company_id))
    if user_id is not None:
        clauses.append("user_id = %s")
        params.append(str(user_id))
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT * FROM deviations {where} ORDER BY detected_at DESC"
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [_row_to_dict(r, cur.description) for r in cur.fetchall()]


def get_reviewed_deviations(company_id=None, user_id=None, limit=None):
    clauses = [
        "review_status IS NOT NULL",
        "TRIM(review_status) != ''",
        "LOWER(TRIM(review_status)) != 'pending'",
    ]
    params = []
    if company_id is not None:
        clauses.append("company_id = %s")
        params.append(str(company_id))
    if user_id is not None:
        clauses.append("user_id = %s")
        params.append(str(user_id))
    where = "WHERE " + " AND ".join(clauses)
    sql = f"SELECT * FROM deviations {where} ORDER BY detected_at DESC"
    if limit and limit > 0:
        sql += f" LIMIT {int(limit)}"
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [_row_to_dict(r, cur.description) for r in cur.fetchall()]


def get_pending_deviations(company_id=None):
    clauses = ["LOWER(TRIM(review_status)) = 'pending'"]
    params = []
    if company_id is not None:
        clauses.append("company_id = %s")
        params.append(str(company_id))
    sql = "SELECT * FROM deviations WHERE " + " AND ".join(clauses)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [_row_to_dict(r, cur.description) for r in cur.fetchall()]


def get_deviation_by_id(deviation_id):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM deviations WHERE id = %s", (int(deviation_id),))
            row = cur.fetchone()
            return _row_to_dict(row, cur.description) if row else None


def deviation_exists(sheet, description, company_id):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM deviations WHERE sheet = %s AND description = %s AND company_id = %s",
                (sheet, description, str(company_id)),
            )
            return cur.fetchone() is not None


# ── Write operations ───────────────────────────────────────────────────────────

_SQL_INSERT_FULL = """
    INSERT INTO deviations
        (sheet, flag, severity, description, row_data,
         detected_at, review_status, review_reason, reason_type,
         user_id, company_id, reviewed_at, reviewed_by_user_id,
         admin_comment, last_reminder_notified_at, filename, job_id,
         expires_at)
    VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            NOW() + (%s || ' days')::INTERVAL)
    RETURNING id
"""

_SQL_INSERT_NO_EXPIRY = """
    INSERT INTO deviations
        (sheet, flag, severity, description, row_data,
         detected_at, review_status, review_reason, reason_type,
         user_id, company_id, reviewed_at, reviewed_by_user_id,
         admin_comment, last_reminder_notified_at, filename, job_id)
    VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    RETURNING id
"""


def insert_deviation(dev: dict) -> int:
    """Insert a single deviation row. Returns the auto-assigned integer id.
    Falls back to an INSERT without expires_at if the column doesn't exist yet.
    """
    params = _insert_params(dev)          # 18 values — last one is expiry_days string
    params_no_expiry = params[:-1]        # 17 values — without expiry_days

    # Try the full insert first
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_SQL_INSERT_FULL, params)
                return cur.fetchone()[0]
    except Exception as e:
        err = str(e).lower()
        # If the failure is unrelated to the expires_at column, re-raise
        if 'expires_at' not in err and 'column' not in err:
            raise
        # expires_at column not yet in table — insert without it and log
        print(f"[deviation_db] expires_at missing, inserting without it: {e}")

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_INSERT_NO_EXPIRY, params_no_expiry)
            return cur.fetchone()[0]


def get_deviations_due_reminder(reminder_days: int = 15) -> list:
    """
    Return pending deviations where:
    - detected_at >= reminder_days ago (so they're 'old enough' for a reminder)
    - last_reminder_notified_at is NULL or was > 7 days ago (no recent reminder)
    - Not yet expired
    """
    sql = """
        SELECT * FROM deviations
        WHERE LOWER(TRIM(review_status)) = 'pending'
          AND detected_at IS NOT NULL AND detected_at != ''
          AND (detected_at::TIMESTAMPTZ + (%s || ' days')::INTERVAL) <= NOW()
          AND (expires_at IS NULL OR expires_at > NOW())
          AND (
              last_reminder_notified_at IS NULL
              OR last_reminder_notified_at = ''
              OR (last_reminder_notified_at::TIMESTAMPTZ + INTERVAL '7 days') <= NOW()
          )
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(reminder_days),))
            return [_row_to_dict(r, cur.description) for r in cur.fetchall()]


def get_deviations_to_expire() -> list:
    """Return pending deviations whose expires_at has passed."""
    sql = """
        SELECT * FROM deviations
        WHERE LOWER(TRIM(review_status)) = 'pending'
          AND expires_at IS NOT NULL
          AND expires_at <= NOW()
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return [_row_to_dict(r, cur.description) for r in cur.fetchall()]


def expire_deviation(deviation_id: int):
    """Mark a deviation as Expired and record when it was auto-locked."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE deviations
                SET review_status = 'Expired',
                    auto_locked_at = NOW()
                WHERE id = %s AND LOWER(TRIM(review_status)) = 'pending'
                """,
                (int(deviation_id),),
            )


def get_deviation_report_by_month(company_id=None) -> list:
    """
    Return month-wise deviation counts scoped to a company (or all for super_admin).
    """
    where  = "WHERE company_id = %s" if company_id else ""
    params = [str(company_id)] if company_id else []
    sql = f"""
        SELECT
            TO_CHAR(
                CASE WHEN detected_at IS NOT NULL AND detected_at != ''
                     THEN detected_at::TIMESTAMPTZ
                     ELSE created_at
                END,
                'YYYY-MM'
            ) AS month,
            COUNT(*)                                                    AS total,
            SUM(CASE WHEN LOWER(TRIM(review_status)) = 'pending'   THEN 1 ELSE 0 END) AS unanswered,
            SUM(CASE WHEN LOWER(TRIM(review_status)) IN
                ('reviewed','approved','rejected','not approved')   THEN 1 ELSE 0 END) AS answered,
            SUM(CASE WHEN LOWER(TRIM(review_status)) = 'expired'   THEN 1 ELSE 0 END) AS expired,
            SUM(CASE WHEN severity = 'High'                        THEN 1 ELSE 0 END) AS high_severity
        FROM deviations
        {where}
        GROUP BY month
        ORDER BY month DESC
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


def update_deviation_fields(deviation_id, fields: dict):
    """Update arbitrary columns for a deviation by id."""
    if not fields:
        return
    set_parts, params = [], []
    for col, val in fields.items():
        if col == 'row_data':
            set_parts.append(f"{col} = %s::jsonb")
            params.append(_j(_parse_row_data(val)))
        else:
            set_parts.append(f"{col} = %s")
            params.append(val)
    params.append(int(deviation_id))
    sql = f"UPDATE deviations SET {', '.join(set_parts)} WHERE id = %s"
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)


def update_reminder_timestamp(deviation_id, timestamp: str):
    update_deviation_fields(deviation_id, {'last_reminder_notified_at': timestamp})


def upsert_cp_deviation(dev: dict) -> int:
    """
    For critical-path deviations: UPDATE the existing pending row if one already
    exists for the same activity_id + company_id, otherwise INSERT a new row.
    Preserves the row id so the SERIAL sequence is not burned on every re-upload.
    Returns the id of the upserted row.
    """
    act_id     = str((dev.get('row_data') or {}).get('activity_id') or '').strip()
    company_id = dev.get('company_id')

    if act_id:
        co_clause, co_params = _company_clause(company_id)
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id FROM deviations
                    WHERE LOWER(TRIM(review_status)) = 'pending'
                      AND {co_clause}
                      AND row_data->>'activity_id' = %s
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    co_params + [act_id],
                )
                row = cur.fetchone()

        if row:
            existing_id = row[0]
            update_deviation_fields(existing_id, {
                'sheet':       dev.get('sheet', ''),
                'flag':        dev.get('flag', ''),
                'severity':    dev.get('severity', ''),
                'description': dev.get('description', ''),
                'row_data':    dev.get('row_data', {}),
                'detected_at': dev.get('detected_at', ''),
                'filename':    dev.get('filename', ''),
                'job_id':      dev.get('job_id', ''),
            })
            return existing_id

    return insert_deviation(dev)


def delete_deviation(deviation_id) -> bool:
    """Delete a single deviation by id. Returns True if a row was deleted."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM deviations WHERE id = %s", (int(deviation_id),))
            return cur.rowcount > 0


def _company_clause(company_id):
    """Return (sql_fragment, params) for company_id matching, handling NULL correctly."""
    if company_id is None:
        return "company_id IS NULL", []
    return "company_id = %s", [str(company_id)]


def delete_deviations_by_filename(filename: str, company_id) -> tuple[int, int]:
    """
    On re-upload of a monthly file:
      - Delete only PENDING (unanswered) deviations for this file.
      - Preserve all answered deviations (Reviewed / Approved / Not Approved / Expired).
    Returns (deleted_count, kept_count).
    """
    _answered = ('reviewed', 'approved', 'not approved', 'expired')
    co_clause, co_params = _company_clause(company_id)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT COUNT(*) FROM deviations
                WHERE filename = %s AND {co_clause}
                  AND LOWER(TRIM(review_status)) = ANY(%s)
                """,
                [filename] + co_params + [list(_answered)],
            )
            kept = cur.fetchone()[0]

            cur.execute(
                f"""
                DELETE FROM deviations
                WHERE filename = %s AND {co_clause}
                  AND LOWER(TRIM(review_status)) = 'pending'
                """,
                [filename] + co_params,
            )
            deleted = cur.rowcount

    return deleted, kept


def delete_pending_cp_deviations_by_activity_ids(activity_ids: list, activity_names: list, company_id) -> int:
    """
    Before inserting fresh CP deviations from a new upload, delete any existing
    PENDING deviations for the same CP activity IDs or names (cross-filename overwrite).
    Reviewed/approved deviations are preserved.
    Returns the number of rows deleted.
    """
    ids   = [str(x).strip() for x in activity_ids  if x and str(x).strip()]
    names = [str(x).strip() for x in activity_names if x and str(x).strip()]
    if not ids and not names:
        return 0
    co_clause, co_params = _company_clause(company_id)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                DELETE FROM deviations
                WHERE LOWER(TRIM(review_status)) = 'pending'
                  AND {co_clause}
                  AND (
                      row_data->>'activity_id'   = ANY(%s::text[])
                      OR LOWER(row_data->>'activity_name') = ANY(%s::text[])
                  )
                """,
                co_params + [ids or [''], [n.lower() for n in names] or ['']],
            )
            return cur.rowcount


def is_deviation_locked(sheet: str, description: str, company_id, filename: str) -> bool:
    """
    True if an already-answered deviation exists for the same
    sheet + description + company + filename.
    Prevents re-inserting a deviation the manager already reviewed on this file.
    """
    _answered = ('reviewed', 'approved', 'not approved', 'expired')
    co_clause, co_params = _company_clause(company_id)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT 1 FROM deviations
                WHERE sheet = %s AND description = %s
                  AND {co_clause} AND filename = %s
                  AND LOWER(TRIM(review_status)) = ANY(%s)
                LIMIT 1
                """,
                [sheet, description] + co_params + [filename, list(_answered)],
            )
            return cur.fetchone() is not None


def delete_all_deviations(company_id=None) -> int:
    """Delete all deviations, optionally scoped to a company_id."""
    with _conn() as conn:
        with conn.cursor() as cur:
            if company_id is not None:
                cur.execute("DELETE FROM deviations WHERE company_id = %s", (str(company_id),))
                return cur.rowcount
            else:
                # TRUNCATE + reset the SERIAL sequence
                cur.execute("TRUNCATE TABLE deviations RESTART IDENTITY")
                return 0
