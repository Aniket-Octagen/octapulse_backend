#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

import matplotlib

# Safe for server/headless execution
matplotlib.use("Agg")
import matplotlib.pyplot as plt


DATE_EPOCH = pd.Timestamp("1970-01-01")


@dataclass
class ModelArtifacts:
    pipeline: Pipeline
    metrics: Dict[str, Optional[float]]
    feature_columns: List[str]

# Create a singleton model cache to avoid re-training the ML model for every request.
# In a real heavy-duty production environment we would serialize/save the trained
# pipeline into a joblib/pickle file. For now, training is cached in memory per process.
_GLOBAL_MODEL_CACHE: Optional[ModelArtifacts] = None
_GLOBAL_CLONED_FILES: List[str] = []

def _normalize_col(col: str) -> str:
    return str(col).strip().lower().replace("_", " ")


def _first_matching_col(columns: Iterable[str], keywords: List[str]) -> Optional[str]:
    normalized = {c: _normalize_col(c) for c in columns}
    for keyword in keywords:
        for raw, norm in normalized.items():
            if keyword in norm:
                return raw
    return None


def _coerce_datetime_series(values: pd.Series) -> pd.Series:
    """Parse mixed date values safely, including Excel serial day numbers."""
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


def _date_to_ordinal(s: pd.Series) -> pd.Series:
    s = _coerce_datetime_series(s)
    return (s - DATE_EPOCH).dt.days.astype("float")


def _ordinal_to_date(v: float) -> pd.Timestamp:
    if pd.isna(v):
        return pd.NaT
    return DATE_EPOCH + pd.Timedelta(days=int(round(float(v))))


def _safe_sheet_df(df: pd.DataFrame) -> pd.DataFrame:
    """Drop all-empty rows/cols and ensure headers are strings."""
    out = df.copy()
    out = out.dropna(axis=0, how="all").dropna(axis=1, how="all")
    out.columns = [str(c).strip() for c in out.columns]
    return out


def _extract_from_sheet(df: pd.DataFrame, source_file: str, source_sheet: str) -> Optional[pd.DataFrame]:
    df = _safe_sheet_df(df)
    if df.empty:
        return None

    actual_col = _first_matching_col(
        df.columns,
        ["actual date", "actual finish", "actual"],
    )
    ep_col = _first_matching_col(
        df.columns,
        ["early planning", "planned start", "ep", "early start", "start"],
    )
    lp_col = _first_matching_col(
        df.columns,
        ["late planning", "planned end", "lp", "late finish", "finish"],
    )

    # If there is no actual/planned signal, skip this sheet.
    if actual_col is None and ep_col is None and lp_col is None:
        return None

    activity_id_col = _first_matching_col(df.columns, ["activity id", "activity code", "wbs", "id"])
    activity_name_col = _first_matching_col(df.columns, ["activity name", "description", "scope", "name"])
    stage_col = _first_matching_col(df.columns, ["stage gate", "milestone", "stage"])

    out = pd.DataFrame(
        {
            "activity_id": df[activity_id_col] if activity_id_col in df.columns else None,
            "activity_name": df[activity_name_col] if activity_name_col in df.columns else None,
            "stage_gate": df[stage_col] if stage_col in df.columns else None,
            "planned_start_date": _coerce_datetime_series(df[ep_col]) if ep_col else pd.NaT,
            "planned_end_date": _coerce_datetime_series(df[lp_col]) if lp_col else pd.NaT,
            "actual_date": _coerce_datetime_series(df[actual_col]) if actual_col else pd.NaT,
            "source_file": source_file,
            "source_sheet": source_sheet,
        }
    )

    out["planned_ref_date"] = out["planned_end_date"].where(
        out["planned_end_date"].notna(), out["planned_start_date"]
    )

    # Keep only rows that have at least one date-related value.
    mask = out[["planned_start_date", "planned_end_date", "planned_ref_date", "actual_date"]].notna().any(axis=1)
    out = out.loc[mask].reset_index(drop=True)

    if out.empty:
        return None
    return out


