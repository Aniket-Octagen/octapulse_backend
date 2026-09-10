"""
theta_sheet_db.py
PostgreSQL backend for Theta Sheets (live Excel-like spreadsheet editing).
One active sheet per company. Also computes dashboard KPIs from sheet data
and provides an in-memory pub/sub used by the SSE endpoint in app.py.
"""

import json
import re
import threading
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta

import psycopg2
import psycopg2.extras

from db_postgres import get_pool, _coerce, _uid


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

def init_theta_sheets_db():
    """Create the theta_sheets table if it does not exist."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS theta_sheets (
                    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    company_id  UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE UNIQUE,
                    name        TEXT NOT NULL DEFAULT 'Theta Sheets',
                    data        JSONB NOT NULL DEFAULT '{"sheets":[]}',
                    version     INTEGER NOT NULL DEFAULT 1,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_theta_sheets_company ON theta_sheets(company_id)")
    print("[theta_sheet_db] PostgreSQL ready — table available")


_SHEET_COLUMNS = 'id, company_id, name, data, version, created_at, updated_at'


def _sheet_row(cur):
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    return {c: _coerce(v) for c, v in zip(cols, row)}


# ── Reads ──────────────────────────────────────────────────────────────────────

def get_sheet_by_company(company_id):
    cid = _uid(company_id)
    if not cid:
        return None
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(f'SELECT {_SHEET_COLUMNS} FROM theta_sheets WHERE company_id = %s', (cid,))
            return _sheet_row(cur)
    except Exception as e:
        print(f'[theta_sheet_db] get_sheet_by_company error: {e}')
        return None


def get_sheet_by_id(sheet_id):
    sid = _uid(sheet_id)
    if not sid:
        return None
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(f'SELECT {_SHEET_COLUMNS} FROM theta_sheets WHERE id = %s', (sid,))
            return _sheet_row(cur)
    except Exception as e:
        print(f'[theta_sheet_db] get_sheet_by_id error: {e}')
        return None


# ── Writes ─────────────────────────────────────────────────────────────────────

def upsert_sheet(company_id, name, sheet_data, expected_version=None):
    """Create or update the active sheet for a company.

    When `expected_version` is given, the update is optimistically locked:
    if the row's current version doesn't match, raises ValueError('version_conflict').
    """
    cid = _uid(company_id)
    if not cid:
        raise ValueError('invalid company_id')

    payload = json.dumps(sheet_data)
    result = None

    with _conn() as conn, conn.cursor() as cur:
        if expected_version is not None:
            cur.execute(
                f"""UPDATE theta_sheets
                    SET data = %s::jsonb, name = %s, version = version + 1, updated_at = NOW()
                    WHERE company_id = %s AND version = %s
                    RETURNING {_SHEET_COLUMNS}""",
                (payload, name, cid, expected_version),
            )
            result = _sheet_row(cur)
            if result is None:
                cur.execute('SELECT 1 FROM theta_sheets WHERE company_id = %s', (cid,))
                if cur.fetchone():
                    raise ValueError('version_conflict')
                # No row yet for this company — fall through to create it below.

        if result is None:
            cur.execute(
                f"""INSERT INTO theta_sheets (company_id, name, data)
                    VALUES (%s, %s, %s::jsonb)
                    ON CONFLICT (company_id) DO UPDATE
                        SET data = EXCLUDED.data, name = EXCLUDED.name,
                            version = theta_sheets.version + 1, updated_at = NOW()
                    RETURNING {_SHEET_COLUMNS}""",
                (cid, name, payload),
            )
            result = _sheet_row(cur)

    _broadcast(str(cid), result)
    return result


# ── Metrics computation ────────────────────────────────────────────────────────
#
# This mirrors the exact formulas from the reference workbook (Dashboard KPIs /
# Schedule Intelligence / Cost Intelligence sheets, business-logic spec shared
# 2026-07-12): activities carry pre-computed Status/Variance/Root Cause/Impact
# fields as INPUT columns (not derived from raw dates like the old placeholder
# logic was), so this function is mostly aggregation, not date-diff heuristics.

_DATE_FORMATS = ('%Y-%m-%d', '%m/%d/%Y', '%d-%b-%Y')

FULL_SCHEDULE_COLUMNS = [
    'Activity ID', 'Activity Name', 'Phase', 'Cost Category', 'Period',
    'Baseline Start', 'Baseline Finish', 'Forecast Start', 'Forecast Finish',
    'Actual Start', 'Actual Finish', '% Complete', 'Status', 'Variance (Days)',
    'Root Cause', 'Impact', 'Budget Cost (AED)', 'Actual Cost (AED)', 'Forecast Cost (AED)',
    'Planned Hours', 'Actual Hours', 'Planned Output', 'Actual Output', 'Output Unit',
    'Productivity Index',
]

# Required for a row to count as a real activity at all.
REQUIRED_SCHEDULE_COLUMNS = ['Activity ID', 'Activity Name']

# Mirrors THETA_DATE_FIELDS / THETA_NUMERIC_FIELDS in
# frontend/src/utils/thetaValidation.js — kept in sync by hand since this is
# the server-side mirror of the same rule set, not a shared module.
THETA_DATE_FIELDS = [
    'Baseline Start', 'Baseline Finish', 'Forecast Start', 'Forecast Finish',
    'Actual Start', 'Actual Finish',
]
THETA_NUMERIC_FIELDS = [
    '% Complete', 'Variance (Days)', 'Budget Cost (AED)', 'Actual Cost (AED)',
    'Forecast Cost (AED)', 'Planned Hours', 'Actual Hours', 'Planned Output',
    'Actual Output', 'Productivity Index',
]

_IMPACT_BADGES = {
    'None':     {'bg': '#f0fdf4', 'color': '#16a34a'},
    'Low':      {'bg': '#fffbeb', 'color': '#b45309'},
    'Medium':   {'bg': '#f1f5f9', 'color': '#475569'},
    'High':     {'bg': '#fef2f2', 'color': '#dc2626'},
    'Critical': {'bg': '#dc2626', 'color': '#fff'},
}

_PERIODS = ['P1', 'P2', 'P3', 'P4']


def _parse_date(v):
    if v is None or v == '':
        return None
    if isinstance(v, datetime):
        return v
    # Client-side XLSX parsing returns raw Excel serial date numbers (e.g.
    # 45323) for date-typed cells rather than formatted strings, unless the
    # cell happens to carry a text format. Excel's epoch is Dec 30 1899.
    if isinstance(v, (int, float)):
        try:
            return datetime(1899, 12, 30) + timedelta(days=v)
        except (OverflowError, ValueError):
            return None
    s = str(v).strip()
    if s.replace('.', '', 1).isdigit():
        try:
            return datetime(1899, 12, 30) + timedelta(days=float(s))
        except (OverflowError, ValueError):
            return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _is_valid_date_value(v):
    if v is None:
        return False
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return 0 < v < 100000
    s = str(v).strip()
    if not s:
        return False
    if re.match(r'^\d+(\.\d+)?$', s):
        n = float(s)
        return 0 < n < 100000
    # P6/MSP exports commonly append a status marker like " A" (Actual) or a
    # trailing "*" after the date itself -- strip it before parsing.
    s = re.sub(r'\s*[A*]$', '', s, flags=re.IGNORECASE).strip()
    return _parse_date(s) is not None


def _is_valid_numeric_value(v):
    if v is None:
        return False
    s = str(v).strip()
    if not s:
        return False
    if not re.match(r'^-?\d+(\.\d+)?%?$', s):
        return False
    try:
        float(s.rstrip('%'))
        return True
    except ValueError:
        return False


def validate_sheet_data(data: dict) -> list:
    """Server-side mirror of frontend/src/utils/thetaValidation.js's
    validateSheetGrid — collects every failing row/field instead of stopping
    at the first, for the same reasons: transform-time rejection needs a
    complete report, not one error per retry. Never raises; always returns a
    (possibly empty) list of {row, field, value, reason} dicts. This is
    informational only — callers attach it to the response, they don't use
    it to block a save."""
    sheets = (data or {}).get('sheets') or []
    if not sheets:
        return [{'row': None, 'field': None, 'value': None, 'reason': 'No sheet data found.'}]

    sheet = sheets[0]
    headers = [str(h).strip() for h in (sheet.get('headers') or [])]
    rows = sheet.get('rows') or []

    missing_columns = [c for c in REQUIRED_SCHEDULE_COLUMNS if c not in headers]
    if missing_columns:
        return [{'row': None, 'field': None, 'value': None,
                  'reason': f"Missing required columns: {', '.join(missing_columns)}"}]
    if not rows:
        return [{'row': None, 'field': None, 'value': None,
                  'reason': 'Add at least one row of data before transforming.'}]

    col_idx = {h: i for i, h in enumerate(headers)}
    errors = []
    real_activity_rows = 0

    for r, row in enumerate(rows):
        row_num = r + 2  # header is row 1, rows are 1-indexed for the user

        def cell(field):
            idx = col_idx.get(field)
            return row[idx] if idx is not None and idx < len(row) else None

        activity_id = str(cell('Activity ID') or '').strip()
        activity_name = str(cell('Activity Name') or '').strip()
        if not activity_id or not activity_name:
            continue
        real_activity_rows += 1

        for field in THETA_DATE_FIELDS:
            val = cell(field)
            is_blank = val is None or str(val).strip() == ''
            if is_blank:
                continue
            if not _is_valid_date_value(val):
                errors.append({'row': row_num, 'field': field, 'value': val,
                                'reason': f'"{field}" value "{val}" is not a valid date.'})
        for field in THETA_NUMERIC_FIELDS:
            val = cell(field)
            is_blank = val is None or str(val).strip() == ''
            if is_blank:
                continue
            if not _is_valid_numeric_value(val):
                errors.append({'row': row_num, 'field': field, 'value': val,
                                'reason': f'"{field}" value "{val}" is not a valid number.'})

    if real_activity_rows == 0:
        errors.insert(0, {'row': None, 'field': None, 'value': None,
                           'reason': 'Add at least one row with both an Activity ID and Activity Name before transforming.'})

    return errors


def _empty_metrics():
    return {
        'healthIndex': 0, 'healthStatus': 'Critical', 'healthTrend': 'Declining',
        'scheduleVariance': 0, 'baselineDate': 'N/A', 'forecastDate': 'N/A',
        'costExposure': 0, 'budget': 0, 'forecastCost': 0,
        'productivity': 0, 'productivityGap': -100,
        'recoveryDays': 0, 'recoverySavings': '0K', 'recoveryConf': 40,
        'scheduleRows': [], 'costChart': [], 'costBreakdown': [],
        'aiInsight': 'No sheet data yet. Add activities to the sheet to see live insights.',
        'costLinkage': 'No schedule variance detected yet.',
    }


def _format_savings(v):
    if v >= 1_000_000:
        return f'{v / 1_000_000:.1f}M'
    return f'{int(round(v / 1000))}K'


def _format_money_aed(v):
    """Format a raw AED amount with a sign and a dynamic K/M unit, so small
    demo-scale figures (tens of thousands of AED) don't all collapse to
    "0.0M" the way a fixed-millions format would."""
    sign = '+' if v >= 0 else '-'
    abs_v = abs(v)
    if abs_v >= 1_000_000:
        return f'{sign}{abs_v / 1_000_000:.1f}M AED'
    return f'{sign}{int(round(abs_v / 1000))}K AED'


def compute_metrics_from_sheet(data: dict) -> dict:
    sheets = (data or {}).get('sheets') or []
    if not sheets:
        return _empty_metrics()

    sheet = sheets[0]
    headers = [str(h).strip() for h in (sheet.get('headers') or [])]
    rows = sheet.get('rows') or []
    col_idx = {h: i for i, h in enumerate(headers)}

    def get(row, col, default=None):
        idx = col_idx.get(col)
        if idx is None or idx >= len(row):
            return default
        v = row[idx]
        return default if v is None or v == '' else v

    def num(row, col, default=0.0):
        v = get(row, col)
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def status_of(row):
        return str(get(row, 'Status', '') or '').strip()

    # Only rows with a real Activity ID + Activity Name count as activities —
    # matches Excel's COUNTA(Activity ID) roll-up.
    activities = [
        r for r in rows
        if str(get(r, 'Activity ID', '') or '').strip() and str(get(r, 'Activity Name', '') or '').strip()
    ]
    total = len(activities)
    if total == 0:
        return _empty_metrics()

    delayed = [r for r in activities if status_of(r) == 'Delayed']
    delayed_count = len(delayed)
    delay_ratio = delayed_count / total

    delayed_variance_days = [num(r, 'Variance (Days)') for r in delayed]
    avg_delay = (sum(delayed_variance_days) / delayed_count) if delayed_count else 0.0

    all_variances = [num(r, 'Variance (Days)') for r in activities]
    schedule_variance = round(max(all_variances) if all_variances else 0)

    baseline_finishes = [d for d in (_parse_date(get(r, 'Baseline Finish')) for r in activities) if d]
    project_baseline_finish = max(baseline_finishes) if baseline_finishes else None
    project_forecast_finish = (
        project_baseline_finish + timedelta(days=schedule_variance)
        if project_baseline_finish else None
    )

    total_budget = sum(num(r, 'Budget Cost (AED)') for r in activities)
    total_forecast = sum(num(r, 'Forecast Cost (AED)') for r in activities)
    cost_exposure_aed = total_forecast - total_budget

    productivity_vals = [
        num(r, 'Productivity Index') for r in activities
        if get(r, 'Productivity Index') not in (None, '')
    ]
    productivity_index = (sum(productivity_vals) / len(productivity_vals)) if productivity_vals else 0.0

    schedule_health = max(0.0, 1 - (avg_delay * 0.05))
    cost_health = max(0.0, 1 - ((cost_exposure_aed / total_budget) * 10)) if total_budget else 1.0
    health_index_raw = round((schedule_health + cost_health + productivity_index) / 3, 3)
    health_index_pct = max(0, min(100, round(health_index_raw * 100)))

    if health_index_raw < 0.5:
        health_status, health_trend = 'Critical', 'Declining'
    elif health_index_raw < 0.75:
        health_status, health_trend = 'At Risk', 'Declining'
    else:
        health_status, health_trend = 'On Track', 'Improving'

    productivity_pct = max(0, round(productivity_index * 100))
    productivity_gap = productivity_pct - 100

    recovery_days = max(0, round(schedule_variance * 0.6))
    recovery_savings_val = max(0.0, round(cost_exposure_aed * 0.7 * 0.5, 0))
    actual_finish_filled = sum(1 for r in activities if get(r, 'Actual Finish'))
    recovery_conf_pct = max(0, min(100, round((0.7 + 0.28 * (actual_finish_filled / total)) * 100)))

    # ── Schedule Intelligence: group by Phase, mirroring the phase-summary
    # table (first non-completed activity's Root Cause/Impact per phase).
    seen_phases = []
    for r in activities:
        p = str(get(r, 'Phase', '') or '').strip()
        if p and p not in seen_phases:
            seen_phases.append(p)

    schedule_rows = []
    for phase in seen_phases:
        phase_rows = [r for r in activities if str(get(r, 'Phase', '') or '').strip() == phase]
        open_rows = [r for r in phase_rows if status_of(r) != 'Completed']
        if not open_rows:
            schedule_rows.append({
                'phase': phase, 'variance': 'Closed', 'rootCause': '-',
                'impact': 'Done', 'badge': _IMPACT_BADGES['None'],
            })
            continue
        avg_var = round(sum(num(r, 'Variance (Days)') for r in open_rows) / len(open_rows), 1)
        first_open = open_rows[0]
        root_cause = get(first_open, 'Root Cause', '-') or '-'
        impact = str(get(first_open, 'Impact', 'None') or 'None').strip()
        badge_key = impact if impact in _IMPACT_BADGES else 'None'
        impact_label = 'Done' if impact == 'None' else impact
        schedule_rows.append({
            'phase': phase,
            'variance': f'+{avg_var} days' if avg_var > 0 else 'Closed',
            'rootCause': root_cause,
            'impact': impact_label,
            'badge': _IMPACT_BADGES[badge_key],
        })

    # ── Cost Intelligence: cost_chart is period-over-period Actual vs
    # cumulative Budget across ALL categories (matches the dashboard bar
    # chart); cost_breakdown is the top-3 categories by Forecast-vs-Budget
    # variance (matches the Cost Intelligence table's "Variance vs Budget").
    cost_chart = []
    cumulative_budget = 0.0
    for p in _PERIODS:
        period_rows = [r for r in activities if str(get(r, 'Period', '') or '').strip() == p]
        period_actual = sum(num(r, 'Actual Cost (AED)') for r in period_rows)
        period_budget = sum(num(r, 'Budget Cost (AED)') for r in period_rows)
        cumulative_budget += period_budget
        overrun_aed = period_actual - period_budget
        overrun = round(overrun_aed / 1_000_000, 2)
        # Compact tag (no " AED" suffix) for the chart bar label.
        tag = _format_money_aed(overrun_aed)[:-4] if overrun_aed > 0 else ''
        cost_chart.append({
            'period': p,
            'budget': round(cumulative_budget / 1_000_000, 2),
            'overrun': overrun if overrun > 0 else 0,
            'tag': tag,
        })
    fcst_overrun = round(cost_exposure_aed / 1_000_000, 2)
    cost_chart.append({
        'period': 'Fcst',
        'budget': round(total_budget / 1_000_000, 2),
        'overrun': fcst_overrun if fcst_overrun > 0 else 0,
        'tag': 'Fcst',
    })

    categories_present = []
    for r in activities:
        c = str(get(r, 'Cost Category', '') or '').strip()
        if c and c not in categories_present:
            categories_present.append(c)

    breakdown_candidates = []
    for cat in categories_present:
        cat_budget = sum(num(r, 'Budget Cost (AED)') for r in activities if str(get(r, 'Cost Category', '') or '').strip() == cat)
        cat_forecast = sum(num(r, 'Forecast Cost (AED)') for r in activities if str(get(r, 'Cost Category', '') or '').strip() == cat)
        breakdown_candidates.append((cat, cat_forecast - cat_budget))
    breakdown_candidates.sort(key=lambda x: x[1], reverse=True)

    cost_breakdown = []
    for cat, variance in breakdown_candidates[:3]:
        cost_breakdown.append({'label': cat, 'value': _format_money_aed(variance)})

    ai_insight = (
        f'Live sheet data: {total} activities tracked. {delayed_count} are behind schedule, '
        f'averaging {round(avg_delay)} days of delay ({delay_ratio * 100:.0f}% of activities), '
        f'driving a live schedule variance signal.'
    )
    cost_linkage = (
        f'{delay_ratio * 100:.0f}% of activities are delayed, contributing an estimated '
        f'AED {(cost_exposure_aed * 0.7 / 1_000_000):.1f}M in cost exposure tied directly to the '
        f'{schedule_variance}-day schedule variance.'
    )

    return {
        'healthIndex': health_index_pct,
        'healthStatus': health_status,
        'healthTrend': health_trend,
        'scheduleVariance': schedule_variance,
        'baselineDate': project_baseline_finish.strftime('%b %Y') if project_baseline_finish else 'N/A',
        'forecastDate': project_forecast_finish.strftime('%b %Y') if project_forecast_finish else 'N/A',
        'costExposure': round(cost_exposure_aed / 1_000_000, 1),
        'budget': round(total_budget / 1_000_000, 1),
        'forecastCost': round(total_forecast / 1_000_000, 1),
        'productivity': productivity_pct,
        'productivityGap': productivity_gap,
        'recoveryDays': recovery_days,
        'recoverySavings': _format_savings(recovery_savings_val),
        'recoveryConf': recovery_conf_pct,
        'scheduleRows': schedule_rows,
        'costChart': cost_chart,
        'costBreakdown': cost_breakdown,
        'aiInsight': ai_insight,
        'costLinkage': cost_linkage,
    }


# ── In-memory pub/sub for SSE ───────────────────────────────────────────────────
# NOTE: this is per-process memory. It works correctly under this app's actual
# deploy model (Waitress, single-process multi-threaded). If ever deployed
# behind a multi-process WSGI server (e.g. gunicorn with >1 worker and no
# shared broker), a subscriber attached to one worker won't see a broadcast
# fired by another. Acceptable today because polling (useSheetData, 2.5s) is
# the primary sync path — SSE is a secondary "ready for future push" channel.

_subs_lock = threading.Lock()
_subscribers = defaultdict(list)


def subscribe(company_id):
    event = threading.Event()
    payload = {'event': None}
    with _subs_lock:
        _subscribers[str(company_id)].append((event, payload))
    return event, payload


def unsubscribe(company_id, token):
    with _subs_lock:
        lst = _subscribers.get(str(company_id))
        if lst and token in lst:
            lst.remove(token)


def _broadcast(company_id, sheet):
    if not sheet:
        return
    msg = {
        'type': 'sheet_updated',
        'sheet_id': sheet['id'],
        'version': sheet['version'],
        'updated_at': sheet['updated_at'],
    }
    with _subs_lock:
        targets = list(_subscribers.get(str(company_id), []))
    for event, payload in targets:
        payload['event'] = msg
        event.set()
