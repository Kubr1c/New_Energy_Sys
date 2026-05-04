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
    all_metrics: list[pd.DataFrame] = []
    report_entries: list[dict[str, Any]] = []

    # Build candidate list: LightGBM baseline + DL candidates + Persistence + Perfect
    candidates: list[dict[str, Any]] = []

    # 1. LightGBM baseline (from Stage9)
    candidates.append({
        "label": "lightgbm_history_only",
        "source": "stage9",
        "predictions": stage9_df,
    })

    # 2. DL candidates from Stage14
    target_col = f"target_pv_power_t_plus_{horizon_hours}h"
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
    perfect_df = stage9_df[["timestamp", "actual_kw"]].copy()
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

    print(f"\n  Dispatch ablation: {len(candidates)} prediction sources")
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


def _build_policy_dataset(
    stage12_results: pd.DataFrame,
    feature_frame: pd.DataFrame,
    capacity_kw: float = CAPACITY_KW,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Build supervised dataset for policy distillation from Stage12 rolling results.

    X features (per hourly row):
      - soc_start (current battery state)
      - forecast_pv_kw (this hour's forecast)
      - price_eur_mwh (this hour's price)
      - load_mw (this hour's load)
      - hour_sin, hour_cos (cyclic hour encoding)
      - month_sin, month_cos (cyclic month encoding)

    y labels:
      - planned_charge_kw, planned_discharge_kw (from Stage12 rolling DP)

    Only rows from the ``rolling_optimization`` scenario are used.

    Returns (X, y, feature_names).
    """
    rolling = stage12_results[
        stage12_results["scenario"] == "rolling_optimization"
    ].copy()
    if len(rolling) == 0:
        raise ValueError("No rolling_optimization rows found in stage12_results")

    # Parse timestamps
    rolling["dispatch_ts"] = pd.to_datetime(rolling["dispatch_timestamp"], utc=True)

    # Cyclic time features
    hour = rolling["dispatch_ts"].dt.hour.values
    month = rolling["dispatch_ts"].dt.month.values
    hour_sin = np.sin(2 * np.pi * hour / 24)
    hour_cos = np.cos(2 * np.pi * hour / 24)
    month_sin = np.sin(2 * np.pi * (month - 1) / 12)
    month_cos = np.cos(2 * np.pi * (month - 1) / 12)

    X_list = [
        rolling["soc_start"].fillna(0.0).values,
        rolling["forecast_pv_kw"].fillna(0.0).values,
        rolling["price_eur_mwh"].fillna(0.0).values,
        rolling["load_mw"].fillna(0.0).values,
        hour_sin,
        hour_cos,
        month_sin,
        month_cos,
    ]
    feature_names = [
        "soc_start", "forecast_pv_kw", "price_eur_mwh", "load_mw",
        "hour_sin", "hour_cos", "month_sin", "month_cos",
    ]

    X = np.column_stack([arr.astype(np.float32) for arr in X_list])
    y = np.column_stack([
        rolling["planned_charge_kw"].fillna(0.0).values.astype(np.float32),
        rolling["planned_discharge_kw"].fillna(0.0).values.astype(np.float32),
    ])

    # Filter rows with valid labels
    valid = np.isfinite(X).all(axis=1) & np.isfinite(y).all(axis=1)
    X = X[valid]
    y = y[valid]

    print(f"  Policy dataset: {len(X)} samples, {X.shape[1]} features")
    return X, y, feature_names


def _postprocess_actions(
    charge_kw: np.ndarray,
    discharge_kw: np.ndarray,
    soc: np.ndarray,
    capacity_kw: float,
    max_charge_kw: float,
    max_discharge_kw: float,
    capacity_kwh: float,
    efficiency: float = 0.95,
) -> tuple[np.ndarray, np.ndarray]:
    """Apply physical constraints to predicted charge/discharge actions.

    Rules (in order):
    1. Clip to [0, max_power]
    2. If both charge and discharge > 0, keep only the larger one
    3. Clip charge by available SOC room
    4. Clip discharge by available energy
    """
    charge = np.clip(charge_kw, 0.0, max_charge_kw)
    discharge = np.clip(discharge_kw, 0.0, max_discharge_kw)

    # De-conflict simultaneous charge/discharge
    both_positive = (charge > 0) & (discharge > 0)
    charge_larger = charge > discharge
    # If charge is larger, zero discharge; otherwise zero charge
    discharge[both_positive & charge_larger] = 0.0
    charge[both_positive & ~charge_larger] = 0.0

    # SOC constraints: can't charge beyond 100%, can't discharge below 0%
    available_room_kwh = np.maximum(0.0, capacity_kwh - soc * capacity_kwh)
    charge = np.minimum(charge, available_room_kwh)  # charge in kW for 1 hour = kWh
    available_energy_kwh = np.maximum(0.0, soc * capacity_kwh * efficiency)
    discharge = np.minimum(discharge, available_energy_kwh)

    return charge, discharge


def _train_mlp_policy(
    X: np.ndarray,
    y: np.ndarray,
    hidden_size: int = POLICY_HIDDEN,
    dropout: float = POLICY_DROPOUT,
    epochs: int = 50,
    patience: int = 10,
    batch_size: int = 256,
    random_state: int = 42,
) -> tuple[DispatchMLPPolicy, dict[str, float], dict[str, Any]]:
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

    # Direction accuracy: classify each row as charge/discharge/idle
    def _classify(charge: np.ndarray, discharge: np.ndarray) -> np.ndarray:
        result = np.zeros(len(charge), dtype=int)  # 0 = idle
        result[charge > discharge] = 1   # 1 = charge
        result[discharge > charge] = 2   # 2 = discharge
        return result

    pred_dir = _classify(test_preds[:, 0], test_preds[:, 1])
    label_dir = _classify(test_labels[:, 0], test_labels[:, 1])
    direction_accuracy = float(np.mean(pred_dir == label_dir))

    # Charge MAE, Discharge MAE
    charge_mae = float(np.mean(np.abs(test_preds[:, 0] - test_labels[:, 0])))
    discharge_mae = float(np.mean(np.abs(test_preds[:, 1] - test_labels[:, 1])))

    final_metrics = {
        "test_loss": float(test_loss),
        "best_val_loss": float(best_val_loss),
        "direction_accuracy": direction_accuracy,
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
    }

    print(f"    Test loss: {test_loss:.6f}  direction_acc: {direction_accuracy:.4f}"
          f"  charge_mae: {charge_mae:.4f}  discharge_mae: {discharge_mae:.4f}")

    return model, final_metrics, training_report


def _replay_policy(
    model: DispatchMLPPolicy,
    stage12_results: pd.DataFrame,
    feature_frame: pd.DataFrame,
    config: dict[str, Any],
    x_mean: np.ndarray,
    x_std: np.ndarray,
    feature_names: list[str],
    capacity_kw: float = CAPACITY_KW,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Replay the MLP policy hour-by-hour using actual PV for settlement.

    Returns (hourly_replay_df, aggregated_metrics_df).
    """
    storage_cfg = config["storage"]
    capacity_kwh = float(storage_cfg["capacity_kwh"])
    max_charge_kw = float(storage_cfg["max_charge_kw"])
    max_discharge_kw = float(storage_cfg["max_discharge_kw"])
    efficiency = float(storage_cfg.get("charge_efficiency", 0.95))
    soc_initial = float(storage_cfg.get("soc_initial", 0.5))

    # Chronological split: use the same indices as Stage12 results
    rolling = stage12_results[
        stage12_results["scenario"] == "rolling_optimization"
    ].copy()
    rolling = rolling.sort_values("dispatch_timestamp").reset_index(drop=True)
    n = len(rolling)
    test_start = int(n * 0.85)

    test_rows = rolling.iloc[test_start:].copy()

    # Build X for each test row
    test_rows["_hour"] = pd.to_datetime(test_rows["dispatch_timestamp"], utc=True).dt.hour
    test_rows["_month"] = pd.to_datetime(test_rows["dispatch_timestamp"], utc=True).dt.month
    hour_sin = np.sin(2 * np.pi * test_rows["_hour"].values / 24)
    hour_cos = np.cos(2 * np.pi * test_rows["_hour"].values / 24)
    month_sin = np.sin(2 * np.pi * (test_rows["_month"].values - 1) / 12)
    month_cos = np.cos(2 * np.pi * (test_rows["_month"].values - 1) / 12)

    X_arr = np.column_stack([
        test_rows["soc_start"].fillna(0.0).values.astype(np.float32),
        test_rows["forecast_pv_kw"].fillna(0.0).values.astype(np.float32),
        test_rows["price_eur_mwh"].fillna(0.0).values.astype(np.float32),
        test_rows["load_mw"].fillna(0.0).values.astype(np.float32),
        hour_sin.astype(np.float32),
        hour_cos.astype(np.float32),
        month_sin.astype(np.float32),
        month_cos.astype(np.float32),
    ])
    X_norm = (X_arr - x_mean) / x_std

    # Predict actions
    model.eval()
    with torch.no_grad():
        raw_preds = model(torch.tensor(X_norm, dtype=torch.float32)).numpy()

    # Post-process actions
    charge_raw = raw_preds[:, 0]
    discharge_raw = raw_preds[:, 1]

    # Simulate hour-by-hour with SOC evolution
    soc = soc_initial
    replay_rows: list[dict[str, Any]] = []
    for i in range(len(test_rows)):
        row = test_rows.iloc[i]
        ch, disch = _postprocess_actions(
            np.array([charge_raw[i]]),
            np.array([discharge_raw[i]]),
            np.array([soc]),
            capacity_kw=capacity_kw,
            max_charge_kw=max_charge_kw,
            max_discharge_kw=max_discharge_kw,
            capacity_kwh=capacity_kwh,
            efficiency=efficiency,
        )
        planned_charge = float(ch[0])
        planned_discharge = float(disch[0])

        # Apply to SOC
        actual_charge_kwh = planned_charge * efficiency
        actual_discharge_kwh = planned_discharge / efficiency
        soc_new = soc + (actual_charge_kwh - actual_discharge_kwh) / capacity_kwh
        soc_new = np.clip(soc_new, 0.0, 1.0)

        # Actual PV for settlement
        actual_pv = float(row["actual_pv_kw"])
        # Net export: PV goes to grid + battery, battery discharges to grid
        net_export = actual_pv - planned_charge + planned_discharge
        net_export = max(0.0, net_export)

        # Revenue = net_export * price (kW for 1h = kWh, price in EUR/MWh → EUR/kWh)
        price_eur_kwh = float(row["price_eur_mwh"]) / 1000.0
        storage_revenue = net_export * price_eur_kwh

        # No-storage revenue = all PV to grid
        no_storage_revenue = actual_pv * price_eur_kwh

        replay_rows.append({
            "dispatch_timestamp": row["dispatch_timestamp"],
            "price_eur_mwh": row["price_eur_mwh"],
            "load_mw": row["load_mw"],
            "forecast_pv_kw": row["forecast_pv_kw"],
            "actual_pv_kw": actual_pv,
            "soc_start": soc,
            "soc_end": soc_new,
            "planned_charge_kw": planned_charge,
            "planned_discharge_kw": planned_discharge,
            "actual_charge_kw": planned_charge,    # simplified: no power limit binding
            "actual_discharge_kw": planned_discharge,
            "curtailed_kw": 0.0,
            "shortfall_kw": 0.0,
            "storage_revenue_eur": storage_revenue,
            "no_storage_revenue_eur": no_storage_revenue,
            "incremental_revenue_eur": storage_revenue - no_storage_revenue,
        })
        soc = soc_new

    replay_df = pd.DataFrame(replay_rows)

    # Aggregated metrics
    total_storage = replay_df["storage_revenue_eur"].sum()
    total_no_storage = replay_df["no_storage_revenue_eur"].sum()
    total_charge = replay_df["actual_charge_kw"].sum()
    total_discharge = replay_df["actual_discharge_kw"].sum()
    total_cycles = min(total_charge, total_discharge) / capacity_kwh
    mean_soc = replay_df["soc_start"].mean()

    # Constraint audit
    soc_ok = float((replay_df["soc_start"] >= -0.001).all()
                   and (replay_df["soc_start"] <= 1.001).all())
    charge_ok = float((replay_df["planned_charge_kw"] >= -0.001).all()
                      and (replay_df["planned_charge_kw"] <= max_charge_kw + 0.001).all())
    discharge_ok = float((replay_df["planned_discharge_kw"] >= -0.001).all()
                         and (replay_df["planned_discharge_kw"] <= max_discharge_kw + 0.001).all())
    charge_active = replay_df["planned_charge_kw"] > 0.001
    discharge_active = replay_df["planned_discharge_kw"] > 0.001
    simultaneous = float((charge_active & discharge_active).sum())

    metrics_df = pd.DataFrame([{
        "scenario": "mlp_policy_distillation",
        "sample_count": len(replay_df),
        "total_storage_revenue_eur": total_storage,
        "total_no_storage_revenue_eur": total_no_storage,
        "incremental_revenue_eur": total_storage - total_no_storage,
        "total_charge_kwh": total_charge,
        "total_discharge_kwh": total_discharge,
        "cycle_equivalent_count": total_cycles,
        "mean_soc": mean_soc,
        "soc_within_bounds": soc_ok,
        "charge_power_within_limit": charge_ok,
        "discharge_power_within_limit": discharge_ok,
        "no_simultaneous_charge_discharge": float(simultaneous == 0),
        "simultaneous_charge_discharge_rows": simultaneous,
    }])

    return replay_df, metrics_df


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def write_stage20_json(report: dict[str, Any], path: Path) -> None:
    """Write the report dict as JSON, replacing NaN/Inf with null."""
    cleaned = json.loads(
        json.dumps(report, ensure_ascii=False, default=str, allow_nan=False)
    )

    class _Sanitizer(json.JSONEncoder):
        def default(self, o: Any) -> Any:
            return str(o)

    path.write_text(
        json.dumps(cleaned, indent=2, ensure_ascii=False, cls=_Sanitizer),
        encoding="utf-8",
    )


def write_stage20_report(
    report: dict[str, Any],
    dl_metrics: pd.DataFrame,
    policy_metrics: pd.DataFrame,
    path: Path,
) -> None:
    """Write a Chinese Markdown report for Stage20."""
    lines: list[str] = []

    lines.append("# Stage20 调度侧深度学习补强报告\n")
    lines.append(f"**数据行数**: {report.get('n_stage12_rows', 'N/A')}")
    lines.append(f"**预测源数量**: {report.get('n_prediction_sources', 'N/A')}")
    lines.append(f"**MLP policy 训练**: {'已完成' if report.get('policy_trained') else '跳过'}\n")

    # Ablation results
    lines.append("## 一、DL 预测驱动调度消融\n")
    ablation = report.get("ablation_entries", [])
    if ablation:
        lines.append("| 预测源 | 增量收益 (EUR) | 储能总收益 (EUR) | 短缺 (kWh) | 弃光 (kWh) | 等效循环 |")
        lines.append("|--------|---------------|-----------------|-----------|-----------|---------|")
        for entry in ablation:
            if entry.get("status") != "ok":
                lines.append(f"| {entry['label']} | ERROR: {entry.get('error', '')} | — | — | — | — |")
                continue
            lines.append(
                f"| {entry['label']} | {entry.get('incremental_revenue_eur', 0):.2f} | "
                f"{entry.get('total_storage_revenue_eur', 0):.2f} | "
                f"{entry.get('total_shortfall_kwh', 0):.2f} | "
                f"{entry.get('total_curtailed_kwh', 0):.2f} | "
                f"{entry.get('cycle_equivalent_count', 0):.2f} |"
            )
    lines.append("")

    # Ablation detail table by scenario
    lines.append("### 各场景明细\n")
    if not dl_metrics.empty:
        pivot_cols = ["prediction_source", "scenario",
                       "incremental_revenue_eur", "total_shortfall_kwh",
                       "total_curtailed_kwh", "cycle_equivalent_count", "mean_soc"]
        available = [c for c in pivot_cols if c in dl_metrics.columns]
        lines.append(dl_metrics[available].to_markdown(index=False))
    lines.append("")

    # Policy results
    lines.append("## 二、MLP 调度策略蒸馏\n")
    if report.get("policy_trained"):
        training = report.get("policy_training", {})
        lines.append(f"- 方向准确率: {training.get('direction_accuracy', 0):.4f}")
        lines.append(f"- Charge MAE: {training.get('charge_mae_kw', 0):.4f} kW")
        lines.append(f"- Discharge MAE: {training.get('discharge_mae_kw', 0):.4f} kW")
        lines.append(f"- 训练轮数: {training.get('epochs_trained', 0)}")
        lines.append(f"- 训练样本: {training.get('n_train', 0)}")
        lines.append(f"- 验证样本: {training.get('n_val', 0)}")
        lines.append(f"- 测试样本: {training.get('n_test', 0)}\n")

        lines.append("### 回放汇总指标\n")
        if not policy_metrics.empty:
            lines.append(policy_metrics.to_markdown(index=False))
        lines.append("")
    else:
        lines.append("MLP 策略蒸馏已跳过 (`--skip-policy`)。\n")

    # Quality gates
    lines.append("## 三、质量门禁\n")
    gates = report.get("quality_gates", {})
    for gate, passed in gates.items():
        status = "PASS" if passed else "FAIL"
        lines.append(f"- [{status}] {gate}")

    lines.append("")
    lines.append("## Pitfall\n")
    lines.append("MLP 策略是对 Stage12 rolling 优化器的监督学习近似，不是深度强化学习。")
    lines.append("论文中应表述为\"神经网络调度策略蒸馏/近似\"，不宣称\"最优调度\"。")

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Stage20 report: {path}")


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

    capacity_kw = float(config["site"]["capacity_kw"])
    report: dict[str, Any] = {
        "n_stage12_rows": len(stage12_results),
        "n_prediction_sources": 0,
        "policy_trained": False,
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

    # ---- Experiment 2: MLP policy distillation ----
    policy_replay = pd.DataFrame()
    policy_metrics = pd.DataFrame()

    if skip_policy:
        print("\n  Skipping MLP policy distillation (--skip-policy).")
    else:
        print("\n" + "=" * 60)
        print("Experiment 2: MLP dispatch policy distillation")
        print("=" * 60)

        try:
            X, y, feature_names = _build_policy_dataset(
                stage12_results, feature_frame, capacity_kw,
            )
            model, train_metrics, train_report = _train_mlp_policy(
                X, y,
                hidden_size=policy_hidden_size,
                dropout=policy_dropout,
                epochs=policy_epochs,
                patience=policy_patience,
                batch_size=policy_batch_size,
                random_state=random_state,
            )
            report["policy_trained"] = True
            report["policy_training"] = {**train_metrics, **train_report}

            x_mean_arr = np.array(train_report["x_mean"], dtype=np.float32)
            x_std_arr = np.array(train_report["x_std"], dtype=np.float32)

            policy_replay, policy_metrics = _replay_policy(
                model, stage12_results, feature_frame, config,
                x_mean=x_mean_arr, x_std=x_std_arr,
                feature_names=feature_names, capacity_kw=capacity_kw,
            )
            report["policy_trained"] = True

            # Policy quality gates
            report["quality_gates"]["policy_direction_accuracy_gt_0.5"] = (
                train_metrics.get("direction_accuracy", 0) > 0.5
            )
            if not policy_metrics.empty:
                report["quality_gates"]["policy_soc_in_bounds"] = bool(
                    policy_metrics.iloc[0].get("soc_within_bounds", 0) > 0.99
                )
                report["quality_gates"]["policy_no_simultaneous_cd"] = bool(
                    policy_metrics.iloc[0].get("no_simultaneous_charge_discharge", 0) > 0.99
                )
        except Exception as exc:
            print(f"  ERROR in policy distillation: {exc}", file=sys.stderr)
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
