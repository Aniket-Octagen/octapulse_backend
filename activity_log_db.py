"""
activity_log_db.py
PostgreSQL backend for cross-platform activity tracking (migrated from SQLite).
All public function signatures are unchanged so app.py imports continue to work.

Access levels:
  user    — own actions only
  manager — manager + user-level actions (own company)
  admin   — everything (all companies, all roles, AI access)
"""

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from db_postgres import get_pool, _j

_BASE_DIR   = Path(__file__).parent
_SQLITE_DB  = _BASE_DIR / 'database' / 'activity_log.db'

# ── Action type constants ──────────────────────────────────────────────────────

ACTION_LOGIN              = 'login'
ACTION_LOGOUT             = 'logout'
ACTION_FILE_UPLOAD        = 'file_upload'
ACTION_FILE_PROCESSED     = 'file_processed'
ACTION_DEVIATION_VIEW     = 'deviation_view'
ACTION_DEVIATION_APPROVE  = 'deviation_approve'
ACTION_DEVIATION_REJECT   = 'deviation_reject'
ACTION_DEVIATION_COMMENT  = 'deviation_comment'
ACTION_NOTIFICATION_VIEW  = 'notification_view'
ACTION_NOTIFICATION_READ  = 'notification_read'
ACTION_SETTINGS_UPDATE    = 'settings_update'
ACTION_PASSWORD_CHANGE    = 'password_change'
ACTION_USER_CREATED       = 'user_created'
ACTION_USER_UPDATED       = 'user_updated'
ACTION_REPORT_BUG         = 'report_bug'
ACTION_AI_CHAT            = 'ai_chat'
ACTION_HISTORY_VIEW       = 'history_view'
ACTION_HISTORY_DELETE     = 'history_delete'
ACTION_KB_VIEW            = 'knowledge_base_view'

SOURCE_WEB    = 'web'
SOURCE_MOBILE = 'mobile'

LEVEL_USER    = 'user'
LEVEL_MANAGER = 'manager'
LEVEL_ADMIN   = 'admin'
LEVEL_SYSTEM  = 'system'


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

def init_activity_log_db():
    """Create the activity_log table if it doesn't exist, then migrate from SQLite."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
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
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_al_user    ON activity_log(user_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_al_company ON activity_log(company_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_al_action  ON activity_log(action_type)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_al_ts      ON activity_log(timestamp DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_al_level   ON activity_log(level)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_al_source  ON activity_log(source)")

    _migrate_from_sqlite()
    count = _row_count()
    print(f"[activity_log_db] PostgreSQL ready — {count} record(s)")


def _row_count():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM activity_log")
            return cur.fetchone()[0]


def _migrate_from_sqlite():
    """One-time migration: copy activity_log.db (SQLite) → PostgreSQL."""
    if not _SQLITE_DB.exists():
        return
    if _row_count() > 0:
        return

    try:
        import sqlite3
        sc = sqlite3.connect(str(_SQLITE_DB))
        sc.row_factory = sqlite3.Row
        rows = sc.execute("SELECT * FROM activity_log").fetchall()
        sc.close()
    except Exception as e:
        print(f"[activity_log_db] SQLite open failed (non-fatal): {e}")
        return

    if not rows:
        _SQLITE_DB.rename(str(_SQLITE_DB) + '.migrated')
        return

    print(f"[activity_log_db] Migrating {len(rows)} record(s) from SQLite …")
    migrated = 0
    for r in rows:
        try:
            d = dict(r)
            meta = d.get('metadata')
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (ValueError, TypeError):
                    meta = None
            _insert_log(
                action_type=d.get('action_type', ''),
                user_id=d.get('user_id'),
                user_name=d.get('user_name'),
                user_role=d.get('user_role'),
                company_id=d.get('company_id'),
                entity_type=d.get('entity_type'),
                entity_id=d.get('entity_id'),
                description=d.get('description'),
                source=d.get('source', SOURCE_WEB),
                level=d.get('level', LEVEL_USER),
                metadata=meta,
                ip_address=d.get('ip_address'),
                session_id=d.get('session_id'),
                timestamp=d.get('timestamp'),
            )
            migrated += 1
        except Exception as e:
            print(f"[activity_log_db] Skip row id={r['id']}: {e}")

    _SQLITE_DB.rename(str(_SQLITE_DB) + '.migrated')
    print(f"[activity_log_db] Migrated {migrated}/{len(rows)}. SQLite renamed to activity_log.db.migrated")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _row_to_dict(row, description):
    cols = [d[0] for d in description]
    d = {}
    for col, val in zip(cols, row):
        if isinstance(val, datetime):
            if val.tzinfo is None:
                val = val.replace(tzinfo=timezone.utc)
            val = val.isoformat()
        d[col] = val
    return d


def _rows(cur):
    if not cur.description:
        return []
    return [_row_to_dict(r, cur.description) for r in cur.fetchall()]


def _insert_log(action_type, user_id=None, user_name=None, user_role=None,
                company_id=None, entity_type=None, entity_id=None,
                description=None, source=SOURCE_WEB, level=LEVEL_USER,
                metadata=None, ip_address=None, session_id=None,
                timestamp=None):
    """Core insert — accepts an optional explicit timestamp for migrations."""
    with _conn() as conn:
        with conn.cursor() as cur:
            if timestamp:
                cur.execute(
                    """
                    INSERT INTO activity_log
                        (timestamp, user_id, user_name, user_role, company_id,
                         action_type, entity_type, entity_id, description,
                         source, level, metadata, ip_address, session_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                    """,
                    (
                        timestamp,
                        str(user_id) if user_id is not None else None,
                        user_name, user_role,
                        str(company_id) if company_id is not None else None,
                        action_type, entity_type,
                        str(entity_id) if entity_id is not None else None,
                        description, source, level,
                        _j(metadata) if metadata is not None else None,
                        ip_address, session_id,
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO activity_log
                        (user_id, user_name, user_role, company_id,
                         action_type, entity_type, entity_id, description,
                         source, level, metadata, ip_address, session_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                    """,
                    (
                        str(user_id) if user_id is not None else None,
                        user_name, user_role,
                        str(company_id) if company_id is not None else None,
                        action_type, entity_type,
                        str(entity_id) if entity_id is not None else None,
                        description, source, level,
                        _j(metadata) if metadata is not None else None,
                        ip_address, session_id,
                    ),
                )
            return cur.fetchone()[0]


