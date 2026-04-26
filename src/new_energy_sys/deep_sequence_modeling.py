from __future__ import annotations

import os
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from new_energy_sys.modeling import TARGET_COLUMNS, _chronological_split, _metrics


STAGE14_DEFAULT_TARGET = "target_pv_power_t_plus_24h"
LIGHTGBM_HISTORY_ONLY_NRMSE = 0.1225
LIGHTGBM_HISTORY_ONLY_DAYTIME_NRMSE = 0.1689
MATERIAL_IMPROVEMENT_NRMSE = 0.0030
NEURAL_MODELS = {"cnn_lstm", "attention_lstm"}
SUPPORTED_MODELS = {"persistence", *NEURAL_MODELS}


@dataclass(frozen=True)
class DeepSequenceResult:
    """Artifacts produced by Stage14B multi-model forecasting experiments."""

    metrics: pd.DataFrame
    predictions: pd.DataFrame
    report: dict[str, Any]


class CnnLstmRegressor(nn.Module):
    """CNN-LSTM regressor for single-site hourly PV power forecasting.

    Conv1d layers extract short local fluctuation patterns inside the rolling
    window, then the LSTM models longer temporal dependence. This keeps the
    model compact enough for CPU training while still being a genuine deep
    sequence model for the thesis.
    """

    def __init__(
        self,
        feature_count: int,
        *,
        cnn_channels: int = 32,
        kernel_size: int = 5,
        lstm_hidden_size: int = 64,
        lstm_layers: int = 1,
        dropout: float = 0.20,
    ) -> None:
        super().__init__()
        padding = kernel_size // 2
        self.cnn = nn.Sequential(
            nn.Conv1d(feature_count, cnn_channels, kernel_size=kernel_size, padding=padding),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Conv1d(cnn_channels, cnn_channels, kernel_size=kernel_size, padding=padding),
            nn.ReLU(),
            nn.Dropout(dropout),
        )
        lstm_dropout = dropout if lstm_layers > 1 else 0.0
        self.lstm = nn.LSTM(
            input_size=cnn_channels,
            hidden_size=lstm_hidden_size,
            num_layers=lstm_layers,
            dropout=lstm_dropout,
            batch_first=True,
        )
        self.head = nn.Sequential(nn.Dropout(dropout), nn.Linear(lstm_hidden_size, 1))

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        # DataLoader gives [batch, window, features]. Conv1d expects
        # [batch, features, window], then LSTM consumes [batch, window, channels].
        encoded = self.cnn(values.transpose(1, 2)).transpose(1, 2)
        output, _ = self.lstm(encoded)
        return self.head(output[:, -1, :]).squeeze(-1)


class AttentionLstmRegressor(nn.Module):
    """Attention-LSTM regressor for hourly PV power forecasting.

    The LSTM encodes the full historical window. A small additive attention
    block learns which historical hours matter most for the t+24h prediction,
    giving the thesis a clear deep-learning variant beyond CNN-LSTM.
    """

    def __init__(
        self,
        feature_count: int,
        *,
        lstm_hidden_size: int = 96,
        lstm_layers: int = 1,
        attention_size: int = 64,
        dropout: float = 0.20,
    ) -> None:
        super().__init__()
        lstm_dropout = dropout if lstm_layers > 1 else 0.0
        self.lstm = nn.LSTM(
            input_size=feature_count,
            hidden_size=lstm_hidden_size,
            num_layers=lstm_layers,
            dropout=lstm_dropout,
            batch_first=True,
        )
        self.attention = nn.Sequential(
            nn.Linear(lstm_hidden_size, attention_size),
            nn.Tanh(),
            nn.Linear(attention_size, 1),
        )
        self.head = nn.Sequential(nn.Dropout(dropout), nn.Linear(lstm_hidden_size, 1))

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        # Attention scores are normalized over the window dimension. The context
        # vector is therefore a weighted summary of important historical hours.
        output, _ = self.lstm(values)
        weights = torch.softmax(self.attention(output), dim=1)
        context = torch.sum(output * weights, dim=1)
        return self.head(context).squeeze(-1)


def _numeric_feature_columns(frame: pd.DataFrame) -> list[str]:
    """Return numeric columns that can safely be considered model features."""

    excluded = {"timestamp", *TARGET_COLUMNS}
    return [column for column in frame.select_dtypes(include=[np.number]).columns if column not in excluded]


