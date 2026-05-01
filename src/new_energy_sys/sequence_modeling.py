"""TCN 时序建模模块。

模块设计原则：
- 采用因果膨胀卷积（TCN）保证时间序列预测的严格因果性，防止未来信息泄漏
- 通过 Padding+Chomp 模式维持输入输出等长，避免卷积看到未来时刻
- 使用窄超参网格而非 AutoML 宽搜，防止单站点三年小时级数据上过拟合
- 所有窗口构造与标准化均在各自时间切分内完成，确保训练/验证/测试无数据泄漏

本模块对应项目 Stage 6 的 TCN 序列建模功能，用于光伏功率多步预测实验。
"""

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
    """TCN 序列建模阶段的输出产物。

    包含评估指标、预测结果和实验报告。
    """

    metrics: pd.DataFrame
    predictions: pd.DataFrame
    report: dict[str, Any]


@dataclass(frozen=True)
class TcnTrainingConfig:
    """TCN 超参数配置包。

    Stage 6 故意采用窄超参网格而非 AutoML 宽搜。数据集为单站点约三年小时级
    样本，过大的网络会以比学习稳定辐照-功率动态更快的速度记住季节和调度
    人为特征，导致过拟合。
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
    """裁剪因果卷积引入的右侧时间填充。

    因果卷积需要在序列右侧补零以保证输出长度不变，本层将补零部分裁掉，
    确保卷积只看到历史信息而不会泄漏未来时刻。
    """

    def __init__(self, chomp_size: int) -> None:
        super().__init__()
        self.chomp_size = int(chomp_size)

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        if self.chomp_size <= 0:
            return values
        return values[:, :, : -self.chomp_size].contiguous()


class TemporalBlock(nn.Module):
    """TCN 的一个残差因果卷积块。

    网络结构设计：
    - 双层因果卷积 + Chomp 裁剪，保持输入输出等长且严格因果
    - 每层卷积后接 ReLU 激活和 Dropout 正则化
    - 膨胀因子按 2 的幂次递增，实现指数级感受野增长
    - 当输入输出通道数不同时，使用 1×1 卷积做残差投影对齐通道
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
    """紧凑型 TCN 回归器，用于单站点小时级光伏功率预测。

    网络结构设计：
    - 由多个 TemporalBlock 堆叠而成，每个块的膨胀率按 2 的幂次递增
    - 最后一层线性头将最终时刻的卷积特征映射为标量功率预测值
    - 输入形状 [batch, window, features] 经转置适配 Conv1d 的 [batch, features, window] 要求
    - 取编码序列最后时间步的特征送入线性头，对应窗口末端监督状态
    """

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
        # DataLoader 输出 [batch, window, features]，Conv1d 需要
        # [batch, features, window]。取最后时间步作为监督时刻。
        encoded = self.tcn(values.transpose(1, 2))
        return self.head(encoded[:, :, -1]).squeeze(-1)


def _numeric_feature_columns(frame: pd.DataFrame) -> list[str]:
    """返回序列模型可用的数值特征列。"""

    excluded = {"timestamp", *TARGET_COLUMNS}
    return [column for column in frame.select_dtypes(include=[np.number]).columns if column not in excluded]


def _weather_history_feature_columns(frame: pd.DataFrame) -> list[str]:
    """返回第二轮 TCN 优化所用的精简特征组。

    该特征组仅保留天气/太阳状态信号与实测光伏历史，故意移除负荷、电价、
    储能和日历字段，因为这些列描述的是调度上下文而非物理光伏过程。
    序列模型已在窗口内看到时间顺序，因此冗余的日历编码对此消融实验
    并非必要。
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
    """返回天气/历史特征加上目标时刻天气字段。

    此特征集用于 t+24h 后续实验。保留 weather_history 精简的非调度特征组，
    并添加 Stage 3 生成的 target_plus_* 列。这些目标时刻列应理解为
    预报可用的外生协变量；历史 NSRDB 数值仅作为该可用性的离线上限代理。
    """

    base_features = _weather_history_feature_columns(frame)
    target_aligned_features = [
        column
        for column in _numeric_feature_columns(frame)
        if column.startswith("target_plus_")
    ]
    return sorted(set(base_features + target_aligned_features))


def _resolve_features(frame: pd.DataFrame, feature_set: str) -> list[str]:
    """将公开特征集名称解析为具体的数值列。"""

    if feature_set == "all":
        return _numeric_feature_columns(frame)
    if feature_set == "weather_history":
        return _weather_history_feature_columns(frame)
    if feature_set == "weather_history_target_aligned":
        return _weather_history_target_aligned_feature_columns(frame)
    raise ValueError(f"Unsupported TCN feature_set={feature_set!r}")


def _resolve_targets(targets: list[str] | None) -> list[str]:
    """解析 CLI 目标别名，同时严格校验目标允许列表。"""

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
    """解析请求的轻量级 TCN 配置。"""

    requested = names or ["baseline"]
    unsupported = [name for name in requested if name not in TCN_CONFIGS]
    if unsupported:
        raise ValueError(f"Unsupported TCN configs: {', '.join(unsupported)}")
    return [TCN_CONFIGS[name] for name in requested]


def _standardize_splits(splits: dict[str, pd.DataFrame], features: list[str]) -> dict[str, pd.DataFrame]:
    """仅使用训练集统计量对特征进行标准化。

    验证和测试集的统计量不得影响缩放。常数列用 std=1 保护，
    以避免除零同时保留零值。
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
    """在单个时间切分内构造无泄漏的滑动窗口。

    该函数对训练/验证/测试分别调用，因此训练窗口不会包含验证行，
    验证窗口不会包含测试行。每个标签对应窗口末端时间戳。
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
    """训练单个 TCN，使用验证集早停。"""

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
    """批量执行裁剪后的 TCN 推理。"""

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
    """针对多预测步长和多窗口长度训练 TCN 模型。"""

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
    """生成 TCN 序列建模的 Markdown 摘要报告。"""

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
