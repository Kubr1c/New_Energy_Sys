"""Import frontend display artifacts into the SQLAlchemy display schema."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from sqlalchemy import delete
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from . import db_models as m


REQUIRED_DISPLAY_FILES = (
    "stage9_main_model_predictions.csv",
    "inspection_predictions.parquet",
    "stage21_rawhide_dispatch_results.csv",
)


@dataclass
class ImportSummary:
    """Small structured return value used by the CLI and tests."""

    rows_by_artifact: dict[str, int] = field(default_factory=dict)
    registered_artifacts: int = 0

    @property
    def total_rows(self) -> int:
        return sum(self.rows_by_artifact.values())


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _relative(path: Path, project_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def _json_safe(value: Any) -> Any:
    """Convert pandas/numpy scalar values into MySQL JSON-compatible values."""
    if value is None:
        return None
    if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
        return None
    if isinstance(value, (np.floating,)):
        val = float(value)
        return None if np.isnan(val) or np.isinf(val) else val
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if pd.isna(value):
        return None
    return value


def _payload_from_record(record: dict[str, Any]) -> dict[str, Any]:
    return {str(k): _json_safe(v) for k, v in record.items()}


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return _read_csv(path)


def _parse_dt(record: dict[str, Any], *columns: str) -> datetime | None:
    for col in columns:
        value = record.get(col)
        if value is None or pd.isna(value):
            continue
        return pd.to_datetime(value, utc=True).to_pydatetime()
    return None


def _require_core_files(source_dir: Path) -> None:
    missing = [name for name in REQUIRED_DISPLAY_FILES if not (source_dir / name).exists()]
    if missing:
        joined = ", ".join(missing)
        raise FileNotFoundError(f"Missing required display artifact(s): {joined}")


def _clear_display_tables(session: Session) -> None:
    """Delete imported display data in dependency-safe order for --replace."""
    for model in (
        m.Stage21DispatchResult,
        m.Stage21WeatherPrediction,
        m.Stage21PriceScenario,
        m.DispatchMetric,
        m.FeatureImportance,
        m.InspectionPredictionPoint,
        m.PredictionPoint,
        m.ModelMetric,
        m.ReportDocument,
        m.ExperimentStage,
        m.ArtifactDocument,
        m.ArtifactRegistry,
        m.User,
    ):
        session.execute(delete(model))


def _register_artifact(
    session: Session,
    *,
    key: str,
    path: Path,
    project_root: Path,
    artifact_type: str,
    stage_id: str | None = None,
    row_count: int | None = None,
) -> None:
    session.merge(
        m.ArtifactRegistry(
            artifact_key=key,
            stage_id=stage_id,
            artifact_type=artifact_type,
            source_path=_relative(path, project_root),
            row_count=row_count,
            content_sha256=sha256_file(path),
        )
    )


def _register_document(
    session: Session,
    *,
    key: str,
    path: Path,
    project_root: Path,
    document_type: str,
    stage_id: str | None = None,
) -> None:
    text = path.read_text(encoding="utf-8")
    payload: dict | list | None = json.loads(text) if path.suffix.lower() == ".json" else None
    session.merge(
        m.ArtifactDocument(
            document_key=key,
            stage_id=stage_id,
            document_type=document_type,
            source_path=_relative(path, project_root),
            content_text=text if path.suffix.lower() != ".json" else None,
            payload=payload,
            content_sha256=sha256_file(path),
        )
    )
    _register_artifact(
        session,
        key=key,
        path=path,
        project_root=project_root,
        artifact_type=document_type,
        stage_id=stage_id,
    )


def _seed_default_users(session: Session) -> None:
    """Create development users in MySQL; production can replace them later."""
    users = (
        ("admin", "admin123", "admin", "System Admin"),
        ("guest", "guest123", "viewer", "Guest User"),
    )
    for username, password, role, display_name in users:
        session.merge(
            m.User(
                username=username,
                password_hash=hashlib.sha256(password.encode()).hexdigest(),
                role=role,
                display_name=display_name,
                active=True,
            )
        )


def _insert_dataframe_rows(
    session: Session,
    model: type,
    rows: Iterable[dict[str, Any]],
    *,
    chunk_size: int,
) -> int:
    count = 0
    batch = []
    for row in rows:
        batch.append(model(**row))
        if len(batch) >= chunk_size:
            session.add_all(batch)
            session.flush()
            count += len(batch)
            batch = []
    if batch:
        session.add_all(batch)
        session.flush()
        count += len(batch)
    return count


def _import_model_metric_csv(
    session: Session,
    source_dir: Path,
    project_root: Path,
    summary: ImportSummary,
    *,
    filename: str,
    family: str,
    stage_id: str,
    chunk_size: int,
) -> None:
    path = source_dir / filename
    if not path.exists():
        return
    df = _read_csv(path)
    rows = []
    for idx, record in enumerate(df.to_dict(orient="records")):
        payload = _payload_from_record(record)
        rows.append(
            {
                "metric_family": family,
                "stage_id": stage_id,
                "model": payload.get("model") or payload.get("model_name"),
                "feature_set": payload.get("feature_set"),
                "target": payload.get("target"),
                "split": payload.get("split"),
                "row_index": idx,
                "payload": payload,
            }
        )
    count = _insert_dataframe_rows(session, m.ModelMetric, rows, chunk_size=chunk_size)
    summary.rows_by_artifact[filename] = count
    _register_artifact(session, key=filename, path=path, project_root=project_root, artifact_type="model_metric", stage_id=stage_id, row_count=count)


def _import_dispatch_metric_csv(
    session: Session,
    source_dir: Path,
    project_root: Path,
    summary: ImportSummary,
    *,
    filename: str,
    family: str,
    stage_id: str,
    chunk_size: int,
) -> None:
    path = source_dir / filename
    if not path.exists():
        return
    df = _read_csv(path)
    rows = []
    for idx, record in enumerate(df.to_dict(orient="records")):
        payload = _payload_from_record(record)
        rows.append(
            {
                "metric_family": family,
                "stage_id": stage_id,
                "scenario": payload.get("scenario"),
                "price_scenario_id": payload.get("price_scenario_id"),
                "row_index": idx,
                "payload": payload,
            }
        )
    count = _insert_dataframe_rows(session, m.DispatchMetric, rows, chunk_size=chunk_size)
    summary.rows_by_artifact[filename] = count
    _register_artifact(session, key=filename, path=path, project_root=project_root, artifact_type="dispatch_metric", stage_id=stage_id, row_count=count)


def _import_prediction_points(session: Session, source_dir: Path, project_root: Path, summary: ImportSummary, *, chunk_size: int) -> None:
    filename = "stage9_main_model_predictions.csv"
    path = source_dir / filename
    df = _read_csv(path)
    rows = []
    for idx, record in enumerate(df.to_dict(orient="records")):
        payload = _payload_from_record(record)
        rows.append(
            {
                "source_stage": "stage9",
                "row_index": idx,
                "timestamp": _parse_dt(record, "timestamp"),
                "target": payload.get("target"),
                "split": payload.get("split"),
                "payload": payload,
            }
        )
    count = _insert_dataframe_rows(session, m.PredictionPoint, rows, chunk_size=chunk_size)
    summary.rows_by_artifact[filename] = count
    _register_artifact(session, key=filename, path=path, project_root=project_root, artifact_type="prediction_points", stage_id="stage9", row_count=count)


def _import_inspection_points(session: Session, source_dir: Path, project_root: Path, summary: ImportSummary, *, chunk_size: int) -> None:
    filename = "inspection_predictions.parquet"
    path = source_dir / filename
    df = _read_table(path)
    rows = []
    for idx, record in enumerate(df.to_dict(orient="records")):
        payload = _payload_from_record(record)
        valid_time = _parse_dt(record, "valid_time")
        if valid_time is None:
            raise ValueError("inspection_predictions.parquet contains a row without valid_time.")
        rows.append(
            {
                "row_index": idx,
                "origin_time": _parse_dt(record, "origin_time"),
                "valid_time": valid_time,
                "horizon_hours": int(payload["horizon_hours"]),
                "experiment": str(payload["experiment"]),
                "scenario": payload.get("scenario"),
                "model_name": payload.get("model_name"),
                "model_version": payload.get("model_version"),
                "feature_set": payload.get("feature_set"),
                "target_type": payload.get("target_type"),
                "payload": payload,
            }
        )
    count = _insert_dataframe_rows(session, m.InspectionPredictionPoint, rows, chunk_size=chunk_size)
    summary.rows_by_artifact[filename] = count
    _register_artifact(session, key=filename, path=path, project_root=project_root, artifact_type="inspection_prediction_points", stage_id="inspection", row_count=count)


def _import_feature_importance(session: Session, source_dir: Path, project_root: Path, summary: ImportSummary, *, chunk_size: int) -> None:
    filename = "stage4_lightgbm_feature_importance.csv"
    path = source_dir / filename
    if not path.exists():
        return
    df = _read_csv(path)
    rows = []
    for idx, record in enumerate(df.to_dict(orient="records")):
        payload = _payload_from_record(record)
        rows.append(
            {
                "source_stage": "stage4",
                "row_index": idx,
                "feature": payload.get("feature"),
                "importance": payload.get("importance") or payload.get("importance_gain") or payload.get("importance_split"),
                "payload": payload,
            }
        )
    count = _insert_dataframe_rows(session, m.FeatureImportance, rows, chunk_size=chunk_size)
    summary.rows_by_artifact[filename] = count
    _register_artifact(session, key=filename, path=path, project_root=project_root, artifact_type="feature_importance", stage_id="stage4", row_count=count)


def _import_stage21_table(
    session: Session,
    source_dir: Path,
    project_root: Path,
    summary: ImportSummary,
    *,
    filename: str,
    model: type,
    artifact_type: str,
    chunk_size: int,
) -> None:
    path = source_dir / filename
    if not path.exists():
        return
    df = _read_csv(path)
    rows = []
    for idx, record in enumerate(df.to_dict(orient="records")):
        payload = _payload_from_record(record)
        base = {
            "row_index": idx,
            "timestamp": _parse_dt(record, "timestamp", "dispatch_timestamp", "forecast_timestamp"),
            "payload": payload,
        }
        if model is m.Stage21PriceScenario:
            base["price_scenario_id"] = payload.get("price_scenario_id")
        elif model is m.Stage21DispatchResult:
            base["scenario"] = payload.get("scenario")
            base["price_scenario_id"] = payload.get("price_scenario_id")
        rows.append(base)
    count = _insert_dataframe_rows(session, model, rows, chunk_size=chunk_size)
    summary.rows_by_artifact[filename] = count
    _register_artifact(session, key=filename, path=path, project_root=project_root, artifact_type=artifact_type, stage_id="stage21", row_count=count)


def _import_artifact_documents(session: Session, source_dir: Path, project_root: Path) -> None:
    document_specs = {
        "site_config": (project_root / "configs" / "data_sources.pvdaq_nsrdb_2020_2022.json", "site_config", None),
        "stage2_quality_report": (source_dir / "stage2_quality_report.json", "quality_report", "stage2"),
        "stage3_feature_report": (source_dir / "stage3_feature_report.json", "feature_report", "stage3"),
        "stage18_rawhide_report": (source_dir / "stage18_rawhide_simulation_report.json", "rawhide_report", "stage18"),
        "stage21_report": (source_dir / "stage21_rawhide_weather_price_dispatch_report.json", "stage21_report", "stage21"),
    }
    for key, (path, document_type, stage_id) in document_specs.items():
        if path.exists():
            _register_document(session, key=key, path=path, project_root=project_root, document_type=document_type, stage_id=stage_id)


def _stage_sort_key(stage_id: str) -> int:
    digits = "".join(ch for ch in stage_id if ch.isdigit())
    return int(digits) if digits else 9999


def _import_stage_reports(session: Session, source_dir: Path, project_root: Path) -> None:
    reports: dict[str, dict[str, Path]] = {}
    for path in sorted(source_dir.glob("stage*_report.json")):
        stage_id = path.name.split("_", 1)[0]
        reports.setdefault(stage_id, {})["json"] = path
    for path in sorted(source_dir.glob("stage*_report.md")):
        stage_id = path.name.split("_", 1)[0]
        reports.setdefault(stage_id, {})["md"] = path

    for stage_id, paths in reports.items():
        json_path = paths.get("json")
        md_path = paths.get("md")
        name = stage_id
        if json_path is not None:
            try:
                payload = json.loads(json_path.read_text(encoding="utf-8"))
                name = str(payload.get("stage") or payload.get("name") or stage_id)
            except Exception:
                name = stage_id
        session.merge(
            m.ExperimentStage(
                stage_id=stage_id,
                name=name,
                has_json=json_path is not None,
                has_md=md_path is not None,
                sort_order=_stage_sort_key(stage_id),
            )
        )
        for fmt, path in paths.items():
            text = path.read_text(encoding="utf-8")
            payload = json.loads(text) if fmt == "json" else None
            session.merge(
                m.ReportDocument(
                    stage_id=stage_id,
                    format=fmt,
                    source_path=_relative(path, project_root),
                    content_text=text if fmt == "md" else None,
                    payload=payload,
                    content_sha256=sha256_file(path),
                )
            )
            _register_artifact(session, key=f"{stage_id}_{fmt}_report", path=path, project_root=project_root, artifact_type=f"report_{fmt}", stage_id=stage_id)


def import_display_data(
    engine: Engine,
    *,
    source_dir: Path,
    project_root: Path,
    replace: bool = False,
    chunk_size: int = 5000,
) -> ImportSummary:
    """Create schema and import all frontend display data currently supported."""
    source_dir = source_dir.resolve()
    project_root = project_root.resolve()
    _require_core_files(source_dir)
    m.Base.metadata.create_all(engine)
    summary = ImportSummary()

    with Session(engine, autoflush=False, future=True) as session:
        if replace:
            _clear_display_tables(session)
            session.flush()

        _seed_default_users(session)
        _import_artifact_documents(session, source_dir, project_root)
        _import_stage_reports(session, source_dir, project_root)

        for filename, family, stage_id in (
            ("stage8_tabular_model_metrics.csv", "tabular", "stage8"),
            ("stage14_deep_learning_metrics.csv", "deep_learning", "stage14"),
            ("stage6_tcn_metrics.csv", "tcn", "stage6"),
            ("stage9_main_model_metrics.csv", "main", "stage9"),
        ):
            _import_model_metric_csv(session, source_dir, project_root, summary, filename=filename, family=family, stage_id=stage_id, chunk_size=chunk_size)

        _import_prediction_points(session, source_dir, project_root, summary, chunk_size=chunk_size)
        _import_inspection_points(session, source_dir, project_root, summary, chunk_size=chunk_size)
        _import_feature_importance(session, source_dir, project_root, summary, chunk_size=chunk_size)

        for filename, family, stage_id in (
            ("stage10_storage_dispatch_metrics.csv", "stage10_dispatch", "stage10"),
            ("stage11_storage_strategy_sensitivity_metrics.csv", "stage11_strategy", "stage11"),
            ("stage12_storage_rolling_optimization_metrics.csv", "stage12_rolling", "stage12"),
            ("stage13_storage_strategy_governance_scorecard.csv", "stage13_governance", "stage13"),
            ("stage15_storage_configuration_sensitivity_metrics.csv", "stage15_sensitivity", "stage15"),
            ("stage18_rawhide_dispatch_metrics.csv", "stage18_rawhide_dispatch", "stage18"),
            ("stage18_rawhide_sensitivity_metrics.csv", "stage18_rawhide_sensitivity", "stage18"),
            ("stage18_rawhide_degradation_metrics.csv", "stage18_rawhide_degradation", "stage18"),
            ("stage21_rawhide_dispatch_metrics.csv", "stage21_dispatch", "stage21"),
        ):
            _import_dispatch_metric_csv(session, source_dir, project_root, summary, filename=filename, family=family, stage_id=stage_id, chunk_size=chunk_size)

        _import_stage21_table(session, source_dir, project_root, summary, filename="stage21_rawhide_price_scenarios.csv", model=m.Stage21PriceScenario, artifact_type="stage21_price_scenarios", chunk_size=chunk_size)
        _import_stage21_table(session, source_dir, project_root, summary, filename="stage21_rawhide_weather_predictions.csv", model=m.Stage21WeatherPrediction, artifact_type="stage21_weather_predictions", chunk_size=chunk_size)
        _import_stage21_table(session, source_dir, project_root, summary, filename="stage21_rawhide_dispatch_results.csv", model=m.Stage21DispatchResult, artifact_type="stage21_dispatch_results", chunk_size=chunk_size)

        summary.registered_artifacts = session.query(m.ArtifactRegistry).count()
        session.commit()
    return summary
