from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RuntimeConfig:
    """Runtime configuration loaded from JSON.

    The project intentionally keeps configuration as plain JSON at this stage:
    it is easy to validate, portable across Windows/Linux, and does not add a
    YAML dependency before the project has a stable configuration surface.
    """

    raw: dict[str, Any]
    config_path: Path

    @property
    def root_dir(self) -> Path:
        """Return the repository root inferred from the config file location."""

        return self.config_path.resolve().parents[1]

    @property
    def raw_dir(self) -> Path:
        """Directory used for immutable downloaded source files."""

        return self.root_dir / self.raw["project"]["raw_dir"]

    @property
    def processed_dir(self) -> Path:
        """Directory used for normalized tables and model-ready datasets."""

        return self.root_dir / self.raw["project"]["processed_dir"]


def load_config(path: str | Path) -> RuntimeConfig:
    """Load and minimally validate the JSON configuration file."""

    config_path = Path(path).resolve()
    with config_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    required_sections = ["project", "site", "date_range", "sources", "storage"]
    missing = [section for section in required_sections if section not in payload]
    if missing:
        raise ValueError(f"配置缺少必要节点: {', '.join(missing)}")

    return RuntimeConfig(raw=payload, config_path=config_path)
