"""
Async task runner for triggering CLI commands from the frontend.

Runs Stage CLI commands in background subprocesses so the frontend
can poll for status without blocking the API server.
"""

from __future__ import annotations

import subprocess
import threading
import time
import uuid
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .data_loader import project_root

# ---------------------------------------------------------------------------
# Task registry
# ---------------------------------------------------------------------------

@dataclass
class TaskRecord:
    task_id: str
    command: str
    status: str = "pending"        # pending | running | completed | failed
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    return_code: Optional[int] = None
    stdout_tail: str = ""          # last 2000 chars
    stderr_tail: str = ""
    created_at: float = field(default_factory=time.time)


_tasks: dict[str, TaskRecord] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Available commands
# ---------------------------------------------------------------------------

_PYTHON = sys.executable  # Use the same interpreter running the API

_COMMAND_TEMPLATES: dict[str, list[str]] = {
    "train_baseline": [
        _PYTHON, "-m", "new_energy_sys.cli.train_baseline",
        "--config", "configs/data_sources.pvdaq_nsrdb_2020_2022.json",
        "--input", "data/processed/pvdaq_nsrdb_2020_2022/stage3_feature_dataset.parquet",
    ],
    "compare_tabular": [
        _PYTHON, "-m", "new_energy_sys.cli.compare_tabular_models",
        "--config", "configs/data_sources.pvdaq_nsrdb_2020_2022.json",
        "--input", "data/processed/pvdaq_nsrdb_2020_2022/stage3_feature_dataset.parquet",
    ],
    "run_inference": [
        _PYTHON, "-m", "new_energy_sys.cli.run_stage9_inference",
        "--config", "configs/data_sources.pvdaq_nsrdb_2020_2022.json",
        "--input", "data/processed/pvdaq_nsrdb_2020_2022/stage3_feature_dataset.parquet",
    ],
    "run_dispatch": [
        _PYTHON, "-m", "new_energy_sys.cli.run_stage10_dispatch",
        "--config", "configs/data_sources.pvdaq_nsrdb_2020_2022.json",
        "--predictions", "data/processed/pvdaq_nsrdb_2020_2022/stage9_main_model_predictions.csv",
        "--feature-input", "data/processed/pvdaq_nsrdb_2020_2022/stage3_feature_dataset.parquet",
    ],
    "run_strategy": [
        _PYTHON, "-m", "new_energy_sys.cli.run_stage11_strategy",
        "--config", "configs/data_sources.pvdaq_nsrdb_2020_2022.json",
        "--predictions", "data/processed/pvdaq_nsrdb_2020_2022/stage9_main_model_predictions.csv",
        "--feature-input", "data/processed/pvdaq_nsrdb_2020_2022/stage3_feature_dataset.parquet",
    ],
    "run_rolling": [
        _PYTHON, "-m", "new_energy_sys.cli.run_stage12_rolling",
        "--config", "configs/data_sources.pvdaq_nsrdb_2020_2022.json",
        "--predictions", "data/processed/pvdaq_nsrdb_2020_2022/stage9_main_model_predictions.csv",
        "--feature-input", "data/processed/pvdaq_nsrdb_2020_2022/stage3_feature_dataset.parquet",
    ],
    "run_governance": [
        _PYTHON, "-m", "new_energy_sys.cli.run_stage13_governance",
        "--config", "configs/data_sources.pvdaq_nsrdb_2020_2022.json",
    ],
    "run_sensitivity": [
        _PYTHON, "-m", "new_energy_sys.cli.run_stage15_sensitivity",
        "--config", "configs/data_sources.pvdaq_nsrdb_2020_2022.json",
        "--predictions", "data/processed/pvdaq_nsrdb_2020_2022/stage9_main_model_predictions.csv",
        "--feature-input", "data/processed/pvdaq_nsrdb_2020_2022/stage3_feature_dataset.parquet",
    ],
}


def list_available_commands() -> list[dict]:
    """Return the list of commands that can be triggered."""
    labels = {
        "train_baseline": "LightGBM 基线训练 (Stage 4)",
        "compare_tabular": "表格模型横向对比 (Stage 8)",
        "run_inference": "主模型推理 (Stage 9)",
        "run_dispatch": "储能调度仿真 (Stage 10)",
        "run_strategy": "策略敏感性分析 (Stage 11)",
        "run_rolling": "滚动优化调度 (Stage 12)",
        "run_governance": "策略治理报告 (Stage 13)",
        "run_sensitivity": "储能配置敏感性 (Stage 15)",
    }
    return [{"command_id": k, "label": v} for k, v in labels.items()]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _run_in_background(task: TaskRecord, cmd: list[str], cwd: Path):
    """Thread target that runs a subprocess."""
    task.status = "running"
    task.started_at = time.time()
    try:
        env = {"PYTHONPATH": str(cwd / "src")}
        import os as _os
        full_env = {**_os.environ, **env}

        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=600,  # 10 min max
            env=full_env,
        )
        task.return_code = result.returncode
        task.stdout_tail = result.stdout[-2000:] if result.stdout else ""
        task.stderr_tail = result.stderr[-2000:] if result.stderr else ""
        task.status = "completed" if result.returncode == 0 else "failed"
    except subprocess.TimeoutExpired:
        task.status = "failed"
        task.stderr_tail = "Task timed out after 600 seconds."
    except Exception as exc:
        task.status = "failed"
        task.stderr_tail = str(exc)
    finally:
        task.finished_at = time.time()


def submit_task(command_id: str) -> Optional[TaskRecord]:
    """Submit a new background task.  Returns the TaskRecord or None if invalid."""
    cmd = _COMMAND_TEMPLATES.get(command_id)
    if cmd is None:
        return None

    task_id = uuid.uuid4().hex[:12]
    task = TaskRecord(task_id=task_id, command=command_id)

    with _lock:
        _tasks[task_id] = task

    t = threading.Thread(
        target=_run_in_background,
        args=(task, cmd, project_root()),
        daemon=True,
    )
    t.start()
    return task


def get_task(task_id: str) -> Optional[TaskRecord]:
    with _lock:
        return _tasks.get(task_id)


def list_tasks(limit: int = 20) -> list[TaskRecord]:
    with _lock:
        items = sorted(_tasks.values(), key=lambda t: t.created_at, reverse=True)
        return items[:limit]
