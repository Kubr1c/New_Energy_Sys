"""Stage 1 数据采集模块。

本模块负责从多个公开数据源下载原始文件并缓存到本地，是整个实验管线的第一环。
设计原则：
  - 幂等性：所有下载函数首行检查本地缓存，已存在则直接返回，重复运行不重复下载。
  - 路径确定性：文件名由经纬度/日期范围/系统ID 等配置参数决定，同配置产生同文件。
  - 配置驱动：URL、字段名、参数全部来自 JSON 配置文件，不硬编码在代码中。
  - 关注点分离：本模块只管"获取原始文件"，schema 差异和列名映射交给
    standardize.py 处理。
  - 可审计：DownloadResult.source_url 记录真实远程来源，assumed 字段显式标记。
"""

from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import os
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import pandas as pd
import requests

from .io_utils import download_file, ensure_dir


# ---------------------------------------------------------------------------
# 数据结构定义
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DownloadResult:
    """已下载或已生成的原始数据源文件元数据。

    frozen=True 保证实例不可变，防止在多函数间传递时被意外修改。

    Attributes:
        name: 数据类别标识，如 "pv_power"、"weather"、"weather_forecast"、"opsd"。
              下游 standardize.py 按此名称取用对应的原始文件。
        path: 本地缓存的绝对路径，指向已下载完成的原始 CSV 文件。
        source_url: 远程来源 URL，用于审计和可复现性追踪。
                    为 None 表示文件已缓存（命中本地）或是本地注册文件。
    """

    name: str
    path: Path
    source_url: str | None


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------


def _safe_filename(url: str, fallback: str) -> str:
    """从远程 URL 中提取稳定的本地文件名。

    动态生成的 URL 可能包含查询参数或非标准路径，直接用作文件名会
    产生非法字符。此函数只取 URL 路径的最后一部分作为文件名；
    若路径为空（如根目录 URL），则使用 fallback。

    Args:
        url: 远程数据源的完整 URL。
        fallback: 当 URL 路径无法提取文件名时的备选名称。

    Returns:
        可用作本地文件名的字符串，不含路径分隔符。
    """
    filename = Path(urlparse(url).path).name
    return filename if filename else fallback


# ---------------------------------------------------------------------------
# PV 功率数据适配器（4 种 kind）
# ---------------------------------------------------------------------------


def download_csv_url(name: str, source: dict, raw_dir: Path) -> DownloadResult:
    """下载配置中声明的通用公开 CSV 数据源。

    此适配器刻意保持通用，因为 PVDAQ/OEDI/DuraMAT 等机构暴露的
    CSV 资源路径格式各不相同。Schema 差异在文件缓存后由
    standardize.py 的 normalize_pv_power 统一处理，此处只负责
    "把远程文件拿到本地"。

    Args:
        name: 数据类别名，用于组织本地目录结构。
        source: 配置中 sources 下对应的数据源声明字典，必须包含 "url" 键。
        raw_dir: 原始数据根目录，下载文件将存放于 raw_dir/name/ 子目录下。

    Returns:
        包含本地路径和远程 URL 的 DownloadResult。
    """
    url = source["url"]
    # 从 URL 提取文件名；若提取失败则使用 "{name}.csv" 作为兜底
    filename = _safe_filename(url, f"{name}.csv")
    # 每种数据源在 raw_dir 下建立同名子目录，避免不同来源的文件互相覆盖
    target = raw_dir / name / filename

    # 幂等检查：本地已缓存则跳过下载
    if not target.exists():
        download_file(url=url, target=target)

    return DownloadResult(name=name, path=target, source_url=url)


def use_local_file(name: str, source: dict, root_dir: Path) -> DownloadResult:
    """将本地已有的原始文件注册为数据源。

    适用场景：公共 API 传输中断但已有部分有效 CSV 可用于特征提取。
    路径仍在配置中声明，保证管线可复现——即使数据来源是本地文件
    而非远程下载，下游代码仍可通过 DownloadResult.path 统一访问。

    Args:
        name: 数据类别名。
        source: 配置字典，必须包含 "path" 键，值为相对于 root_dir 的路径。
        root_dir: 仓库根目录，用于解析 source["path"] 的相对路径。

    Returns:
        source_url 为 None 的 DownloadResult（本地文件无远程来源）。

    Raises:
        FileNotFoundError: 配置声明的本地文件不存在时直接报错，
            不允许静默生成空数据。
    """
    path = (root_dir / source["path"]).resolve()
    if not path.exists():
        raise FileNotFoundError(f"本地数据源不存在: {path}")
    return DownloadResult(name=name, path=path, source_url=None)