def _history_only_features(frame: pd.DataFrame) -> list[str]:
    """Resolve the production-safe history-only feature group.

    This mirrors the Stage8 primary group: deterministic time encodings plus
    measured PV history. It deliberately rejects `target_plus_*` columns because
    those columns are not production-safe weather forecast-cycle inputs.
    """

    numeric = _numeric_feature_columns(frame)
    time_columns = [
        column
        for column in numeric
        if column
        in {
            "hour",
            "day_of_week",
            "month",
            "day_of_year",
            "quarter",
            "is_weekend",
            "is_business_hour",
            "hour_sin",
            "hour_cos",
            "day_of_week_sin",
            "day_of_week_cos",
            "month_sin",
            "month_cos",
            "day_of_year_sin",
            "day_of_year_cos",
        }
    ]
    history_columns = [
        column
        for column in numeric
        if any(
            marker in column
            for marker in [
                "pv_power_kw",
                "pv_power_lag_",
                "pv_power_roll_",
                "pv_power_ramp",
                "pv_power_capacity_ratio",
            ]
        )
    ]
    features = sorted(set(time_columns + history_columns))
    leaked = [column for column in features if column.startswith("target_plus_")]
    if leaked:
        raise ValueError(f"history_only contains forbidden target_plus features: {', '.join(leaked)}")
    return features


def _weather_history_target_aligned_features(frame: pd.DataFrame) -> list[str]:
    """Resolve the offline upper-bound feature group for sequence models."""

    numeric = _numeric_feature_columns(frame)
    weather_markers = [
        "ghi",
        "dni",
        "dhi",
        "clearsky",
        "temperature",
        "dew_point",
        "humidity",
        "pressure",
        "wind",
        "zenith",
        "albedo",
        "precipitable_water",
        "cloud",
        "weather_fill_flag",
    ]
    history_markers = [
        "pv_power_kw",
        "pv_power_lag_",
        "pv_power_roll_",
        "pv_power_capacity_ratio",
        "pv_power_ramp",
    ]
    weather_history = [
        column
        for column in numeric
        if any(marker in column for marker in weather_markers)
        or any(marker in column for marker in history_markers)
    ]
    target_aligned = [column for column in numeric if column.startswith("target_plus_")]
    return sorted(set(weather_history + target_aligned))


def _resolve_feature_sets(frame: pd.DataFrame, names: list[str] | None) -> dict[str, list[str]]:
    """Resolve public feature-set names to concrete Stage3 columns."""

    requested = names or ["history_only", "weather_history_target_aligned"]
    resolvers = {
        "history_only": _history_only_features,
        "weather_history_target_aligned": _weather_history_target_aligned_features,
    }
    unsupported = [name for name in requested if name not in resolvers]
    if unsupported:
        raise ValueError(f"Unsupported Stage14 feature sets: {', '.join(unsupported)}")
    feature_sets = {name: resolvers[name](frame) for name in requested}
    empty = [name for name, features in feature_sets.items() if not features]
    if empty:
        raise ValueError(f"Stage14 feature sets resolved to zero columns: {', '.join(empty)}")
    return feature_sets


def _resolve_targets(targets: list[str] | None) -> list[str]:
    """Resolve CLI target aliases while keeping a strict target allow-list."""

    aliases = {
        "1h": "target_pv_power_t_plus_1h",
        "t+1h": "target_pv_power_t_plus_1h",
        "6h": "target_pv_power_t_plus_6h",
        "t+6h": "target_pv_power_t_plus_6h",
        "24h": "target_pv_power_t_plus_24h",
        "t+24h": "target_pv_power_t_plus_24h",
    }
    requested = targets or ["24h"]
    resolved = [aliases.get(target, target) for target in requested]
    unsupported = [target for target in resolved if target not in TARGET_COLUMNS]
    if unsupported:
        raise ValueError(f"Unsupported Stage14 targets: {', '.join(unsupported)}")
    return list(dict.fromkeys(resolved))


def _resolve_models(models: list[str] | None) -> list[str]:
    """Resolve and validate public Stage14B model names."""

    requested = models or ["persistence", "cnn_lstm", "attention_lstm"]
    normalized = [model.strip().lower() for model in requested if model.strip()]
    unsupported = [model for model in normalized if model not in SUPPORTED_MODELS]
    if unsupported:
        raise ValueError(f"Unsupported Stage14 models: {', '.join(unsupported)}")
    return list(dict.fromkeys(normalized))