def _files_from_history_db(project_root: Path, max_files: int = 800) -> List[Path]:
    try:
        from db_postgres import read_db
        history = read_db(str(project_root / "database" / "history.json"))
    except Exception:
        return []

    if not isinstance(history, list):
        return []

    # Newest records first
    history_sorted = sorted(
        history,
        key=lambda x: str((x or {}).get("processed_at", "")),
        reverse=True,
    )

    files: List[Path] = []
    seen: set[str] = set()

    for item in history_sorted:
        if str((item or {}).get("status", "")).lower() != "completed":
            continue

        results = (item or {}).get("results", [])
        if not isinstance(results, list):
            continue

        for result in results:
            if str((result or {}).get("status", "")).lower() != "success":
                continue
            output_path = (result or {}).get("output_path")
            if not output_path:
                continue

            rel = Path(str(output_path).replace("\\", "/"))
            full = (project_root / rel).resolve() if not rel.is_absolute() else rel
            if not full.exists() or not full.is_file():
                continue
            if full.name.startswith("~$"):
                continue
            if full.suffix.lower() not in {".xlsx", ".xlsm", ".xls"}:
                continue

            key = str(full).lower()
            if key in seen:
                continue
            seen.add(key)
            files.append(full)

            if len(files) >= max_files:
                return files

    return files


def _load_activity_data(source_path: Optional[Path], explicit_files: Optional[List[Path]] = None) -> pd.DataFrame:
    files: List[Path] = []
    if explicit_files:
        files = [p for p in explicit_files if p.exists() and p.is_file()]
    elif source_path is not None:
        if source_path.is_file():
            files = [source_path]
        else:
            files = [
                p
                for p in source_path.rglob("*.xls*")
                if p.is_file() and not p.name.startswith("~$")
            ]

    if not files:
        if source_path is not None:
            raise FileNotFoundError(f"No Excel files found in: {source_path}")
        raise FileNotFoundError("No Excel files found from auto-detected sources.")

    extracted: List[pd.DataFrame] = []

    for fp in files:
        try:
            all_sheets = pd.read_excel(fp, sheet_name=None, engine="openpyxl")
        except Exception:
            continue

        for sheet_name, sheet_df in all_sheets.items():
            maybe = _extract_from_sheet(sheet_df, source_file=fp.name, source_sheet=sheet_name)
            if maybe is not None and not maybe.empty:
                extracted.append(maybe)

    if not extracted:
        raise ValueError(
            "Could not detect usable date columns in any sheet. "
            "Expected columns similar to 'Actual Date', 'Early Planning', 'Late Planning'."
        )

    data = pd.concat(extracted, ignore_index=True)
    data = data.drop_duplicates().reset_index(drop=True)
    return data


def _build_feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    X = pd.DataFrame(index=df.index)

    X["planned_start_ordinal"] = _date_to_ordinal(df["planned_start_date"])
    X["planned_end_ordinal"] = _date_to_ordinal(df["planned_end_date"])
    X["planned_ref_ordinal"] = _date_to_ordinal(df["planned_ref_date"])
    
    # Feature: Planned Duration
    X["planned_duration"] = X["planned_end_ordinal"] - X["planned_start_ordinal"]
    X["planned_duration"] = X["planned_duration"].fillna(0)

    ref = _coerce_datetime_series(df["planned_ref_date"])
    X["planned_month"] = ref.dt.month.astype("float")
    X["planned_weekday"] = ref.dt.weekday.astype("float")
    X["planned_quarter"] = ref.dt.quarter.astype("float")

    X["activity_id"] = df["activity_id"].astype("object")
    X["activity_name"] = df["activity_name"].astype("object")
    X["stage_gate"] = df["stage_gate"].astype("object")
    X["source_sheet"] = df["source_sheet"].astype("object")

    for c in ["activity_id", "activity_name", "stage_gate", "source_sheet"]:
        X[c] = X[c].where(pd.notna(X[c]), np.nan)

    return X


