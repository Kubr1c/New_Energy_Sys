from __future__ import annotations

import json
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


@dataclass(frozen=True)
class SequenceModelingResult:
    """Artifacts produced by the TCN sequence-modeling stage."""

    metrics: pd.DataFrame
    predictions: pd.DataFrame
    report: dict[str, Any]


@dataclass(frozen=True)
class TcnTrainingConfig:
    """Small, explicit TCN hyperparameter bundle.

    Stage 6 deliberately uses a narrow grid instead of a broad AutoML search.
    The dataset is one PV site with about three years of hourly samples, so an
    oversized network can memorize seasonal and dispatch artifacts faster than
    it learns stable irradiance-to-power dynamics.
    """

    name: str
    channels: list[int]
    kernel_size: int
    dropout: float
    learning_rate: float
    weight_decay: float


TCN_CONFIGS: dict[str, TcnTrainingConfig] = {
    "baseline": TcnTrainingConfig(
        name="baseline",
        channels=[48, 48, 32],
        kernel_size=3,
        dropout=0.10,
        learning_rate=1e-3,
        weight_decay=1e-4,
    ),
    "compact": TcnTrainingConfig(
        name="compact",
        channels=[32, 32],
        kernel_size=3,
        dropout=0.10,
        learning_rate=1e-3,
        weight_decay=1e-4,
    ),
    "regularized": TcnTrainingConfig(
        name="regularized",
        channels=[32, 32, 16],
        kernel_size=5,
        dropout=0.20,
        learning_rate=7e-4,
        weight_decay=2e-4,
    ),
}


class Chomp1d(nn.Module):
    """Remove right-side temporal padding introduced by causal convolutions."""

    def __init__(self, chomp_size: int) -> None:
        super().__init__()
        self.chomp_size = int(chomp_size)

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        if self.chomp_size <= 0:
            return values
        return values[:, :, : -self.chomp_size].contiguous()


class TemporalBlock(nn.Module):
    """One residual causal-convolution block used by the TCN.

    The padding + chomp pattern keeps output length equal to input length while
    preventing the convolution from seeing future sequence positions. Residual
    projection is used when channel counts differ.
    """

    def __init__(self, in_channels: int, out_channels: int, kernel_size: int, dilation: int, dropout: float) -> None:
        super().__init__()
        padding = (kernel_size - 1) * dilation
        self.network = nn.Sequential(
            nn.Conv1d(in_channels, out_channels, kernel_size, padding=padding, dilation=dilation),
            Chomp1d(padding),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Conv1d(out_channels, out_channels, kernel_size, padding=padding, dilation=dilation),
            Chomp1d(padding),
            nn.ReLU(),
            nn.Dropout(dropout),
        )
        self.residual = nn.Conv1d(in_channels, out_channels, kernel_size=1) if in_channels != out_channels else nn.Identity()
        self.activation = nn.ReLU()

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        return self.activation(self.network(values) + self.residual(values))


class TcnRegressor(nn.Module):
    """Compact TCN regressor for single-station hourly PV forecasting."""

    def __init__(self, feature_count: int, channels: list[int], kernel_size: int, dropout: float) -> None:
        super().__init__()
        blocks: list[nn.Module] = []
        in_channels = feature_count
        for layer_index, out_channels in enumerate(channels):
            blocks.append(
                TemporalBlock(
                    in_channels=in_channels,
                    out_channels=out_channels,
                    kernel_size=kernel_size,
                    dilation=2**layer_index,
                    dropout=dropout,
                )
            )
            in_channels = out_channels
        self.tcn = nn.Sequential(*blocks)
        self.head = nn.Linear(in_channels, 1)

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        # DataLoader gives [batch, window, features]. Conv1d expects
        # [batch, features, window]. The last time step is the supervised state.
        encoded = self.tcn(values.transpose(1, 2))
        return self.head(encoded[:, :, -1]).squeeze(-1)