def download_nrel_solar_zip(source: dict, raw_dir: Path) -> DownloadResult:
    """下载 NREL Solar Power Data for Integration Studies 的州级 ZIP 文件。

    该数据集以州为单位打包，包含实际光伏出力、日前预测和 4 小时前预测。
    下载后由 standardize.py 中的 normalize_nrel_solar_zip 解压和解析。

    Args:
        source: 配置字典，必须包含 "url" 键。
        raw_dir: 原始数据根目录。

    Returns:
        name 固定为 "nrel_solar" 的 DownloadResult。
    """
    url = source["url"]
    filename = _safe_filename(url, "nrel_solar.zip")
    target = raw_dir / "nrel_solar" / filename
    # 幂等检查：ZIP 文件已缓存则跳过
    if not target.exists():
        download_file(url=url, target=target)
    return DownloadResult(name="nrel_solar", path=target, source_url=url)


def download_pvdaq_s3_year(source: dict, raw_dir: Path) -> DownloadResult:
    """从 OEDI 公开 S3 存储中下载并合并一个 PVDAQ 系统-年度的全部数据。

    PVDAQ 按天存储测量值，每天一个 CSV 文件，路径格式为：
      pvdaq/csv/pvdata/system_id=<id>/year=<year>/month=<m>/day=<d>/...

    本函数将同一 system_id + year 下的所有日文件合并为一个本地 CSV，
    为下游管线提供稳定的单文件输入，无需数据库客户端或 AWS 凭证。

    流程：
      1. 通过 S3 ListObjectsV2 API 列出该系统-年度下所有 CSV 文件 Key。
      2. 用 ThreadPoolExecutor 并发读取每日 CSV（网络延迟是瓶颈，
         并发可将总耗时控制在可接受范围内）。
      3. pd.concat 合并所有日数据，写入本地年度缓存文件。

    Args:
        source: 配置字典，必须包含 "system_id" 和 "year"；
                可选 "bucket_url"、"download_workers"、"timeout_seconds"、"target_name"。
        raw_dir: 原始数据根目录。

    Returns:
        name 为 "pv_power" 的 DownloadResult。

    Raises:
        ValueError: 该系统-年度下没有 CSV 文件时抛出。
        RuntimeError: 单个日文件读取失败时抛出，不静默跳过。
    """
    # OEDI 公开 S3 存储的基础 URL，配置中可覆盖
    bucket_url = source.get("bucket_url", "https://oedi-data-lake.s3.amazonaws.com")
    system_id = int(source["system_id"])
    year = int(source["year"])
    # 确保 pvdaq 子目录存在
    ensure_dir(raw_dir / "pvdaq")

    # 本地年度合并缓存路径；配置可指定 target_name，否则按默认格式生成
    target = raw_dir / "pvdaq" / source.get("target_name", f"pvdaq_system_{system_id}_{year}.csv")
    # 幂等检查：年度缓存已存在则直接返回，不重新下载
    if target.exists():
        return DownloadResult(name="pv_power", path=target, source_url=None)

    # 构造 S3 ListObjectsV2 请求 URL，列出该系统-年度前缀下的最多 1000 个对象
    list_url = (
        f"{bucket_url}/?list-type=2&prefix=pvdaq/csv/pvdata/system_id={system_id}/year={year}/"
        "&max-keys=1000"
    )
    response = requests.get(list_url, timeout=int(source.get("timeout_seconds", 60)))
    response.raise_for_status()

    # 解析 S3 XML 响应，提取所有 .csv 后缀的对象 Key
    # S3 XML 使用命名空间 http://s3.amazonaws.com/doc/2006-03-01/
    namespace = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
    root = ET.fromstring(response.text)
    keys = [
        node.text
        for node in root.findall("s:Contents/s:Key", namespace)
        if node.text and node.text.endswith(".csv")
    ]
    if not keys:
        raise ValueError(f"PVDAQ system {system_id} has no CSV files for year {year}")

    def read_day(key: str) -> pd.DataFrame:
        """读取一个 PVDAQ 日文件。

        网络延迟是此工作负载的瓶颈，并发读取可将总耗时大幅压缩。
        返回的 DataFrame 顺序无关紧要，最终合并后由下游按时间戳排序。
        """
        file_url = f"{bucket_url}/{key}"
        return pd.read_csv(file_url, low_memory=False)

    # 并发读取所有日文件
    frames: list[pd.DataFrame] = []
    # 默认 12 个工作线程；网络 I/O 密集型场景下此值可根据带宽调整
    max_workers = int(source.get("download_workers", 12))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有读取任务，建立 future -> key 的映射以便出错时定位
        future_to_key = {executor.submit(read_day, key): key for key in sorted(keys)}
        # as_completed 按完成顺序收集结果（非提交顺序），最大化并发效率
        for index, future in enumerate(as_completed(future_to_key), start=1):
            key = future_to_key[future]
            try:
                frames.append(future.result())
            except Exception as exc:
                # 单个日文件失败时直接报错，不允许静默跳过——缺失天数据
                # 可能导致时序断裂，下游特征构造会因此产生对不齐
                raise RuntimeError(f"Failed to read PVDAQ day file {key}") from exc
            # 每 50 个文件打印一次进度，避免大量无输出等待
            if index % 50 == 0:
                print(f"PVDAQ downloaded {index}/{len(keys)} day files for system {system_id} {year}")

    # 合并所有日数据并写入本地年度缓存
    merged = pd.concat(frames, ignore_index=True)
    merged.to_csv(target, index=False)
    return DownloadResult(name="pv_power", path=target, source_url=list_url)


