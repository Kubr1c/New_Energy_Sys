"""
FastAPI application entry point.

Serves the read-only experiment data API and the frontend static files.
Start with:
    uvicorn backend.app.main:app --reload --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI, Depends, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from pathlib import Path
from typing import Optional
import dataclasses
import os

import numpy as np
import pandas as pd

from . import auth, data_loader, tasks

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="New Energy Sys — Visualization API",
    version="1.0.0",
    description="Read-only API for the PV forecasting & storage dispatch experiment results.",
)

def _get_cors_origins() -> list[str]:
    """Read CORS whitelist from env and reject wildcard credentials in production."""
    app_env = os.environ.get("NES_APP_ENV", os.environ.get("APP_ENV", "development")).lower()
    raw_origins = os.environ.get("NES_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    origins = [item.strip() for item in raw_origins.split(",") if item.strip()]
    if app_env in {"production", "prod"} and ("*" in origins or not origins):
        raise RuntimeError("NES_CORS_ORIGINS must be an explicit whitelist in production.")
    return origins


app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str


def get_current_user(authorization: Optional[str] = Header(None)) -> auth.UserInfo:
    """Dependency: extract and verify JWT from Authorization header."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid authorization scheme")
    user = auth.verify_token(token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@app.post("/api/auth/login")
def login(req: LoginRequest):
    token = auth.authenticate(req.username, req.password)
    if token is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    user = auth.verify_token(token)
    return {
        "token": token,
        "user": {
            "username": user.username,
            "role": user.role,
            "display_name": user.display_name,
        },
    }


@app.get("/api/auth/me")
def me(user: auth.UserInfo = Depends(get_current_user)):
    return {
        "username": user.username,
        "role": user.role,
        "display_name": user.display_name,
    }


# ---------------------------------------------------------------------------
# Data endpoints (read-only)
# ---------------------------------------------------------------------------

@app.get("/api/config")
def get_config(user: auth.UserInfo = Depends(get_current_user)):
    return data_loader.get_site_config()


@app.get("/api/models/tabular")
def get_tabular_metrics(user: auth.UserInfo = Depends(get_current_user)):
    return data_loader.get_tabular_model_metrics()


@app.get("/api/models/deep-learning")
def get_dl_metrics(user: auth.UserInfo = Depends(get_current_user)):
    return data_loader.get_deep_learning_metrics()


@app.get("/api/models/tcn")
def get_tcn_metrics(user: auth.UserInfo = Depends(get_current_user)):
    return data_loader.get_tcn_metrics()


@app.get("/api/models/main")
def get_main_metrics(user: auth.UserInfo = Depends(get_current_user)):
    return data_loader.get_main_model_metrics()


@app.get("/api/predictions/main")
def get_predictions(
    limit: int = 2000,
    offset: int = 0,
    user: auth.UserInfo = Depends(get_current_user),
):
    return data_loader.get_main_model_predictions(limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# Inspection / prediction inspection endpoints (Stage 26+)
# ---------------------------------------------------------------------------

_INSPECTION_CAPACITY_KW = 1.12  # PV system capacity for the inspected dataset


@app.get("/api/predictions/metadata")
async def inspection_metadata(user: auth.UserInfo = Depends(get_current_user)):
    """Return metadata about the inspection predictions dataset.

    Reads directly from the parquet to avoid hardcoding values.
    """
    df = data_loader.load_inspection_parquet()

    # Build experiment descriptors from first row of each group
    experiments = []
    for exp_id in df["experiment"].unique():
        exp_df = df[df["experiment"] == exp_id].iloc[0]
        experiments.append({
            "id": str(exp_id),
            "model_name": str(exp_df["model_name"]),
            "version": str(exp_df["model_version"]),
            "feature_set": str(exp_df["feature_set"]),
            "target_type": str(exp_df["target_type"]),
        })

    scenarios: list[str] = sorted(df["scenario"].unique().tolist())
    horizons: list[int] = sorted(df["horizon_hours"].unique().tolist())

    return {
        "date_min": str(df["valid_time"].min().date()),
        "date_max": str(df["valid_time"].max().date()),
        "horizons": horizons,
        "experiments": experiments,
        "scenarios": scenarios,
        "capacity_kw": _INSPECTION_CAPACITY_KW,
        "baselines": {
            "persistence_origin": "prediction = pv_power at origin_time",
            "persistence_same_hour_yesterday": "prediction = pv_power at valid_time - 24h",
        },
    }


@app.get("/api/predictions/inspect")
async def inspection_inspect(
    start: str,
    end: str,
    horizons: str | None = None,
    experiments: str | None = None,
    granularity: str = "hour",
    user: auth.UserInfo = Depends(get_current_user),
):
    """Range query on valid_time with optional filters.

    Parameters
    ----------
    start : str
        ISO date string, inclusive lower bound, e.g. ``"2022-09-20"``.
    end : str
        ISO date string, exclusive upper bound, e.g. ``"2022-09-23"``.
    horizons : str, optional
        Comma-separated horizon hours, e.g. ``"1,6,24"``.  Default all.
    experiments : str, optional
        Comma-separated experiment ids, e.g. ``"stage5,e1"``.  Default all.
    granularity : str
        ``"hour"`` (default) or ``"day"`` – controls whether raw hourly data
        or a daily roll-up is returned in the ``data`` field.

    Returns
    -------
    dict
        ``{"data": [...], "daily_summary": {...}}``
    """
    df = data_loader.load_inspection_parquet()

    # --- Half-open interval filter on valid_time ---
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    mask = (df["valid_time"] >= start_ts) & (df["valid_time"] < end_ts)
    result = df[mask].copy()

    if result.empty:
        return {"data": [], "daily_summary": {}}

    # --- Filter by horizons ---
    if horizons:
        h_list = [int(h) for h in horizons.split(",")]
        result = result[result["horizon_hours"].isin(h_list)]

    # --- Filter by experiments ---
    if experiments:
        exp_list = experiments.split(",")
        result = result[result["experiment"].isin(exp_list)]

    if result.empty:
        return {"data": [], "daily_summary": {}}

    # --- Build daily_summary ---
    result["valid_date"] = result["valid_time"].dt.date

    # Overall daily totals (any experiment / horizon – kWh = sum(kW) * 1h)
    daily_actual = result.groupby("valid_date")["actual_kw"].sum()

    daily_summary: dict[str, dict] = {}

    for date_val in sorted(result["valid_date"].unique()):
        date_str = str(date_val)
        day_df = result[result["valid_date"] == date_val]

        # Overall daily actual energy (kWh)
        daily_actual_kwh = round(float(day_df["actual_kw"].sum()), 3)

        # Per-experiment / per-horizon metrics
        by_experiment: dict[str, dict] = {}
        for exp_id in day_df["experiment"].unique():
            exp_df = day_df[day_df["experiment"] == exp_id]
            by_horizon: dict[str, dict] = {}
            for hor in sorted(exp_df["horizon_hours"].unique()):
                hor_df = exp_df[exp_df["horizon_hours"] == hor]
                n = len(hor_df)
                if n == 0:
                    continue
                daily_pred_kwh = round(float(hor_df["prediction_kw"].sum()), 3)
                daily_error_kwh = round(daily_pred_kwh - daily_actual_kwh, 3)
                rmse_val = (hor_df["error_kw"] ** 2).mean()
                rmse_kw = round(float(rmse_val) ** 0.5, 4) if pd.notna(rmse_val) and rmse_val >= 0 else None
                mae_val = hor_df["abs_error_kw"].mean()
                mae_kw = round(float(mae_val), 4) if pd.notna(mae_val) else None
                bias_val = hor_df["error_kw"].mean()
                bias_kw = round(float(bias_val), 4) if pd.notna(bias_val) else None
                by_horizon[str(hor)] = {
                    "daily_pred_kwh": daily_pred_kwh,
                    "daily_error_kwh": daily_error_kwh,
                    "rmse_kw": rmse_kw,
                    "mae_kw": mae_kw,
                    "bias_kw": bias_kw,
                }
            by_experiment[str(exp_id)] = by_horizon

        # Dominant daytime scenario (exclude night)
        daytime = day_df[day_df["scenario"] != "night"]
        if not daytime.empty:
            dominant_scenario = daytime["scenario"].mode().iloc[0]
        else:
            dominant_scenario = "night"

        daily_summary[date_str] = {
            "daily_actual_kwh": daily_actual_kwh,
            "scenario_dominant": str(dominant_scenario),
            "experiments": by_experiment,
        }

    # --- Build hourly data payload ---
    if granularity == "day":
        # Return daily roll-up rows instead of raw hourly rows
        data = []
        for date_str, summary in sorted(daily_summary.items()):
            date_df = result[result["valid_date"] == pd.Timestamp(date_str).date()]
            # One row per date with overall aggregate
            data.append({
                "valid_date": date_str,
                "daily_actual_kwh": summary["daily_actual_kwh"],
                "scenario_dominant": summary["scenario_dominant"],
                "n_hours": len(date_df),
            })
    else:
        # Raw hourly data — clean NaNs before JSON serialization.
        # NOTE: pandas 2.3+ does NOT convert NaN->None via .where(cond, None)
        # on float64 columns.  Use explicit replace instead.
        result_no_date = result.drop(columns=["valid_date"])
        result_clean = result_no_date.replace({np.nan: None})
        data = result_clean.to_dict(orient="records")
        for row in data:
            # Convert timestamp columns to ISO strings
            if isinstance(row.get("origin_time"), pd.Timestamp):
                row["origin_time"] = row["origin_time"].isoformat()
            if isinstance(row.get("valid_time"), pd.Timestamp):
                row["valid_time"] = row["valid_time"].isoformat()

    return {"data": data, "daily_summary": daily_summary}


@app.get("/api/dispatch/metrics/{stage}")
def get_dispatch_metrics(
    stage: str,
    user: auth.UserInfo = Depends(get_current_user),
):
    return data_loader.get_dispatch_metrics(stage)


@app.get("/api/governance/scorecard")
def get_governance(user: auth.UserInfo = Depends(get_current_user)):
    return data_loader.get_governance_scorecard()


@app.get("/api/sensitivity/metrics")
def get_sensitivity(user: auth.UserInfo = Depends(get_current_user)):
    return data_loader.get_sensitivity_metrics()


@app.get("/api/rawhide/report")
def get_rawhide_report(user: auth.UserInfo = Depends(get_current_user)):
    data = data_loader.get_rawhide_report()
    if data is None:
        raise HTTPException(status_code=404, detail="No S18 Rawhide simulation report")
    return data


@app.get("/api/rawhide/dispatch-metrics")
def get_rawhide_dispatch_metrics(user: auth.UserInfo = Depends(get_current_user)):
    return data_loader.get_rawhide_dispatch_metrics()


@app.get("/api/rawhide/sensitivity-metrics")
def get_rawhide_sensitivity_metrics(user: auth.UserInfo = Depends(get_current_user)):
    return data_loader.get_rawhide_sensitivity_metrics()


@app.get("/api/rawhide/degradation-metrics")
def get_rawhide_degradation_metrics(user: auth.UserInfo = Depends(get_current_user)):
    return data_loader.get_rawhide_degradation_metrics()


@app.get("/api/features/importance")
def get_features(
    top_n: int = 30,
    user: auth.UserInfo = Depends(get_current_user),
):
    return data_loader.get_feature_importance(top_n)


@app.get("/api/data/quality")
def get_quality(user: auth.UserInfo = Depends(get_current_user)):
    return data_loader.get_data_quality_report()


@app.get("/api/data/features")
def get_feature_report(user: auth.UserInfo = Depends(get_current_user)):
    return data_loader.get_feature_report()


@app.get("/api/reports/list")
def list_reports(user: auth.UserInfo = Depends(get_current_user)):
    return data_loader.list_available_stages()


@app.get("/api/reports/{stage}/json")
def get_report_json(
    stage: str,
    user: auth.UserInfo = Depends(get_current_user),
):
    data = data_loader.get_stage_report_json(stage)
    if data is None:
        raise HTTPException(status_code=404, detail=f"No JSON report for {stage}")
    return data


@app.get("/api/reports/{stage}/md")
def get_report_md(
    stage: str,
    user: auth.UserInfo = Depends(get_current_user),
):
    content = data_loader.get_stage_report_md(stage)
    if content is None:
        raise HTTPException(status_code=404, detail=f"No Markdown report for {stage}")
    return {"content": content}


# ---------------------------------------------------------------------------
# Task endpoints (trigger CLI commands)
# ---------------------------------------------------------------------------

@app.get("/api/tasks/commands")
def list_commands(user: auth.UserInfo = Depends(get_current_user)):
    return tasks.list_available_commands()


class TaskSubmitRequest(BaseModel):
    command_id: str


@app.post("/api/tasks/submit")
def submit_task(
    req: TaskSubmitRequest,
    user: auth.UserInfo = Depends(get_current_user),
):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可执行任务")
    task = tasks.submit_task(req.command_id)
    if task is None:
        raise HTTPException(status_code=400, detail=f"Unknown command: {req.command_id}")
    return dataclasses.asdict(task)


@app.get("/api/tasks/{task_id}")
def get_task_status(
    task_id: str,
    user: auth.UserInfo = Depends(get_current_user),
):
    task = tasks.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return dataclasses.asdict(task)


@app.get("/api/tasks")
def list_tasks_endpoint(
    limit: int = 20,
    user: auth.UserInfo = Depends(get_current_user),
):
    return [dataclasses.asdict(t) for t in tasks.list_tasks(limit)]


# ---------------------------------------------------------------------------
# Serve frontend static files (production)
# ---------------------------------------------------------------------------

_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

if _FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def serve_frontend(full_path: str):
        """Serve the Vue SPA — fallback to index.html for client-side routing."""
        file_path = _FRONTEND_DIST / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(_FRONTEND_DIST / "index.html"))