# ── Write helpers ──────────────────────────────────────────────────────────────

def log_activity(
    action_type,
    user_id=None,
    user_name=None,
    user_role=None,
    company_id=None,
    entity_type=None,
    entity_id=None,
    description=None,
    source=SOURCE_WEB,
    level=LEVEL_USER,
    metadata=None,
    ip_address=None,
    session_id=None,
):
    """Insert one activity record. Returns the new row id."""
    return _insert_log(
        action_type=action_type,
        user_id=user_id, user_name=user_name, user_role=user_role,
        company_id=company_id, entity_type=entity_type, entity_id=entity_id,
        description=description, source=source, level=level,
        metadata=metadata, ip_address=ip_address, session_id=session_id,
    )


# ── Read helpers ───────────────────────────────────────────────────────────────

def get_activities_for_user(user_id, limit=200):
    """Return own activities only (regular user view)."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM activity_log
                WHERE user_id = %s
                ORDER BY timestamp DESC
                LIMIT %s
                """,
                (str(user_id), int(limit)),
            )
            return _rows(cur)


def get_activities_for_manager(company_id, limit=500):
    """Manager view: user + manager + system level actions for the same company."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM activity_log
                WHERE company_id = %s
                  AND level IN ('user', 'manager', 'system')
                ORDER BY timestamp DESC
                LIMIT %s
                """,
                (str(company_id), int(limit)),
            )
            return _rows(cur)


def get_all_activities_admin(limit=1000, filters=None):
    """
    Admin / AI view: every record with optional filters.
    filters keys: user_id, company_id, action_type, source, level,
                  date_from (ISO string), date_to (ISO string)
    """
    filters = filters or {}
    clauses, params = [], []

    if filters.get('user_id'):
        clauses.append("user_id = %s")
        params.append(str(filters['user_id']))
    if filters.get('company_id'):
        clauses.append("company_id = %s")
        params.append(str(filters['company_id']))
    if filters.get('action_type'):
        clauses.append("action_type ILIKE %s")
        params.append(f"%{filters['action_type']}%")
    if filters.get('source'):
        clauses.append("source = %s")
        params.append(filters['source'])
    if filters.get('level'):
        clauses.append("level = %s")
        params.append(filters['level'])
    if filters.get('date_from'):
        clauses.append("timestamp >= %s")
        params.append(filters['date_from'])
    if filters.get('date_to'):
        clauses.append("timestamp <= %s")
        params.append(filters['date_to'])

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(int(limit))

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM activity_log {where} ORDER BY timestamp DESC LIMIT %s",
                params,
            )
            return _rows(cur)


