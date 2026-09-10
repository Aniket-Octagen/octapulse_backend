from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd


def _normalize_col(col: str) -> str:
    return str(col).strip().lower().replace("_", " ")


def _first_matching_col(columns: Iterable[str], keywords: List[str]) -> Optional[str]:
    normalized = {c: _normalize_col(c) for c in columns}
    normalized_keywords = [_normalize_col(k) for k in keywords]

    # 1) Exact match first (most reliable)
    for keyword in normalized_keywords:
        for raw, norm in normalized.items():
            if norm == keyword:
                return raw

    # 2) Prefix/word-boundary style match
    for keyword in normalized_keywords:
        for raw, norm in normalized.items():
            if norm.startswith(f"{keyword} ") or norm.endswith(f" {keyword}"):
                return raw

    # 3) Contains fallback, with stricter handling for very generic "id"
    for keyword in normalized_keywords:
        for raw, norm in normalized.items():
            if keyword == "id":
                if norm in {"id", "activity id", "activityid", "activity code id"}:
                    return raw
                continue
            if keyword in norm:
                return raw

    return None


def _split_dependency_tokens(raw: object) -> List[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    return [t.strip() for t in re.split(r"[\n,;|]+", text) if t and str(t).strip()]


def _coerce_datetime_series(values: pd.Series) -> pd.Series:
    s = values.copy()
    try:
        parsed = pd.to_datetime(s, errors="coerce", format="mixed")
    except TypeError:
        parsed = pd.to_datetime(s, errors="coerce")

    numeric_values = pd.to_numeric(s, errors="coerce")
    numeric_mask = numeric_values.notna() & parsed.isna()
    if numeric_mask.any():
        parsed.loc[numeric_mask] = pd.to_datetime(
            numeric_values.loc[numeric_mask],
            unit="D",
            origin="1899-12-30",
            errors="coerce",
        )
    return parsed


def _coerce_datetime_value(value: object) -> pd.Timestamp:
    if value is None:
        return pd.NaT
    try:
        return pd.to_datetime(value, errors="coerce", format="mixed")
    except TypeError:
        return pd.to_datetime(value, errors="coerce")


def _normalize_activity_id(value: object) -> str:
    if value is None:
        return ""

    if isinstance(value, float) and value.is_integer():
        value = int(value)

    text = str(value).strip().upper().strip("'\"")
    text = re.sub(r"\.0+$", "", text)
    # collapse internal whitespace for safer matching across sources
    text = re.sub(r"\s+", "", text)
    return text


def _parse_dependency_token(token: str) -> Optional[Tuple[str, str, int]]:
    # Examples:
    #   A60700: FS
    #   A1-PM-PMM-0001: FS 14
    #   A60680: FS 72
    token = str(token or "").strip()
    if not token:
        return None

    match = re.match(r"^\s*([^:]+?)\s*:\s*([A-Za-z]{2})(?:\s*\+?\s*(-?\d+))?(?:\s*[dD])?\s*$", token)
    if not match:
        # Alternate style: "ACTID FS 14" or "ACTID FS"
        match = re.match(r"^\s*([^\s:]+)\s+([A-Za-z]{2})(?:\s*\+?\s*(-?\d+))?(?:\s*[dD])?\s*$", token)
    if not match:
        # Bare predecessor id fallback => assume FS with 0 lag
        bare = _normalize_activity_id(token)
        if bare:
            return bare, "FS", 0
        return None

    predecessor_raw = match.group(1)
    relation = (match.group(2) or "").upper()
    lag_days = int(match.group(3) or 0)

    predecessor_id = _normalize_activity_id(predecessor_raw)
    if not predecessor_id:
        return None

    return predecessor_id, relation, lag_days


def _load_dependency_edges_from_dataframe(df_eval: pd.DataFrame) -> List[Tuple[str, str, str, int]]:
    activity_id_col = _first_matching_col(df_eval.columns, ["activity id", "activity code", "wbs", "id"])
    pred_col = _first_matching_col(df_eval.columns, ["predecessor details", "predecessor", "predecessors"])

    if not activity_id_col or not pred_col:
        return []

    edges: List[Tuple[str, str, str, int]] = []
    for _, row in df_eval.iterrows():
        successor_id = _normalize_activity_id(row.get(activity_id_col))
        if not successor_id:
            continue

        pred_raw = row.get(pred_col)
        if pred_raw is None:
            continue
        pred_text = str(pred_raw).strip()
        if not pred_text or pred_text.lower() in {"none", "nan", "null"}:
            continue

        tokens = _split_dependency_tokens(pred_text)
        for token in tokens:
            parsed = _parse_dependency_token(token)
            if not parsed:
                continue
            predecessor_id, relation, lag_days = parsed
            edges.append((predecessor_id, successor_id, relation, lag_days))

    return edges


def _load_dependency_edges_from_json(
    dependency_json_path: Path,
) -> Tuple[List[Tuple[str, str, str, int]], Dict[str, int], Dict[str, Dict[str, pd.Timestamp]]]:
    diagnostics = {
        "total_rows": 0,
        "rows_with_successor_id": 0,
        "rows_with_predecessors": 0,
        "total_parsed_edges": 0,
        "fs_edges": 0,
        "unsupported_relation_edges": 0,
        "malformed_tokens": 0,
    }

    if not dependency_json_path.exists():
        return [], diagnostics, {}

    with dependency_json_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    rows = payload.get("P6", []) if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return [], diagnostics, {}

    edges: List[Tuple[str, str, str, int]] = []
    activity_dates: Dict[str, Dict[str, pd.Timestamp]] = {}

    for row in rows:
        if not isinstance(row, dict):
            continue

        diagnostics["total_rows"] += 1

        successor_id = _normalize_activity_id(row.get("Activity ID"))
        if not successor_id:
            continue
        diagnostics["rows_with_successor_id"] += 1

        early_start = _coerce_datetime_value(row.get("Early Start"))
        early_finish = _coerce_datetime_value(row.get("Early Finish"))
        current_dates = activity_dates.get(successor_id, {"early_start": pd.NaT, "early_finish": pd.NaT})
        if pd.isna(current_dates.get("early_start")) and pd.notna(early_start):
            current_dates["early_start"] = early_start
        if pd.isna(current_dates.get("early_finish")) and pd.notna(early_finish):
            current_dates["early_finish"] = early_finish
        activity_dates[successor_id] = current_dates

        pred_details = row.get("Predecessor Details")
        if not pred_details or str(pred_details).strip().lower() in {"none", "nan"}:
            continue

        diagnostics["rows_with_predecessors"] += 1
        tokens = _split_dependency_tokens(pred_details)
        for token in tokens:
            parsed = _parse_dependency_token(token)
            if not parsed:
                diagnostics["malformed_tokens"] += 1
                continue

            predecessor_id, relation, lag_days = parsed
            diagnostics["total_parsed_edges"] += 1
            if relation == "FS":
                diagnostics["fs_edges"] += 1
            else:
                diagnostics["unsupported_relation_edges"] += 1

            edges.append((predecessor_id, successor_id, relation, lag_days))

    return edges, diagnostics, activity_dates


def _build_row_state(df_eval: pd.DataFrame) -> pd.DataFrame:
    actual_col = _first_matching_col(
        df_eval.columns,
        ["actual start date", "actual start", "actual date", "actual finish", "actual"],
    )
    ep_col = _first_matching_col(df_eval.columns, ["early planning", "planned start", "ep", "early start", "start"])
    lp_col = _first_matching_col(df_eval.columns, ["late planning", "planned end", "lp", "late finish", "finish"])
    activity_id_col = _first_matching_col(df_eval.columns, ["activity id", "activity code", "wbs", "id"])

    state = pd.DataFrame(index=df_eval.index)
    state["activity_id_raw"] = df_eval[activity_id_col] if activity_id_col else None
    state["activity_id_norm"] = state["activity_id_raw"].apply(_normalize_activity_id)
    state["planned_start_date"] = _coerce_datetime_series(df_eval[ep_col]) if ep_col else pd.NaT
    state["planned_end_date"] = _coerce_datetime_series(df_eval[lp_col]) if lp_col else pd.NaT
    state["actual_date"] = _coerce_datetime_series(df_eval[actual_col]) if actual_col else pd.NaT
    state["planned_ref_date"] = state["planned_end_date"].where(
        state["planned_end_date"].notna(), state["planned_start_date"]
    )

    duration_days = (state["planned_end_date"] - state["planned_start_date"]).dt.days
    state["planned_duration_days"] = duration_days.where(duration_days.notna(), 0)
    state.loc[state["planned_duration_days"] < 0, "planned_duration_days"] = 0

    return state


def _pick_rep_row(series: pd.Series):
    non_null = series.dropna()
    return non_null.iloc[0] if not non_null.empty else pd.NaT


def get_formula_forecast_for_dataframe(
    target_df: pd.DataFrame,
    project_root: Path,
    dependency_json_path: Optional[Path] = None,
) -> pd.DataFrame:
    """
    Independent formula-based forecast engine (phase-1):
    - Supports FS + lag constraints from Knowledgebase/PREDECESSOR-SUCCESSOR-LAG.json
    - Computes constrained start/finish for not-started activities
    - Leaves legacy ML columns untouched (additive outputs only)
    """
    out = target_df.copy()
    state = _build_row_state(out)

    dep_path = dependency_json_path or (project_root / "Knowledgebase" / "PREDECESSOR-SUCCESSOR-LAG.json")
    edges_json, dep_diag, kb_activity_dates = _load_dependency_edges_from_json(dep_path)
    edges_sheet = _load_dependency_edges_from_dataframe(out)

    # Merge and de-duplicate edges from knowledgebase + sheet
    edges_all = list({(p, s, r, int(l)) for p, s, r, l in (edges_json + edges_sheet)})

    # Build representative activity record per normalized activity id.
    valid_rows = state[state["activity_id_norm"].astype(bool)].copy()
    grouped = valid_rows.groupby("activity_id_norm", as_index=False).agg(
        planned_start_date=("planned_start_date", _pick_rep_row),
        planned_end_date=("planned_end_date", _pick_rep_row),
        planned_ref_date=("planned_ref_date", _pick_rep_row),
        actual_date=("actual_date", _pick_rep_row),
        planned_duration_days=("planned_duration_days", "max"),
    )

    node_ids = set(grouped["activity_id_norm"].tolist())

    # Keep only in-sheet FS edges for phase-1.
    incoming: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    outgoing: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    incoming_all: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    outgoing_all: Dict[str, List[Tuple[str, int]]] = defaultdict(list)

    mapped_edges = 0

    for pred_id, succ_id, relation, lag_days in edges_all:
        if relation != "FS":
            continue
        if succ_id in node_ids:
            incoming_all[succ_id].append((pred_id, lag_days))
        if pred_id in node_ids:
            outgoing_all[pred_id].append((succ_id, lag_days))
        if pred_id not in node_ids or succ_id not in node_ids:
            continue
        incoming[succ_id].append((pred_id, lag_days))
        outgoing[pred_id].append((succ_id, lag_days))
        mapped_edges += 1

    # Topological ordering (cycle-aware).
    indegree: Dict[str, int] = {nid: 0 for nid in node_ids}
    for succ_id, preds in incoming.items():
        indegree[succ_id] = len(preds)

    queue = deque(sorted([nid for nid in node_ids if indegree.get(nid, 0) == 0]))
    topo_order: List[str] = []
    while queue:
        nid = queue.popleft()
        topo_order.append(nid)
        for succ_id, _ in outgoing.get(nid, []):
            indegree[succ_id] -= 1
            if indegree[succ_id] == 0:
                queue.append(succ_id)

    cycle_nodes = sorted([nid for nid, deg in indegree.items() if deg > 0])

    # Per-activity computed schedule.
    row_by_id = grouped.set_index("activity_id_norm", drop=False)
    calc: Dict[str, Dict[str, object]] = {}

    def _baseline_start_finish(nid: str) -> Tuple[pd.Timestamp, pd.Timestamp, int]:
        row = row_by_id.loc[nid]
        planned_start = row["planned_start_date"]
        planned_ref = row["planned_ref_date"]
        planned_end = row["planned_end_date"]
        duration_days = int(row["planned_duration_days"] if pd.notna(row["planned_duration_days"]) else 0)

        base_start = planned_start if pd.notna(planned_start) else planned_ref
        if pd.isna(base_start):
            return pd.NaT, pd.NaT, duration_days

        if pd.notna(planned_end) and duration_days == 0 and pd.notna(planned_start):
            # Milestone-like: start/end same date or explicit end
            base_finish = planned_end
        elif duration_days > 0:
            base_finish = base_start + pd.Timedelta(days=duration_days)
        else:
            base_finish = base_start

        return base_start, base_finish, duration_days

    for nid in topo_order:
        row = row_by_id.loc[nid]
        actual_date = row["actual_date"]
        base_start, base_finish, duration_days = _baseline_start_finish(nid)
        pred_pairs = incoming.get(nid, [])
        succ_pairs = outgoing.get(nid, [])

        dependency_eligible = bool(incoming_all.get(nid))
        if not dependency_eligible:
            calc[nid] = {
                "start": pd.NaT,
                "finish": pd.NaT,
                "status": "Dependency Not Eligible (No Pred)",
                "constraint_source": "",
                "lag_impact_days": 0,
                "eligible": False,
            }
            continue

        candidate_dates: List[Tuple[pd.Timestamp, str]] = []
        for pred_id, lag_days in pred_pairs:
            pred_calc = calc.get(pred_id)
            pred_finish = pred_calc.get("finish") if pred_calc else pd.NaT

            if pd.isna(pred_finish):
                kb_pred = kb_activity_dates.get(pred_id, {})
                kb_pred_finish = kb_pred.get("early_finish", pd.NaT)
                kb_pred_start = kb_pred.get("early_start", pd.NaT)
                pred_finish = kb_pred_finish if pd.notna(kb_pred_finish) else kb_pred_start

            if pd.isna(pred_finish):
                continue
            candidate = pred_finish + pd.Timedelta(days=int(lag_days))
            candidate_dates.append((candidate, f"{pred_id}:FS+{lag_days}"))

        if candidate_dates:
            dep_floor, source = max(candidate_dates, key=lambda x: x[0])
            if pd.notna(base_start):
                constrained_start = max(base_start, dep_floor)
                lag_impact_days = int((constrained_start - base_start).days)
                status = "Constrained by Dependency" if lag_impact_days > 0 else "Dependency Checked"
            else:
                constrained_start = dep_floor
                lag_impact_days = 0 
                status = "Inferred from Dependency"
            constraint_source = source
        else:
            if pd.notna(base_start):
                constrained_start = base_start
                lag_impact_days = 0
                status = "Dependency Listed (Anchor Missing)"
                constraint_source = "PredFinishMissing->PlanFallback"
            else:
                constrained_start = pd.NaT
                lag_impact_days = 0
                status = "Missing Dependency Anchor"
                constraint_source = "PredFinishMissing"

        if pd.isna(constrained_start) and pd.isna(actual_date):
            calc[nid] = {
                "start": pd.NaT,
                "finish": pd.NaT,
                "status": "Missing Dates & Ancestors",
                "constraint_source": "",
                "lag_impact_days": 0,
                "eligible": True,
            }
            continue

        # For activities with an actual date present, treat that date as a started anchor
        # and still forecast finish using duration + dependency floor.
        if pd.notna(actual_date):
            if pd.notna(constrained_start):
                started_anchor = max(actual_date, constrained_start)
            else:
                started_anchor = actual_date

            if duration_days > 0:
                constrained_finish = started_anchor + pd.Timedelta(days=duration_days)
                status = "Started Dependency Forecast"
            else:
                constrained_finish = started_anchor
                status = "Observed"

            calc[nid] = {
                "start": started_anchor,
                "finish": constrained_finish,
                "status": status,
                "constraint_source": constraint_source,
                "lag_impact_days": lag_impact_days,
                "eligible": True,
            }
            continue

        if duration_days > 0:
            constrained_finish = constrained_start + pd.Timedelta(days=duration_days)
        else:
            constrained_finish = constrained_start if pd.notna(constrained_start) else base_finish

        calc[nid] = {
            "start": constrained_start,
            "finish": constrained_finish,
            "status": status,
            "constraint_source": constraint_source,
            "lag_impact_days": lag_impact_days,
            "eligible": True,
        }

    # Safe fallback for cycle nodes: keep baseline (no propagation inside cycles).
    for nid in cycle_nodes:
        pred_pairs = incoming.get(nid, [])
        succ_pairs = outgoing.get(nid, [])
        dependency_eligible = bool(incoming_all.get(nid))
        if not dependency_eligible:
            calc[nid] = {
                "start": pd.NaT,
                "finish": pd.NaT,
                "status": "Dependency Not Eligible (No Pred)",
                "constraint_source": "",
                "lag_impact_days": 0,
                "eligible": False,
            }
            continue

        row = row_by_id.loc[nid]
        actual_date = row["actual_date"]
        if pd.notna(actual_date):
            calc[nid] = {
                "start": actual_date,
                "finish": actual_date,
                "status": "Observed",
                "constraint_source": "",
                "lag_impact_days": 0,
                "eligible": True,
            }
            continue

        base_start, base_finish, _ = _baseline_start_finish(nid)
        calc[nid] = {
            "start": base_start,
            "finish": base_finish,
            "status": "Cycle Fallback",
            "constraint_source": "CycleDetected",
            "lag_impact_days": 0,
            "eligible": True,
        }

    # Emit per-row fields by activity id.
    out["formula_forecast_start_date"] = pd.NaT
    out["formula_forecast_finish_date"] = pd.NaT
    out["formula_forecast_date"] = pd.NaT
    out["formula_constraint_source"] = ""
    out["formula_predecessors"] = ""
    out["formula_successors"] = ""
    out["formula_lag_impact_days"] = 0
    out["formula_delay_vs_plan_days"] = pd.NA
    out["formula_engine_status"] = "No Activity ID"

    for idx in out.index:
        nid = state.at[idx, "activity_id_norm"]
        planned_ref = state.at[idx, "planned_ref_date"]

        if not nid:
            continue

        item = calc.get(nid)
        if not item:
            out.at[idx, "formula_engine_status"] = "Unmapped Activity"
            continue

        start_date = item.get("start")
        finish_date = item.get("finish")

        pred_pairs = incoming_all.get(nid, [])
        succ_pairs = outgoing_all.get(nid, [])

        pred_text = ", ".join(
            sorted(
                [
                    f"{pred_id}:FS+{int(lag_days)}" if int(lag_days) != 0 else f"{pred_id}:FS"
                    for pred_id, lag_days in pred_pairs
                ]
            )
        )
        succ_text = ", ".join(
            sorted(
                [
                    f"{succ_id}:FS+{int(lag_days)}" if int(lag_days) != 0 else f"{succ_id}:FS"
                    for succ_id, lag_days in succ_pairs
                ]
            )
        )

        out.at[idx, "formula_forecast_start_date"] = start_date
        out.at[idx, "formula_forecast_finish_date"] = finish_date
        out.at[idx, "formula_forecast_date"] = finish_date
        out.at[idx, "formula_constraint_source"] = item.get("constraint_source", "")
        out.at[idx, "formula_predecessors"] = pred_text
        out.at[idx, "formula_successors"] = succ_text
        out.at[idx, "formula_lag_impact_days"] = int(item.get("lag_impact_days", 0) or 0)

        if not bool(item.get("eligible", False)):
            out.at[idx, "formula_forecast_start_date"] = pd.NaT
            out.at[idx, "formula_forecast_finish_date"] = pd.NaT
            out.at[idx, "formula_forecast_date"] = pd.NaT
            out.at[idx, "formula_delay_vs_plan_days"] = pd.NA
            out.at[idx, "formula_engine_status"] = "Dependency Not Eligible (No Pred)"
            continue

        out.at[idx, "formula_engine_status"] = "Dependency Eligible (Pred Found)"

        observed = pd.notna(state.at[idx, "actual_date"])

        if pd.notna(planned_ref) and pd.notna(finish_date):
            delay_days = int((finish_date - planned_ref).days)
            out.at[idx, "formula_delay_vs_plan_days"] = delay_days
            if delay_days > 0:
                out.at[idx, "formula_engine_status"] = "Observed Delayed" if observed else "Forecast Delayed"
            elif delay_days < 0:
                out.at[idx, "formula_engine_status"] = "Observed Early" if observed else "Forecast Early"
            else:
                out.at[idx, "formula_engine_status"] = "Observed On Time" if observed else "Forecast On Time"
        else:
            out.at[idx, "formula_engine_status"] = "Observed (No Plan Date)" if observed else "Forecast (No Plan Date)"

    out.attrs["formula_diagnostics"] = {
        "dependency_file": str(dep_path),
        "dependency_rows": dep_diag.get("total_rows", 0),
        "sheet_edges": len(edges_sheet),
        "parsed_edges": dep_diag.get("total_parsed_edges", 0),
        "fs_edges_total": dep_diag.get("fs_edges", 0),
        "unsupported_relation_edges": dep_diag.get("unsupported_relation_edges", 0),
        "malformed_tokens": dep_diag.get("malformed_tokens", 0),
        "mapped_edges_in_sheet": mapped_edges,
        "listed_predecessor_edges_in_sheet": sum(len(v) for v in incoming_all.values()),
        "cycle_nodes_in_sheet": len(cycle_nodes),
        "activity_nodes_in_sheet": len(node_ids),
    }

    return out