def _numeric_feature_columns(frame: pd.DataFrame) -> list[str]:
    """Return numeric features used by the sequence model."""

    excluded = {"timestamp", *TARGET_COLUMNS}
    return [column for column in frame.select_dtypes(include=[np.number]).columns if column not in excluded]


def _weather_history_feature_columns(frame: pd.DataFrame) -> list[str]:
    """Return the reduced feature group for second-pass TCN optimization.

    The group keeps only weather/solar-state signals plus measured PV history.
    It intentionally drops load, price, storage, and calendar fields because
    those columns describe dispatch context rather than the physical PV process.
    A sequence model already sees temporal order inside the window, so redundant
    calendar encodings are not needed for this targeted ablation.
    """

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
    features = [
        column
        for column in numeric
        if any(marker in column for marker in weather_markers)
        or any(marker in column for marker in history_markers)
    ]
    return sorted(set(features))


def _weather_history_target_aligned_feature_columns(frame: pd.DataFrame) -> list[str]:
    """Return weather/history features plus target-valid-time weather fields.

    This feature set is intended for the t+24h follow-up experiment. It keeps
    the reduced, non-dispatch feature group from `weather_history` and adds
    `target_plus_*` columns generated by Stage 3. Those target-aligned columns
    should be interpreted as forecast-available exogenous covariates; historical
    NSRDB values are only an offline proxy for that availability.
    """

    base_features = _weather_history_feature_columns(frame)
    target_aligned_features = [
        column
        for column in _numeric_feature_columns(frame)
        if column.startswith("target_plus_")
    ]
    return sorted(set(base_features + target_aligned_features))


def _resolve_features(frame: pd.DataFrame, feature_set: str) -> list[str]:
    """Resolve a public feature-set name to concrete numeric columns."""

    if feature_set == "all":
        return _numeric_feature_columns(frame)
    if feature_set == "weather_history":
        return _weather_history_feature_columns(frame)
    if feature_set == "weather_history_target_aligned":
        return _weather_history_target_aligned_feature_columns(frame)
    raise ValueError(f"Unsupported TCN feature_set={feature_set!r}")


def _resolve_targets(targets: list[str] | None) -> list[str]:
    """Resolve CLI target aliases while keeping a strict target allow-list."""

    if not targets:
        return list(TARGET_COLUMNS)

    aliases = {
        "1h": "target_pv_power_t_plus_1h",
        "t+1h": "target_pv_power_t_plus_1h",
        "6h": "target_pv_power_t_plus_6h",
        "t+6h": "target_pv_power_t_plus_6h",
        "24h": "target_pv_power_t_plus_24h",
        "t+24h": "target_pv_power_t_plus_24h",
    }
    resolved = [aliases.get(target, target) for target in targets]
    unsupported = [target for target in resolved if target not in TARGET_COLUMNS]
    if unsupported:
        raise ValueError(f"Unsupported TCN targets: {', '.join(unsupported)}")
    return list(dict.fromkeys(resolved))


def _resolve_tcn_configs(names: list[str] | None) -> list[TcnTrainingConfig]:
    """Resolve requested lightweight TCN configurations."""

    requested = names or ["baseline"]
    unsupported = [name for name in requested if name not in TCN_CONFIGS]
    if unsupported:
        raise ValueError(f"Unsupported TCN configs: {', '.join(unsupported)}")
    return [TCN_CONFIGS[name] for name in requested]


def _standardize_splits(splits: dict[str, pd.DataFrame], features: list[str]) -> dict[str, pd.DataFrame]:
    """Standardize features using train statistics only.

    Validation and test statistics must not influence scaling. Constant columns
    are protected with std=1 to avoid division by zero while preserving zeros.
    """

    train = splits["train"]
    mean = train[features].mean()
    std = train[features].std(ddof=0).replace(0.0, 1.0)
    standardized: dict[str, pd.DataFrame] = {}
    for name, split in splits.items():
        copy = split.copy()
        copy[features] = (copy[features] - mean) / std
        standardized[name] = copy
    return standardized


