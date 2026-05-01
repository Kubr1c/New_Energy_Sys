"""运行时配置模块。

本模块负责从 JSON 文件加载项目配置并提供路径访问接口，是整个实验管线的
配置中枢——所有模块的目录路径和数据源声明均通过此模块的 RuntimeConfig 实例获取。

模块设计原则：
  - 纯 JSON 配置：当前阶段使用 JSON 而非 YAML，便于校验、跨平台兼容，
    且在配置面尚未稳定前不引入额外依赖。
  - 不可变配置：RuntimeConfig 使用 frozen=True，防止运行时意外修改。
  - 最小校验：加载时只检查必要节点是否存在，不做深度校验，
    避免在配置格式频繁迭代的早期阶段过度约束。

本模块对应项目 Stage 0 的配置加载功能。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RuntimeConfig:
    """运行时配置，从 JSON 文件加载并解析为不可变对象。

    frozen=True 保证实例不可变，防止在多模块间传递时被意外修改。

    Attributes:
        raw: 从 JSON 文件解析的完整配置字典。
        config_path: 配置文件的绝对路径，用于推断仓库根目录。
    """

    raw: dict[str, Any]
    config_path: Path

    @property
    def root_dir(self) -> Path:
        """根据配置文件位置推断的仓库根目录。"""

        return self.config_path.resolve().parents[1]

    @property
    def raw_dir(self) -> Path:
        """原始数据目录，用于存放不可变的下载源文件。"""

        return self.root_dir / self.raw["project"]["raw_dir"]

    @property
    def processed_dir(self) -> Path:
        """处理后数据目录，用于存放标准化表格和模型就绪数据集。"""

        return self.root_dir / self.raw["project"]["processed_dir"]


def load_config(path: str | Path) -> RuntimeConfig:
    """加载并最小校验 JSON 配置文件。

    Args:
        path: JSON 配置文件路径，支持字符串或 Path 对象。

    Returns:
        加载完成的 RuntimeConfig 实例。

    Raises:
        ValueError: 配置缺少必要节点时抛出。
    """

    config_path = Path(path).resolve()
    with config_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    # 校验必要顶层节点是否存在
    required_sections = ["project", "site", "date_range", "sources", "storage"]
    missing = [section for section in required_sections if section not in payload]
    if missing:
        raise ValueError(f"配置缺少必要节点: {', '.join(missing)}")

    return RuntimeConfig(raw=payload, config_path=config_path)