def _train_model(df: pd.DataFrame) -> ModelArtifacts:
    train_df = df[df["actual_date"].notna() & df["planned_ref_date"].notna()].copy()
    if len(train_df) < 10:
        raise ValueError(
            f"Not enough historical rows with Actual Date for ML training. Found: {len(train_df)} (minimum: 10)."
        )

    X = _build_feature_frame(train_df)
    
    # Target variable: Predict Delay in Days rather than raw dates. 
    # RF and GBM cannot extrapolate unbounded dates into the future effectively, but they can predict delays!
    y = _date_to_ordinal(train_df["actual_date"]) - X["planned_ref_ordinal"]

    numeric_cols = [
        "planned_start_ordinal",
        "planned_end_ordinal",
        "planned_ref_ordinal",
        "planned_duration",
        "planned_month",
        "planned_weekday",
        "planned_quarter",
    ]
    categorical_cols = ["activity_id", "activity_name", "stage_gate", "source_sheet"]

    preprocessor = ColumnTransformer(
        transformers=[
            ("num", Pipeline([("imputer", SimpleImputer(strategy="median"))]), numeric_cols),
            (
                "cat",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                categorical_cols,
            ),
        ]
    )

    from sklearn.ensemble import GradientBoostingRegressor
    # GradientBoosting typically performs better for delay distributions and extrapolation
    regressor = GradientBoostingRegressor(
        n_estimators=300,
        max_depth=5,
        min_samples_leaf=3,
        learning_rate=0.05,
        random_state=42,
    )

    pipeline = Pipeline(steps=[("preprocess", preprocessor), ("model", regressor)])

    metrics: Dict[str, Optional[float]] = {
        "train_rows": float(len(train_df)),
        "mae_days": None,
        "r2": None,
    }

    # Holdout evaluation only when enough data exists.
    if len(train_df) >= 30:
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.20, random_state=42)
        pipeline.fit(X_train, y_train)
        y_pred = pipeline.predict(X_test)
        metrics["mae_days"] = float(mean_absolute_error(y_test, y_pred))
        metrics["r2"] = float(r2_score(y_test, y_pred))
    else:
        pipeline.fit(X, y)

    return ModelArtifacts(pipeline=pipeline, metrics=metrics, feature_columns=list(X.columns))


def _predict_dates(df: pd.DataFrame, model_artifacts: ModelArtifacts) -> pd.DataFrame:
    out = df.copy()

    X_all = _build_feature_frame(out)
    
    # Since the model predicts delay in days, we add it back to the ordinal planned ref date
    predicted_delay_days = model_artifacts.pipeline.predict(X_all)
    
    out["forecast_actual_date"] = [
        _ordinal_to_date(ref + delay) if pd.notna(ref) and pd.notna(delay) else pd.NaT 
        for ref, delay in zip(X_all["planned_ref_ordinal"], predicted_delay_days)
    ]

    # If actual already exists, keep it as final used date; otherwise use forecast.
    out["final_used_date"] = out["actual_date"].where(out["actual_date"].notna(), out["forecast_actual_date"])

    out["actual_delay_days"] = (out["actual_date"] - out["planned_ref_date"]).dt.days
    out["forecast_delay_days"] = (out["forecast_actual_date"] - out["planned_ref_date"]).dt.days
    out["final_delay_days"] = (out["final_used_date"] - out["planned_ref_date"]).dt.days

    out["record_type"] = np.where(out["actual_date"].notna(), "Observed", "Forecasted")

    # Add a convenient string attribute for delay logic
    def _format_delay(days_val):
        if pd.isna(days_val):
            return "Unknown"
        d = int(round(float(days_val)))
        if d > 0:
            return f"{d} Days Late"
        elif d < 0:
            return f"{abs(d)} Days Early"
        return "On Time"

    out["delay_status_string"] = out["final_delay_days"].apply(_format_delay)

    return out