def _make_windows(split: pd.DataFrame, features: list[str], target: str, window_size: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Create leakage-safe windows inside one chronological split.

    The function is called separately for train/validation/test. Therefore no
    training window can include validation rows, and no validation window can
    include test rows. Each label belongs to the timestamp at the window end.
    """

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


def _train_tcn(
    *,
    train_x: np.ndarray,
    train_y: np.ndarray,
    validation_x: np.ndarray,
    validation_y: np.ndarray,
    feature_count: int,
    random_state: int,
    max_epochs: int,
    patience: int,
    batch_size: int,
    training_config: TcnTrainingConfig,
) -> tuple[TcnRegressor, dict[str, Any]]:
    """Train one TCN with validation early stopping."""

    torch.manual_seed(random_state)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = TcnRegressor(
        feature_count=feature_count,
        channels=training_config.channels,
        kernel_size=training_config.kernel_size,
        dropout=training_config.dropout,
    ).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=training_config.learning_rate,
        weight_decay=training_config.weight_decay,
    )
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
        train_loss = float(np.mean(train_losses))
        history.append({"epoch": epoch, "train_loss": train_loss, "validation_loss": validation_loss})

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
        "config_name": training_config.name,
        "device": str(device),
        "epochs_ran": len(history),
        "best_validation_loss": best_validation_loss,
        "history": history,
    }


def _predict(model: TcnRegressor, values: np.ndarray, *, batch_size: int, capacity_kw: float) -> np.ndarray:
    """Run clipped TCN inference in batches."""

    model.eval()
    predictions: list[np.ndarray] = []
    loader = DataLoader(torch.from_numpy(values), batch_size=batch_size, shuffle=False)
    with torch.no_grad():
        for batch in loader:
            predictions.append(model(batch).detach().cpu().numpy())
    return np.clip(np.concatenate(predictions), 0.0, capacity_kw * 1.05)


def run_tcn_experiments(
    frame: pd.DataFrame,
    config: dict[str, Any],
    *,
    output_dir: Path,
    model_subdir: str = "stage6_tcn_models",
    window_sizes: list[int] | None = None,
    targets: list[str] | None = None,
    feature_set: str = "all",
    tcn_config_names: list[str] | None = None,
    random_state: int = 42,
    max_epochs: int = 20,
    patience: int = 4,
    batch_size: int = 256,
) -> SequenceModelingResult:
    """Train TCN models for multiple horizons and window sizes."""

    capacity_kw = float(config["site"]["capacity_kw"])
    working = frame.copy()
    working["timestamp"] = pd.to_datetime(working["timestamp"], errors="coerce", utc=True)
    working = working.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    resolved_targets = _resolve_targets(targets)
    missing_targets = [target for target in resolved_targets if target not in working.columns]
    if missing_targets:
        raise ValueError(f"TCN input is missing target columns: {', '.join(missing_targets)}")

    features = _resolve_features(working, feature_set)
    if not features:
        raise ValueError(f"TCN feature set {feature_set!r} resolved to zero columns")
    splits = _standardize_splits(_chronological_split(working), features)
    windows = window_sizes or [24, 48, 72]
    training_configs = _resolve_tcn_configs(tcn_config_names)
    model_dir = output_dir / model_subdir
    model_dir.mkdir(parents=True, exist_ok=True)

    metric_rows: list[dict[str, Any]] = []
    prediction_frames: list[pd.DataFrame] = []
    training_summaries: list[dict[str, Any]] = []

    for window_size in windows:
        for target in resolved_targets:
            train_x, train_y, _ = _make_windows(splits["train"], features, target, window_size)
            validation_x, validation_y, validation_timestamps = _make_windows(splits["validation"], features, target, window_size)
            test_x, test_y, test_timestamps = _make_windows(splits["test"], features, target, window_size)

            for training_config in training_configs:
                model, train_summary = _train_tcn(
                    train_x=train_x,
                    train_y=train_y,
                    validation_x=validation_x,
                    validation_y=validation_y,
                    feature_count=len(features),
                    random_state=random_state,
                    max_epochs=max_epochs,
                    patience=patience,
                    batch_size=batch_size,
                    training_config=training_config,
                )
                model_path = model_dir / f"tcn_{training_config.name}_window_{window_size}_{target}.pkl"
                model_config = {
                    "name": training_config.name,
                    "channels": training_config.channels,
                    "kernel_size": training_config.kernel_size,
                    "dropout": training_config.dropout,
                    "learning_rate": training_config.learning_rate,
                    "weight_decay": training_config.weight_decay,
                }
                with model_path.open("wb") as handle:
                    pickle.dump(
                        {
                            "model_state_dict": model.state_dict(),
                            "features": features,
                            "feature_set": feature_set,
                            "target": target,
                            "window_size": window_size,
                            "capacity_kw": capacity_kw,
                            "model_config": model_config,
                        },
                        handle,
                    )

                for split_name, split_x, split_y, split_timestamps in [
                    ("validation", validation_x, validation_y, validation_timestamps),
                    ("test", test_x, test_y, test_timestamps),
                ]:
                    prediction = _predict(model, split_x, batch_size=batch_size, capacity_kw=capacity_kw)
                    metric_rows.append(
                        {
                            "model": "tcn",
                            "config_name": training_config.name,
                            "target": target,
                            "window_size": window_size,
                            "split": split_name,
                            "feature_set": feature_set,
                            "feature_count": len(features),
                            "model_path": str(model_path),
                            **_metrics(split_y, prediction, capacity_kw=capacity_kw),
                        }
                    )
                    prediction_frames.append(
                        pd.DataFrame(
                            {
                                "timestamp": split_timestamps,
                                "model": "tcn",
                                "config_name": training_config.name,
                                "target": target,
                                "window_size": window_size,
                                "split": split_name,
                                "feature_set": feature_set,
                                "actual_kw": split_y,
                                "prediction_kw": prediction,
                                "error_kw": prediction - split_y,
                            }
                        )
                    )
                training_summaries.append({"target": target, "window_size": window_size, **train_summary})

    metrics = pd.DataFrame(metric_rows)
    predictions = pd.concat(prediction_frames, ignore_index=True)
    best_test = (
        metrics[metrics["split"] == "test"]
        .sort_values(["target", "rmse_kw", "mae_kw"])
        .groupby("target", as_index=False)
        .first()
    )
    report = {
        "stage": "stage_6_tcn_sequence_modeling",
        "input_rows": int(len(working)),
        "input_columns": int(len(working.columns)),
        "feature_count": int(len(features)),
        "feature_set": feature_set,
        "features": features,
        "window_sizes": windows,
        "targets": resolved_targets,
        "tcn_configs": [
            {
                "name": config.name,
                "channels": config.channels,
                "kernel_size": config.kernel_size,
                "dropout": config.dropout,
                "learning_rate": config.learning_rate,
                "weight_decay": config.weight_decay,
            }
            for config in training_configs
        ],
        "splits": {
            name: {"rows": int(len(split)), "start": str(split["timestamp"].min()), "end": str(split["timestamp"].max())}
            for name, split in splits.items()
        },
        "windowed_sample_counts": {
            str(window): {
                name: int(max(0, len(split) - window + 1))
                for name, split in splits.items()
            }
            for window in windows
        },
        "best_test_rows": best_test.to_dict(orient="records"),
        "training_summaries": training_summaries,
        "quality_gates": {
            "no_missing_numeric_values": bool(working[features + TARGET_COLUMNS].isna().sum().sum() == 0),
            "timestamp_monotonic": bool(working["timestamp"].is_monotonic_increasing),
            "all_models_trained": bool(len(metrics) == len(windows) * len(resolved_targets) * len(training_configs) * 2),
            "test_predictions_within_physical_bound": bool(
                predictions[predictions["split"] == "test"]["prediction_kw"].between(0.0, capacity_kw * 1.05).all()
            ),
        },
        "pitfall": (
            "96h/168h 长窗口会减少有效验证/测试样本，并可能放大陈旧天气形态。"
            "若使用 target_plus_* 目标时刻天气特征，当前 NSRDB 数据只能视为离线上限实验；"
            "真实上线必须替换为预测时刻已经可用的天气预报。"
        ),
    }
    return SequenceModelingResult(metrics=metrics, predictions=predictions, report=report)


def write_tcn_report(
    report: dict[str, Any],
    metrics: pd.DataFrame,
    baseline_metrics: pd.DataFrame | None,
    path: Path,
) -> None:
    """Write a concise Markdown report for TCN sequence modeling."""

    best_test = (
        metrics[metrics["split"] == "test"]
        .sort_values(["target", "rmse_kw", "mae_kw"])
        .groupby("target", as_index=False)
        .first()
    )
    lines = [
        "# 第六阶段 TCN 序列建模报告",
        "",
        "## 实验范围",
        "",
        f"- 输入行数: `{report['input_rows']}`",
        f"- 输入列数: `{report['input_columns']}`",
        f"- 特征组: `{report.get('feature_set', 'all')}`",
        f"- 特征数: `{report['feature_count']}`",
        f"- 窗口长度: `{', '.join(map(str, report['window_sizes']))}` 小时",
        f"- 预测目标: `{', '.join(report.get('targets', []))}`",
        f"- TCN 配置: `{', '.join(config['name'] for config in report.get('tcn_configs', []))}`",
        "",
        "```mermaid",
        "flowchart LR",
        '    A["第三阶段特征数据"] --> B["按时间切分"]',
        '    B --> C["在每个切分内构造窗口"]',
        '    C --> D["训练 TCN"]',
        '    D --> E["验证集早停"]',
        '    E --> F["测试集对比"]',
        "```",
        "",
        "## 最佳 TCN 测试结果",
        "",
        "| 目标 | 配置 | 窗口 | RMSE kW | MAE kW | nRMSE | 日间 nRMSE |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for _, row in best_test.iterrows():
        lines.append(
            f"| `{row['target']}` | `{row.get('config_name', 'baseline')}` | {int(row['window_size'])} | {row['rmse_kw']:.4f} | "
            f"{row['mae_kw']:.4f} | {row['nrmse_capacity']:.4f} | {row['daytime_nrmse_capacity']:.4f} |"
        )

    if baseline_metrics is not None and not baseline_metrics.empty:
        baseline = baseline_metrics[baseline_metrics["split"] == "test"].copy()
        lines.extend(["", "## TCN 与调优 LightGBM 对比", ""])
        lines.append("| 目标 | LightGBM nRMSE | TCN nRMSE | TCN-LGBM 差值 | LightGBM 日间 nRMSE | TCN 日间 nRMSE |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for _, row in best_test.iterrows():
            target = row["target"]
            base = baseline[baseline["target"] == target]
            if len(base):
                base_row = base.iloc[0]
                delta = float(row["nrmse_capacity"] - base_row["nrmse_capacity"])
                lines.append(
                    f"| `{target}` | {float(base_row['nrmse_capacity']):.4f} | {float(row['nrmse_capacity']):.4f} | "
                    f"{delta:.4f} | {float(base_row['daytime_nrmse_capacity']):.4f} | "
                    f"{float(row['daytime_nrmse_capacity']):.4f} |"
                )

    lines.extend(["", "## 质量门禁", ""])
    for gate, passed in report["quality_gates"].items():
        lines.append(f"- {gate}: `{passed}`")
    lines.extend(["", "## 潜在坑点", "", report["pitfall"], ""])
    path.write_text("\n".join(lines), encoding="utf-8")
