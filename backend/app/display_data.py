"""Display-data facade used by API endpoints.

When ``NES_DATABASE_URL`` is configured this module reads from MySQL through
``DisplayRepository``.  Without that environment variable it preserves the
historical file-backed behavior so local checks still run before a database is
created.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import data_loader, database
from .db_repository import DatabaseDataMissing, DisplayRepository


def database_enabled() -> bool:
    return database.is_database_enabled()


def _db_call(method_name: str, *args, **kwargs):
    with database.session_scope() as session:
        repo = DisplayRepository(session)
        return getattr(repo, method_name)(*args, **kwargs)


def _call(method_name: str, file_func: Callable[..., Any], *args, **kwargs):
    if database_enabled():
        return _db_call(method_name, *args, **kwargs)
    return file_func(*args, **kwargs)


def get_site_config():
    return _call("get_site_config", data_loader.get_site_config)


def get_tabular_model_metrics():
    return _call("get_tabular_model_metrics", data_loader.get_tabular_model_metrics)


def get_deep_learning_metrics():
    return _call("get_deep_learning_metrics", data_loader.get_deep_learning_metrics)


def get_tcn_metrics():
    return _call("get_tcn_metrics", data_loader.get_tcn_metrics)


def get_main_model_predictions(limit: int = 2000, offset: int = 0):
    return _call("get_main_model_predictions", data_loader.get_main_model_predictions, limit=limit, offset=offset)


def get_main_model_metrics():
    return _call("get_main_model_metrics", data_loader.get_main_model_metrics)


def get_dispatch_metrics(stage: str = "stage10"):
    return _call("get_dispatch_metrics", data_loader.get_dispatch_metrics, stage=stage)


def get_governance_scorecard():
    return _call("get_governance_scorecard", data_loader.get_governance_scorecard)


def get_sensitivity_metrics():
    return _call("get_sensitivity_metrics", data_loader.get_sensitivity_metrics)


def get_rawhide_report():
    return _call("get_rawhide_report", data_loader.get_rawhide_report)


def get_rawhide_dispatch_metrics():
    return _call("get_rawhide_dispatch_metrics", data_loader.get_rawhide_dispatch_metrics)


def get_rawhide_sensitivity_metrics():
    return _call("get_rawhide_sensitivity_metrics", data_loader.get_rawhide_sensitivity_metrics)


def get_rawhide_degradation_metrics():
    return _call("get_rawhide_degradation_metrics", data_loader.get_rawhide_degradation_metrics)


def get_stage21_report():
    return _call("get_stage21_report", data_loader.get_stage21_report)


def get_stage21_weather_predictions():
    return _call("get_stage21_weather_predictions", data_loader.get_stage21_weather_predictions)


def get_stage21_price_scenarios():
    return _call("get_stage21_price_scenarios", data_loader.get_stage21_price_scenarios)


def get_stage21_dispatch_results():
    return _call("get_stage21_dispatch_results", data_loader.get_stage21_dispatch_results)


def get_stage21_dispatch_metrics():
    return _call("get_stage21_dispatch_metrics", data_loader.get_stage21_dispatch_metrics)


def get_stage23_scenarios():
    """Stage23 showcase — fixed file path, no DB fallback."""
    return data_loader.get_stage23_scenarios()


def get_stage23_summary():
    """Stage23 summary — fixed file path, no DB fallback."""
    return data_loader.get_stage23_summary()


def get_feature_importance(top_n: int = 30):
    return _call("get_feature_importance", data_loader.get_feature_importance, top_n=top_n)


def get_stage_report_json(stage: str):
    return _call("get_stage_report_json", data_loader.get_stage_report_json, stage=stage)


def get_stage_report_md(stage: str):
    return _call("get_stage_report_md", data_loader.get_stage_report_md, stage=stage)


def get_data_quality_report():
    return _call("get_data_quality_report", data_loader.get_data_quality_report)


def get_feature_report():
    return _call("get_feature_report", data_loader.get_feature_report)


def list_available_stages():
    return _call("list_available_stages", data_loader.list_available_stages)


def load_inspection_parquet():
    if database_enabled():
        return _db_call("load_inspection_frame")
    return data_loader.load_inspection_parquet()


def inspection_metadata():
    if database_enabled():
        return _db_call("inspection_metadata")
    return None


def inspection_data(start: str, end: str, horizons: str | None, experiments: str | None, granularity: str):
    if database_enabled():
        return _db_call(
            "inspection_data",
            start=start,
            end=end,
            horizons=horizons,
            experiments=experiments,
            granularity=granularity,
        )
    return None