def download_pvdaq_s3_years(source: dict, raw_dir: Path) -> DownloadResult:
    """下载并合并多个 PVDAQ 系统-年度的数据为一个跨年度原始 CSV。

    Stage 5 的序列模型（TCN/CNN-LSTM）需要多年数据来评估季节泛化能力，
    而不只是证明管线能跑通。PVDAQ 仍按单年暴露文件，因此此包装器
    将工作委托给经过生产验证的 download_pvdaq_s3_year，再创建一个
    确定性的跨年度缓存文件供 standardize.py 消费。

    此函数刻意保持源数据 schema 不做修改——时间戳和功率列的选择
    在 normalize_pv_power 中仍是配置驱动的，这样未来换一个不同
    PVDAQ 通道名的电站可以复用此适配器而无需改代码。

    Args:
        source: 配置字典，必须包含 "system_id" 和 "years"（非空列表）；
                可选 "target_name"。
        raw_dir: 原始数据根目录。

    Returns:
        name 为 "pv_power" 的 DownloadResult，path 指向跨年度合并文件。

    Raises:
        ValueError: years 列表为空时抛出。
    """
    system_id = int(source["system_id"])
    years = source.get("years")
    if not years:
        raise ValueError("PVDAQ multi-year source requires a non-empty `years` list.")

    # 去重并排序，保证年度顺序确定性
    parsed_years = sorted({int(year) for year in years})
    ensure_dir(raw_dir / "pvdaq")
    # 跨年度缓存文件名由首尾年份决定，如 pvdaq_system_10_2020_2022.csv
    target = raw_dir / "pvdaq" / source.get(
        "target_name",
        f"pvdaq_system_{system_id}_{parsed_years[0]}_{parsed_years[-1]}.csv",
    )
    # 幂等检查：跨年度缓存已存在则直接返回
    if target.exists():
        return DownloadResult(name="pv_power", path=target, source_url=None)

    # 逐年调用单年下载器（复用缓存逻辑），再合并
    frames: list[pd.DataFrame] = []
    source_urls: list[str] = []
    for year in parsed_years:
        # 构造单年配置，设置独立的 target_name 使单年缓存也独立存储
        yearly_source = dict(source)
        yearly_source["year"] = year
        yearly_source["target_name"] = f"pvdaq_system_{system_id}_{year}.csv"
        yearly_result = download_pvdaq_s3_year(yearly_source, raw_dir)
        # 读取单年缓存（已由 download_pvdaq_s3_year 写好）
        frames.append(pd.read_csv(yearly_result.path, low_memory=False))
        if yearly_result.source_url:
            source_urls.append(yearly_result.source_url)

    # 合并所有年度数据并写入跨年度缓存
    merged = pd.concat(frames, ignore_index=True)
    merged.to_csv(target, index=False)
    return DownloadResult(
        name="pv_power",
        path=target,
        source_url=source_urls[0] if source_urls else None,
    )


# ---------------------------------------------------------------------------
# 天气数据适配器（5 种 kind）
# ---------------------------------------------------------------------------


def fetch_open_meteo_archive(
    *,
    latitude: float,
    longitude: float,
    start: str,
    end: str,
    raw_dir: Path,
    target_name: str | None = None,
) -> DownloadResult:
    """从 Open-Meteo Archive API 获取小时级历史/再分析天气数据。

    Open-Meteo 作为默认首选天气源，原因：
      - 无需 API key 即可访问；
      - 返回分析就绪的小时级气象字段；
      - 底层数据基于 ERA5 再分析，质量有保障。

    使用 keyword-only 参数（* 分隔）防止调用时参数位置错乱——
    经纬度、起止日期等参数类型相同，位置传参容易出错。

    Args:
        latitude: 站点纬度。
        longitude: 站点经度。
        start: 起始日期，格式 "YYYY-MM-DD"。
        end: 结束日期，格式 "YYYY-MM-DD"。
        raw_dir: 原始数据根目录。
        target_name: 可选的本地文件名，未指定时按经纬度和日期范围自动生成。

    Returns:
        name 为 "weather" 的 DownloadResult。
    """
    ensure_dir(raw_dir / "weather")
    # 文件名由起止日期决定，同配置产生同文件
    target = raw_dir / "weather" / (target_name or f"open_meteo_{start}_{end}.csv")
    # 幂等检查：已缓存则跳过
    if target.exists():
        return DownloadResult(name="weather", path=target, source_url=None)

    # 请求的小时级气象字段列表，覆盖温度、湿度、气压、降水、
    # 风速风向、辐照度（短波/直射/法向直射/散射/长波）和云量
    hourly_fields = [
        "temperature_2m",           # 2米气温
        "relative_humidity_2m",     # 2米相对湿度
        "dew_point_2m",             # 2米露点温度
        "surface_pressure",         # 地表气压
        "pressure_msl",             # 海平面气压
        "precipitation",            # 降水量
        "wind_speed_10m",           # 10米风速
        "wind_direction_10m",       # 10米风向
        "wind_gusts_10m",           # 10米阵风
        "shortwave_radiation",      # 短波辐射
        "direct_radiation",         # 直射辐射
        "direct_normal_irradiance", # 法向直射辐照度 (DNI)
        "diffuse_radiation",        # 散射辐射 (DHI)
        "terrestrial_radiation",    # 长波辐射
        "cloud_cover",              # 总云量
        "cloud_cover_low",          # 低层云量
        "cloud_cover_mid",          # 中层云量
        "cloud_cover_high",         # 高层云量
    ]
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start,
        "end_date": end,
        "hourly": ",".join(hourly_fields),  # API 要求逗号分隔的字段列表
        "timezone": "UTC",                   # 统一使用 UTC 时区
        "wind_speed_unit": "ms",             # 风速单位统一为 m/s
    }
    url = "https://archive-api.open-meteo.com/v1/archive"
    response = requests.get(url, params=params, timeout=60)
    response.raise_for_status()
    payload = response.json()

    # 校验返回结构：必须包含 hourly 字典且其中有 time 列
    hourly = payload.get("hourly")
    if not hourly or "time" not in hourly:
        raise ValueError("Open-Meteo 返回结构异常：缺少 hourly.time")

    # 将 JSON 中的 hourly 字典转为 DataFrame，统一时间列名为 timestamp
    frame = pd.DataFrame(hourly).rename(columns={"time": "timestamp"})
    frame.to_csv(target, index=False)
    return DownloadResult(name="weather", path=target, source_url=response.url)


