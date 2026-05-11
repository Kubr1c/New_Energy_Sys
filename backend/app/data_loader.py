"""
Read-only data loader for experiment artifacts.

Reads CSV / JSON / Parquet files produced by Stage 1–15 and caches them
in memory so the FastAPI endpoints can serve them without repeated disk I/O.
"""

from __future__ import annotations

import json
import functools
import math
from pathlib import Path
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[2]  # -> C:\Project\New_Energy_Sys
_DATA_DIR = _PROJECT_ROOT / "data" / "processed" / "pvdaq_nsrdb_2020_2022"
_CONFIG_DIR = _PROJECT_ROOT / "configs"
_REPORTS_DIR = _PROJECT_ROOT / "reports"


def project_root() -> Path:
    return _PROJECT_ROOT


def data_dir() -> Path:
    return _DATA_DIR


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def _json_safe_scalar(value: Any) -> Any:
    """Normalize pandas/numpy scalar values before FastAPI JSON serialization."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _dataframe_to_json_records(df: pd.DataFrame) -> list[dict]:
    """Return records with no NaN/Inf values.

    Pandas keeps float columns as float dtype even after ``where(..., None)``,
    so NaN can survive and crash Starlette's strict JSON renderer. Converting
    row values explicitly keeps all API endpoints JSON-compliant.
    """
    return [
        {str(key): _json_safe_scalar(value) for key, value in record.items()}
        for record in df.to_dict(orient="records")
    ]


@functools.lru_cache(maxsize=64)
def read_csv_cached(path: str) -> list[dict]:
    """Read a CSV file and return a list of row dicts.  Cached."""
    p = Path(path)
    if not p.exists():
        return []
    df = pd.read_csv(p)
    return _dataframe_to_json_records(df)


@functools.lru_cache(maxsize=32)
def read_json_cached(path: str) -> Any:
    """Read a JSON file.  Cached."""
    p = Path(path)
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


@functools.lru_cache(maxsize=8)
def read_parquet_sample(path: str, n: int = 500) -> list[dict]:
    """Read the first *n* rows of a Parquet file."""
    p = Path(path)
    if not p.exists():
        return []
    df = pd.read_parquet(p).head(n)
    # Convert timestamps to ISO strings
    for col in df.select_dtypes(include=["datetime64", "datetimetz"]).columns:
        df[col] = df[col].astype(str)
    return _dataframe_to_json_records(df)


@functools.lru_cache(maxsize=16)
def read_markdown_cached(path: str) -> str | None:
    """Read a Markdown file and return its content."""
    p = Path(path)
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


@functools.lru_cache(maxsize=16)
def read_stage21_csv_required(path: str) -> list[dict] | None:
    """Read a Stage21 CSV artifact or return ``None`` when it is missing.

    Most legacy endpoints return an empty list for missing files because the
    early dashboard treated absent experiments as optional.  Stage21 is a
    named page-level feature, so a missing artifact must remain distinguishable
    from a valid-but-empty result.  The API layer converts ``None`` to an
    explicit HTTP 404.
    """
    p = Path(path)
    if not p.exists():
        return None
    df = pd.read_csv(p)
    return _dataframe_to_json_records(df)


# ---------------------------------------------------------------------------
# Specific loaders
# ---------------------------------------------------------------------------

def get_site_config() -> dict:
    path = _CONFIG_DIR / "data_sources.pvdaq_nsrdb_2020_2022.json"
    return read_json_cached(str(path)) or {}


def get_tabular_model_metrics() -> list[dict]:
    return read_csv_cached(str(_DATA_DIR / "stage8_tabular_model_metrics.csv"))


def get_deep_learning_metrics() -> list[dict]:
    return read_csv_cached(str(_DATA_DIR / "stage14_deep_learning_metrics.csv"))


def get_tcn_metrics() -> list[dict]:
    return read_csv_cached(str(_DATA_DIR / "stage6_tcn_metrics.csv"))


def get_main_model_predictions(limit: int = 2000, offset: int = 0) -> list[dict]:
    path = str(_DATA_DIR / "stage9_main_model_predictions.csv")
    p = Path(path)
    if not p.exists():
        return []
    df = pd.read_csv(p, skiprows=range(1, offset + 1) if offset else None, nrows=limit)
    return _dataframe_to_json_records(df)


def get_main_model_metrics() -> list[dict]:
    return read_csv_cached(str(_DATA_DIR / "stage9_main_model_metrics.csv"))


def get_dispatch_metrics(stage: str = "stage10") -> list[dict]:
    mapping = {
        "stage10": "stage10_storage_dispatch_metrics.csv",
        "stage11": "stage11_storage_strategy_sensitivity_metrics.csv",
        "stage12": "stage12_storage_rolling_optimization_metrics.csv",
    }
    filename = mapping.get(stage)
    if not filename:
        return []
    return read_csv_cached(str(_DATA_DIR / filename))


def get_governance_scorecard() -> list[dict]:
    return read_csv_cached(str(_DATA_DIR / "stage13_storage_strategy_governance_scorecard.csv"))


def get_sensitivity_metrics() -> list[dict]:
    return read_csv_cached(str(_DATA_DIR / "stage15_storage_configuration_sensitivity_metrics.csv"))


def get_rawhide_report() -> dict | None:
    """Return the compact S18 Rawhide report.

    The report JSON is intentionally used as the summary payload because it
    already contains the public reference-site metadata, scaling audit fields,
    recommended Pareto configuration, degradation summary, and quality gates.
    Large hourly replay files stay on disk and are not exposed through the UI.
    """
    return read_json_cached(str(_DATA_DIR / "stage18_rawhide_simulation_report.json"))


def get_rawhide_dispatch_metrics() -> list[dict]:
    """Return scenario-level S18 dispatch metrics for frontend charts."""
    return read_csv_cached(str(_DATA_DIR / "stage18_rawhide_dispatch_metrics.csv"))


def get_rawhide_sensitivity_metrics() -> list[dict]:
    """Return S18 Rawhide configuration scan metrics.

    This loader reads only the small metrics file, not the full sensitivity
    replay CSV, so the API remains responsive even when the experiment folder
    contains hundreds of megabytes of hourly results.
    """
    return read_csv_cached(str(_DATA_DIR / "stage18_rawhide_sensitivity_metrics.csv"))


def get_rawhide_degradation_metrics() -> list[dict]:
    """Return S18 degradation summary metrics for net-value presentation."""
    return read_csv_cached(str(_DATA_DIR / "stage18_rawhide_degradation_metrics.csv"))


def get_stage21_report() -> dict | None:
    """Return the Stage21 weather-price dispatch report JSON."""
    return read_json_cached(str(_DATA_DIR / "stage21_rawhide_weather_price_dispatch_report.json"))


def get_stage21_weather_predictions() -> list[dict] | None:
    """Return Stage21 Rawhide weather-driven PV prediction rows."""
    return read_stage21_csv_required(str(_DATA_DIR / "stage21_rawhide_weather_predictions.csv"))


def get_stage21_price_scenarios() -> list[dict] | None:
    """Return Stage21 hourly price scenario rows."""
    return read_stage21_csv_required(str(_DATA_DIR / "stage21_rawhide_price_scenarios.csv"))


def get_stage21_dispatch_results() -> list[dict] | None:
    """Return Stage21 hourly dispatch replay rows for charting."""
    return read_stage21_csv_required(str(_DATA_DIR / "stage21_rawhide_dispatch_results.csv"))


def get_stage21_dispatch_metrics() -> list[dict] | None:
    """Return Stage21 scenario-level dispatch metrics."""
    return read_stage21_csv_required(str(_DATA_DIR / "stage21_rawhide_dispatch_metrics.csv"))


def get_feature_importance(top_n: int = 30) -> list[dict]:
    rows = read_csv_cached(str(_DATA_DIR / "stage4_lightgbm_feature_importance.csv"))
    if not rows:
        return []
    # Sort by importance descending and take top N
    sorted_rows = sorted(rows, key=lambda r: r.get("importance", 0) or 0, reverse=True)
    return sorted_rows[:top_n]


def get_stage_report_json(stage: str) -> dict | None:
    candidates = list(_DATA_DIR.glob(f"{stage}*_report.json"))
    if not candidates:
        return None
    return read_json_cached(str(candidates[0]))


def get_stage_report_md(stage: str) -> str | None:
    candidates = list(_DATA_DIR.glob(f"{stage}*_report.md"))
    if not candidates:
        # Also check reports/ directory
        candidates = list(_REPORTS_DIR.glob(f"{stage}*_report.md"))
    if not candidates:
        return None
    return read_markdown_cached(str(candidates[0]))


def get_data_quality_report() -> dict | None:
    return read_json_cached(str(_DATA_DIR / "stage2_quality_report.json"))


def get_feature_report() -> dict | None:
    return read_json_cached(str(_DATA_DIR / "stage3_feature_report.json"))


# ---------------------------------------------------------------------------
# Inspection predictions loader (Stage 26+)
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def load_inspection_parquet() -> pd.DataFrame:
    """Load precomputed inspection predictions parquet.  Cached once.

    Returns
    -------
    pd.DataFrame
        Columns: origin_time, valid_time, horizon_hours, experiment,
        model_name, model_version, feature_set, target_type,
        raw_prediction_kw, prediction_kw, actual_kw,
        persistence_origin_kw, persistence_same_hour_yesterday_kw,
        error_kw, abs_error_kw,
        ghi_wm2, clearsky_ghi_wm2, solar_elevation_deg, cloud_cover_pct,
        scenario, split.
    """
    path = _DATA_DIR / "inspection_predictions.parquet"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


def list_available_stages() -> list[dict]:
    """List all stages that have report files."""
    stages = []
    for json_file in sorted(_DATA_DIR.glob("stage*_report.json")):
        name = json_file.stem.replace("_report", "")
        # Extract stage number
        stage_id = name.split("_")[0]  # e.g. "stage4"
        stages.append({
            "stage_id": stage_id,
            "name": name,
            "has_json": True,
            "has_md": (_DATA_DIR / f"{name}.md").exists()
                      or any(_DATA_DIR.glob(f"{stage_id}*_report.md")),
        })
    return stages
