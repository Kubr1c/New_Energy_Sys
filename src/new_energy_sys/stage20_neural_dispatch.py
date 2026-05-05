"""
Stage20: 调度侧深度学习补强。

两个实验：
1. DL 预测驱动调度消融 — 将 Stage14 TCN/DLinear t+24h 预测接入 Stage12 rolling
2. MLP 调度策略蒸馏 — 用 Stage12 rolling 动作训练神经网络调度策略

Usage (via CLI):
    $env:PYTHONPATH='src'
    python -m new_energy_sys.cli.run_stage20_neural_dispatch ...

Reference:
    - Stage12 rolling: src/new_energy_sys/stage12_storage_rolling.py
    - Stage14 predictions: data/processed/.../stage14_deep_learning_predictions.csv
    - Stage9 format: data/processed/.../stage9_main_model_predictions.csv
"""

from __future__ import annotations

import json
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from new_energy_sys.modeling import _chronological_split
from new_energy_sys.stage12_storage_rolling import run_stage12_rolling_optimization
from new_energy_sys.storage import _bounded_power, _constraint_summary, _prepare_dispatch_input

warnings.filterwarnings("ignore", category=UserWarning)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CAPACITY_KW: float = 1.12
PREDICTION_UPPER: float = CAPACITY_KW * 1.05
HORIZON_HOURS_DEFAULT: int = 24

# Default DL candidates for dispatch ablation
# Each dict maps to a Stage14 model:feature_set pair
DL_CANDIDATES_DEFAULT: list[dict[str, str]] = [
    {"model": "tcn", "feature_set": "history_only"},
    {"model": "tcn", "feature_set": "csi_enhanced"},
    {"model": "dlinear", "feature_set": "history_only"},
]

# MLP policy hyperparameters
POLICY_HIDDEN: int = 128
POLICY_DROPOUT: float = 0.1
POLICY_LR: float = 1e-3
POLICY_WEIGHT_DECAY: float = 1e-4
POLICY_MODE_REGRESSION: str = "regression"
POLICY_MODE_TWO_STAGE: str = "two-stage"
POLICY_START_DEFAULT: str = "2021-01-01"
POLICY_END_DEFAULT: str = "2022-12-31"

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Stage20NeuralDispatchResult:
    """Results from Stage20 dispatch-side DL experiments.

    Attributes
    ----------
    dl_dispatch_metrics : pd.DataFrame
        Per-prediction-source dispatch comparison metrics (Experiment 1).
    neural_policy_replay : pd.DataFrame
        Hourly replay trace of the MLP policy (Experiment 2).
    neural_policy_metrics : pd.DataFrame
        Aggregated MLP policy metrics (Experiment 2).
    report : dict
        Quality gates, comparison summaries, and output paths.
    """

    dl_dispatch_metrics: pd.DataFrame
    neural_policy_replay: pd.DataFrame
    neural_policy_metrics: pd.DataFrame
    report: dict[str, Any]


# ---------------------------------------------------------------------------
# Stage14 → Stage9 schema conversion
# ---------------------------------------------------------------------------


def convert_stage14_to_stage9(
    stage14_df: pd.DataFrame,
    model: str,
    feature_set: str,
    capacity_kw: float = CAPACITY_KW,
    horizon_hours: int = HORIZON_HOURS_DEFAULT,
) -> pd.DataFrame:
    """Convert Stage14 prediction rows to Stage9 dispatch-compatible format.

    Stage14 has: timestamp, model, target, window_size, split, feature_set,
                  actual_kw, prediction_kw, error_kw
    Stage9 needs: timestamp, target, model_name, feature_set, prediction_kw,
                  prediction_capacity_ratio, prediction_lower_bound_kw,
                  prediction_upper_bound_kw, actual_kw, error_kw

    Parameters
    ----------
    stage14_df : pd.DataFrame
        Stage14 predictions, filtered to a single model + feature_set + target.
    model : str
        Model name for the ``model_name`` column.
    feature_set : str
        Feature set label for the ``feature_set`` column.
    capacity_kw : float
        Site nameplate capacity for ratio calculation.
    horizon_hours : int
        Forecast horizon (used to construct the target column string).

    Returns
    -------
    pd.DataFrame
        Stage9-compatible DataFrame with 10 standard columns.
    """
    target_col = f"target_pv_power_t_plus_{horizon_hours}h"

    result = pd.DataFrame()
    result["timestamp"] = pd.to_datetime(stage14_df["timestamp"], utc=True)
    result["target"] = target_col
    result["model_name"] = model
    result["feature_set"] = feature_set
    result["prediction_kw"] = np.clip(
        stage14_df["prediction_kw"].values, 0.0, capacity_kw * 1.05
    )
    result["prediction_capacity_ratio"] = result["prediction_kw"] / capacity_kw
    result["prediction_lower_bound_kw"] = 0.0
    result["prediction_upper_bound_kw"] = capacity_kw * 1.05
    result["actual_kw"] = stage14_df["actual_kw"].values
    result["error_kw"] = result["prediction_kw"] - result["actual_kw"]
    return result


def _filter_target_predictions(predictions: pd.DataFrame, target_col: str) -> pd.DataFrame:
    """Return a clean copy for one forecast target.

    Stage12 expects a single horizon in each run.  Some upstream prediction
    artifacts can contain multiple targets, so Stage20 normalizes every source
    before building the common comparison window.
    """
    result = predictions.copy()
    if "target" in result.columns:
        result = result[result["target"] == target_col].copy()
    if result.empty:
        raise ValueError(f"No prediction rows found for {target_col}.")
    result["timestamp"] = pd.to_datetime(result["timestamp"], utc=True)
    return result.sort_values("timestamp").reset_index(drop=True)