def fetch_open_meteo_historical_forecast(
    *,
    latitude: float,
    longitude: float,
    start: str,
    end: str,
    source: dict,
    raw_dir: Path,
) -> DownloadResult:
    """从 Open-Meteo Historical Forecast API 获取预报时刻天气字段。

    此适配器与 archive/reanalysis 适配器分离。archive 端点用于天气补充，
    但历史预报数据是更严格的生产预测输入——它更好地代表预测时刻
    实际可获得的天气信息，而非事后再分析的最优估计。

    关键处理：
      Open-Meteo Historical Forecast 端点返回的是 valid-time（预报生效时刻）
      的小时级数据，而非完整的 issue-cycle（起报时次）× lead-time（预报时效）
      矩阵。因此：
      - 用配置中的 assumed_lead_time_hour（默认 24h）反推起报时刻：
        issue_time = valid_time - lead_time
      - 标记 weather_forecast_issue_time_is_assumed = True，
        保证审计时不会被误认为是真实的 forecast cycle 起报时间。
      - 这三条审计字段写入 CSV，由 Stage7 的泄漏检查消费。

    Args:
        latitude: 站点纬度。
        longitude: 站点经度。
        start: 起始日期。
        end: 结束日期。
        source: 配置字典，可选 "assumed_lead_time_hour"、"hourly_fields"、
                "url"、"timeout_seconds"、"target_name"。
        raw_dir: 原始数据根目录。

    Returns:
        name 为 "weather_forecast" 的 DownloadResult。
    """
    ensure_dir(raw_dir / "weather_forecast")

    provider = "open_meteo_historical_forecast"
    # 预报时效假设：端点不暴露完整 issue-cycle × lead-time 矩阵，
    # 用配置值显式声明假设的预报提前量，便于审计
    assumed_lead_time_hour = int(source.get("assumed_lead_time_hour", 24))
    target = raw_dir / "weather_forecast" / (
        source.get("target_name")
        or f"{provider}_{start}_{end}_{latitude:.4f}_{longitude:.4f}.csv"
    )
    # 幂等检查
    if target.exists():
        return DownloadResult(name="weather_forecast", path=target, source_url=None)

    # 请求的气象字段列表，默认与 archive 相同，但可通过配置覆盖
    hourly_fields = source.get(
        "hourly_fields",
        [
            "temperature_2m",
            "relative_humidity_2m",
            "dew_point_2m",
            "surface_pressure",
            "pressure_msl",
            "precipitation",
            "wind_speed_10m",
            "wind_direction_10m",
            "wind_gusts_10m",
            "shortwave_radiation",
            "direct_radiation",
            "direct_normal_irradiance",
            "diffuse_radiation",
            "cloud_cover",
            "cloud_cover_low",
            "cloud_cover_mid",
            "cloud_cover_high",
        ],
    )
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start,
        "end_date": end,
        "hourly": ",".join(hourly_fields),
        "timezone": "UTC",
        "wind_speed_unit": "ms",
    }
    # Historical Forecast API 端点，与 Archive API 不同
    url = source.get("url", "https://historical-forecast-api.open-meteo.com/v1/forecast")
    response = requests.get(url, params=params, timeout=int(source.get("timeout_seconds", 120)))
    response.raise_for_status()
    payload = response.json()

    hourly = payload.get("hourly")
    if not hourly or "time" not in hourly:
        raise ValueError("Open-Meteo Historical Forecast response missing hourly.time")

    frame = pd.DataFrame(hourly).rename(columns={"time": "timestamp"})
    # 将 timestamp 列转为 UTC datetime，用于计算起报时刻
    valid_time = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)

    # 写入预报审计字段：
    #   weather_provider —— 数据提供商标识
    #   weather_forecast_lead_time_hour —— 假设的预报时效（小时）
    #   weather_forecast_issue_time —— 由 valid_time - lead_time 反推的起报时刻
    #   weather_forecast_issue_time_is_assumed —— 标记起报时刻为假设值，
    #       不是来自真实 forecast cycle 的元数据
    frame["weather_provider"] = provider
    frame["weather_forecast_lead_time_hour"] = assumed_lead_time_hour
    frame["weather_forecast_issue_time"] = (
        valid_time - pd.to_timedelta(assumed_lead_time_hour, unit="h")
    ).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    frame["weather_forecast_issue_time_is_assumed"] = True

    frame.to_csv(target, index=False)
    return DownloadResult(name="weather_forecast", path=target, source_url=response.url)


