"""HRRR 高分辨率快速更新预报提取模块。

模块设计原则：
- 单站点最近邻提取，不做精确测地重映射
- 仅选取项目所需最小变量集，保持首次实现小且可测试
- GRIB2 子集按字节范围下载，控制磁盘和算力开销
- 禁用 cfgrib 磁盘索引复用，避免过期 .idx 文件产生兼容警告
- 单位感知转换强制执行，防止量级错误静默传播

本模块对应项目天气数据链路中 HRRR GRIB2 点预报提取功能。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import math
import re
import warnings

import pandas as pd
import requests

try:
    import cfgrib
except (ImportError, RuntimeError):  # ecCodes native library not available (e.g., Windows)
    cfgrib = None

DEFAULT_REQUIRED_PATTERNS = (
    ("TMP", "2 m above ground"),
    ("RH", "2 m above ground"),
    ("UGRD", "10 m above ground"),
    ("VGRD", "10 m above ground"),
    ("APCP", "surface"),
    ("TCDC", "entire atmosphere"),
    ("DSWRF", "surface"),
    ("PRES", "surface"),
)
DSWRF_REQUIRED_PATTERNS = (("DSWRF", "surface"),)


@dataclass(frozen=True)
class HrrrPointSample:
    """从单个 HRRR GRIB2 文件提取的点预报样本。

    当前阶段目标不是全量生产入库。该容器保持提取输出显式且可审计，
    后续多文件入库可复用同一天气模式，无需重写下游阶段。

    Attributes:
        frame: 单行 DataFrame，包含提取的气象变量
        metadata: 提取元数据字典（路径、坐标、可用变量列表等）
    """

    frame: pd.DataFrame
    metadata: dict[str, Any]


@dataclass(frozen=True)
class HrrrIndexRecord:
    """HRRR .idx 伴生文件中一条可按字节范围寻址的记录。

    Attributes:
        line_number: 行号
        start_byte: 起始字节偏移
        short_name: GRIB 变量短名
        level: 垂直层描述
        descriptor: 预报时次等描述信息
    """

    line_number: int
    start_byte: int
    short_name: str
    level: str
    descriptor: str


def _open_hrrr_datasets(grib_path: Path) -> list[Any]:
    """打开单个 HRRR GRIB2 文件中的所有逻辑 xarray 数据集。

    HRRR 地面产品包含多个不同垂直坐标的变量组，cfgrib 将它们
    暴露为多个 xarray 数据集。此函数禁用磁盘索引复用，因为
    文件替换或部分重试后过期 .idx 文件经常产生嘈杂的兼容性警告。

    Args:
        grib_path: GRIB2 文件路径

    Returns:
        xarray 数据集列表
    """

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=FutureWarning)
        if cfgrib is None:
            raise ImportError("cfgrib requires ecCodes native library; install via conda or system package manager")
        return cfgrib.open_datasets(grib_path, backend_kwargs={"indexpath": ""})


def _parse_idx_records(idx_text: str) -> list[HrrrIndexRecord]:
    """解析 HRRR .idx 文件为可按字节寻址的记录元数据。

    示例行：
    `71:49695389:d=2022010100:TMP:2 m above ground:24 hour fcst:`

    Args:
        idx_text: .idx 文件原始文本

    Returns:
        HrrrIndexRecord 列表
    """

    records: list[HrrrIndexRecord] = []
    for raw_line in idx_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(":")
        if len(parts) < 6:
            continue
        records.append(
            HrrrIndexRecord(
                line_number=int(parts[0]),
                start_byte=int(parts[1]),
                short_name=parts[3],
                level=parts[4],
                descriptor=":".join(parts[5:]).rstrip(":"),
            )
        )
    return records


def _selected_hrrr_records(
    records: list[HrrrIndexRecord],
    *,
    required_patterns: tuple[tuple[str, str], ...] = DEFAULT_REQUIRED_PATTERNS,
) -> list[HrrrIndexRecord]:
    """选取严格天气试点所需的最小变量集。

    所需变量：2m 温度、2m 相对湿度、10m 风速分量、地面累积降水、
    全天空云量、地面向下短波辐射、地面气压。

    Args:
        records: 全部 idx 记录列表

    Returns:
        按 start_byte 排序的选中记录列表

    Raises:
        ValueError: 缺少必需变量时抛出
    """

    selected: list[HrrrIndexRecord] = []
    missing: list[str] = []
    for short_name, level in required_patterns:
        match = next(
            (
                record
                for record in records
                if record.short_name == short_name and record.level == level
            ),
            None,
        )
        if match is None:
            missing.append(f"{short_name}@{level}")
        else:
            selected.append(match)

    if missing:
        raise ValueError(f"HRRR idx missing required records: {', '.join(missing)}")
    return sorted(selected, key=lambda record: record.start_byte)


def _record_range_end(
    *,
    all_records: list[HrrrIndexRecord],
    selected_record: HrrrIndexRecord,
    content_length: int,
) -> int:
    """Return the inclusive byte-range end for one selected GRIB message.

    HRRR `.idx` files describe every GRIB message in physical byte order.  The
    end byte for a selected message is therefore the byte immediately before
    the *next record in the full idx*, not before the next selected record.  This
    distinction is important for production runs: using the next selected record
    silently downloads every unrelated message between two project variables and
    can turn a small point subset into hundreds of megabytes per timestamp.
    """

    ordered_records = sorted(all_records, key=lambda record: record.start_byte)
    for index, record in enumerate(ordered_records):
        if record.line_number != selected_record.line_number:
            continue
        if index + 1 < len(ordered_records):
            return ordered_records[index + 1].start_byte - 1
        return content_length - 1
    raise ValueError(f"selected HRRR idx record is not present in full idx: {selected_record}")


def _download_hrrr_subset(
    *,
    grib_url: str,
    idx_url: str,
    subset_target: Path,
    timeout_seconds: int = 120,
    required_patterns: tuple[tuple[str, str], ...] = DEFAULT_REQUIRED_PATTERNS,
) -> Path:
    """仅下载项目变量所需的 GRIB 消息子集。

    月度 HRRR 提取只有在每个大型源 GRIB2 文件被按字节范围裁剪到
    项目所需变量后才是可行的。这控制了磁盘使用和解码时间，
    无需引入单独的 GRIB 命令行工具。

    Args:
        grib_url: 源 GRIB2 文件 URL
        idx_url: 源 .idx 伴生文件 URL
        subset_target: 子集目标本地路径
        timeout_seconds: HTTP 请求超时秒数

    Returns:
        子集文件路径（若已存在则直接返回）
    """

    if subset_target.exists():
        return subset_target

    idx_response = requests.get(idx_url, timeout=timeout_seconds)
    idx_response.raise_for_status()
    records = _parse_idx_records(idx_response.text)
    selected = _selected_hrrr_records(records, required_patterns=required_patterns)

    head_response = requests.head(grib_url, timeout=timeout_seconds)
    head_response.raise_for_status()
    content_length = int(head_response.headers["Content-Length"])

    subset_target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = subset_target.with_suffix(subset_target.suffix + ".tmp")
    with temp_target.open("wb") as handle:
        for record in selected:
            end_byte = _record_range_end(
                all_records=records,
                selected_record=record,
                content_length=content_length,
            )
            response = requests.get(
                grib_url,
                headers={"Range": f"bytes={record.start_byte}-{end_byte}"},
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            handle.write(response.content)

    temp_target.replace(subset_target)
    return subset_target


def extract_hrrr_dswrf_point_sample(
    *,
    grib_path: Path,
    latitude: float,
    longitude: float,
) -> HrrrPointSample:
    """Extract only surface downward shortwave radiation from a GRIB subset.

    This is used as the strict DSWRF source for HRRR forecast runs because the
    public `hrrrzarr` forecast product exposes DSWRF metadata but no forecast
    chunk objects for the tested 2022 cycles.  The function intentionally keeps
    the same point-selection metadata as the full extractor so audits can prove
    the radiation value came from the same station-nearest grid.
    """

    datasets = _open_hrrr_datasets(grib_path)
    dswrf = _first_data_array(datasets, "sdswrf")
    if dswrf is None:
        available = sorted({str(name) for dataset in datasets for name in dataset.data_vars})
        raise ValueError(f"HRRR DSWRF subset is missing sdswrf; available variables: {available}")

    value, grid_lat, grid_lon = _select_point(dswrf, latitude, longitude)
    issue_time = _scalar_coord(dswrf, "time")
    valid_time = _scalar_coord(dswrf, "valid_time")
    step_value = dswrf.coords.get("step")
    lead_time_hour = None
    if step_value is not None:
        lead_time_hour = float(pd.to_timedelta(step_value.values).total_seconds() / 3600.0)

    frame = pd.DataFrame(
        [
            {
                "timestamp": valid_time,
                "weather_forecast_issue_time": issue_time,
                "weather_forecast_lead_time_hour": lead_time_hour,
                "grid_latitude": grid_lat,
                "grid_longitude": grid_lon,
                "ghi_wm2": value,
            }
        ]
    )
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame["weather_forecast_issue_time"] = pd.to_datetime(frame["weather_forecast_issue_time"], utc=True)
    metadata = {
        "grib_path": str(grib_path),
        "requested_latitude": latitude,
        "requested_longitude": longitude,
        "grid_latitude": grid_lat,
        "grid_longitude": grid_lon,
        "available_variables": sorted({str(name) for dataset in datasets for name in dataset.data_vars}),
    }
    return HrrrPointSample(frame=frame, metadata=metadata)


def _first_data_array(datasets: list[Any], variable_name: str) -> Any | None:
    """返回第一个匹配请求变量名的 xarray DataArray。

    Args:
        datasets: xarray 数据集列表
        variable_name: 目标变量名

    Returns:
        匹配的 DataArray，未找到则返回 None
    """

    for dataset in datasets:
        if variable_name in dataset.data_vars:
            return dataset[variable_name]
    return None


def _select_point(data_array: Any, latitude: float, longitude: float) -> tuple[float, float, float]:
    """选择最近的 HRRR 网格点并返回数值与网格坐标。

    HRRR 的经纬度是原生 Lambert 投影网格上的二维坐标。
    xarray 标准的 .sel(..., method="nearest") 在二维坐标上不可靠，
    因此本函数手动计算最近网格单元。距离度量故意简化，
    因为目标用例是单站点最近邻提取，不是精确测地重映射。

    Args:
        data_array: 目标变量 DataArray
        latitude: 请求纬度
        longitude: 请求经度

    Returns:
        元组 (变量值, 网格纬度, 网格经度)
    """

    lat_grid = data_array.latitude.values
    lon_grid = data_array.longitude.values
    if lon_grid.max() > 180.0 and longitude < 0.0:
        normalized_longitude = longitude + 360.0
    else:
        normalized_longitude = longitude

    distance = (lat_grid - latitude) ** 2 + (lon_grid - normalized_longitude) ** 2
    flat_index = int(distance.argmin())
    row_index, col_index = divmod(flat_index, distance.shape[1])

    point = data_array.isel(y=row_index, x=col_index)
    value = float(point.values.squeeze())
    grid_lat = float(lat_grid[row_index, col_index])
    grid_lon = float(lon_grid[row_index, col_index])
    if grid_lon > 180.0:
        grid_lon -= 360.0
    return value, grid_lat, grid_lon


def _scalar_coord(data_array: Any, coord_name: str) -> str | None:
    """当标量坐标可用时，返回其 ISO 格式字符串。

    Args:
        data_array: 目标 DataArray
        coord_name: 坐标名称

    Returns:
        ISO 格式时间字符串，坐标不存在则返回 None
    """

    if coord_name not in data_array.coords:
        return None
    value = pd.to_datetime(data_array.coords[coord_name].values).tz_localize(None)
    return value.isoformat()


def _convert_temperature_k_to_c(value: float | None) -> float | None:
    """将温度从开尔文转换为摄氏度。

    Args:
        value: 开尔文温度值

    Returns:
        摄氏度温度值，输入为 None 或 NaN 时返回 None
    """
    return None if value is None or math.isnan(value) else value - 273.15


def _convert_pressure_pa_to_hpa(value: float | None) -> float | None:
    """将气压从帕斯卡转换为百帕。

    Args:
        value: 帕斯卡气压值

    Returns:
        百帕气压值，输入为 None 或 NaN 时返回 None
    """
    return None if value is None or math.isnan(value) else value / 100.0


def _convert_precipitation_to_mm(value: float | None, units: str | None) -> float | None:
    """根据 GRIB 声明单位将 HRRR 降水量转换为毫米。

    HRRR 通常以 kg m**-2 暴露累积降水，数值上等效于毫米水深。
    部分产品使用米。此处必须进行单位感知转换，因为当 GRIB 载荷
    已经是 kg m**-2 时，简单的 *1000 会将降水总量静默放大三个量级。

    Args:
        value: 原始降水量值
        units: GRIB 声明的单位字符串

    Returns:
        毫米降水量，输入为 None 或 NaN 时返回 None
    """

    if value is None or math.isnan(value):
        return None
    normalized_units = (units or "").strip().lower()
    if normalized_units in {"kg m**-2", "kg m^-2", "mm"}:
        return value
    if normalized_units in {"m", "metre", "meter", "metres", "meters"}:
        return value * 1000.0
    return value


def _convert_cloud_cover_to_pct(value: float | None) -> float | None:
    """将云量从比例值 [0,1] 转换为百分比 [0,100]。

    当值 > 1.5 时认为已经是百分比，直接返回。

    Args:
        value: 原始云量值

    Returns:
        百分比云量值，输入为 None 或 NaN 时返回 None
    """
    if value is None or math.isnan(value):
        return None
    return value * 100.0 if value <= 1.5 else value


def extract_hrrr_point_sample(
    *,
    grib_path: Path,
    latitude: float,
    longitude: float,
) -> HrrrPointSample:
    """从单个 HRRR GRIB2 预报文件提取严格点天气样本。

    变量保守选取：
    - t2m、r2、u10、v10、tp、tcc、sdswrf、sp
    - 风速由 u10 和 v10 合成
    - 起报时间/有效时间从 GRIB 坐标保留

    这足以证明严格天气链路能覆盖项目已使用的核心天气特征，
    同时保持首次 HRRR 实现小且可测试。

    Args:
        grib_path: GRIB2 文件路径
        latitude: 请求纬度
        longitude: 请求经度

    Returns:
        HrrrPointSample 包含提取的 DataFrame 和元数据

    Raises:
        ValueError: 缺少必需变量时抛出
    """

    datasets = _open_hrrr_datasets(grib_path)

    selected = {
        "t2m": _first_data_array(datasets, "t2m"),
        "r2": _first_data_array(datasets, "r2"),
        "u10": _first_data_array(datasets, "u10"),
        "v10": _first_data_array(datasets, "v10"),
        "tp": _first_data_array(datasets, "tp"),
        "tcc": _first_data_array(datasets, "tcc"),
        "sdswrf": _first_data_array(datasets, "sdswrf"),
        "sp": _first_data_array(datasets, "sp"),
    }
    missing = [name for name, data_array in selected.items() if data_array is None]
    if missing:
        raise ValueError(f"HRRR sample is missing required variables: {', '.join(missing)}")

    extracted: dict[str, float | None] = {}
    grid_lat: float | None = None
    grid_lon: float | None = None
    for name, data_array in selected.items():
        value, current_lat, current_lon = _select_point(data_array, latitude, longitude)
        extracted[name] = value
        if grid_lat is None:
            grid_lat = current_lat
            grid_lon = current_lon

    issue_time = _scalar_coord(selected["t2m"], "time")
    valid_time = _scalar_coord(selected["t2m"], "valid_time")
    step_value = selected["t2m"].coords.get("step")
    if step_value is None:
        lead_time_hour = None
    else:
        lead_time_hour = float(pd.to_timedelta(step_value.values).total_seconds() / 3600.0)

    u10 = extracted["u10"]
    v10 = extracted["v10"]
    wind_speed_ms = None
    if u10 is not None and v10 is not None and not math.isnan(u10) and not math.isnan(v10):
        wind_speed_ms = math.sqrt(u10**2 + v10**2)

    frame = pd.DataFrame(
        [
            {
                "timestamp": valid_time,
                "weather_forecast_issue_time": issue_time,
                "weather_forecast_lead_time_hour": lead_time_hour,
                "grid_latitude": grid_lat,
                "grid_longitude": grid_lon,
                "temperature_c": _convert_temperature_k_to_c(extracted["t2m"]),
                "relative_humidity_pct": extracted["r2"],
                "wind_u10_ms": u10,
                "wind_v10_ms": v10,
                "wind_speed_ms": wind_speed_ms,
                "precipitation_mm": _convert_precipitation_to_mm(
                    extracted["tp"],
                    selected["tp"].attrs.get("units"),
                ),
                "cloud_cover_pct": _convert_cloud_cover_to_pct(extracted["tcc"]),
                "ghi_wm2": extracted["sdswrf"],
                "surface_pressure_hpa": _convert_pressure_pa_to_hpa(extracted["sp"]),
            }
        ]
    )
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame["weather_forecast_issue_time"] = pd.to_datetime(frame["weather_forecast_issue_time"], utc=True)

    metadata = {
        "grib_path": str(grib_path),
        "requested_latitude": latitude,
        "requested_longitude": longitude,
        "grid_latitude": grid_lat,
        "grid_longitude": grid_lon,
        "dataset_count": len(datasets),
        "available_variables": sorted({str(name) for dataset in datasets for name in dataset.data_vars}),
    }
    return HrrrPointSample(frame=frame, metadata=metadata)


def build_hrrr_cycle_urls(*, valid_time: pd.Timestamp, lead_time_hour: int) -> tuple[str, str, str]:
    """构建单个 HRRR 预报样本的 GRIB2 URL、idx URL 和缓存主干名。

    Args:
        valid_time: 有效时间（UTC）
        lead_time_hour: 预报提前时间（小时）

    Returns:
        元组 (GRIB2 URL, idx URL, 缓存主干名)
    """

    if valid_time.tzinfo is None:
        valid_time = valid_time.tz_localize("UTC")
    else:
        valid_time = valid_time.tz_convert("UTC")

    issue_time = valid_time - pd.Timedelta(hours=lead_time_hour)
    issue_time = issue_time.floor("h")
    cycle_hour = issue_time.strftime("%H")
    date_folder = issue_time.strftime("%Y%m%d")
    lead_text = f"{lead_time_hour:02d}"
    relative_path = f"hrrr.{date_folder}/conus/hrrr.t{cycle_hour}z.wrfsfcf{lead_text}.grib2"
    base_url = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
    return (
        f"{base_url}/{relative_path}",
        f"{base_url}/{relative_path}.idx",
        f"hrrr_t{cycle_hour}z_f{lead_text}_{valid_time.strftime('%Y%m%d%H')}",
    )


def build_hrrr_monthly_point_table(
    *,
    start: str,
    end: str,
    latitude: float,
    longitude: float,
    lead_time_hour: int,
    cache_dir: Path,
    timeout_seconds: int = 120,
) -> pd.DataFrame:
    """构建固定提前时间的月度 HRRR 点天气表。

    试点阶段故意保持整个月使用固定提前时间。这样产生一个可审计的天气表，
    足以在与 Stage1-3 对齐验证后再添加混合 f01/f06/f24 逻辑。

    Args:
        start: 起始日期字符串（含时区）
        end: 结束日期字符串（含时区）
        latitude: 请求纬度
        longitude: 请求经度
        lead_time_hour: 固定预报提前时间（小时）
        cache_dir: GRIB 子集缓存目录
        timeout_seconds: HTTP 请求超时秒数

    Returns:
        按 timestamp 排序且去重的月度天气 DataFrame
    """

    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(hours=1)
    valid_times = pd.date_range(start=start_ts, end=end_ts, freq="1h", tz="UTC")

    frames: list[pd.DataFrame] = []
    for index, valid_time in enumerate(valid_times, start=1):
        grib_url, idx_url, cache_stem = build_hrrr_cycle_urls(
            valid_time=valid_time,
            lead_time_hour=lead_time_hour,
        )
        subset_target = cache_dir / f"{cache_stem}.grib2"
        subset_path = _download_hrrr_subset(
            grib_url=grib_url,
            idx_url=idx_url,
            subset_target=subset_target,
            timeout_seconds=timeout_seconds,
        )
        sample = extract_hrrr_point_sample(
            grib_path=subset_path,
            latitude=latitude,
            longitude=longitude,
        )
        frames.append(sample.frame)
        if index % 24 == 0:
            print(f"HRRR extracted {index}/{len(valid_times)} hourly samples for fixed f{lead_time_hour:02d}")

    table = pd.concat(frames, ignore_index=True)
    table = table.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="first").reset_index(drop=True)
    return table
