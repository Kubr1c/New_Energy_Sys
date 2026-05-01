"""文件与网络 I/O 工具模块。

本模块提供数据采集管线所需的基础 I/O 原语：目录创建和原子下载。
所有适配器（data_sources.py）通过这些原语完成文件获取，保证下载行为
的可靠性和一致性。

模块设计原则：
  - 原子写入：下载时先写临时文件再替换目标，防止中断后残留半写文件
    被误认为有效缓存。
  - 链式调用：ensure_dir 返回传入路径，支持函数式链式写法。
  - 流式下载：大文件分块写入，避免内存溢出。

本模块对应项目 Stage 1 数据采集的 I/O 基础设施。
"""

from __future__ import annotations

from pathlib import Path

import requests


def ensure_dir(path: Path) -> Path:
    """确保目录存在，不存在则递归创建，并返回传入路径以支持链式调用。

    Args:
        path: 目标目录路径。

    Returns:
        传入的同一 Path 对象。
    """

    path.mkdir(parents=True, exist_ok=True)
    return path


def download_file(url: str, target: Path, timeout: int = 60) -> Path:
    """流式下载远程文件，写入完成后原子替换目标路径。

    下载过程中使用 .tmp 后缀的临时文件，防止中断后残留半写文件
    被误判为有效缓存数据。

    Args:
        url: 远程文件 URL。
        target: 本地目标路径，下载完成后文件将存放于此。
        timeout: 请求超时时间（秒），默认 60。

    Returns:
        下载完成后的目标 Path 对象。
    """

    ensure_dir(target.parent)
    # 使用 .tmp 后缀作为临时文件，下载完成后再原子替换
    temp_target = target.with_suffix(target.suffix + ".tmp")

    with requests.get(url, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        with temp_target.open("wb") as handle:
            # 分块写入，每块 1MB，避免大文件占用过多内存
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)

    # 原子替换：临时文件重命名为目标文件
    temp_target.replace(target)
    return target