def _restrict_candidates_to_common_dispatch_window(
    candidates: list[dict[str, Any]],
    feature_frame: pd.DataFrame,
    *,
    horizon_hours: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Restrict every prediction source to the same dispatch timestamps.

    The first Stage20 implementation compared full Stage9 LightGBM rows against
    Stage14 test-only rows.  That made revenue, shortfall and cycle metrics
    period-dependent rather than model-dependent.  Here we resolve the exact
    dispatch timestamps that Stage12 can settle for each source, intersect them,
    and then trim every source before calling Stage12.
    """
    timestamp_sets: dict[str, set[pd.Timestamp]] = {}
    available_rows: dict[str, int] = {}

    for cand in candidates:
        label = str(cand["label"])
        dispatch_input = _prepare_dispatch_input(
            cand["predictions"],
            feature_frame,
            horizon_hours=horizon_hours,
        )
        dispatch_ts = pd.to_datetime(dispatch_input["dispatch_timestamp"], utc=True)
        timestamp_sets[label] = set(dispatch_ts)
        available_rows[label] = int(len(dispatch_input))

    if not timestamp_sets:
        raise ValueError("No prediction candidates are available for common-window dispatch.")

    common_ts = set.intersection(*timestamp_sets.values())
    if not common_ts:
        counts = {label: len(values) for label, values in timestamp_sets.items()}
        raise ValueError(f"Prediction sources have no common dispatch timestamps: {counts}")

    common_index = pd.Index(sorted(common_ts))
    common_start = common_index.min()
    common_end = common_index.max()

    trimmed: list[dict[str, Any]] = []
    for cand in candidates:
        preds = cand["predictions"].copy()
        preds["timestamp"] = pd.to_datetime(preds["timestamp"], utc=True)
        dispatch_ts = preds["timestamp"] + pd.Timedelta(hours=horizon_hours)
        keep_mask = dispatch_ts.isin(common_index)
        trimmed_preds = preds.loc[keep_mask].copy().sort_values("timestamp").reset_index(drop=True)

        # Re-run Stage12 input preparation after trimming.  This catches duplicate
        # or market-alignment edge cases before expensive rolling optimization.
        verified = _prepare_dispatch_input(
            trimmed_preds,
            feature_frame,
            horizon_hours=horizon_hours,
        )
        verified_ts = set(pd.to_datetime(verified["dispatch_timestamp"], utc=True))
        if verified_ts != common_ts:
            raise ValueError(
                f"{cand['label']} does not match the common dispatch window after trimming."
            )

        next_cand = dict(cand)
        next_cand["predictions"] = trimmed_preds
        next_cand["common_window_rows"] = int(len(common_index))
        next_cand["common_start"] = str(common_start)
        next_cand["common_end"] = str(common_end)
        next_cand["available_dispatch_rows_before_intersection"] = available_rows[str(cand["label"])]
        trimmed.append(next_cand)

    metadata = {
        "common_window_rows": int(len(common_index)),
        "common_start": str(common_start),
        "common_end": str(common_end),
        "available_dispatch_rows_by_source": available_rows,
    }
    return trimmed, metadata


# ---------------------------------------------------------------------------
# Experiment 1: DL prediction-driven dispatch ablation
# ---------------------------------------------------------------------------


def _run_dl_dispatch_ablation(
    stage9_df: pd.DataFrame,
    stage14_df: pd.DataFrame,
    feature_frame: pd.DataFrame,
    config: dict[str, Any],
    dl_candidates: list[dict[str, str]],
    horizon_hours: int = HORIZON_HOURS_DEFAULT,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Run Stage12 rolling dispatch with each DL prediction source.

    Returns (combined_metrics_df, per_candidate_report_entries).
    """
    capacity_kw = float(config["site"]["capacity_kw"])
    target_col = f"target_pv_power_t_plus_{horizon_hours}h"
    all_metrics: list[pd.DataFrame] = []
    report_entries: list[dict[str, Any]] = []

    # Build candidate list: LightGBM baseline + DL candidates + Persistence + Perfect
    candidates: list[dict[str, Any]] = []

    # 1. LightGBM baseline (from Stage9)
    candidates.append({
        "label": "lightgbm_history_only",
        "source": "stage9",
        "predictions": _filter_target_predictions(stage9_df, target_col),
    })

    # 2. DL candidates from Stage14
    for dl in dl_candidates:
        label = f"stage14_{dl['model']}_{dl['feature_set']}"
        subset = stage14_df[
            (stage14_df["model"] == dl["model"])
            & (stage14_df["feature_set"] == dl["feature_set"])
            & (stage14_df["target"] == target_col)
            & (stage14_df["split"] == "test")
        ].copy()
        if len(subset) == 0:
            print(f"  WARNING: no rows for {label}, skipping")
            continue
        converted = convert_stage14_to_stage9(
            subset, model=dl["model"], feature_set=dl["feature_set"],
            capacity_kw=capacity_kw, horizon_hours=horizon_hours,
        )
        candidates.append({
            "label": label,
            "source": "stage14",
            "predictions": converted,
        })

    # 3. Persistence baseline
    persistence_subset = stage14_df[
        (stage14_df["model"] == "persistence")
        & (stage14_df["target"] == target_col)
        & (stage14_df["split"] == "test")
    ].copy()
    if len(persistence_subset) > 0:
        converted = convert_stage14_to_stage9(
            persistence_subset, model="persistence",
            feature_set="persistence_baseline",
            capacity_kw=capacity_kw, horizon_hours=horizon_hours,
        )
        candidates.append({
            "label": "persistence_baseline",
            "source": "stage14",
            "predictions": converted,
        })

    # 4. Perfect forecast upper bound (prediction = actual)
    stage9_target = _filter_target_predictions(stage9_df, target_col)
    perfect_df = stage9_target[["timestamp", "actual_kw"]].copy()
    perfect_df["target"] = target_col
    perfect_df["model_name"] = "perfect_forecast"
    perfect_df["feature_set"] = "oracle"
    perfect_df["prediction_kw"] = perfect_df["actual_kw"]
    perfect_df["prediction_capacity_ratio"] = perfect_df["prediction_kw"] / capacity_kw
    perfect_df["prediction_lower_bound_kw"] = 0.0
    perfect_df["prediction_upper_bound_kw"] = capacity_kw * 1.05
    perfect_df["error_kw"] = 0.0
    candidates.append({
        "label": "perfect_forecast_upper_bound",
        "source": "oracle",
        "predictions": perfect_df,
    })

    candidates, common_window = _restrict_candidates_to_common_dispatch_window(
        candidates,
        feature_frame,
        horizon_hours=horizon_hours,
    )

    print(f"\n  Dispatch ablation: {len(candidates)} prediction sources")
    print(
        "  Common dispatch window: "
        f"{common_window['common_window_rows']} rows, "
        f"{common_window['common_start']} -> {common_window['common_end']}"
    )
    for cand in candidates:
        label = cand["label"]
        preds = cand["predictions"]
        print(f"\n  --- {label} ({len(preds)} rows) ---")

        try:
            result = run_stage12_rolling_optimization(
                predictions=preds,
                feature_frame=feature_frame,
                config=config,
                horizon_hours=horizon_hours,
            )
        except Exception as exc:
            print(f"    ERROR: {exc}")
            report_entries.append({"label": label, "status": "error", "error": str(exc)})
            continue

        # Tag metrics with prediction source
        metrics_df = result.metrics.copy()
        metrics_df["prediction_source"] = label
        metrics_df["common_window_rows"] = int(cand["common_window_rows"])
        metrics_df["common_start"] = cand["common_start"]
        metrics_df["common_end"] = cand["common_end"]
        all_metrics.append(metrics_df)

        # Extract key headline numbers for the report
        rolling_row = metrics_df[
            metrics_df["scenario"] == "rolling_optimization"
        ]
        if len(rolling_row) == 0:
            continue
        row = rolling_row.iloc[0]
        report_entries.append({
            "label": label,
            "status": "ok",
            "common_window_rows": int(cand["common_window_rows"]),
            "common_start": cand["common_start"],
            "common_end": cand["common_end"],
            "available_dispatch_rows_before_intersection": int(
                cand["available_dispatch_rows_before_intersection"]
            ),
            "incremental_revenue_eur": float(row.get("incremental_revenue_eur", float("nan"))),
            "total_storage_revenue_eur": float(row.get("total_storage_revenue_eur", float("nan"))),
            "total_shortfall_kwh": float(row.get("total_shortfall_kwh", float("nan"))),
            "total_curtailed_kwh": float(row.get("total_curtailed_kwh", float("nan"))),
            "cycle_equivalent_count": float(row.get("cycle_equivalent_count", float("nan"))),
            "mean_soc": float(row.get("mean_soc", float("nan"))),
            "sample_count": int(row.get("sample_count", 0)),
        })
        print(f"    增量收益: {row.get('incremental_revenue_eur', float('nan')):.2f} EUR"
              f"  短缺: {row.get('total_shortfall_kwh', float('nan')):.2f} kWh"
              f"  循环: {row.get('cycle_equivalent_count', float('nan')):.2f}")

    combined = pd.concat(all_metrics, ignore_index=True) if all_metrics else pd.DataFrame()
    return combined, report_entries


# ---------------------------------------------------------------------------
# Experiment 2: MLP dispatch policy distillation
# ---------------------------------------------------------------------------


class DispatchMLPPolicy(nn.Module):
    """Two-layer MLP that predicts charge/discharge power from dispatch context.

    Input:  [soc, pv_forecast(24h), price(24h), load, hour_sin, hour_cos,
             month_sin, month_cos]
    Output: [charge_kw, discharge_kw]
    """

    def __init__(self, input_dim: int, hidden: int = 128, dropout: float = 0.1):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden, hidden),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden, 2),  # [charge_kw, discharge_kw]
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class TwoStageDispatchPolicy(nn.Module):
    """Shared-encoder neural policy with separate direction and power heads.

    The Stage12 teacher first decides *what kind* of action is useful in the
    current rolling window, and only then decides action size.  A direct
    charge/discharge regressor blurs these two decisions and can turn a small
    regression residual into the opposite physical action.  This model keeps
    the two decisions explicit:

    - ``direction_head`` predicts idle/charge/discharge.
    - ``charge_power_head`` predicts charge power as a fraction of max charge.
    - ``discharge_power_head`` predicts discharge power as a fraction of max
      discharge.
    """

    def __init__(self, input_dim: int, dropout: float = 0.1):
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Linear(input_dim, 128),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Dropout(dropout),
        )
        self.direction_head = nn.Linear(64, 3)
        self.charge_power_head = nn.Sequential(nn.Linear(64, 1), nn.Sigmoid())
        self.discharge_power_head = nn.Sequential(nn.Linear(64, 1), nn.Sigmoid())

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        encoded = self.encoder(x)
        return (
            self.direction_head(encoded),
            self.charge_power_head(encoded).squeeze(-1),
            self.discharge_power_head(encoded).squeeze(-1),
        )


