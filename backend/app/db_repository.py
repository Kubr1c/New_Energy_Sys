"""Database-backed readers for frontend display data."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import db_models as m


class DatabaseDataMissing(RuntimeError):
    """Raised when DB mode is enabled but required imported data is absent."""


def _payload(row: Any) -> dict:
    return dict(row.payload or {})


def _rows_payload(rows: list[Any]) -> list[dict]:
    return [_payload(row) for row in rows]


def _parse_utc(value: str) -> pd.Timestamp:
    return pd.Timestamp(value, tz="UTC")


class DisplayRepository:
    """Repository used by the FastAPI layer when ``NES_DATABASE_URL`` is set."""

    def __init__(self, session: Session):
        self.session = session

    def _require_rows(self, rows: list[Any], name: str) -> list[Any]:
        if not rows:
            raise DatabaseDataMissing(f"MySQL display table has no imported rows for {name}.")
        return rows

    def _artifact_document(self, key: str) -> m.ArtifactDocument:
        document = self.session.get(m.ArtifactDocument, key)
        if document is None:
            raise DatabaseDataMissing(f"MySQL artifact document is missing: {key}.")
        return document

    def get_site_config(self) -> dict:
        return dict(self._artifact_document("site_config").payload or {})

    def get_data_quality_report(self) -> dict | None:
        return dict(self._artifact_document("stage2_quality_report").payload or {})

    def get_feature_report(self) -> dict | None:
        return dict(self._artifact_document("stage3_feature_report").payload or {})

    def get_rawhide_report(self) -> dict | None:
        return dict(self._artifact_document("stage18_rawhide_report").payload or {})

    def get_stage21_report(self) -> dict | None:
        return dict(self._artifact_document("stage21_report").payload or {})

    def _model_metrics(self, family: str) -> list[dict]:
        stmt = (
            select(m.ModelMetric)
            .where(m.ModelMetric.metric_family == family)
            .order_by(m.ModelMetric.row_index)
        )
        rows = self._require_rows(list(self.session.scalars(stmt)), family)
        return _rows_payload(rows)

    def get_tabular_model_metrics(self) -> list[dict]:
        return self._model_metrics("tabular")

    def get_deep_learning_metrics(self) -> list[dict]:
        return self._model_metrics("deep_learning")

    def get_tcn_metrics(self) -> list[dict]:
        return self._model_metrics("tcn")

    def get_main_model_metrics(self) -> list[dict]:
        return self._model_metrics("main")

    def get_main_model_predictions(self, limit: int = 2000, offset: int = 0) -> list[dict]:
        stmt = (
            select(m.PredictionPoint)
            .where(m.PredictionPoint.source_stage == "stage9")
            .order_by(m.PredictionPoint.row_index)
            .offset(max(offset, 0))
            .limit(max(limit, 0))
        )
        rows = self._require_rows(list(self.session.scalars(stmt)), "stage9 predictions")
        return _rows_payload(rows)

    def get_feature_importance(self, top_n: int = 30) -> list[dict]:
        stmt = (
            select(m.FeatureImportance)
            .where(m.FeatureImportance.source_stage == "stage4")
            .order_by(m.FeatureImportance.importance.desc(), m.FeatureImportance.row_index)
            .limit(max(top_n, 0))
        )
        rows = self._require_rows(list(self.session.scalars(stmt)), "feature importance")
        return _rows_payload(rows)

    def _dispatch_metrics(self, family: str) -> list[dict]:
        stmt = (
            select(m.DispatchMetric)
            .where(m.DispatchMetric.metric_family == family)
            .order_by(m.DispatchMetric.row_index)
        )
        rows = self._require_rows(list(self.session.scalars(stmt)), family)
        return _rows_payload(rows)

    def get_dispatch_metrics(self, stage: str = "stage10") -> list[dict]:
        mapping = {
            "stage10": "stage10_dispatch",
            "stage11": "stage11_strategy",
            "stage12": "stage12_rolling",
        }
        family = mapping.get(stage)
        if not family:
            return []
        return self._dispatch_metrics(family)

    def get_governance_scorecard(self) -> list[dict]:
        return self._dispatch_metrics("stage13_governance")

    def get_sensitivity_metrics(self) -> list[dict]:
        return self._dispatch_metrics("stage15_sensitivity")

    def get_rawhide_dispatch_metrics(self) -> list[dict]:
        return self._dispatch_metrics("stage18_rawhide_dispatch")

    def get_rawhide_sensitivity_metrics(self) -> list[dict]:
        return self._dispatch_metrics("stage18_rawhide_sensitivity")

    def get_rawhide_degradation_metrics(self) -> list[dict]:
        return self._dispatch_metrics("stage18_rawhide_degradation")

    def get_stage21_dispatch_metrics(self) -> list[dict] | None:
        return self._dispatch_metrics("stage21_dispatch")

    def get_stage21_price_scenarios(self) -> list[dict] | None:
        stmt = select(m.Stage21PriceScenario).order_by(m.Stage21PriceScenario.row_index)
        rows = self._require_rows(list(self.session.scalars(stmt)), "stage21 price scenarios")
        return _rows_payload(rows)

    def get_stage21_weather_predictions(self) -> list[dict] | None:
        stmt = select(m.Stage21WeatherPrediction).order_by(m.Stage21WeatherPrediction.row_index)
        rows = self._require_rows(list(self.session.scalars(stmt)), "stage21 weather predictions")
        return _rows_payload(rows)

    def get_stage21_dispatch_results(self) -> list[dict] | None:
        stmt = select(m.Stage21DispatchResult).order_by(m.Stage21DispatchResult.row_index)
        rows = self._require_rows(list(self.session.scalars(stmt)), "stage21 dispatch results")
        return _rows_payload(rows)

    def list_available_stages(self) -> list[dict]:
        stmt = select(m.ExperimentStage).order_by(m.ExperimentStage.sort_order, m.ExperimentStage.stage_id)
        stages = self._require_rows(list(self.session.scalars(stmt)), "experiment stages")
        return [
            {
                "stage_id": row.stage_id,
                "name": row.name,
                "has_json": row.has_json,
                "has_md": row.has_md,
            }
            for row in stages
        ]

    def get_stage_report_json(self, stage: str) -> dict | None:
        stmt = select(m.ReportDocument).where(
            m.ReportDocument.stage_id == stage,
            m.ReportDocument.format == "json",
        )
        row = self.session.scalars(stmt).first()
        if row is None:
            return None
        return dict(row.payload or {})

    def get_stage_report_md(self, stage: str) -> str | None:
        stmt = select(m.ReportDocument).where(
            m.ReportDocument.stage_id == stage,
            m.ReportDocument.format == "md",
        )
        row = self.session.scalars(stmt).first()
        if row is None:
            return None
        return row.content_text

    def load_inspection_frame(self) -> pd.DataFrame:
        stmt = select(m.InspectionPredictionPoint).order_by(m.InspectionPredictionPoint.valid_time)
        rows = self._require_rows(list(self.session.scalars(stmt)), "inspection predictions")
        df = pd.DataFrame([_payload(row) for row in rows])
        for col in ("origin_time", "valid_time"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], utc=True)
        return df

    def inspection_metadata(self) -> dict:
        min_time, max_time = self.session.execute(
            select(
                func.min(m.InspectionPredictionPoint.valid_time),
                func.max(m.InspectionPredictionPoint.valid_time),
            )
        ).one()
        if min_time is None or max_time is None:
            raise DatabaseDataMissing("MySQL inspection_prediction_points is empty.")

        horizons = list(self.session.scalars(
            select(m.InspectionPredictionPoint.horizon_hours)
            .distinct()
            .order_by(m.InspectionPredictionPoint.horizon_hours)
        ))
        scenarios = list(self.session.scalars(
            select(m.InspectionPredictionPoint.scenario)
            .distinct()
            .order_by(m.InspectionPredictionPoint.scenario)
        ))
        experiments = []
        exp_ids = list(self.session.scalars(
            select(m.InspectionPredictionPoint.experiment)
            .distinct()
            .order_by(m.InspectionPredictionPoint.experiment)
        ))
        for exp_id in exp_ids:
            row = self.session.scalars(
                select(m.InspectionPredictionPoint)
                .where(m.InspectionPredictionPoint.experiment == exp_id)
                .order_by(m.InspectionPredictionPoint.row_index)
                .limit(1)
            ).first()
            if row is None:
                continue
            experiments.append({
                "id": row.experiment,
                "model_name": row.model_name or "",
                "version": row.model_version or "",
                "feature_set": row.feature_set or "",
                "target_type": row.target_type or "",
            })

        return {
            "date_min": min_time.date().isoformat(),
            "date_max": max_time.date().isoformat(),
            "horizons": horizons,
            "experiments": experiments,
            "scenarios": [str(item) for item in scenarios if item is not None],
            "capacity_kw": 1.12,
            "baselines": {
                "persistence_origin": "prediction = pv_power at origin_time",
                "persistence_same_hour_yesterday": "prediction = pv_power at valid_time - 24h",
            },
        }

    def inspection_data(
        self,
        start: str,
        end: str,
        horizons: str | None = None,
        experiments: str | None = None,
        granularity: str = "hour",
    ) -> dict:
        start_ts = _parse_utc(start).to_pydatetime()
        end_ts = _parse_utc(end).to_pydatetime()
        stmt = select(m.InspectionPredictionPoint).where(
            m.InspectionPredictionPoint.valid_time >= start_ts,
            m.InspectionPredictionPoint.valid_time < end_ts,
        )
        if horizons:
            stmt = stmt.where(m.InspectionPredictionPoint.horizon_hours.in_([int(h) for h in horizons.split(",")]))
        if experiments:
            stmt = stmt.where(m.InspectionPredictionPoint.experiment.in_(experiments.split(",")))
        stmt = stmt.order_by(m.InspectionPredictionPoint.valid_time, m.InspectionPredictionPoint.row_index)
        rows = list(self.session.scalars(stmt))
        if not rows:
            return {"data": [], "daily_summary": {}}
        result = pd.DataFrame([_payload(row) for row in rows])
        result["valid_time"] = pd.to_datetime(result["valid_time"], utc=True)
        if "origin_time" in result.columns:
            result["origin_time"] = pd.to_datetime(result["origin_time"], utc=True)
        return _inspection_payload_from_frame(result, granularity)


def _inspection_payload_from_frame(result: pd.DataFrame, granularity: str) -> dict:
    result = result.copy()
    result["valid_date"] = result["valid_time"].dt.date
    daily_summary: dict[str, dict] = {}

    for date_val in sorted(result["valid_date"].unique()):
        date_str = str(date_val)
        day_df = result[result["valid_date"] == date_val]
        daily_actual_kwh = round(float(day_df["actual_kw"].sum()), 3)
        by_experiment: dict[str, dict] = {}
        for exp_id in day_df["experiment"].unique():
            exp_df = day_df[day_df["experiment"] == exp_id]
            by_horizon: dict[str, dict] = {}
            for hor in sorted(exp_df["horizon_hours"].unique()):
                hor_df = exp_df[exp_df["horizon_hours"] == hor]
                daily_pred_kwh = round(float(hor_df["prediction_kw"].sum()), 3)
                rmse_val = (hor_df["error_kw"] ** 2).mean()
                by_horizon[str(hor)] = {
                    "daily_pred_kwh": daily_pred_kwh,
                    "daily_error_kwh": round(daily_pred_kwh - daily_actual_kwh, 3),
                    "rmse_kw": round(float(rmse_val) ** 0.5, 4) if pd.notna(rmse_val) and rmse_val >= 0 else None,
                    "mae_kw": round(float(hor_df["abs_error_kw"].mean()), 4),
                    "bias_kw": round(float(hor_df["error_kw"].mean()), 4),
                }
            by_experiment[str(exp_id)] = by_horizon

        daytime = day_df[day_df["scenario"] != "night"]
        dominant_scenario = daytime["scenario"].mode().iloc[0] if not daytime.empty else "night"
        daily_summary[date_str] = {
            "daily_actual_kwh": daily_actual_kwh,
            "scenario_dominant": str(dominant_scenario),
            "experiments": by_experiment,
        }

    if granularity == "day":
        data = [
            {
                "valid_date": date_str,
                "daily_actual_kwh": summary["daily_actual_kwh"],
                "scenario_dominant": summary["scenario_dominant"],
                "n_hours": int(len(result[result["valid_date"] == pd.Timestamp(date_str).date()])),
            }
            for date_str, summary in sorted(daily_summary.items())
        ]
    else:
        clean = result.drop(columns=["valid_date"]).replace({np.nan: None})
        data = clean.to_dict(orient="records")
        for row in data:
            for col in ("origin_time", "valid_time"):
                if isinstance(row.get(col), pd.Timestamp):
                    row[col] = row[col].isoformat()
    return {"data": data, "daily_summary": daily_summary}