def get_activity_stats(company_id=None):
    """Summary statistics for the knowledge-base dashboard."""
    where  = "WHERE company_id = %s" if company_id else ""
    params = [str(company_id)] if company_id else []

    with _conn() as conn:
        with conn.cursor() as cur:
            # Total count
            cur.execute(f"SELECT COUNT(*) FROM activity_log {where}", params)
            total = cur.fetchone()[0]

            # By source
            cur.execute(
                f"SELECT source, COUNT(*) AS cnt FROM activity_log {where} GROUP BY source",
                params,
            )
            sources = cur.fetchall()

            # Top 10 action types
            cur.execute(
                f"""
                SELECT action_type, COUNT(*) AS cnt FROM activity_log {where}
                GROUP BY action_type ORDER BY cnt DESC LIMIT 10
                """,
                params,
            )
            actions = cur.fetchall()

            # Active users last 24 h
            and_or = "AND" if where else "WHERE"
            cur.execute(
                f"""
                SELECT COUNT(DISTINCT user_id) FROM activity_log
                {where} {and_or} timestamp >= NOW() - INTERVAL '1 day'
                """,
                params,
            )
            active_24h = cur.fetchone()[0]

            # Daily breakdown last 7 days
            cur.execute(
                f"""
                SELECT timestamp::date AS day, COUNT(*) AS cnt
                FROM activity_log
                {where} {and_or} timestamp >= NOW() - INTERVAL '7 days'
                GROUP BY day ORDER BY day
                """,
                params,
            )
            daily = cur.fetchall()

    return {
        'total_activities': total,
        'active_users_24h': active_24h,
        'by_source':   {r[0]: r[1] for r in sources},
        'top_actions': [{'action': r[0], 'count': r[1]} for r in actions],
        'daily_last_7d': [{'date': str(r[0]), 'count': r[1]} for r in daily],
    }


def get_company_last_activities() -> dict:
    """Return last activity timestamp and event counts per company_id (for super admin overview)."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT company_id,
                       MAX(timestamp)            AS last_activity,
                       COUNT(*)                  AS total_events,
                       COUNT(DISTINCT user_id)   AS unique_users
                FROM activity_log
                WHERE company_id IS NOT NULL
                GROUP BY company_id
                """
            )
            rows = cur.fetchall()
            result = {}
            for cid, last_activity, total_events, unique_users in rows:
                result[str(cid)] = {
                    'last_activity': last_activity.isoformat() if last_activity else None,
                    'total_events':  int(total_events),
                    'unique_users':  int(unique_users),
                }
            return result


def get_knowledge_base_summary(company_id=None, limit=200):
    """Combined payload for the Knowledge Base view."""
    recent = get_all_activities_admin(
        limit=limit,
        filters={'company_id': company_id} if company_id else {},
    )
    stats = get_activity_stats(company_id=company_id)

    where  = "WHERE company_id = %s" if company_id else ""
    params = [str(company_id)] if company_id else []

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT user_id, user_name, user_role,
                       COUNT(*) AS total_actions,
                       MAX(timestamp) AS last_active
                FROM activity_log {where}
                GROUP BY user_id, user_name, user_role
                ORDER BY last_active DESC
                LIMIT 50
                """,
                params,
            )
            users = _rows(cur)

    return {
        'stats': stats,
        'recent_activities': recent,
        'active_users': users,
        'generated_at': datetime.now(tz=timezone.utc).isoformat(),
    }


def get_deviation_activity_history(deviation_id):
    """Full chronological activity trail for a single deviation."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM activity_log
                WHERE entity_type = 'deviation'
                  AND entity_id = %s
                  AND action_type IN (%s, %s, %s)
                ORDER BY timestamp ASC
                """,
                (
                    str(deviation_id),
                    ACTION_DEVIATION_APPROVE,
                    ACTION_DEVIATION_REJECT,
                    ACTION_DEVIATION_COMMENT,
                ),
            )
            return _rows(cur)


def get_recursive_deviations(company_id=None, limit=500):
    """
    Find deviations that went through multiple reject → resubmit rounds
    before being approved (approve_count >= 1 AND reject_count >= 1).
    """
    company_clause = "AND company_id = %s" if company_id else ""

    # Params must match placeholder order in the SQL below:
    # 1-3: CASE WHEN in SELECT,  4-6: IN clause,  7: company_id (optional),  8: LIMIT
    params = [
        ACTION_DEVIATION_APPROVE,   # SELECT approve_count
        ACTION_DEVIATION_REJECT,    # SELECT reject_count
        ACTION_DEVIATION_COMMENT,   # SELECT comment_count
        ACTION_DEVIATION_APPROVE,   # IN clause
        ACTION_DEVIATION_REJECT,    # IN clause
        ACTION_DEVIATION_COMMENT,   # IN clause
    ]
    if company_id:
        params.append(str(company_id))
    params.append(int(limit))

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT entity_id,
                       SUM(CASE WHEN action_type = %s THEN 1 ELSE 0 END) AS approve_count,
                       SUM(CASE WHEN action_type = %s THEN 1 ELSE 0 END) AS reject_count,
                       SUM(CASE WHEN action_type = %s THEN 1 ELSE 0 END) AS comment_count,
                       COUNT(*)       AS total_actions,
                       MAX(timestamp) AS last_action
                FROM activity_log
                WHERE entity_type = 'deviation'
                  AND action_type IN (%s, %s, %s)
                  {company_clause}
                GROUP BY entity_id
                HAVING
                    SUM(CASE WHEN action_type = '{ACTION_DEVIATION_APPROVE}' THEN 1 ELSE 0 END) >= 1
                    AND
                    SUM(CASE WHEN action_type = '{ACTION_DEVIATION_REJECT}'  THEN 1 ELSE 0 END) >= 1
                ORDER BY last_action DESC
                LIMIT %s
                """,
                params,
            )
            return _rows(cur)