def get_ml_forecast_for_dataframe(target_df: pd.DataFrame, project_root: Path) -> pd.DataFrame:
    """
    Exposed API intended for app.py endpoints (e.g. S-Curve generation).
    Given a target DataFrame (e.g., from an analytics output), append the
    ML-predicted actual date bounds and strings.
    """
    global _GLOBAL_MODEL_CACHE, _GLOBAL_CLONED_FILES

    # 1. Setup global cache / Historical model
    files = _files_from_history_db(project_root, max_files=10)
    current_files_hash = [str(f.resolve()) for f in files]
    
    # Needs training? (Model empty, or new files detected)
    if _GLOBAL_MODEL_CACHE is None or current_files_hash != _GLOBAL_CLONED_FILES:
        try:
            hist_df = _load_activity_data(None, explicit_files=files)
            _GLOBAL_MODEL_CACHE = _train_model(hist_df)
            _GLOBAL_CLONED_FILES = current_files_hash
            print("[FORECASTING INFO] ML Model trained and cached in memory.")
        except Exception as e:
            print(f"[FORECASTING WARN] Failed to train ML Model -> {str(e)}")
            # Fallback handling — just return unmodified data with blank forecast columns
            df_err = target_df.copy()
            df_err["forecast_actual_date"] = pd.NaT
            # Safely detect the actual date column from original headers (not guaranteed to be 'actual_date')
            _actual_col_fb = _first_matching_col(target_df.columns, ["actual date", "actual finish", "actual"])
            df_err["final_used_date"] = (
                _coerce_datetime_series(df_err[_actual_col_fb])
                if _actual_col_fb else pd.NaT
            )
            df_err["final_delay_days"] = np.nan
            df_err["delay_status_string"] = "ML Offline"
            return df_err
    
    # 2. Extract needed ML schema elements directly from target_df 
    # Must rename target columns temporarily to match internal representation
    df_eval = target_df.copy()
    
    actual_col = _first_matching_col(df_eval.columns, ["actual date", "actual finish", "actual"])
    ep_col = _first_matching_col(df_eval.columns, ["early planning", "planned start", "ep", "early start", "start"])
    lp_col = _first_matching_col(df_eval.columns, ["late planning", "planned end", "lp", "late finish", "finish"])
    activity_id_col = _first_matching_col(df_eval.columns, ["activity id", "activity code", "wbs", "id"])
    activity_name_col = _first_matching_col(df_eval.columns, ["activity name", "description", "scope", "name"])
    stage_col = _first_matching_col(df_eval.columns, ["stage gate", "milestone", "stage"])

    # Temporarily reconstruct standard internal columns explicitly on the eval dataframe
    eval_standard = pd.DataFrame(index=df_eval.index)
    eval_standard["activity_id"] = df_eval[activity_id_col] if activity_id_col else None
    eval_standard["activity_name"] = df_eval[activity_name_col] if activity_name_col else None
    eval_standard["stage_gate"] = df_eval[stage_col] if stage_col else None
    eval_standard["planned_start_date"] = _coerce_datetime_series(df_eval[ep_col]) if ep_col else pd.NaT
    eval_standard["planned_end_date"] = _coerce_datetime_series(df_eval[lp_col]) if lp_col else pd.NaT
    eval_standard["actual_date"] = _coerce_datetime_series(df_eval[actual_col]) if actual_col else pd.NaT
    eval_standard["source_sheet"] = "TargetSheet" 
    
    # Set references
    eval_standard["planned_ref_date"] = eval_standard["planned_end_date"].where(
        eval_standard["planned_end_date"].notna(), eval_standard["planned_start_date"]
    )
    
    # Need at least a reference date to predict
    predictable_mask = eval_standard["planned_ref_date"].notna()
    
    # Predict
    if predictable_mask.any():
        preds_df = _predict_dates(eval_standard[predictable_mask], _GLOBAL_MODEL_CACHE)
        
        # Merge back to original structure
        df_eval["forecast_actual_date"] = pd.NaT 
        df_eval["final_used_date"] = pd.NaT
        df_eval["final_delay_days"] = np.nan
        df_eval["delay_status_string"] = "Unknown"
        
        # Map back to matching rows
        df_eval.loc[predictable_mask, "forecast_actual_date"] = preds_df["forecast_actual_date"]
        df_eval.loc[predictable_mask, "final_used_date"] = preds_df["final_used_date"]
        df_eval.loc[predictable_mask, "final_delay_days"] = preds_df["final_delay_days"]
        df_eval.loc[predictable_mask, "delay_status_string"] = preds_df["delay_status_string"]
    else:
        # None have planned dates to predict against
        df_eval["forecast_actual_date"] = pd.NaT
        df_eval["final_used_date"] = _coerce_datetime_series(df_eval[actual_col]) if actual_col else pd.NaT
        df_eval["final_delay_days"] = np.nan
        df_eval["delay_status_string"] = "Missing Planned Date"

    return df_eval