def _parse_policy_timestamp(value: str | None, *, is_end: bool) -> pd.Timestamp | None:
    """Parse policy-window boundaries as UTC timestamps.

    CLI users commonly pass dates such as ``2022-12-31``.  For an end boundary
    that means the end of that day, not midnight at the start of it.  Keeping
    the conversion here prevents off-by-one-day policy datasets.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    ts = pd.Timestamp(text)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    if is_end and len(text) == 10:
        ts = ts + pd.Timedelta(hours=23)
    return ts


def _build_policy_dataset(
    stage12_results: pd.DataFrame,
    feature_frame: pd.DataFrame,
    capacity_kw: float = CAPACITY_KW,
    *,
    policy_start: str | None = None,
    policy_end: str | None = None,
    require_full_policy_window: bool = False,
) -> tuple[np.ndarray, np.ndarray, list[str], pd.DataFrame]:
    """Build supervised dataset for policy distillation from Stage12 rolling results.

    X features (per hourly row):
      - current SOC and cyclic time encodings
      - future 24h forecast PV sequence
      - future 24h price sequence
      - future 24h load sequence

    y labels:
      - planned_charge_kw, planned_discharge_kw (from Stage12 rolling DP)

    Only rows from the ``rolling_optimization`` scenario are used.

    Returns (X, y, feature_names, policy_frame).
    """
    rolling = stage12_results[
        stage12_results["scenario"] == "rolling_optimization"
    ].copy()
    if len(rolling) == 0:
        raise ValueError("No rolling_optimization rows found in stage12_results")

    rolling["dispatch_ts"] = pd.to_datetime(rolling["dispatch_timestamp"], utc=True)
    start_ts = _parse_policy_timestamp(policy_start, is_end=False)
    end_ts = _parse_policy_timestamp(policy_end, is_end=True)

    if require_full_policy_window and start_ts is not None and rolling["dispatch_ts"].min() > start_ts:
        raise ValueError(
            "Stage12 rolling labels start after the requested policy window: "
            f"available_start={rolling['dispatch_ts'].min()}, requested_start={start_ts}."
        )
    if require_full_policy_window and end_ts is not None and rolling["dispatch_ts"].max() < end_ts:
        raise ValueError(
            "Stage12 rolling labels end before the requested policy window: "
            f"available_end={rolling['dispatch_ts'].max()}, requested_end={end_ts}. "
            "Regenerate Stage12/Stage3 data or pass a narrower --policy-end."
        )

    if start_ts is not None:
        rolling = rolling[rolling["dispatch_ts"] >= start_ts].copy()
    if end_ts is not None:
        rolling = rolling[rolling["dispatch_ts"] <= end_ts].copy()
    if rolling.empty:
        raise ValueError(
            f"No rolling_optimization rows remain in policy window {start_ts} -> {end_ts}."
        )
    rolling = rolling.sort_values("dispatch_ts").reset_index(drop=True)

    lookahead_hours = 24
    feature_names = ["soc_start", "hour_sin", "hour_cos", "month_sin", "month_cos"]
    feature_names += [f"forecast_pv_kw_t_plus_{i}h" for i in range(lookahead_hours)]
    feature_names += [f"price_eur_mwh_t_plus_{i}h" for i in range(lookahead_hours)]
    feature_names += [f"load_mw_t_plus_{i}h" for i in range(lookahead_hours)]

    X_rows: list[list[float]] = []
    y_rows: list[list[float]] = []
    kept_indices: list[int] = []
    dropped_gap_count = 0

    for idx in range(0, len(rolling) - lookahead_hours + 1):
        row = rolling.iloc[idx]
        window = rolling.iloc[idx:idx + lookahead_hours]

        # Require a continuous hourly look-ahead window.  If a source file has a
        # gap, silently padding future features would teach the MLP a different
        # information boundary from Stage12, so the sample is dropped instead.
        expected_end = row["dispatch_ts"] + pd.Timedelta(hours=lookahead_hours - 1)
        if window["dispatch_ts"].iloc[-1] != expected_end:
            dropped_gap_count += 1
            continue

        hour = int(row["dispatch_ts"].hour)
        month = int(row["dispatch_ts"].month)
        features = [
            float(row["soc_start"]),
            float(np.sin(2 * np.pi * hour / 24)),
            float(np.cos(2 * np.pi * hour / 24)),
            float(np.sin(2 * np.pi * (month - 1) / 12)),
            float(np.cos(2 * np.pi * (month - 1) / 12)),
        ]
        features.extend(window["forecast_pv_kw"].astype(float).tolist())
        features.extend(window["price_eur_mwh"].astype(float).tolist())
        features.extend(window["load_mw"].astype(float).tolist())

        label = [
            float(row["planned_charge_kw"]),
            float(row["planned_discharge_kw"]),
        ]
        if np.isfinite(features).all() and np.isfinite(label).all():
            X_rows.append(features)
            y_rows.append(label)
            kept_indices.append(idx)

    if not X_rows:
        raise ValueError("No valid 24h look-ahead samples could be built for MLP policy.")

    X = np.asarray(X_rows, dtype=np.float32)
    y = np.asarray(y_rows, dtype=np.float32)
    policy_frame = rolling.iloc[kept_indices].copy().reset_index(drop=True)
    policy_frame.attrs["policy_start"] = str(start_ts) if start_ts is not None else None
    policy_frame.attrs["policy_end"] = str(end_ts) if end_ts is not None else None
    policy_frame.attrs["dropped_gap_count"] = int(dropped_gap_count)

    print(f"  Policy dataset: {len(X)} samples, {X.shape[1]} features (24h look-ahead)")
    return X, y, feature_names, policy_frame


def _postprocess_actions(
    charge_kw: np.ndarray,
    discharge_kw: np.ndarray,
    soc: np.ndarray,
    forecast_pv_kw: np.ndarray,
    capacity_kw: float,
    max_charge_kw: float,
    max_discharge_kw: float,
    capacity_kwh: float,
    charge_efficiency: float = 0.95,
    discharge_efficiency: float = 0.95,
    soc_min: float = 0.0,
    soc_max: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Apply physical constraints to predicted charge/discharge actions.

    Rules (in order):
    1. Clip to [0, max_power]
    2. If both charge and discharge > 0, keep only the larger one
    3. Clip planned charge by forecast PV and SOC room
    4. Clip planned discharge by available SOC energy and forecast export headroom
    """
    charge = np.clip(charge_kw, 0.0, max_charge_kw)
    discharge = np.clip(discharge_kw, 0.0, max_discharge_kw)
    forecast_pv = np.clip(forecast_pv_kw, 0.0, capacity_kw * 1.05)

    # De-conflict simultaneous charge/discharge
    both_positive = (charge > 0) & (discharge > 0)
    charge_larger = charge > discharge
    # If charge is larger, zero discharge; otherwise zero charge
    discharge[both_positive & charge_larger] = 0.0
    charge[both_positive & ~charge_larger] = 0.0

    # Planned actions use forecast information, matching Stage12's information
    # boundary.  Actual execution is clipped again by actual PV during replay.
    available_room_kw = np.maximum(
        0.0,
        ((soc_max - soc) * capacity_kwh) / charge_efficiency,
    )
    charge = np.minimum.reduce([charge, forecast_pv, available_room_kw])

    available_energy_kw = np.maximum(
        0.0,
        ((soc - soc_min) * capacity_kwh) * discharge_efficiency,
    )
    export_headroom_kw = np.maximum(capacity_kw - forecast_pv, 0.0)
    discharge = np.minimum.reduce([discharge, export_headroom_kw, available_energy_kw])

    return charge, discharge


def _classify_dispatch_direction(
    charge_kw: np.ndarray,
    discharge_kw: np.ndarray,
    *,
    eps_kw: float = 1e-3,
) -> np.ndarray:
    """Classify actions as idle=0, charge=1, discharge=2.

    The threshold prevents tiny neural-network residuals around zero from being
    counted as deliberate charge/discharge decisions.
    """
    result = np.zeros(len(charge_kw), dtype=int)
    charge_active = (charge_kw > eps_kw) & (charge_kw >= discharge_kw)
    discharge_active = (discharge_kw > eps_kw) & (discharge_kw > charge_kw)
    result[charge_active] = 1
    result[discharge_active] = 2
    return result


def _direction_metrics(pred_dir: np.ndarray, label_dir: np.ndarray) -> dict[str, Any]:
    """Compute robust direction metrics for imbalanced dispatch labels."""
    labels = [0, 1, 2]
    confusion = np.zeros((3, 3), dtype=int)
    for actual, pred in zip(label_dir, pred_dir, strict=False):
        confusion[int(actual), int(pred)] += 1

    f1_scores: list[float] = []
    recalls: dict[int, float] = {}
    for label in labels:
        tp = float(confusion[label, label])
        fp = float(confusion[:, label].sum() - tp)
        fn = float(confusion[label, :].sum() - tp)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        recalls[label] = recall
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
        f1_scores.append(f1)

    label_counts = {str(label): int((label_dir == label).sum()) for label in labels}
    majority_count = max(label_counts.values()) if label_counts else 0
    majority_baseline = majority_count / len(label_dir) if len(label_dir) else 0.0

    return {
        "direction_accuracy": float(np.mean(pred_dir == label_dir)) if len(label_dir) else 0.0,
        "direction_random_baseline": 1.0 / 3.0,
        "direction_majority_baseline": float(majority_baseline),
        "direction_macro_f1": float(np.mean(f1_scores)),
        "direction_idle_recall": float(recalls[0]),
        "direction_charge_recall": float(recalls[1]),
        "direction_discharge_recall": float(recalls[2]),
        "direction_charge_to_discharge_errors": int(confusion[1, 2]),
        "direction_discharge_to_charge_errors": int(confusion[2, 1]),
        "direction_label_distribution": label_counts,
        "direction_confusion_matrix": confusion.tolist(),
    }