def _standardize_splits(
    splits: dict[str, pd.DataFrame],
    features: list[str],
) -> tuple[dict[str, pd.DataFrame], dict[str, dict[str, float]]]:
    """Standardize features using train statistics only."""

    train = splits["train"]
    mean = train[features].mean()
    std = train[features].std(ddof=0).replace(0.0, 1.0)
    standardized: dict[str, pd.DataFrame] = {}
    for name, split in splits.items():
        copy = split.copy()
        copy[features] = (copy[features] - mean) / std
        standardized[name] = copy
    scaler = {
        "mean": {column: float(mean[column]) for column in features},
        "std": {column: float(std[column]) for column in features},
    }
    return standardized, scaler


def _make_windows(
    split: pd.DataFrame,
    features: list[str],
    target: str,
    window_size: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Create leakage-safe windows inside one chronological split."""

    if len(split) < window_size:
        raise ValueError(f"Split has {len(split)} rows, smaller than window_size={window_size}")
    feature_values = split[features].to_numpy(dtype=np.float32)
    target_values = split[target].to_numpy(dtype=np.float32)
    timestamps = split["timestamp"].to_numpy()
    windows: list[np.ndarray] = []
    labels: list[float] = []
    label_timestamps: list[np.datetime64] = []
    for end_index in range(window_size - 1, len(split)):
        start_index = end_index - window_size + 1
        windows.append(feature_values[start_index : end_index + 1])
        labels.append(float(target_values[end_index]))
        label_timestamps.append(timestamps[end_index])
    return np.stack(windows), np.asarray(labels, dtype=np.float32), np.asarray(label_timestamps)


def _model_config(model_name: str, feature_count: int) -> dict[str, Any]:
    """Return a serializable model configuration."""

    if model_name == "cnn_lstm":
        return {
            "feature_count": feature_count,
            "cnn_channels": 32,
            "kernel_size": 5,
            "lstm_hidden_size": 64,
            "lstm_layers": 1,
            "dropout": 0.20,
        }
    if model_name == "attention_lstm":
        return {
            "feature_count": feature_count,
            "lstm_hidden_size": 96,
            "lstm_layers": 1,
            "attention_size": 64,
            "dropout": 0.20,
        }
    raise ValueError(f"Unsupported neural model: {model_name}")


def _build_neural_model(model_name: str, feature_count: int) -> nn.Module:
    """Instantiate a supported PyTorch sequence regressor."""

    config = _model_config(model_name, feature_count)
    if model_name == "cnn_lstm":
        return CnnLstmRegressor(**config)
    if model_name == "attention_lstm":
        return AttentionLstmRegressor(**config)
    raise ValueError(f"Unsupported neural model: {model_name}")


def _train_neural_model(
    *,
    model_name: str,
    train_x: np.ndarray,
    train_y: np.ndarray,
    validation_x: np.ndarray,
    validation_y: np.ndarray,
    feature_count: int,
    random_state: int,
    max_epochs: int,
    patience: int,
    batch_size: int,
) -> tuple[nn.Module, dict[str, Any]]:
    """Train one supported neural sequence model with validation early stopping."""

    torch.manual_seed(random_state)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = _build_neural_model(model_name, feature_count).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=7e-4, weight_decay=2e-4)
    loss_function = nn.SmoothL1Loss()
    train_loader = DataLoader(
        TensorDataset(torch.from_numpy(train_x), torch.from_numpy(train_y)),
        batch_size=batch_size,
        shuffle=True,
        drop_last=False,
    )
    validation_tensor_x = torch.from_numpy(validation_x).to(device)
    validation_tensor_y = torch.from_numpy(validation_y).to(device)

    best_state: dict[str, torch.Tensor] | None = None
    best_validation_loss = float("inf")
    epochs_without_improvement = 0
    history: list[dict[str, float | int]] = []

    for epoch in range(1, max_epochs + 1):
        model.train()
        train_losses: list[float] = []
        for batch_x, batch_y in train_loader:
            batch_x = batch_x.to(device)
            batch_y = batch_y.to(device)
            optimizer.zero_grad(set_to_none=True)
            loss = loss_function(model(batch_x), batch_y)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=2.0)
            optimizer.step()
            train_losses.append(float(loss.detach().cpu()))

        model.eval()
        with torch.no_grad():
            validation_loss = float(loss_function(model(validation_tensor_x), validation_tensor_y).detach().cpu())
        history.append(
            {
                "epoch": epoch,
                "train_loss": float(np.mean(train_losses)),
                "validation_loss": validation_loss,
            }
        )
        if validation_loss < best_validation_loss - 1e-5:
            best_validation_loss = validation_loss
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1
            if epochs_without_improvement >= patience:
                break

    if best_state is not None:
        model.load_state_dict(best_state)
    return model.to(torch.device("cpu")), {
        "model": model_name,
        "device": str(device),
        "epochs_ran": len(history),
        "best_validation_loss": best_validation_loss,
        "history": history,
    }


def _predict(model: nn.Module, values: np.ndarray, *, batch_size: int, capacity_kw: float) -> np.ndarray:
    """Run clipped neural-model inference in batches."""

    model.eval()
    predictions: list[np.ndarray] = []
    loader = DataLoader(torch.from_numpy(values), batch_size=batch_size, shuffle=False)
    with torch.no_grad():
        for batch in loader:
            predictions.append(model(batch).detach().cpu().numpy())
    return np.clip(np.concatenate(predictions), 0.0, capacity_kw * 1.05)


def _persistence_predictions(
    split: pd.DataFrame,
    target: str,
    *,
    capacity_kw: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build the strict t+24h persistence baseline.

    The baseline predicts future PV power with the current measured
    `pv_power_kw`. It ignores all learned parameters and future weather fields,
    so it is useful for proving the task is not a trivial copy problem.
    """

    if "pv_power_kw" not in split.columns:
        raise ValueError("Persistence baseline requires `pv_power_kw` in Stage14 input.")
    actual = split[target].to_numpy(dtype=np.float32)
    prediction = split["pv_power_kw"].to_numpy(dtype=np.float32)
    prediction = np.clip(prediction, 0.0, capacity_kw * 1.05)
    return actual, prediction, split["timestamp"].to_numpy()


def _load_lightgbm_reference(baseline_metrics: pd.DataFrame | None) -> dict[str, float | str]:
    """Resolve the LightGBM history-only reference used by replacement rules."""

    fallback = {
        "nrmse_capacity": LIGHTGBM_HISTORY_ONLY_NRMSE,
        "daytime_nrmse_capacity": LIGHTGBM_HISTORY_ONLY_DAYTIME_NRMSE,
        "source": "stage8_constants",
    }
    if baseline_metrics is None or baseline_metrics.empty:
        return fallback
    candidate = baseline_metrics[
        (baseline_metrics["split"] == "test")
        & (baseline_metrics["target"] == STAGE14_DEFAULT_TARGET)
        & (baseline_metrics["feature_set"] == "history_only")
        & (baseline_metrics["model"] == "lightgbm_tuned")
    ]
    if candidate.empty:
        return fallback
    row = candidate.iloc[0]
    return {
        "nrmse_capacity": float(row["nrmse_capacity"]),
        "daytime_nrmse_capacity": float(row["daytime_nrmse_capacity"]),
        "source": "stage8_metrics",
    }


def _load_tcn_reference(tcn_metrics: pd.DataFrame | None) -> dict[str, Any] | None:
    """Return the best Stage6 TCN test row for the 24h target when available."""

    if tcn_metrics is None or tcn_metrics.empty:
        return None
    candidate = tcn_metrics[
        (tcn_metrics["split"] == "test")
        & (tcn_metrics["target"] == STAGE14_DEFAULT_TARGET)
    ].copy()
    if candidate.empty:
        return None
    row = candidate.sort_values(["nrmse_capacity", "daytime_nrmse_capacity"]).iloc[0]
    return {
        "model": str(row.get("model", "tcn")),
        "config_name": str(row.get("config_name", "")),
        "window_size": int(row["window_size"]),
        "feature_set": str(row.get("feature_set", "")),
        "nrmse_capacity": float(row["nrmse_capacity"]),
        "daytime_nrmse_capacity": float(row["daytime_nrmse_capacity"]),
        "rmse_kw": float(row["rmse_kw"]),
        "mae_kw": float(row["mae_kw"]),
    }


def _select_recommendation(
    metrics: pd.DataFrame,
    *,
    lightgbm_reference: dict[str, float | str],
    quality_gates: dict[str, bool],
) -> dict[str, Any]:
    """Apply the explicit Stage14B model-selection rule."""

    history_test = metrics[
        (metrics["split"] == "test")
        & (metrics["target"] == STAGE14_DEFAULT_TARGET)
        & (metrics["feature_set"] == "history_only")
        & (metrics["model"].isin(sorted(NEURAL_MODELS)))
    ].copy()
    if history_test.empty:
        return {
            "selected_for_dispatch": "lightgbm_tuned_history_only",
            "can_replace_lightgbm": False,
            "reason": "没有可用于替代判断的 history_only 深度学习测试结果。",
        }
    best = history_test.sort_values(["nrmse_capacity", "daytime_nrmse_capacity"]).iloc[0]
    nrmse_improvement = float(float(lightgbm_reference["nrmse_capacity"]) - best["nrmse_capacity"])
    daytime_not_worse = float(best["daytime_nrmse_capacity"]) <= float(lightgbm_reference["daytime_nrmse_capacity"])
    all_quality_gates_passed = all(bool(value) for value in quality_gates.values())
    can_replace = (
        nrmse_improvement >= MATERIAL_IMPROVEMENT_NRMSE
        and daytime_not_worse
        and all_quality_gates_passed
    )
    selected_model = str(best["model"])
    return {
        "selected_for_dispatch": selected_model if can_replace else "lightgbm_tuned_history_only",
        "best_history_only_model": selected_model,
        "best_history_only_window": int(best["window_size"]),
        "best_history_only_nrmse": float(best["nrmse_capacity"]),
        "best_history_only_daytime_nrmse": float(best["daytime_nrmse_capacity"]),
        "lightgbm_reference_nrmse": float(lightgbm_reference["nrmse_capacity"]),
        "lightgbm_reference_daytime_nrmse": float(lightgbm_reference["daytime_nrmse_capacity"]),
        "nrmse_improvement_vs_lightgbm": nrmse_improvement,
        "daytime_not_worse": bool(daytime_not_worse),
        "all_quality_gates_passed": bool(all_quality_gates_passed),
        "can_replace_lightgbm": bool(can_replace),
        "reason": (
            f"{selected_model} 在 history_only 测试集上达到替代 LightGBM 的工程阈值。"
            if can_replace
            else "Attention-LSTM/CNN-LSTM 已完成深度学习实验验证，但在真实可用 history_only 输入条件下未达到替代 LightGBM 的工程阈值，因此调度主线仍采用 LightGBM。"
        ),
    }


def _append_metric_and_predictions(
    *,
    metric_rows: list[dict[str, Any]],
    prediction_frames: list[pd.DataFrame],
    model_name: str,
    target: str,
    window_size: int,
    split_name: str,
    feature_set_name: str,
    feature_count: int,
    model_path: str,
    timestamps: np.ndarray,
    actual: np.ndarray,
    prediction: np.ndarray,
    capacity_kw: float,
) -> None:
    """Append one split's metrics and prediction rows in the shared schema."""

    metric_rows.append(
        {
            "model": model_name,
            "target": target,
            "window_size": window_size,
            "split": split_name,
            "feature_set": feature_set_name,
            "feature_count": feature_count,
            "model_path": model_path,
            **_metrics(actual, prediction, capacity_kw=capacity_kw),
        }
    )
    prediction_frames.append(
        pd.DataFrame(
            {
                "timestamp": timestamps,
                "model": model_name,
                "target": target,
                "window_size": window_size,
                "split": split_name,
                "feature_set": feature_set_name,
                "actual_kw": actual,
                "prediction_kw": prediction,
                "error_kw": prediction - actual,
            }
        )
    )


def run_deep_learning_experiments(
    frame: pd.DataFrame,
    config: dict[str, Any],
    *,
    output_dir: Path,
    model_subdir: str = "stage14_models",
    window_sizes: list[int] | None = None,
    targets: list[str] | None = None,
    feature_set_names: list[str] | None = None,
    model_names: list[str] | None = None,
    baseline_metrics: pd.DataFrame | None = None,
    tcn_metrics: pd.DataFrame | None = None,
    random_state: int = 42,
    max_epochs: int = 30,
    patience: int = 5,
    batch_size: int = 256,
    torch_threads: int | None = None,
) -> DeepSequenceResult:
    """Run Stage14B Persistence/CNN-LSTM/Attention-LSTM experiments."""

    if torch_threads is not None and torch_threads > 0:
        torch.set_num_threads(int(torch_threads))

    capacity_kw = float(config["site"]["capacity_kw"])
    working = frame.copy()
    working["timestamp"] = pd.to_datetime(working["timestamp"], errors="coerce", utc=True)
    working = working.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    resolved_targets = _resolve_targets(targets)
    selected_models = _resolve_models(model_names)
    windows = window_sizes or [96, 168]
    missing_targets = [target for target in resolved_targets if target not in working.columns]
    if missing_targets:
        raise ValueError(f"Stage14 input is missing target columns: {', '.join(missing_targets)}")
    numeric = working.select_dtypes(include=[np.number])
    if numeric.isna().sum().sum() != 0:
        raise ValueError("Stage14 input contains missing numeric values; run Stage3 quality gates first.")
    if not np.isfinite(numeric.to_numpy()).all():
        raise ValueError("Stage14 input contains infinite numeric values.")

    feature_sets = _resolve_feature_sets(working, feature_set_names)
    raw_splits = _chronological_split(working)
    model_dir = output_dir / model_subdir
    model_dir.mkdir(parents=True, exist_ok=True)

    metric_rows: list[dict[str, Any]] = []
    prediction_frames: list[pd.DataFrame] = []
    training_summaries: list[dict[str, Any]] = []
    trained_models: list[dict[str, Any]] = []

    if "persistence" in selected_models:
        for target in resolved_targets:
            for split_name in ["validation", "test"]:
                actual, prediction, timestamps = _persistence_predictions(
                    raw_splits[split_name],
                    target,
                    capacity_kw=capacity_kw,
                )
                _append_metric_and_predictions(
                    metric_rows=metric_rows,
                    prediction_frames=prediction_frames,
                    model_name="persistence",
                    target=target,
                    window_size=0,
                    split_name=split_name,
                    feature_set_name="persistence_baseline",
                    feature_count=1,
                    model_path="",
                    timestamps=timestamps,
                    actual=actual,
                    prediction=prediction,
                    capacity_kw=capacity_kw,
                )

    neural_models = [model for model in selected_models if model in NEURAL_MODELS]
    for feature_set_name, features in feature_sets.items():
        splits, scaler = _standardize_splits(raw_splits, features)
        for window_size in windows:
            for target in resolved_targets:
                train_x, train_y, _ = _make_windows(splits["train"], features, target, window_size)
                validation_x, validation_y, validation_timestamps = _make_windows(
                    splits["validation"], features, target, window_size
                )
                test_x, test_y, test_timestamps = _make_windows(splits["test"], features, target, window_size)
                for model_name in neural_models:
                    model, train_summary = _train_neural_model(
                        model_name=model_name,
                        train_x=train_x,
                        train_y=train_y,
                        validation_x=validation_x,
                        validation_y=validation_y,
                        feature_count=len(features),
                        random_state=random_state,
                        max_epochs=max_epochs,
                        patience=patience,
                        batch_size=batch_size,
                    )
                    model_path = model_dir / f"{model_name}_{feature_set_name}_window_{window_size}_{target}.pkl"
                    with model_path.open("wb") as handle:
                        pickle.dump(
                            {
                                "model_state_dict": model.state_dict(),
                                "model_class": type(model).__name__,
                                "model_config": _model_config(model_name, len(features)),
                                "features": features,
                                "feature_set": feature_set_name,
                                "target": target,
                                "window_size": window_size,
                                "capacity_kw": capacity_kw,
                                "scaler": scaler,
                                "prediction_lower_bound_kw": 0.0,
                                "prediction_upper_bound_kw": capacity_kw * 1.05,
                            },
                            handle,
                        )

                    for split_name, split_x, split_y, split_timestamps in [
                        ("validation", validation_x, validation_y, validation_timestamps),
                        ("test", test_x, test_y, test_timestamps),
                    ]:
                        prediction = _predict(model, split_x, batch_size=batch_size, capacity_kw=capacity_kw)
                        _append_metric_and_predictions(
                            metric_rows=metric_rows,
                            prediction_frames=prediction_frames,
                            model_name=model_name,
                            target=target,
                            window_size=window_size,
                            split_name=split_name,
                            feature_set_name=feature_set_name,
                            feature_count=len(features),
                            model_path=str(model_path),
                            timestamps=split_timestamps,
                            actual=split_y,
                            prediction=prediction,
                            capacity_kw=capacity_kw,
                        )
                    training_summaries.append(
                        {
                            "target": target,
                            "feature_set": feature_set_name,
                            "window_size": window_size,
                            **train_summary,
                        }
                    )
                    trained_models.append(
                        {
                            "model": model_name,
                            "feature_set": feature_set_name,
                            "target": target,
                            "window_size": window_size,
                            "model_path": str(model_path),
                        }
                    )

    metrics = pd.DataFrame(metric_rows)
    predictions = pd.concat(prediction_frames, ignore_index=True)
    lightgbm_reference = _load_lightgbm_reference(baseline_metrics)
    tcn_reference = _load_tcn_reference(tcn_metrics)
    expected_metric_rows = (
        len(neural_models) * len(feature_sets) * len(windows) * len(resolved_targets) * 2
        + (2 * len(resolved_targets) if "persistence" in selected_models else 0)
    )
    quality_gates = {
        "input_non_empty": bool(len(working) > 0),
        "timestamp_monotonic": bool(working["timestamp"].is_monotonic_increasing),
        "history_only_has_no_target_plus": bool(
            "history_only" not in feature_sets
            or not any(column.startswith("target_plus_") for column in feature_sets["history_only"])
        ),
        "no_missing_numeric_values": bool(numeric.isna().sum().sum() == 0),
        "no_infinite_numeric_values": bool(np.isfinite(numeric.to_numpy()).all()),
        "all_requested_metric_rows_written": bool(len(metrics) == expected_metric_rows),
        "persistence_has_no_model_file": bool(
            "persistence" not in selected_models
            or metrics.loc[metrics["model"] == "persistence", "model_path"].fillna("").eq("").all()
        ),
        "test_predictions_within_physical_bound": bool(
            predictions[predictions["split"] == "test"]["prediction_kw"].between(0.0, capacity_kw * 1.05).all()
        ),
        "prediction_schema_stage9_compatible": bool(
            {"timestamp", "target", "prediction_kw", "actual_kw", "error_kw"}.issubset(predictions.columns)
        ),
    }
    recommendation = _select_recommendation(
        metrics,
        lightgbm_reference=lightgbm_reference,
        quality_gates=quality_gates,
    )
    best_test_rows = (
        metrics[metrics["split"] == "test"]
        .sort_values(["target", "feature_set", "model", "nrmse_capacity", "daytime_nrmse_capacity"])
        .groupby(["target", "feature_set", "model"], as_index=False)
        .first()
    )
    report = {
        "stage": "stage14b_multi_model_deep_learning_forecast",
        "input_rows": int(len(working)),
        "input_columns": int(len(working.columns)),
        "targets": resolved_targets,
        "window_sizes": windows,
        "models": selected_models,
        "runtime": {
            "torch_threads": int(torch.get_num_threads()),
            "torch_interop_threads": int(torch.get_num_interop_threads()),
            "cpu_count": int(os.cpu_count() or 0),
            "cuda_available": bool(torch.cuda.is_available()),
        },
        "feature_sets": {
            name: {
                "feature_count": len(features),
                "contains_target_plus": bool(any(column.startswith("target_plus_") for column in features)),
                "features": features,
            }
            for name, features in feature_sets.items()
        },
        "splits": {
            name: {"rows": int(len(split)), "start": str(split["timestamp"].min()), "end": str(split["timestamp"].max())}
            for name, split in raw_splits.items()
        },
        "windowed_sample_counts": {
            str(window): {name: int(max(0, len(split) - window + 1)) for name, split in raw_splits.items()}
            for window in windows
        },
        "trained_models": trained_models,
        "training_summaries": training_summaries,
        "best_test_rows": best_test_rows.to_dict(orient="records"),
        "lightgbm_reference": lightgbm_reference,
        "tcn_reference": tcn_reference,
        "recommendation": recommendation,
        "quality_gates": quality_gates,
        "paper_narrative": (
            "本文并非以 LightGBM 替代深度学习，而是以 Persistence 作为最简单预测基线，"
            "以 LightGBM 作为稳定工程基线，以 TCN、CNN-LSTM 和 Attention-LSTM 作为深度学习预测模型进行对比实验。"
            "实验结果用于判断深度学习模型在历史序列建模和真实可用输入条件下的表现边界。"
        ),
        "pitfall": (
            "weather_history_target_aligned 使用 NSRDB target_plus 历史太阳资源字段，只能解释为离线上限或方法验证，"
            "不能写成真实 forecast-cycle 天气预报上线效果。"
        ),
    }
    return DeepSequenceResult(metrics=metrics, predictions=predictions, report=report)


def write_deep_learning_report(
    report: dict[str, Any],
    metrics: pd.DataFrame,
    path: Path,
) -> None:
    """Write the Stage14B Chinese Markdown report."""

    test_rows = metrics[metrics["split"] == "test"].sort_values(
        ["model", "feature_set", "nrmse_capacity", "daytime_nrmse_capacity"]
    )
    recommendation = report["recommendation"]
    tcn_reference = report.get("tcn_reference")
    lightgbm_reference = report["lightgbm_reference"]

    lines = [
        "# Stage14B 多模型预测对比与深度学习补强报告",
        "",
        "## 实验范围",
        "",
        f"- 输入行数: `{report['input_rows']}`",
        f"- 输入列数: `{report['input_columns']}`",
        f"- 模型: `{', '.join(report['models'])}`",
        f"- 预测目标: `{', '.join(report['targets'])}`",
        f"- 序列窗口: `{', '.join(map(str, report['window_sizes']))}` 小时",
        f"- 特征组: `{', '.join(report['feature_sets'].keys())}`",
        "",
        "```mermaid",
        "flowchart TD",
        '    A["Stage3 特征数据"] --> B["70/15/15 时间顺序切分"]',
        '    B --> C["train-only 标准化"]',
        '    C --> D["切分内构造序列窗口"]',
        '    D --> E1["Persistence 简单基线"]',
        '    D --> E2["CNN-LSTM"]',
        '    D --> E3["Attention-LSTM"]',
        '    E1 --> F["统一指标与预测产物"]',
        '    E2 --> F',
        '    E3 --> F',
        '    F --> G["对比 LightGBM / TCN"]',
        '    G --> H["调度主模型替代判断"]',
        "```",
        "",
        "## 测试集指标",
        "",
        "| 模型 | 目标 | 特征组 | 窗口 | nRMSE | 日间 nRMSE | RMSE kW | MAE kW |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for _, row in test_rows.iterrows():
        lines.append(
            f"| `{row['model']}` | `{row['target']}` | `{row['feature_set']}` | {int(row['window_size'])} | "
            f"{row['nrmse_capacity']:.4f} | {row['daytime_nrmse_capacity']:.4f} | "
            f"{row['rmse_kw']:.4f} | {row['mae_kw']:.4f} |"
        )

    lines.extend(
        [
            "",
            "## 基线与深度学习对比",
            "",
            f"- LightGBM history_only 参考 nRMSE: `{float(lightgbm_reference['nrmse_capacity']):.4f}`",
            f"- LightGBM history_only 参考日间 nRMSE: `{float(lightgbm_reference['daytime_nrmse_capacity']):.4f}`",
            f"- LightGBM 参考来源: `{lightgbm_reference['source']}`",
        ]
    )
    if tcn_reference:
        lines.extend(
            [
                f"- Stage6 TCN 最佳 nRMSE: `{tcn_reference['nrmse_capacity']:.4f}`",
                f"- Stage6 TCN 最佳日间 nRMSE: `{tcn_reference['daytime_nrmse_capacity']:.4f}`",
                f"- Stage6 TCN 配置: `{tcn_reference['config_name']}`, 窗口 `{tcn_reference['window_size']}`",
            ]
        )
    else:
        lines.append("- Stage6 TCN 参考指标: `未提供`")

    lines.extend(
        [
            "",
            "## 工程主模型替代判断",
            "",
            f"- 推荐调度输入: `{recommendation['selected_for_dispatch']}`",
            f"- 是否替代 LightGBM: `{recommendation['can_replace_lightgbm']}`",
            f"- history_only 最佳深度学习模型: `{recommendation.get('best_history_only_model', 'NA')}`",
            f"- history_only 最佳窗口: `{recommendation.get('best_history_only_window', 'NA')}`",
            f"- 相对 LightGBM nRMSE 改善: `{recommendation.get('nrmse_improvement_vs_lightgbm', 0.0):.4f}`",
            f"- 原因: {recommendation['reason']}",
            "",
            "## 特征组边界",
            "",
            "- `persistence_baseline`: 当前时刻 `pv_power_kw` 直接预测 t+24h 功率，不训练模型，不保存模型文件。",
            "- `history_only`: 只使用确定性时间特征和历史光伏功率特征，不包含 `target_plus_*`，适合作为生产安全对比组。",
            "- `weather_history_target_aligned`: 加入目标时刻天气/太阳资源字段，用于离线上限实验；当前 NSRDB 字段不是原生 forecast-cycle 天气预报。",
            "",
            "## 论文建议表述",
            "",
            report["paper_narrative"],
            "",
            "## 质量门禁",
            "",
        ]
    )
    for gate, passed in report["quality_gates"].items():
        lines.append(f"- {gate}: `{passed}`")

    lines.extend(
        [
            "",
            "## 阶段进度评估",
            "",
            "- 工作内容: 新增 Persistence 简单基线和 Attention-LSTM 深度学习模型，保留 CNN-LSTM，并统一输出对比指标。",
            "- 目标完成情况: S14B 能支撑论文形成 Persistence → LightGBM → CNN-LSTM → Attention-LSTM 的模型梯度。",
            "- 下一阶段可行性: 若深度学习模型仍未满足替代规则，继续保持 LightGBM 调度主线，并推进储能配置与目标函数敏感性分析。",
            "",
            "## Pitfall",
            "",
            report["pitfall"],
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