def _build_scurve(df: pd.DataFrame) -> pd.DataFrame:
    planned = _coerce_datetime_series(df["planned_ref_date"])
    actual = _coerce_datetime_series(df["actual_date"])
    final_used = _coerce_datetime_series(df["final_used_date"])

    all_dates = pd.concat([planned, actual, final_used], axis=0).dropna()
    if all_dates.empty:
        raise ValueError("No date values available for S-curve construction.")

    start_month = all_dates.min().to_period("M").to_timestamp()
    end_month = all_dates.max().to_period("M").to_timestamp()
    month_index = pd.date_range(start=start_month, end=end_month, freq="MS")

    total = max(len(df), 1)

    rows = []
    for m in month_index:
        month_end = (m + pd.offsets.MonthEnd(1)).normalize()
        planned_cum = int((planned <= month_end).sum())
        actual_cum = int((actual <= month_end).sum())
        forecast_cum = int((final_used <= month_end).sum())

        rows.append(
            {
                "month": m.strftime("%b-%Y"),
                "month_start": m,
                "planned_cumulative": planned_cum,
                "actual_cumulative": actual_cum,
                "forecast_cumulative": forecast_cum,
                "planned_cumulative_pct": round((planned_cum / total) * 100, 2),
                "actual_cumulative_pct": round((actual_cum / total) * 100, 2),
                "forecast_cumulative_pct": round((forecast_cum / total) * 100, 2),
            }
        )

    return pd.DataFrame(rows)


def _save_scurve_chart(scurve_df: pd.DataFrame, chart_path: Path, title: str) -> None:
    fig, ax = plt.subplots(figsize=(12, 6))

    x = scurve_df["month"]
    ax.plot(x, scurve_df["planned_cumulative_pct"], marker="o", linewidth=2.0, label="Planned (LP/EP)")
    ax.plot(x, scurve_df["actual_cumulative_pct"], marker="o", linewidth=2.0, label="Actual (Observed)")
    ax.plot(
        x,
        scurve_df["forecast_cumulative_pct"],
        marker="o",
        linewidth=2.0,
        linestyle="--",
        label="Actual + ML Forecast",
    )

    ax.set_title(title)
    ax.set_xlabel("Month")
    ax.set_ylabel("Cumulative Progress (%)")
    ax.set_ylim(0, 105)
    ax.grid(alpha=0.3)
    ax.legend()

    # Keep x-axis readable for long timeline
    tick_step = max(1, int(len(x) / 12))
    ax.set_xticks(range(0, len(x), tick_step))
    ax.set_xticklabels([x.iloc[i] for i in range(0, len(x), tick_step)], rotation=45, ha="right")

    plt.tight_layout()
    chart_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(chart_path, dpi=180)
    plt.close(fig)


def _save_excel_output(
    out_df: pd.DataFrame,
    scurve_df: pd.DataFrame,
    model_artifacts: ModelArtifacts,
    output_excel_path: Path,
) -> None:
    output_excel_path.parent.mkdir(parents=True, exist_ok=True)

    comparison = out_df[
        [
            "source_file",
            "source_sheet",
            "activity_id",
            "activity_name",
            "stage_gate",
            "planned_start_date",
            "planned_end_date",
            "planned_ref_date",
            "actual_date",
            "forecast_actual_date",
            "final_used_date",
            "actual_delay_days",
            "forecast_delay_days",
            "final_delay_days",
            "record_type",
        ]
    ].copy()

    for col in [
        "planned_start_date",
        "planned_end_date",
        "planned_ref_date",
        "actual_date",
        "forecast_actual_date",
        "final_used_date",
    ]:
        comparison[col] = _coerce_datetime_series(comparison[col]).dt.date

    metrics_df = pd.DataFrame(
        {
            "metric": [
                "train_rows",
                "holdout_mae_days",
                "holdout_r2",
                "feature_count",
                "total_rows_scored",
                "rows_forecasted",
                "rows_observed",
            ],
            "value": [
                model_artifacts.metrics.get("train_rows"),
                model_artifacts.metrics.get("mae_days"),
                model_artifacts.metrics.get("r2"),
                len(model_artifacts.feature_columns),
                len(out_df),
                int((out_df["record_type"] == "Forecasted").sum()),
                int((out_df["record_type"] == "Observed").sum()),
            ],
        }
    )

    with pd.ExcelWriter(output_excel_path, engine="openpyxl") as writer:
        comparison.to_excel(writer, sheet_name="Forecast Comparison", index=False)
        scurve_df.to_excel(writer, sheet_name="S-Curve Data", index=False)
        metrics_df.to_excel(writer, sheet_name="Model Summary", index=False)