def _train_mlp_policy(
    X: np.ndarray,
    y: np.ndarray,
    hidden_size: int = POLICY_HIDDEN,
    dropout: float = POLICY_DROPOUT,
    epochs: int = 50,
    patience: int = 10,
    batch_size: int = 256,
    random_state: int = 42,
) -> tuple[DispatchMLPPolicy, dict[str, Any], dict[str, Any]]:
    """Train the MLP dispatch policy with chronological split and early stopping.

    Returns (trained_model, final_metrics, training_report).
    """
    torch.manual_seed(random_state)
    np.random.seed(random_state)

    # Chronological 70/15/15 split
    n = len(X)
    train_end = int(n * 0.70)
    val_end = int(n * 0.85)

    # Standardize using train stats only
    X_train_raw = X[:train_end]
    X_val_raw = X[train_end:val_end]
    X_test_raw = X[val_end:]
    y_train_raw = y[:train_end]
    y_val_raw = y[train_end:val_end]
    y_test_raw = y[val_end:]

    x_mean = X_train_raw.mean(axis=0, keepdims=True)
    x_std = X_train_raw.std(axis=0, keepdims=True)
    x_std = np.where(x_std < 1e-8, 1.0, x_std)

    X_train = (X_train_raw - x_mean) / x_std
    X_val = (X_val_raw - x_mean) / x_std
    X_test = (X_test_raw - x_mean) / x_std

    # Build DataLoaders
    train_ds = TensorDataset(
        torch.tensor(X_train, dtype=torch.float32),
        torch.tensor(y_train_raw, dtype=torch.float32),
    )
    val_ds = TensorDataset(
        torch.tensor(X_val, dtype=torch.float32),
        torch.tensor(y_val_raw, dtype=torch.float32),
    )
    test_ds = TensorDataset(
        torch.tensor(X_test, dtype=torch.float32),
        torch.tensor(y_test_raw, dtype=torch.float32),
    )
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=False)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False)
    test_loader = DataLoader(test_ds, batch_size=batch_size, shuffle=False)

    # Model, loss, optimizer
    input_dim = X.shape[1]
    model = DispatchMLPPolicy(input_dim, hidden=hidden_size, dropout=dropout)
    criterion = nn.SmoothL1Loss()
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=POLICY_LR, weight_decay=POLICY_WEIGHT_DECAY
    )

    best_val_loss = float("inf")
    best_state: dict[str, Any] = {}
    patience_counter = 0
    train_losses: list[float] = []
    val_losses: list[float] = []

    for epoch in range(epochs):
        # Train
        model.train()
        epoch_loss = 0.0
        for xb, yb in train_loader:
            optimizer.zero_grad()
            pred = model(xb)
            loss = criterion(pred, yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=2.0)
            optimizer.step()
            epoch_loss += loss.item() * xb.size(0)
        epoch_loss /= len(train_loader.dataset)  # type: ignore[arg-type]
        train_losses.append(epoch_loss)

        # Validate
        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for xb, yb in val_loader:
                pred = model(xb)
                val_loss += criterion(pred, yb).item() * xb.size(0)
        val_loss /= len(val_loader.dataset)  # type: ignore[arg-type]
        val_losses.append(val_loss)

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            patience_counter = 0
        else:
            patience_counter += 1

        if patience_counter >= patience:
            print(f"    Early stop at epoch {epoch + 1}, best_val_loss={best_val_loss:.6f}")
            break

    # Restore best model
    if best_state:
        model.load_state_dict(best_state)

    # Test metrics
    model.eval()
    test_loss = 0.0
    all_preds: list[np.ndarray] = []
    all_labels: list[np.ndarray] = []
    with torch.no_grad():
        for xb, yb in test_loader:
            pred = model(xb)
            test_loss += criterion(pred, yb).item() * xb.size(0)
            all_preds.append(pred.numpy())
            all_labels.append(yb.numpy())
    test_loss /= len(test_loader.dataset)  # type: ignore[arg-type]
    test_preds = np.concatenate(all_preds)
    test_labels = np.concatenate(all_labels)

    # Direction metrics must be judged against the imbalanced teacher-label
    # distribution, not only against a 33.3% random baseline.
    pred_dir = _classify_dispatch_direction(test_preds[:, 0], test_preds[:, 1])
    label_dir = _classify_dispatch_direction(test_labels[:, 0], test_labels[:, 1])
    direction_report = _direction_metrics(pred_dir, label_dir)

    # Charge MAE, Discharge MAE
    charge_mae = float(np.mean(np.abs(test_preds[:, 0] - test_labels[:, 0])))
    discharge_mae = float(np.mean(np.abs(test_preds[:, 1] - test_labels[:, 1])))

    final_metrics = {
        "test_loss": float(test_loss),
        "best_val_loss": float(best_val_loss),
        **direction_report,
        "charge_mae_kw": charge_mae,
        "discharge_mae_kw": discharge_mae,
        "epochs_trained": len(train_losses),
    }

    training_report = {
        "train_losses": [float(v) for v in train_losses],
        "val_losses": [float(v) for v in val_losses],
        "x_mean": x_mean.flatten().tolist(),
        "x_std": x_std.flatten().tolist(),
        "n_train": int(train_end),
        "n_val": int(val_end - train_end),
        "n_test": int(n - val_end),
        "test_start_index": int(val_end),
    }

    print(f"    Test loss: {test_loss:.6f}  direction_acc: {direction_report['direction_accuracy']:.4f}"
          f"  majority: {direction_report['direction_majority_baseline']:.4f}"
          f"  macro_f1: {direction_report['direction_macro_f1']:.4f}"
          f"  charge_mae: {charge_mae:.4f}  discharge_mae: {discharge_mae:.4f}")

    return model, final_metrics, training_report


