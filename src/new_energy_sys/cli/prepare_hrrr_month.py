"""HRRR 月度站点提取模块。

模块设计原则：
- 对指定月份提取固定 lead-time 的 HRRR 站点预报气象
- 输出归一化天气 CSV 与提取元数据 JSON
- GRIB2 子集缓存至 cache_dir，避免重复下载

本模块对应项目 Stage 7 前置的 HRRR 月度提取功能。

入口命令: new-energy-sys prepare-hrrr-month --start <date> --end <date> --latitude <float> --longitude <float>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from new_energy_sys.hrrr import build_hrrr_monthly_point_table
from new_energy_sys.io_utils import ensure_dir


def parse_args() -> argparse.Namespace:
    """解析 HRRR 月度固定 lead-time 提取命令行参数。"""

    parser = argparse.ArgumentParser(description="准备一个月的严格 HRRR 站点预报气象。")
    parser.add_argument("--start", required=True, help="起始日期，格式 YYYY-MM-DD。")
    parser.add_argument("--end", required=True, help="结束日期，格式 YYYY-MM-DD。")
    parser.add_argument("--latitude", required=True, type=float, help="站点纬度。")
    parser.add_argument("--longitude", required=True, type=float, help="站点经度。")
    parser.add_argument("--lead-time-hour", required=True, type=int, help="HRRR 固定预报提前时间（小时）。")
    parser.add_argument("--cache-dir", required=True, help="GRIB2 子集缓存文件目录。")
    parser.add_argument("--output-csv", required=True, help="归一化天气 CSV 输出路径。")
    parser.add_argument("--output-json", required=True, help="提取元数据 JSON 输出路径。")
    return parser.parse_args()


def main() -> None:
    """执行月度 HRRR 提取并落盘 CSV 与 JSON 产物。"""
    args = parse_args()
    output_csv = Path(args.output_csv)
    output_json = Path(args.output_json)
    cache_dir = ensure_dir(Path(args.cache_dir))
    ensure_dir(output_csv.parent)
    ensure_dir(output_json.parent)

    table = build_hrrr_monthly_point_table(
        start=args.start,
        end=args.end,
        latitude=args.latitude,
        longitude=args.longitude,
        lead_time_hour=args.lead_time_hour,
        cache_dir=cache_dir,
    )
    table.to_csv(output_csv, index=False)

    report = {
        "start": args.start,
        "end": args.end,
        "latitude": args.latitude,
        "longitude": args.longitude,
        "lead_time_hour": args.lead_time_hour,
        "rows": int(len(table)),
        "min_timestamp": str(table["timestamp"].min()) if not table.empty else None,
        "max_timestamp": str(table["timestamp"].max()) if not table.empty else None,
        "cache_dir": str(cache_dir),
        "output_csv": str(output_csv),
    }
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"HRRR month CSV: {output_csv}")
    print(f"HRRR month metadata: {output_json}")
    print(table.head(5).to_string(index=False))


if __name__ == "__main__":
    main()