def run_forecast(
    source: Optional[Path],
    output_excel: Path,
    output_chart: Path,
    chart_title: str,
    explicit_files: Optional[List[Path]] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Optional[float]]]:
    data = _load_activity_data(source, explicit_files=explicit_files)
    model_artifacts = _train_model(data)
    scored = _predict_dates(data, model_artifacts)
    scurve = _build_scurve(scored)

    _save_excel_output(scored, scurve, model_artifacts, output_excel)
    _save_scurve_chart(scurve, output_chart, title=chart_title)

    return scored, scurve, model_artifacts.metrics


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Standalone ML forecasting for Actual Date + S-curve comparison."
    )
    parser.add_argument(
        "--source",
        required=False,
        help=(
            "Excel file path OR folder path containing tracker/output Excel files. "
            "If omitted, script auto-loads processed files from database/history.json "
            "and falls back to ./outputs."
        ),
    )
    parser.add_argument(
        "--max-history-files",
        type=int,
        default=800,
        help="Maximum number of processed output files to load from history DB when --source is omitted.",
    )
    parser.add_argument(
        "--output-excel",
        default="outputs/ml_forecast/actual_date_forecast_output.xlsx",
        help="Path for generated comparison workbook.",
    )
    parser.add_argument(
        "--output-chart",
        default="outputs/ml_forecast/actual_date_scurve.png",
        help="Path for generated S-curve PNG chart.",
    )
    parser.add_argument(
        "--chart-title",
        default="Actual Date Forecast S-Curve",
        help="Chart title for S-curve plot.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    project_root = Path(__file__).resolve().parent
    explicit_files: Optional[List[Path]] = None

    if args.source:
        source = Path(args.source).expanduser().resolve()
        source_label = str(source)
    else:
        history_files = _files_from_history_db(project_root, max_files=max(1, int(args.max_history_files)))
        if history_files:
            source = None
            explicit_files = history_files
            source_label = f"database/history.json ({len(history_files)} processed file(s))"
        else:
            source = (project_root / "outputs").resolve()
            source_label = f"{source} (fallback: outputs folder)"

    output_excel = Path(args.output_excel).expanduser().resolve()
    output_chart = Path(args.output_chart).expanduser().resolve()

    if source is not None and not source.exists():
        raise FileNotFoundError(f"Source path not found: {source}")

    scored, scurve, metrics = run_forecast(
        source=source,
        output_excel=output_excel,
        output_chart=output_chart,
        chart_title=args.chart_title,
        explicit_files=explicit_files,
    )

    print("=" * 72)
    print("ML ACTUAL DATE FORECASTING COMPLETE")
    print("=" * 72)
    print(f"Source path          : {source_label}")
    print(f"Rows scored          : {len(scored):,}")
    print(f"Observed rows        : {(scored['record_type'] == 'Observed').sum():,}")
    print(f"Forecasted rows      : {(scored['record_type'] == 'Forecasted').sum():,}")
    print(f"S-curve months       : {len(scurve):,}")
    print(f"Output workbook      : {output_excel}")
    print(f"S-curve chart (PNG)  : {output_chart}")

    mae = metrics.get("mae_days")
    r2 = metrics.get("r2")
    if mae is not None:
        print(f"Model holdout MAE    : {mae:.2f} days")
    else:
        print("Model holdout MAE    : N/A (dataset too small for holdout split)")
    if r2 is not None:
        print(f"Model holdout R²     : {r2:.4f}")
    else:
        print("Model holdout R²     : N/A (dataset too small for holdout split)")
    print("=" * 72)


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
