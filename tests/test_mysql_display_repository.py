from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backend.app import auth, database
from backend.app import db_models as m
from backend.app.db_importer import import_display_data
from backend.app.db_repository import DisplayRepository


@pytest.fixture(autouse=True)
def _reset_database_cache():
    database.reset_engine_cache()
    yield
    database.reset_engine_cache()


def _sqlite_url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path.as_posix()}"


def _write_display_fixture(root: Path) -> Path:
    source_dir = root / "data" / "processed" / "pvdaq_nsrdb_2020_2022"
    source_dir.mkdir(parents=True)
    (root / "configs").mkdir()
    (root / "configs" / "data_sources.pvdaq_nsrdb_2020_2022.json").write_text(
        '{"site":"fixture","capacity_kw":1.12}',
        encoding="utf-8",
    )

    pd.DataFrame(
        [
            {
                "timestamp": "2022-01-01 00:00:00+00:00",
                "target": "target_pv_power_t_plus_24h",
                "model_name": "lightgbm_tuned",
                "feature_set": "history_only",
                "prediction_kw": 0.4,
                "actual_kw": 0.5,
                "error_kw": -0.1,
            },
            {
                "timestamp": "2022-01-01 01:00:00+00:00",
                "target": "target_pv_power_t_plus_24h",
                "model_name": "lightgbm_tuned",
                "feature_set": "history_only",
                "prediction_kw": 0.6,
                "actual_kw": 0.55,
                "error_kw": 0.05,
            },
        ]
    ).to_csv(source_dir / "stage9_main_model_predictions.csv", index=False)

    pd.DataFrame(
        [
            {
                "split": "all",
                "target": "target_pv_power_t_plus_24h",
                "sample_count": 2,
                "mae_kw": 0.1,
                "rmse_kw": 0.2,
            }
        ]
    ).to_csv(source_dir / "stage9_main_model_metrics.csv", index=False)

    pd.DataFrame(
        [
            {
                "origin_time": pd.Timestamp("2022-01-01T00:00:00Z"),
                "valid_time": pd.Timestamp("2022-01-01T01:00:00Z"),
                "horizon_hours": 1,
                "experiment": "stage5",
                "model_name": "lightgbm",
                "model_version": "fixture",
                "feature_set": "full_features",
                "target_type": "pv_power",
                "prediction_kw": 0.4,
                "actual_kw": 0.5,
                "error_kw": -0.1,
                "abs_error_kw": 0.1,
                "scenario": "cloudy",
            },
            {
                "origin_time": pd.Timestamp("2022-01-01T00:00:00Z"),
                "valid_time": pd.Timestamp("2022-01-02T00:00:00Z"),
                "horizon_hours": 24,
                "experiment": "stage5",
                "model_name": "lightgbm",
                "model_version": "fixture",
                "feature_set": "full_features",
                "target_type": "pv_power",
                "prediction_kw": 0.8,
                "actual_kw": 0.7,
                "error_kw": 0.1,
                "abs_error_kw": 0.1,
                "scenario": "clear",
            },
        ]
    ).to_parquet(source_dir / "inspection_predictions.parquet", index=False)

    pd.DataFrame(
        [
            {
                "scenario": "rolling_optimization",
                "dispatch_timestamp": "2022-06-01 00:00:00+00:00",
                "price_scenario_id": "flat_proxy_30",
                "price_eur_mwh": 30.0,
                "forecast_pv_kw": 100.0,
                "actual_pv_kw": 90.0,
            }
        ]
    ).to_csv(source_dir / "stage21_rawhide_dispatch_results.csv", index=False)

    pd.DataFrame(
        [{"timestamp": "2022-06-01 00:00:00+00:00", "price_scenario_id": "flat_proxy_30", "price_eur_mwh": 30.0}]
    ).to_csv(source_dir / "stage21_rawhide_price_scenarios.csv", index=False)
    pd.DataFrame(
        [{"timestamp": "2022-06-01 00:00:00+00:00", "prediction_kw": 100.0, "actual_kw": 90.0}]
    ).to_csv(source_dir / "stage21_rawhide_weather_predictions.csv", index=False)
    pd.DataFrame([{"scenario": "rolling_optimization", "revenue_eur": 1.0}]).to_csv(
        source_dir / "stage21_rawhide_dispatch_metrics.csv",
        index=False,
    )

    (source_dir / "stage9_main_model_report.json").write_text('{"stage":"stage9","ok":true}', encoding="utf-8")
    (source_dir / "stage9_main_model_report.md").write_text("# Stage9\n", encoding="utf-8")
    return source_dir


def test_importer_replace_is_idempotent_and_repository_reads_mysql(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source_dir = _write_display_fixture(tmp_path)
    monkeypatch.setenv("NES_DATABASE_URL", _sqlite_url(tmp_path / "display.db"))
    database.reset_engine_cache()

    engine = database.get_engine()
    first = import_display_data(engine, source_dir=source_dir, project_root=tmp_path, replace=True, chunk_size=2)
    second = import_display_data(engine, source_dir=source_dir, project_root=tmp_path, replace=True, chunk_size=2)

    assert first.rows_by_artifact["stage9_main_model_predictions.csv"] == 2
    assert second.rows_by_artifact["inspection_predictions.parquet"] == 2

    with database.session_scope() as session:
        assert session.query(m.PredictionPoint).count() == 2
        assert session.query(m.InspectionPredictionPoint).count() == 2
        assert session.query(m.Stage21DispatchResult).count() == 1

        repo = DisplayRepository(session)
        assert repo.get_site_config()["site"] == "fixture"
        assert repo.get_main_model_metrics()[0]["sample_count"] == 2
        assert repo.get_main_model_predictions(limit=10)[0]["prediction_kw"] == 0.4
        assert repo.inspection_metadata()["horizons"] == [1, 24]
        assert repo.inspection_data("2022-01-01", "2022-01-03")["data"]
        assert repo.get_stage21_dispatch_results()[0]["price_scenario_id"] == "flat_proxy_30"
        assert repo.get_stage_report_json("stage9")["ok"] is True

    token = auth.authenticate("admin", "admin123")
    assert token is not None
    assert auth.verify_token(token).role == "admin"


def test_importer_reports_missing_required_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source_dir = tmp_path / "empty"
    source_dir.mkdir()
    monkeypatch.setenv("NES_DATABASE_URL", _sqlite_url(tmp_path / "missing.db"))
    database.reset_engine_cache()

    with pytest.raises(FileNotFoundError, match="stage9_main_model_predictions.csv"):
        import_display_data(database.get_engine(), source_dir=source_dir, project_root=tmp_path, replace=True)