def _train_two_stage_policy(
    X: np.ndarray,
    y: np.ndarray,
    config: dict[str, Any],
    *,
    dropout: float = POLICY_DROPOUT,
    epochs: int = 50,
    patience: int = 10,
    batch_size: int = 256,
    random_state: int = 42,
    action_eps_kw: float,
) -> tuple[TwoStageDispatchPolicy, dict[str, Any], dict[str, Any]]:
    """Train a two-stage policy: direction classification + conditional power.

    The direction loss is deliberately primary because the current failure mode
    is not small kW error; it is physically opposite actions, especially
    discharge hours predicted as charge.  Power regression is only applied on
    samples where the teacher actually chose that direction.
    """
    torch.manual_seed(random_state)
    np.random.seed(random_state)

    storage_cfg = config["storage"]
    max_charge_kw = float(storage_cfg["max_charge_kw"])
    max_discharge_kw = float(storage_cfg["max_discharge_kw"])

    n = len(X)
    train_end = int(n * 0.70)
    val_end = int(n * 0.85)

    X_train_raw = X[:train_end]
    X_val_raw = X[train_end:val_end]
    X_test_raw = X[val_end:]
    y_train_raw = y[:train_end]
    y_val_raw = y[train_end:val_end]
    y_test_raw = y[val_end:]

    x_mean = X_train_raw.mean(axis=0, keepdims=True)
    x_std = X_train_raw.std(axis=0, keepdims=True)
    x_std = np.where(x_std < 1e-8, 1.0, x_std)

    X_train = (X_train_raw - x_mean) / x_std
    X_val = (X_val_raw - x_mean) / x_std
    X_test = (X_test_raw - x_mean) / x_std

    y_dir = _classify_dispatch_direction(y[:, 0], y[:, 1], eps_kw=action_eps_kw)
    y_train_dir = y_dir[:train_end]
    y_val_dir = y_dir[train_end:val_end]
    y_test_dir = y_dir[val_end:]

    # Power heads learn normalized magnitudes so the loss scale is stable across
    # battery configurations.  Direction gating later decides which head is used.
    y_charge_norm = np.clip(y[:, 0] / max(max_charge_kw, 1e-12), 0.0, 1.0)
    y_discharge_norm = np.clip(y[:, 1] / max(max_discharge_kw, 1e-12), 0.0, 1.0)
    y_power_norm = np.stack([y_charge_norm, y_discharge_norm], axis=1).astype(np.float32)

    train_ds = TensorDataset(
        torch.tensor(X_train, dtype=torch.float32),
        torch.tensor(y_train_dir, dtype=torch.long),
        torch.tensor(y_power_norm[:train_end], dtype=torch.float32),
    )
    val_ds = TensorDataset(
        torch.tensor(X_val, dtype=torch.float32),
        torch.tensor(y_val_dir, dtype=torch.long),
        torch.tensor(y_power_norm[train_end:val_end], dtype=torch.float32),
    )
    test_ds = TensorDataset(
        torch.tensor(X_test, dtype=torch.float32),
        torch.tensor(y_test_dir, dtype=torch.long),
        torch.tensor(y_power_norm[val_end:], dtype=torch.float32),
    )
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=False)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False)
    test_loader = DataLoader(test_ds, batch_size=batch_size, shuffle=False)

    class_counts = np.bincount(y_train_dir, minlength=3).astype(np.float32)
    if np.any(class_counts == 0):
        raise ValueError(f"Two-stage policy needs all direction classes in train split: {class_counts.tolist()}")
    class_weights = len(y_train_dir) / (3.0 * class_counts)
    class_weights = np.clip(class_weights, 0.25, 10.0)

    model = TwoStageDispatchPolicy(X.shape[1], dropout=dropout)
    ce_loss = nn.CrossEntropyLoss(weight=torch.tensor(class_weights, dtype=torch.float32))
    power_loss = nn.SmoothL1Loss()
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=POLICY_LR, weight_decay=POLICY_WEIGHT_DECAY
    )

    def _batch_loss(
        xb: torch.Tensor,
        dir_label: torch.Tensor,
        power_label: torch.Tensor,
    ) -> torch.Tensor:
        logits, charge_norm, discharge_norm = model(xb)
        direction_loss = ce_loss(logits, dir_label)

        charge_mask = dir_label == 1
        discharge_mask = dir_label == 2
        charge_loss = (
            power_loss(charge_norm[charge_mask], power_label[charge_mask, 0])
            if torch.any(charge_mask)
            else torch.zeros((), dtype=torch.float32)
        )
        discharge_loss = (
            power_loss(discharge_norm[discharge_mask], power_label[discharge_mask, 1])
            if torch.any(discharge_mask)
            else torch.zeros((), dtype=torch.float32)
        )

        probabilities = torch.softmax(logits, dim=1)
        # Opposite physical actions are the most expensive direction mistakes:
        # charge<->discharge errors can reverse the storage objective, while
        # idle mistakes are usually less destructive.
        charge_as_discharge = probabilities[dir_label == 1, 2]
        discharge_as_charge = probabilities[dir_label == 2, 1]
        opposite_penalty = torch.zeros((), dtype=torch.float32)
        if charge_as_discharge.numel() > 0:
            opposite_penalty = opposite_penalty + charge_as_discharge.mean()
        if discharge_as_charge.numel() > 0:
            opposite_penalty = opposite_penalty + discharge_as_charge.mean()

        return direction_loss + 0.5 * (charge_loss + discharge_loss) + 1.0 * opposite_penalty

    best_val_loss = float("inf")
    best_state: dict[str, Any] = {}
    patience_counter = 0
    train_losses: list[float] = []
    val_losses: list[float] = []

    for epoch in range(epochs):
        model.train()
        epoch_loss = 0.0
        for xb, dir_label, power_label in train_loader:
            optimizer.zero_grad()
            loss = _batch_loss(xb, dir_label, power_label)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=2.0)
            optimizer.step()
            epoch_loss += loss.item() * xb.size(0)
        epoch_loss /= len(train_loader.dataset)  # type: ignore[arg-type]
        train_losses.append(epoch_loss)

        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for xb, dir_label, power_label in val_loader:
                val_loss += _batch_loss(xb, dir_label, power_label).item() * xb.size(0)
        val_loss /= len(val_loader.dataset)  # type: ignore[arg-type]
        val_losses.append(val_loss)

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            patience_counter = 0
        else:
            patience_counter += 1
        if patience_counter >= patience:
            print(f"    Early stop at epoch {epoch + 1}, best_val_loss={best_val_loss:.6f}")
            break

    if best_state:
        model.load_state_dict(best_state)

    model.eval()
    test_loss = 0.0
    pred_dirs: list[np.ndarray] = []
    pred_charge: list[np.ndarray] = []
    pred_discharge: list[np.ndarray] = []
    label_dirs: list[np.ndarray] = []
    with torch.no_grad():
        for xb, dir_label, power_label in test_loader:
            logits, charge_norm, discharge_norm = model(xb)
            test_loss += _batch_loss(xb, dir_label, power_label).item() * xb.size(0)
            pred_dirs.append(torch.argmax(logits, dim=1).numpy())
            pred_charge.append((charge_norm.numpy() * max_charge_kw))
            pred_discharge.append((discharge_norm.numpy() * max_discharge_kw))
            label_dirs.append(dir_label.numpy())
    test_loss /= len(test_loader.dataset)  # type: ignore[arg-type]

    pred_dir = np.concatenate(pred_dirs)
    label_dir = np.concatenate(label_dirs)
    direction_report = _direction_metrics(pred_dir, label_dir)
    charge_pred_kw = np.concatenate(pred_charge)
    discharge_pred_kw = np.concatenate(pred_discharge)

    final_metrics = {
        "policy_mode": POLICY_MODE_TWO_STAGE,
        "test_loss": float(test_loss),
        "best_val_loss": float(best_val_loss),
        **direction_report,
        "charge_mae_kw": float(np.mean(np.abs(charge_pred_kw - y_test_raw[:, 0]))),
        "discharge_mae_kw": float(np.mean(np.abs(discharge_pred_kw - y_test_raw[:, 1]))),
        "epochs_trained": len(train_losses),
        "action_eps_kw": float(action_eps_kw),
        "class_weights": [float(v) for v in class_weights.tolist()],
    }

    training_report = {
        "train_losses": [float(v) for v in train_losses],
        "val_losses": [float(v) for v in val_losses],
        "x_mean": x_mean.flatten().tolist(),
        "x_std": x_std.flatten().tolist(),
        "n_train": int(train_end),
        "n_val": int(val_end - train_end),
        "n_test": int(n - val_end),
        "test_start_index": int(val_end),
    }

    print(
        f"    Test loss: {test_loss:.6f}  direction_acc: "
        f"{direction_report['direction_accuracy']:.4f}  majority: "
        f"{direction_report['direction_majority_baseline']:.4f}  macro_f1: "
        f"{direction_report['direction_macro_f1']:.4f}  discharge_recall: "
        f"{direction_report['direction_discharge_recall']:.4f}  d->c errors: "
        f"{direction_report['direction_discharge_to_charge_errors']}"
    )

    return model, final_metrics, training_report


# The exploratory replay implementation was removed after review because it bypassed
# Stage12 physical settlement.  The only supported replay path is _replay_policy.


