"""
FastAPI application entry point.

Serves the read-only experiment data API and the frontend static files.
Start with:
    uvicorn new_energy_sys.api.main:app --reload --port 8000
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

_FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"

if _FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def serve_frontend(full_path: str):
        """Serve the Vue SPA — fallback to index.html for client-side routing."""
        file_path = _FRONTEND_DIST / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(_FRONTEND_DIST / "index.html"))