def fetch_open_meteo_historical_forecast_range(
    *,
    latitude: float,
    longitude: float,
    start: str,
    end: str,
    source: dict,
    raw_dir: Path,
) -> DownloadResult:
    """按月分块获取长时间范围的 Open-Meteo Historical Forecast 数据。

    公共天气 API 对长时间范围请求的稳定性较差（可能超时或截断）。
    此包装器按月切分请求：
      1. 逐月调用 fetch_open_meteo_historical_forecast，每个月份块独立缓存；
      2. 合并所有月块，按 timestamp 去重排序；
      3. 写入一个去重后的合并 CSV 供下游 normalization 消费。

    优势：
      - 单月请求失败可重试，不必重下整个时间范围；
      - 月级缓存避免重复下载；
      - 合并时去重处理月份边界可能的重叠。

    Args:
        latitude: 站点纬度。
        longitude: 站点经度。
        start: 起始日期。
        end: 结束日期。
        source: 配置字典，可选 "merged_target_name"。
        raw_dir: 原始数据根目录。

    Returns:
        name 为 "weather_forecast" 的 DownloadResult，path 指向合并文件。

    Raises:
        ValueError: start > end 时抛出。
    """
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    if start_ts > end_ts:
        raise ValueError(f"Invalid weather forecast range: {start} > {end}")

    ensure_dir(raw_dir / "weather_forecast")
    # 合并缓存文件名由起止日期和经纬度决定
    merged_target = raw_dir / "weather_forecast" / (
        source.get("merged_target_name")
        or f"open_meteo_historical_forecast_merged_{start}_{end}_{latitude:.4f}_{longitude:.4f}.csv"
    )
    # 幂等检查：合并缓存已存在则直接返回
    if merged_target.exists():
        return DownloadResult(name="weather_forecast", path=merged_target, source_url=None)

    # 逐月分块获取
    chunk_frames: list[pd.DataFrame] = []
    source_urls: list[str] = []
    cursor = start_ts
    while cursor <= end_ts:
        # MonthEnd(0) 将 cursor 推到当月最后一天，不超过 end_ts
        chunk_end = min(cursor + pd.offsets.MonthEnd(0), end_ts)
        # 为每个月份块设置独立的 target_name，使月级缓存也独立存储
        chunk_source = dict(source)
        chunk_source["target_name"] = (
            f"open_meteo_historical_forecast_{cursor.date()}_{chunk_end.date()}_"
            f"{latitude:.4f}_{longitude:.4f}.csv"
        )
        # 调用单月获取函数（复用缓存逻辑）
        chunk = fetch_open_meteo_historical_forecast(
            latitude=latitude,
            longitude=longitude,
            start=str(cursor.date()),
            end=str(chunk_end.date()),
            source=chunk_source,
            raw_dir=raw_dir,
        )
        chunk_frames.append(pd.read_csv(chunk.path, low_memory=False))
        if chunk.source_url:
            source_urls.append(chunk.source_url)
        # 游标推进到下月第一天
        cursor = chunk_end + pd.Timedelta(days=1)

    # 合并所有月块，去重排序
    merged = pd.concat(chunk_frames, ignore_index=True)
    merged["timestamp"] = pd.to_datetime(merged["timestamp"], errors="coerce", utc=True)
    merged = (
        merged.dropna(subset=["timestamp"])
        .drop_duplicates(subset=["timestamp"], keep="first")  # 月份边界可能有重叠行
        .sort_values("timestamp")                              # 保证时间轴单调递增
    )
    # 转回字符串格式存储，与单文件格式一致
    merged["timestamp"] = merged["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    merged.to_csv(merged_target, index=False)
    return DownloadResult(
        name="weather_forecast",
        path=merged_target,
        source_url=source_urls[0] if source_urls else None,
    )


def fetch_nsrdb_weather(
    *,
    latitude: float,
    longitude: float,
    year: str,
    source: dict,
    raw_dir: Path,
) -> DownloadResult:
    """从 NSRDB PSM API 获取单点单年的太阳资源数据。

    NSRDB 直接 CSV 流式下载限制为单个点和单个年份，与当前单站点实验匹配。
    API 返回 SAM 格式 CSV：前两行分别为元数据行和列头行，后续为小时级记录。

    认证要求：
      - API key：通过环境变量（默认 NREL_API_KEY）或配置中的 api_key 字段提供。
      - 联系邮箱：通过环境变量（默认 NREL_API_EMAIL）或配置中的 email 字段提供。
      环境变量优先级高于配置文件中的硬编码值。

    Args:
        latitude: 站点纬度。
        longitude: 站点经度。
        year: 目标年份字符串，如 "2022"。
        source: 配置字典，可选 "api_key"/"api_key_env"、"email"/"email_env"、
                "attributes"、"url"、"interval"、"leap_day"、"target_name" 等。
        raw_dir: 原始数据根目录。

    Returns:
        name 为 "weather" 的 DownloadResult。

    Raises:
        ValueError: 缺少 API key 或邮箱时抛出；API 返回错误响应时抛出。
    """
    ensure_dir(raw_dir / "weather")
    target = raw_dir / "weather" / source.get(
        "target_name",
        f"nsrdb_{year}_{latitude:.4f}_{longitude:.4f}.csv",
    )
    # 幂等检查
    if target.exists():
        return DownloadResult(name="weather", path=target, source_url=None)

    # --- 认证信息获取（环境变量优先于配置文件中的硬编码值） ---
    api_key = source.get("api_key")
    api_key_env = source.get("api_key_env")
    if api_key_env:
        # 优先从环境变量读取，配置值作为兜底
        api_key = os.environ.get(str(api_key_env), api_key)
    if not api_key:
        raise ValueError(
            "NSRDB requires an API key. Set the configured environment variable "
            f"`{api_key_env or 'NREL_API_KEY'}` or provide `api_key` in the config."
        )

    email = source.get("email")
    email_env = source.get("email_env")
    if email_env:
        email = os.environ.get(str(email_env), email)
    if not email:
        raise ValueError(
            "NSRDB requires a contact email. Set the configured environment variable "
            f"`{email_env or 'NREL_API_EMAIL'}` or provide `email` in the config."
        )

    # --- 请求参数构造 ---
    # 请求的气象属性列表，默认覆盖辐照、云、温湿、风和填充标志
    attributes = source.get(
        "attributes",
        [
            "dhi",                       # 散射水平辐照度
            "dni",                       # 法向直射辐照度
            "ghi",                       # 总水平辐照度
            "clearsky_dhi",              # 晴空散射水平辐照度
            "clearsky_dni",              # 晴空法向直射辐照度
            "clearsky_ghi",              # 晴空总水平辐照度
            "cloud_type",                # 云类型
            "dew_point",                 # 露点温度
            "air_temperature",           # 气温
            "surface_pressure",          # 地表气压
            "relative_humidity",         # 相对湿度
            "solar_zenith_angle",        # 太阳天顶角
            "surface_albedo",            # 地表反照率
            "total_precipitable_water",  # 总可降水量
            "wind_direction",            # 风向
            "wind_speed",                # 风速
            "fill_flag",                 # 数据填充标志
        ],
    )
    params = {
        "api_key": api_key,
        "full_name": source.get("full_name", "New Energy Sys"),
        "email": email,
        "affiliation": source.get("affiliation", "Academic"),
        "reason": source.get("reason", "research"),
        "mailing_list": str(source.get("mailing_list", "false")).lower(),
        # WKT POINT 格式：经度在前、纬度在后（NSRDB 约定）
        "wkt": f"POINT({longitude} {latitude})",
        "names": year,
        "attributes": ",".join(attributes),
        "leap_day": str(source.get("leap_day", "false")).lower(),
        "utc": "true",                   # 统一使用 UTC 时区
        "interval": int(source.get("interval", 60)),  # 时间粒度（分钟），默认 60
    }
    # NSRDB API 端点，配置中可覆盖
    url = source.get(
        "url",
        "https://developer.nlr.gov/api/nsrdb/v2/solar/nsrdb-GOES-aggregated-v4-0-0-download.csv",
    )
    response = requests.get(url, params=params, timeout=120)
    response.raise_for_status()

    # --- 响应校验 ---
    # NSRDB 在认证失败或参数错误时可能返回 JSON 错误而非 CSV，
    # 检查响应头 500 字符中是否包含错误关键词，或是否以 JSON 开头
    text = response.text
    header = text[:500].lower()
    if "errors" in header or "api key" in header or text.lstrip().startswith("{"):
        raise ValueError(f"NSRDB returned an error response: {text[:500]}")

    # 直接写入原始文本（SAM 格式 CSV，含元数据行），不做解析
    target.write_text(text, encoding="utf-8")
    return DownloadResult(name="weather", path=target, source_url=response.url)


def fetch_nsrdb_weather_years(
    *,
    latitude: float,
    longitude: float,
    start: str,
    end: str,
    source: dict,
    raw_dir: Path,
) -> DownloadResult:
    """下载并合并 NSRDB 单点多年份数据，为 PVDAQ 时间跨度提供天气覆盖。

    NSRDB 直接 CSV 下载限制为 point-year 请求（一次一个点 + 一年）。
    PVDAQ system 10 覆盖 2020-2022 完整三年，因此此包装器逐年下载，
    然后创建一个只有一组元数据/列头行的 SAM 格式合并 CSV。
    下游 normalizer 只需处理一种稳定的解析路径。

    合并策略：
      - 第一年：保留完整文件（元数据行 + 列头行 + 数据行）。
      - 后续年份：跳过前 2 行（元数据行和列头行），只保留数据行。
      这样避免重复列头行被 pandas 解析为 NaN 行，后者会在数据中
      产生无法解释的天气缺失假象。

    Args:
        latitude: 站点纬度。
        longitude: 站点经度。
        start: 起始日期。
        end: 结束日期。
        source: 配置字典，可选 "merged_target_name"。
        raw_dir: 原始数据根目录。

    Returns:
        name 为 "weather" 的 DownloadResult，path 指向多年合并文件。

    Raises:
        ValueError: 单年度 CSV 行数不足 3 行时抛出（文件可能损坏）。
    """
    # 从起止日期推算年份范围
    start_year = int(pd.Timestamp(start).year)
    end_year = int(pd.Timestamp(end).year)
    years = list(range(start_year, end_year + 1))

    ensure_dir(raw_dir / "weather")
    target = raw_dir / "weather" / source.get(
        "merged_target_name",
        f"nsrdb_merged_{start}_{end}_{latitude:.4f}_{longitude:.4f}.csv",
    )
    # 幂等检查
    if target.exists():
        return DownloadResult(name="weather", path=target, source_url=None)

    source_urls: list[str] = []
    # 逐行合并：直接操作原始文本行，避免 pandas 解析 SAM 格式的元数据行
    merged_lines: list[str] = []
    for index, year in enumerate(years):
        # 为每年设置独立的 target_name，使单年缓存也独立存储
        year_source = dict(source)
        year_source["target_name"] = f"nsrdb_{year}_{latitude:.4f}_{longitude:.4f}.csv"
        result = fetch_nsrdb_weather(
            latitude=latitude,
            longitude=longitude,
            year=str(year),
            source=year_source,
            raw_dir=raw_dir,
        )
        # 读取单年缓存文件的原始文本
        lines = result.path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if len(lines) < 3:
            # SAM 格式最少需要 2 行头 + 1 行数据
            raise ValueError(f"NSRDB CSV for {year} is too short: {result.path}")

        # 第一年保留完整文件（元数据 + 列头 + 数据）；
        # 后续年份跳过前 2 行（元数据行 + 列头行），只追加数据行。
        # 重复的列头行被 pandas 解析为 NaN 后会产生无法解释的天气缺失。
        if index == 0:
            merged_lines.extend(lines)
        else:
            merged_lines.extend(lines[2:])
        if result.source_url:
            source_urls.append(result.source_url)

    # 写入合并后的 SAM 格式文件
    target.write_text("\n".join(merged_lines) + "\n", encoding="utf-8")
    return DownloadResult(name="weather", path=target, source_url=source_urls[0] if source_urls else None)


def fetch_weather_with_fallback(
    *,
    latitude: float,
    longitude: float,
    start: str,
    end: str,
    source: dict,
    raw_dir: Path,
) -> DownloadResult:
    """优先从 NSRDB 获取天气数据，失败时降级到 Open-Meteo/ERA5。

    降级场景包括：
      - NSRDB API key 过期或未配置；
      - NSRDB 服务维护或请求频率限制；
      - 网络不稳定导致 NSRDB 超时。

    降级后获取的 Open-Meteo 数据在字段和精度上与 NSRDB 有差异，
    下游 standardize.py 会做列名映射和单位统一。

    Args:
        latitude: 站点纬度。
        longitude: 站点经度。
        start: 起始日期。
        end: 结束日期。
        source: 配置字典，必须包含 "nsrdb" 子字典传递给 NSRDB 适配器。
        raw_dir: 原始数据根目录。

    Returns:
        name 为 "weather" 的 DownloadResult，来源可能是 NSRDB 或 Open-Meteo。
    """
    year = str(pd.Timestamp(start).year)
    fallback_name = f"open_meteo_era5_fallback_{start}_{end}.csv"
    try:
        # 优先尝试 NSRDB
        return fetch_nsrdb_weather(
            latitude=latitude,
            longitude=longitude,
            year=year,
            source=source.get("nsrdb", {}),
            raw_dir=raw_dir,
        )
    except Exception as exc:
        # NSRDB 失败时打印原因并降级到 Open-Meteo Archive
        print(f"NSRDB 获取失败，切换 Open-Meteo/ERA5: {exc}")
        return fetch_open_meteo_archive(
            latitude=latitude,
            longitude=longitude,
            start=start,
            end=end,
            raw_dir=raw_dir,
            target_name=fallback_name,
        )


# ---------------------------------------------------------------------------
# 调度入口：根据配置中的 sources.kind 分发到对应适配器
# ---------------------------------------------------------------------------


def fetch_declared_sources(config: dict, raw_dir: Path, root_dir: Path | None = None) -> dict[str, DownloadResult]:
    """根据配置字典中声明的数据源，分发调用对应的下载/注册函数。

    此函数是 Stage 1 数据采集的统一入口，由 cli.bootstrap_data 调用。
    读取 config["sources"] 中各数据源的 "kind" 字段，分发到对应的适配器：
      - pv_power: csv_url / nrel_solar_zip / pvdaq_s3_year / pvdaq_s3_years
      - weather: open_meteo_archive / open_meteo_historical_forecast /
                 nsrdb / nsrdb_then_open_meteo / local_csv
      - opsd: csv_url / local_csv

    返回字典的 key 为数据类别名（"pv_power"、"weather"、"opsd"），
    下游 standardize.py 按此名称取用对应的原始文件。

    Args:
        config: 从 JSON 配置文件加载的完整字典，必须包含
                "site"、"date_range"、"sources" 三个顶层节点。
        raw_dir: 原始数据根目录（data/raw）。
        root_dir: 仓库根目录，仅当使用 local_csv 数据源时需要，
                  用于解析相对路径。为 None 时使用 local_csv 会报错。

    Returns:
        数据类别名到 DownloadResult 的映射字典。
    """
    # 从配置中提取站点信息和日期范围，供天气/OPSD 适配器使用
    site = config["site"]
    date_range = config["date_range"]
    sources = config["sources"]
    results: dict[str, DownloadResult] = {}

    # --- PV 功率数据源分发 ---
    pv_source = sources.get("pv_power")
    if pv_source and pv_source.get("kind") == "csv_url":
        results["pv_power"] = download_csv_url("pv_power", pv_source, raw_dir)
    elif pv_source and pv_source.get("kind") == "nrel_solar_zip":
        results["pv_power"] = download_nrel_solar_zip(pv_source, raw_dir)
    elif pv_source and pv_source.get("kind") == "pvdaq_s3_year":
        results["pv_power"] = download_pvdaq_s3_year(pv_source, raw_dir)
    elif pv_source and pv_source.get("kind") == "pvdaq_s3_years":
        results["pv_power"] = download_pvdaq_s3_years(pv_source, raw_dir)

    # --- 天气数据源分发 ---
    weather_source = sources.get("weather")
    if weather_source and weather_source.get("kind") == "open_meteo_archive":
        results["weather"] = fetch_open_meteo_archive(
            latitude=float(site["latitude"]),
            longitude=float(site["longitude"]),
            start=date_range["start"],
            end=date_range["end"],
            raw_dir=raw_dir,
        )
    elif weather_source and weather_source.get("kind") == "open_meteo_historical_forecast":
        # 长时间范围使用分月获取的包装器，而非单次请求
        results["weather"] = fetch_open_meteo_historical_forecast_range(
            latitude=float(site["latitude"]),
            longitude=float(site["longitude"]),
            start=date_range["start"],
            end=date_range["end"],
            source=weather_source,
            raw_dir=raw_dir,
        )
    elif weather_source and weather_source.get("kind") == "nsrdb":
        # NSRDB 限制 point-year 请求，使用多年合并包装器
        results["weather"] = fetch_nsrdb_weather_years(
            latitude=float(site["latitude"]),
            longitude=float(site["longitude"]),
            start=date_range["start"],
            end=date_range["end"],
            source=weather_source,
            raw_dir=raw_dir,
        )
    elif weather_source and weather_source.get("kind") == "nsrdb_then_open_meteo":
        # NSRDB 优先 + Open-Meteo 降级策略
        results["weather"] = fetch_weather_with_fallback(
            latitude=float(site["latitude"]),
            longitude=float(site["longitude"]),
            start=date_range["start"],
            end=date_range["end"],
            source=weather_source,
            raw_dir=raw_dir,
        )
    elif weather_source and weather_source.get("kind") == "local_csv":
        # 本地注册文件需要 root_dir 来解析相对路径
        if root_dir is None:
            raise ValueError("local_csv 数据源需要 root_dir")
        results["weather"] = use_local_file("weather", weather_source, root_dir)

    # --- OPSD（负荷与电价）数据源分发 ---
    opsd_source = sources.get("opsd")
    if opsd_source and opsd_source.get("kind") == "csv_url":
        results["opsd"] = download_csv_url("opsd", opsd_source, raw_dir)
    elif opsd_source and opsd_source.get("kind") == "local_csv":
        if root_dir is None:
            raise ValueError("local_csv 数据源需要 root_dir")
        results["opsd"] = use_local_file("opsd", opsd_source, root_dir)

    return results