def _replay_policy(
    model: DispatchMLPPolicy | TwoStageDispatchPolicy,
    policy_frame: pd.DataFrame,
    X: np.ndarray,
    config: dict[str, Any],
    x_mean: np.ndarray,
    x_std: np.ndarray,
    feature_names: list[str],
    test_start_index: int,
    capacity_kw: float = CAPACITY_KW,
    policy_mode: str = POLICY_MODE_REGRESSION,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Replay the MLP policy with Stage12-equivalent physical settlement.

    This function supersedes the exploratory replay above.  It keeps the
    24-hour look-ahead feature boundary, but replaces teacher SOC with the
    replayed SOC at each step so the neural policy is evaluated as an executable
    dispatch policy rather than as an offline label-fitting score.
    """

    storage_cfg = config["storage"]
    capacity_kwh = float(storage_cfg["capacity_kwh"])
    max_charge_kw = float(storage_cfg["max_charge_kw"])
    max_discharge_kw = float(storage_cfg["max_discharge_kw"])
    charge_efficiency = float(storage_cfg.get("charge_efficiency", 0.95))
    discharge_efficiency = float(storage_cfg.get("discharge_efficiency", 0.95))
    soc_min = float(storage_cfg["soc_min"])
    soc_max = float(storage_cfg["soc_max"])

    if "soc_start" not in feature_names:
        raise ValueError("Policy feature schema must include soc_start.")
    soc_feature_idx = feature_names.index("soc_start")
    test_rows = policy_frame.iloc[test_start_index:].copy().reset_index(drop=True)
    X_test = X[test_start_index:].copy()

    if test_rows.empty:
        raise ValueError("Policy replay test window is empty.")
    soc = float(test_rows.iloc[0]["soc_start"])
    replay_rows: list[dict[str, Any]] = []
    model.eval()
    for i in range(len(test_rows)):
        row = test_rows.iloc[i]
        x_row = X_test[i].copy()
        x_row[soc_feature_idx] = soc
        x_norm = (x_row - x_mean) / x_std
        with torch.no_grad():
            model_input = torch.tensor(x_norm[None, :], dtype=torch.float32)

        forecast_pv = _bounded_power(row["forecast_pv_kw"], 0.0, capacity_kw * 1.05)
        predicted_direction = -1
        if policy_mode == POLICY_MODE_TWO_STAGE:
            with torch.no_grad():
                logits, charge_norm, discharge_norm = model(model_input)  # type: ignore[misc]
            predicted_direction = int(torch.argmax(logits, dim=1).item())
            raw_charge = float(charge_norm.item()) * max_charge_kw if predicted_direction == 1 else 0.0
            raw_discharge = (
                float(discharge_norm.item()) * max_discharge_kw
                if predicted_direction == 2
                else 0.0
            )
        else:
            with torch.no_grad():
                raw_pred = model(model_input).numpy()[0]  # type: ignore[operator]
            raw_charge = float(raw_pred[0])
            raw_discharge = float(raw_pred[1])

        ch, disch = _postprocess_actions(
            np.array([raw_charge]),
            np.array([raw_discharge]),
            np.array([soc]),
            np.array([forecast_pv]),
            capacity_kw=capacity_kw,
            max_charge_kw=max_charge_kw,
            max_discharge_kw=max_discharge_kw,
            capacity_kwh=capacity_kwh,
            charge_efficiency=charge_efficiency,
            discharge_efficiency=discharge_efficiency,
            soc_min=soc_min,
            soc_max=soc_max,
        )
        planned_charge = float(ch[0])
        planned_discharge = float(disch[0])
        if predicted_direction < 0:
            predicted_direction = int(
                _classify_dispatch_direction(
                    np.array([planned_charge]),
                    np.array([planned_discharge]),
                )[0]
            )

        # Strict Stage12-style settlement: forecast limits the plan, realized PV
        # limits execution, grid export is capped, and SOC never leaves the
        # configured operating band.
        actual_pv = _bounded_power(row["actual_pv_kw"], 0.0, capacity_kw * 1.05)
        available_room_kw = max(((soc_max - soc) * capacity_kwh) / charge_efficiency, 0.0)
        available_energy_kw = max(((soc - soc_min) * capacity_kwh) * discharge_efficiency, 0.0)
        actual_charge = min(planned_charge, actual_pv, max_charge_kw, available_room_kw)
        actual_discharge = min(planned_discharge, max_discharge_kw, available_energy_kw)

        soc_new = soc + (actual_charge * charge_efficiency) / capacity_kwh
        soc_new -= (actual_discharge / discharge_efficiency) / capacity_kwh
        soc_new = float(np.clip(soc_new, soc_min, soc_max))

        planned_net_export = min(
            max(forecast_pv - planned_charge + planned_discharge, 0.0),
            capacity_kw,
        )
        actual_net_export_before_clip = max(actual_pv - actual_charge + actual_discharge, 0.0)
        actual_net_export = min(actual_net_export_before_clip, capacity_kw)
        curtailed = max(actual_net_export_before_clip - capacity_kw, 0.0)
        shortfall = max(planned_net_export - actual_net_export, 0.0)
        surplus = max(actual_net_export - planned_net_export, 0.0)

        price_eur_kwh = float(row["price_eur_mwh"]) / 1000.0
        storage_revenue = actual_net_export * price_eur_kwh
        no_storage_export = min(actual_pv, capacity_kw)
        no_storage_revenue = no_storage_export * price_eur_kwh
        planned_revenue = planned_net_export * price_eur_kwh

        replay_rows.append({
            "scenario": (
                "two_stage_policy_distillation"
                if policy_mode == POLICY_MODE_TWO_STAGE
                else "mlp_policy_distillation"
            ),
            "forecast_timestamp": row.get("forecast_timestamp", pd.NaT),
            "dispatch_timestamp": row["dispatch_timestamp"],
            "target": row.get("target", "target_pv_power_t_plus_24h"),
            "price_eur_mwh": row["price_eur_mwh"],
            "load_mw": row["load_mw"],
            "forecast_pv_kw": forecast_pv,
            "actual_pv_kw": actual_pv,
            "soc_start": soc,
            "soc_end": soc_new,
            "planned_charge_kw": planned_charge,
            "planned_discharge_kw": planned_discharge,
            "actual_charge_kw": actual_charge,
            "actual_discharge_kw": actual_discharge,
            "planned_net_export_kw": planned_net_export,
            "actual_net_export_kw": actual_net_export,
            "no_storage_export_kw": no_storage_export,
            "curtailed_kw": curtailed,
            "shortfall_kw": shortfall,
            "surplus_kw": surplus,
            "planned_revenue_eur": planned_revenue,
            "storage_revenue_eur": storage_revenue,
            "no_storage_revenue_eur": no_storage_revenue,
            "incremental_revenue_eur": storage_revenue - no_storage_revenue,
            "predicted_direction": predicted_direction,
            "teacher_planned_charge_kw": row["planned_charge_kw"],
            "teacher_planned_discharge_kw": row["planned_discharge_kw"],
            "teacher_direction": int(
                _classify_dispatch_direction(
                    np.array([row["planned_charge_kw"]]),
                    np.array([row["planned_discharge_kw"]]),
                )[0]
            ),
        })
        soc = soc_new

    replay_df = pd.DataFrame(replay_rows)

    def _aggregate(rows: pd.DataFrame, scenario: str) -> dict[str, Any]:
        constraints = _constraint_summary(rows, storage_cfg)
        total_charge = float(rows["actual_charge_kw"].sum())
        total_discharge = float(rows["actual_discharge_kw"].sum())
        return {
            "scenario": scenario,
            "sample_count": int(len(rows)),
            "total_storage_revenue_eur": float(rows["storage_revenue_eur"].sum()),
            "total_no_storage_revenue_eur": float(rows["no_storage_revenue_eur"].sum()),
            "incremental_revenue_eur": float(rows["incremental_revenue_eur"].sum()),
            "planned_revenue_eur": float(rows["planned_revenue_eur"].sum()),
            "total_charge_kwh": total_charge,
            "total_discharge_kwh": total_discharge,
            "cycle_equivalent_count": float(min(total_charge, total_discharge) / capacity_kwh),
            "total_curtailed_kwh": float(rows["curtailed_kw"].sum()),
            "total_shortfall_kwh": float(rows["shortfall_kw"].sum()),
            "total_surplus_kwh": float(rows["surplus_kw"].sum()),
            "mean_soc": float(rows["soc_end"].mean()),
            "min_soc": float(rows["soc_end"].min()),
            "max_soc": float(rows["soc_end"].max()),
            "capacity_kw": float(capacity_kw),
            **constraints,
        }

    teacher_rows = test_rows.copy()
    teacher_rows["scenario"] = "stage12_teacher_same_window"
    replay_scenario = (
        "two_stage_policy_distillation"
        if policy_mode == POLICY_MODE_TWO_STAGE
        else "mlp_policy_distillation"
    )
    metric_rows = [
        _aggregate(replay_df, replay_scenario),
        _aggregate(teacher_rows, "stage12_teacher_same_window"),
    ]

    mlp_increment = float(metric_rows[0]["incremental_revenue_eur"])
    teacher_increment = float(metric_rows[1]["incremental_revenue_eur"])
    retention = mlp_increment / teacher_increment if abs(teacher_increment) > 1e-9 else float("nan")
    for metric_row in metric_rows:
        metric_row["same_window_stage12_incremental_revenue_eur"] = teacher_increment
        metric_row["mlp_vs_stage12_incremental_revenue_delta_eur"] = mlp_increment - teacher_increment
        metric_row["mlp_revenue_retention_ratio"] = retention

    return replay_df, pd.DataFrame(metric_rows)


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def _json_ready(value: Any) -> Any:
    """Recursively convert report objects to strict JSON-compatible values."""
    if isinstance(value, dict):
        return {str(k): _json_ready(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_json_ready(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if np.isfinite(number) else None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return str(value)
    return value


def write_stage20_json(report: dict[str, Any], path: Path) -> None:
    """Write strict JSON without NaN/Inf values."""
    path.write_text(
        json.dumps(_json_ready(report), indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    print(f"  Stage20 JSON report: {path}")


def write_stage20_report(
    report: dict[str, Any],
    dl_metrics: pd.DataFrame,
    policy_metrics: pd.DataFrame,
    path: Path,
) -> None:
    """Write the corrected Stage20 Markdown report.

    The report deliberately avoids overstating the MLP result: direction
    accuracy is compared with the majority-class baseline, and revenue retention
    is only reported for the same Stage12 test window.
    """
    lines: list[str] = [
        "# Stage20 调度侧深度学习补强报告",
        "",
        "## 一、结论摘要",
        "",
        "- 本阶段将 Stage14 深度学习预测接入 Stage12 rolling 调度，并训练 MLP policy 蒸馏 Stage12 首小时动作。",
        "- 预测源调度消融已经限制到共同 dispatch timestamp 窗口，避免不同时间段收益不可比。",
        "- MLP 回放已改为 Stage12 物理结算口径：SOC 边界、PV 侧充电、并网容量、shortfall、curtailment 均纳入。",
        "- MLP 是 24h look-ahead rolling optimizer 的首动作近似，不是 PPO/DRL，也不应宣称最优调度。",
        "",
    ]

    ablation = report.get("ablation_entries", [])
    if ablation:
        first_ok = next((entry for entry in ablation if entry.get("status") == "ok"), {})
        lines += [
            "## 二、DL 预测驱动调度消融",
            "",
            f"- 共同调度窗口行数: {first_ok.get('common_window_rows', 'N/A')}",
            f"- 共同窗口起止: {first_ok.get('common_start', 'N/A')} -> {first_ok.get('common_end', 'N/A')}",
            "",
            "| 预测源 | common rows | 增量收益(EUR) | 短缺(kWh) | 弃光(kWh) | 等效循环 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for entry in ablation:
            if entry.get("status") != "ok":
                lines.append(f"| {entry.get('label')} | - | ERROR: {entry.get('error')} | - | - | - |")
                continue
            lines.append(
                f"| {entry['label']} | {entry.get('common_window_rows', 0)} | "
                f"{entry.get('incremental_revenue_eur', 0.0):.4f} | "
                f"{entry.get('total_shortfall_kwh', 0.0):.4f} | "
                f"{entry.get('total_curtailed_kwh', 0.0):.4f} | "
                f"{entry.get('cycle_equivalent_count', 0.0):.4f} |"
            )
        lines.append("")

    if not dl_metrics.empty:
        detail_cols = [
            "prediction_source",
            "scenario",
            "sample_count",
            "common_window_rows",
            "incremental_revenue_eur",
            "total_shortfall_kwh",
            "total_curtailed_kwh",
            "cycle_equivalent_count",
            "mean_soc",
        ]
        available = [col for col in detail_cols if col in dl_metrics.columns]
        lines += ["### 场景明细", "", dl_metrics[available].to_markdown(index=False), ""]

    lines += ["## 三、MLP 策略蒸馏", ""]
    if report.get("policy_trained"):
        training = report.get("policy_training", {})
        lines += [
            f"- 特征维度: {len(training.get('x_mean', []))}，包含当前 SOC、时间编码、未来 24h PV/price/load。",
            f"- 方向准确率: {training.get('direction_accuracy', 0.0):.4f}",
            f"- 多数类 baseline: {training.get('direction_majority_baseline', 0.0):.4f}",
            f"- 随机 baseline: {training.get('direction_random_baseline', 1 / 3):.4f}",
            f"- Macro-F1: {training.get('direction_macro_f1', 0.0):.4f}",
            f"- Charge MAE: {training.get('charge_mae_kw', 0.0):.4f} kW",
            f"- Discharge MAE: {training.get('discharge_mae_kw', 0.0):.4f} kW",
            f"- 训练/验证/测试样本: {training.get('n_train', 0)} / {training.get('n_val', 0)} / {training.get('n_test', 0)}",
            "",
            "### 方向混淆矩阵",
            "",
            "`rows=actual [idle, charge, discharge], cols=pred [idle, charge, discharge]`",
            "",
            f"`{training.get('direction_confusion_matrix', [])}`",
            "",
        ]
        if not policy_metrics.empty:
            lines += ["### 严格物理回放指标", "", policy_metrics.to_markdown(index=False), ""]
    else:
        lines += ["MLP policy 已跳过或训练失败。", ""]

    lines += ["## 四、质量门禁", ""]
    for gate, passed in report.get("quality_gates", {}).items():
        lines.append(f"- [{'PASS' if passed else 'FAIL'}] {gate}")

    lines += [
        "",
        "## Pitfall",
        "",
        "MLP policy distillation 只能说明神经网络对 Stage12 rolling 首动作有一定近似能力。若严格回放收益低于 Stage12，论文应如实表述为“深度学习调度策略可实现但未超过显式优化器”，不能写成端到端最优调度。",
        "",
        "## 阶段进度评估",
        "",
        "- 已完成: 公共窗口调度消融、24h look-ahead MLP、严格物理回放、baseline 修正。",
        "- 目标完成情况: Stage20 已从探索版推进到可审计实验版。",
        "- 下一阶段可行性: 可将本阶段表格写入论文调度侧深度学习章节；是否突出 MLP，取决于严格回放后的收益和 Macro-F1。",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Stage20 report: {path}")


def write_stage20b_report(
    report: dict[str, Any],
    dl_metrics: pd.DataFrame,
    policy_metrics: pd.DataFrame,
    path: Path,
) -> None:
    """Write a clean Stage20B report for the two-stage policy experiment."""
    training = report.get("policy_training", {})
    policy_window = report.get("policy_window", {})
    baseline = dict(report.get("stage20_regression_baseline", {}))
    if "direction_confusion_matrix" in baseline:
        base_confusion = np.asarray(baseline["direction_confusion_matrix"], dtype=float)
        if base_confusion.shape == (3, 3):
            discharge_total = float(base_confusion[2, :].sum())
            baseline.setdefault(
                "direction_discharge_recall",
                float(base_confusion[2, 2] / discharge_total) if discharge_total > 0 else 0.0,
            )
            baseline.setdefault("direction_discharge_to_charge_errors", int(base_confusion[2, 1]))
    ablation = report.get("ablation_entries", [])

    lines: list[str] = [
        "# Stage20B 两阶段神经调度策略报告",
        "",
        "## 结论摘要",
        "",
        "- 本报告不引入 PPO/DRL；调度侧深度学习采用监督式策略蒸馏。",
        "- Stage20B 将策略模型改为 two-stage：先判断 idle/charge/discharge，再按方向预测功率。",
        "- 策略回放继续使用 Stage12 物理结算口径：SOC 边界、PV 侧充电、并网容量、shortfall 和 curtailment 均纳入。",
        "- 预测源调度消融仍使用公共 dispatch timestamp 窗口，不能和全年策略蒸馏混成同一张公平对比表。",
        "",
    ]

    if ablation:
        first_ok = next((entry for entry in ablation if entry.get("status") == "ok"), {})
        lines += [
            "## DL 预测驱动调度消融",
            "",
            f"- 公共窗口行数: {first_ok.get('common_window_rows', 'N/A')}",
            f"- 公共窗口: {first_ok.get('common_start', 'N/A')} -> {first_ok.get('common_end', 'N/A')}",
            "",
            "| 预测源 | common rows | 增量收益(EUR) | 短缺(kWh) | 弃光(kWh) | 等效循环 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for entry in ablation:
            if entry.get("status") != "ok":
                lines.append(f"| {entry.get('label')} | - | ERROR: {entry.get('error')} | - | - | - |")
                continue
            lines.append(
                f"| {entry['label']} | {entry.get('common_window_rows', 0)} | "
                f"{entry.get('incremental_revenue_eur', 0.0):.4f} | "
                f"{entry.get('total_shortfall_kwh', 0.0):.4f} | "
                f"{entry.get('total_curtailed_kwh', 0.0):.4f} | "
                f"{entry.get('cycle_equivalent_count', 0.0):.4f} |"
            )
        lines.append("")

    lines += ["## Two-stage Policy Distillation", ""]
    if report.get("policy_trained"):
        lines += [
            f"- 策略窗口: {policy_window.get('actual_start', 'N/A')} -> {policy_window.get('actual_end', 'N/A')}",
            f"- 窗口样本: {policy_window.get('dataset_rows', 'N/A')}",
            f"- 24h look-ahead 缺口丢弃样本: {policy_window.get('dropped_gap_count', 0)}",
            f"- action_eps_kw: {report.get('action_eps_kw', 'N/A')}",
            "",
            "| 指标 | Stage20B two-stage | 原 Stage20 regression MLP |",
            "|---|---:|---:|",
            (
                f"| Direction accuracy | {training.get('direction_accuracy', 0.0):.4f} | "
                f"{baseline.get('direction_accuracy', float('nan')):.4f} |"
            ),
            (
                f"| Majority baseline | {training.get('direction_majority_baseline', 0.0):.4f} | "
                f"{baseline.get('direction_majority_baseline', float('nan')):.4f} |"
            ),
            (
                f"| Macro-F1 | {training.get('direction_macro_f1', 0.0):.4f} | "
                f"{baseline.get('direction_macro_f1', float('nan')):.4f} |"
            ),
            (
                f"| Discharge recall | {training.get('direction_discharge_recall', 0.0):.4f} | "
                f"{baseline.get('direction_discharge_recall', float('nan')):.4f} |"
            ),
            (
                f"| Discharge -> charge errors | "
                f"{training.get('direction_discharge_to_charge_errors', 0)} | "
                f"{baseline.get('direction_discharge_to_charge_errors', 'N/A')} |"
            ),
            "",
            f"- 随机 baseline: {training.get('direction_random_baseline', 1 / 3):.4f}",
            f"- Charge MAE: {training.get('charge_mae_kw', 0.0):.4f} kW",
            f"- Discharge MAE: {training.get('discharge_mae_kw', 0.0):.4f} kW",
            f"- 训练/验证/测试样本: {training.get('n_train', 0)} / {training.get('n_val', 0)} / {training.get('n_test', 0)}",
            "",
            "### 方向混淆矩阵",
            "",
            "`rows=actual [idle, charge, discharge], cols=pred [idle, charge, discharge]`",
            "",
            f"`{training.get('direction_confusion_matrix', [])}`",
            "",
        ]
        if not policy_metrics.empty:
            lines += ["### 严格物理回放指标", "", policy_metrics.to_markdown(index=False), ""]
    else:
        lines += ["Two-stage policy 训练失败或被跳过。", ""]

    lines += ["## 质量门禁", ""]
    for gate, passed in report.get("quality_gates", {}).items():
        lines.append(f"- [{'PASS' if passed else 'FAIL'}] {gate}")

    lines += [
        "",
        "## Pitfall",
        "",
        "Stage20B 的全年策略蒸馏结果不能和 Stage14 预测源公共窗口消融混成一个公平对比表；二者回答的问题不同。",
        "",
        "## 阶段进度评估",
        "",
        "- 已完成: two-stage policy 训练、严格物理回放、同窗口 Stage12 teacher 对比和 baseline 报告。",
        "- 目标完成情况: 重点看 discharge->charge 错误是否下降、Macro-F1 是否高于原 MLP。",
        "- 下一阶段可行性: 若 two-stage 仍弱，可继续尝试 TCN policy；不建议直接转 PPO/DRL。",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Stage20B report: {path}")


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def run_stage20_neural_dispatch(
    stage9_predictions: pd.DataFrame,
    stage14_predictions: pd.DataFrame,
    stage12_results: pd.DataFrame,
    feature_frame: pd.DataFrame,
    config: dict[str, Any],
    *,
    dl_candidates: list[dict[str, str]] | None = None,
    horizon_hours: int = HORIZON_HOURS_DEFAULT,
    policy_hidden_size: int = POLICY_HIDDEN,
    policy_dropout: float = POLICY_DROPOUT,
    policy_epochs: int = 50,
    policy_patience: int = 10,
    policy_batch_size: int = 256,
    policy_mode: str = POLICY_MODE_TWO_STAGE,
    policy_start: str | None = POLICY_START_DEFAULT,
    policy_end: str | None = POLICY_END_DEFAULT,
    action_eps_ratio: float = 0.01,
    skip_policy: bool = False,
    random_state: int = 42,
    output_paths: dict[str, Path] | None = None,
) -> Stage20NeuralDispatchResult:
    """Run Stage20 dispatch-side DL experiments.

    Parameters
    ----------
    stage9_predictions : pd.DataFrame
        LightGBM baseline predictions in Stage9 format.
    stage14_predictions : pd.DataFrame
        DL model predictions in Stage14 format.
    stage12_results : pd.DataFrame
        Stage12 rolling optimization hourly results (for policy labels).
    feature_frame : pd.DataFrame
        Stage3 feature table with market signals (load_mw, price_eur_mwh).
    config : dict
        Site + storage configuration dict.
    dl_candidates : list[dict] | None
        DL model:feature_set pairs to evaluate. Defaults to TCN/DLinear combos.
    horizon_hours : int
        Forecast horizon (default 24).
    policy_hidden_size : int
        MLP hidden layer size.
    policy_dropout : float
        MLP dropout rate.
    policy_epochs : int
        Max training epochs.
    policy_patience : int
        Early stopping patience.
    policy_batch_size : int
        Training batch size.
    skip_policy : bool
        If True, skip MLP policy distillation.
    random_state : int
        Random seed for reproducibility.
    output_paths : dict | None
        Paths for output files (results_csv, metrics_csv, report_json, report_md).

    Returns
    -------
    Stage20NeuralDispatchResult
    """
    if dl_candidates is None:
        dl_candidates = DL_CANDIDATES_DEFAULT
    if policy_mode not in {POLICY_MODE_REGRESSION, POLICY_MODE_TWO_STAGE}:
        raise ValueError(
            f"policy_mode must be '{POLICY_MODE_REGRESSION}' or '{POLICY_MODE_TWO_STAGE}', "
            f"got {policy_mode!r}."
        )

    capacity_kw = float(config["site"]["capacity_kw"])
    storage_cfg = config["storage"]
    action_eps_kw = max(
        1e-6,
        float(action_eps_ratio)
        * max(float(storage_cfg["max_charge_kw"]), float(storage_cfg["max_discharge_kw"])),
    )
    report: dict[str, Any] = {
        "n_stage12_rows": len(stage12_results),
        "n_prediction_sources": 0,
        "policy_trained": False,
        "policy_mode": policy_mode,
        "policy_window": {
            "start": policy_start,
            "end": policy_end,
            "require_full_window": policy_mode == POLICY_MODE_TWO_STAGE,
        },
        "action_eps_kw": float(action_eps_kw),
        "ablation_entries": [],
        "policy_training": {},
        "quality_gates": {},
        "output_paths": {str(k): str(v) for k, v in (output_paths or {}).items()},
    }

    # ---- Experiment 1: DL dispatch ablation ----
    print("=" * 60)
    print("Experiment 1: DL prediction-driven dispatch ablation")
    print("=" * 60)

    dl_metrics, ablation_entries = _run_dl_dispatch_ablation(
        stage9_predictions, stage14_predictions,
        feature_frame, config, dl_candidates, horizon_hours,
    )
    report["ablation_entries"] = ablation_entries
    report["n_prediction_sources"] = len(ablation_entries)

    # Quality gate: at least LightGBM + 3 DL candidates succeeded
    n_ok = sum(1 for e in ablation_entries if e.get("status") == "ok")
    report["quality_gates"]["at_least_4_sources_ok"] = n_ok >= 4
    common_rows = {
        int(e.get("common_window_rows", -1))
        for e in ablation_entries
        if e.get("status") == "ok"
    }
    report["quality_gates"]["dispatch_common_window_consistent"] = (
        n_ok > 0 and len(common_rows) == 1 and next(iter(common_rows)) > 0
    )

    # ---- Experiment 2: MLP policy distillation ----
    policy_replay = pd.DataFrame()
    policy_metrics = pd.DataFrame()

    if skip_policy:
        print("\n  Skipping MLP policy distillation (--skip-policy).")
    else:
        print("\n" + "=" * 60)
        print(f"Experiment 2: {policy_mode} dispatch policy distillation")
        print("=" * 60)

        try:
            X, y, feature_names, policy_frame = _build_policy_dataset(
                stage12_results,
                feature_frame,
                capacity_kw,
                policy_start=policy_start if policy_mode == POLICY_MODE_TWO_STAGE else None,
                policy_end=policy_end if policy_mode == POLICY_MODE_TWO_STAGE else None,
                require_full_policy_window=policy_mode == POLICY_MODE_TWO_STAGE,
            )
            report["policy_window"]["actual_start"] = str(policy_frame["dispatch_ts"].min())
            report["policy_window"]["actual_end"] = str(policy_frame["dispatch_ts"].max())
            report["policy_window"]["dataset_rows"] = int(len(policy_frame))
            report["policy_window"]["dropped_gap_count"] = int(
                policy_frame.attrs.get("dropped_gap_count", 0)
            )

            if policy_mode == POLICY_MODE_TWO_STAGE:
                model, train_metrics, train_report = _train_two_stage_policy(
                    X,
                    y,
                    config,
                    dropout=policy_dropout,
                    epochs=policy_epochs,
                    patience=policy_patience,
                    batch_size=policy_batch_size,
                    random_state=random_state,
                    action_eps_kw=action_eps_kw,
                )
            else:
                model, train_metrics, train_report = _train_mlp_policy(
                    X, y,
                    hidden_size=policy_hidden_size,
                    dropout=policy_dropout,
                    epochs=policy_epochs,
                    patience=policy_patience,
                    batch_size=policy_batch_size,
                    random_state=random_state,
                )
                train_metrics["policy_mode"] = POLICY_MODE_REGRESSION
            report["policy_trained"] = True
            report["policy_training"] = {**train_metrics, **train_report}

            x_mean_arr = np.array(train_report["x_mean"], dtype=np.float32)
            x_std_arr = np.array(train_report["x_std"], dtype=np.float32)

            policy_replay, policy_metrics = _replay_policy(
                model, policy_frame, X, config,
                x_mean=x_mean_arr, x_std=x_std_arr,
                feature_names=feature_names,
                test_start_index=int(train_report["test_start_index"]),
                capacity_kw=capacity_kw,
                policy_mode=policy_mode,
            )
            report["policy_trained"] = True

            # Policy quality gates
            report["quality_gates"]["policy_direction_accuracy_beats_majority"] = (
                train_metrics.get("direction_accuracy", 0)
                >= train_metrics.get("direction_majority_baseline", 1)
            )
            report["quality_gates"]["policy_macro_f1_above_stage20_regression"] = (
                train_metrics.get("direction_macro_f1", 0.0) > 0.3806
                if policy_mode == POLICY_MODE_TWO_STAGE
                else False
            )
            report["quality_gates"]["policy_has_discharge_recall"] = (
                train_metrics.get("direction_discharge_recall", 0.0) > 0.0
            )
            if not policy_metrics.empty:
                report["quality_gates"]["policy_soc_in_bounds"] = bool(
                    policy_metrics.iloc[0].get("soc_within_bounds", False)
                )
                report["quality_gates"]["policy_no_simultaneous_cd"] = bool(
                    policy_metrics.iloc[0].get("no_simultaneous_charge_discharge", False)
                )
                report["quality_gates"]["policy_stage12_physical_replay_passed"] = bool(
                    policy_metrics.iloc[0].get("soc_within_bounds", False)
                    and policy_metrics.iloc[0].get("charge_power_within_limit", False)
                    and policy_metrics.iloc[0].get("discharge_power_within_limit", False)
                    and policy_metrics.iloc[0].get("no_simultaneous_charge_discharge", False)
                    and policy_metrics.iloc[0].get("energy_balance_error_within_tolerance", False)
                )
        except Exception as exc:
            print(f"  ERROR in policy distillation: {exc}", file=sys.stderr)
            if policy_mode == POLICY_MODE_TWO_STAGE:
                raise
            report["quality_gates"]["policy_training_error"] = False
            report["policy_trained"] = False

    # ---- Write outputs (CSVs only; report is written by CLI) ----
    if output_paths:
        results_csv = output_paths.get("results_csv")
        policy_csv = output_paths.get("policy_replay_csv")
        policy_metrics_csv = output_paths.get("policy_metrics_csv")

        if results_csv and not dl_metrics.empty:
            dl_metrics.to_csv(results_csv, index=False)
            print(f"  DL dispatch metrics: {results_csv}")

        if policy_csv and not policy_replay.empty:
            policy_replay.to_csv(policy_csv, index=False)
            print(f"  Policy replay: {policy_csv}")

        if policy_metrics_csv and not policy_metrics.empty:
            policy_metrics.to_csv(policy_metrics_csv, index=False)
            print(f"  Policy metrics: {policy_metrics_csv}")

    return Stage20NeuralDispatchResult(
        dl_dispatch_metrics=dl_metrics,
        neural_policy_replay=policy_replay,
        neural_policy_metrics=policy_metrics,
        report=report,
    )
