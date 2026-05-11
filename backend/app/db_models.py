"""SQLAlchemy tables for the MySQL-backed display repository."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ArtifactRegistry(Base):
    __tablename__ = "artifact_registry"

    artifact_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    stage_id: Mapped[str | None] = mapped_column(String(64))
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_path: Mapped[str] = mapped_column(String(512), nullable=False)
    row_count: Mapped[int | None] = mapped_column(Integer)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ArtifactDocument(Base):
    __tablename__ = "artifact_documents"

    document_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    stage_id: Mapped[str | None] = mapped_column(String(64), index=True)
    document_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_path: Mapped[str] = mapped_column(String(512), nullable=False)
    content_text: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict | list | None] = mapped_column(JSON)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ExperimentStage(Base):
    __tablename__ = "experiment_stages"

    stage_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    has_json: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_md: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)


class ReportDocument(Base):
    __tablename__ = "report_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stage_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    source_path: Mapped[str] = mapped_column(String(512), nullable=False)
    content_text: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict | list | None] = mapped_column(JSON)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (UniqueConstraint("stage_id", "format", name="uq_report_stage_format"),)


class ModelMetric(Base):
    __tablename__ = "model_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    metric_family: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    stage_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model: Mapped[str | None] = mapped_column(String(128), index=True)
    feature_set: Mapped[str | None] = mapped_column(String(160), index=True)
    target: Mapped[str | None] = mapped_column(String(160), index=True)
    split: Mapped[str | None] = mapped_column(String(32), index=True)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    __table_args__ = (Index("ix_model_metrics_family_row", "metric_family", "row_index"),)


class PredictionPoint(Base):
    __tablename__ = "prediction_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_stage: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    target: Mapped[str | None] = mapped_column(String(160), index=True)
    split: Mapped[str | None] = mapped_column(String(32), index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    __table_args__ = (Index("ix_prediction_stage_row", "source_stage", "row_index"),)


class InspectionPredictionPoint(Base):
    __tablename__ = "inspection_prediction_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    origin_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    valid_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    horizon_hours: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    experiment: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scenario: Mapped[str | None] = mapped_column(String(64), index=True)
    model_name: Mapped[str | None] = mapped_column(String(128))
    model_version: Mapped[str | None] = mapped_column(String(128))
    feature_set: Mapped[str | None] = mapped_column(String(160))
    target_type: Mapped[str | None] = mapped_column(String(160))
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    __table_args__ = (Index("ix_inspection_filter", "valid_time", "horizon_hours", "experiment"),)


class FeatureImportance(Base):
    __tablename__ = "feature_importance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_stage: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    feature: Mapped[str | None] = mapped_column(String(180), index=True)
    importance: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


class DispatchMetric(Base):
    __tablename__ = "dispatch_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    metric_family: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    stage_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scenario: Mapped[str | None] = mapped_column(String(160), index=True)
    price_scenario_id: Mapped[str | None] = mapped_column(String(160), index=True)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    __table_args__ = (Index("ix_dispatch_family_row", "metric_family", "row_index"),)


class Stage21PriceScenario(Base):
    __tablename__ = "stage21_price_scenarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    price_scenario_id: Mapped[str | None] = mapped_column(String(160), index=True)
    timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


class Stage21WeatherPrediction(Base):
    __tablename__ = "stage21_weather_predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


class Stage21DispatchResult(Base):
    __tablename__ = "stage21_dispatch_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    scenario: Mapped[str | None] = mapped_column(String(160), index=True)
    price_scenario_id: Mapped[str | None] = mapped_column(String(160), index=True)
    timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    __table_args__ = (Index("ix_stage21_dispatch_filter", "price_scenario_id", "timestamp"),)
