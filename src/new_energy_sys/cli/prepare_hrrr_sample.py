"""HRRR 单文件站点提取测试模块。

模块设计原则：
- 从单个 HRRR GRIB2 文件提取指定经纬度的站点气象样本
- 用于验证 HRRR 数据可读性与字段完整性
- 输出提取结果 CSV 与元数据 JSON

本模块对应项目 Stage 7 前置的 HRRR 单文件提取验证功能。

入口命令: new-energy-sys prepare-hrrr-sample --grib <path> --latitude <float> --longitude <float>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from new_energy_sys.hrrr import extract_hrrr_point_sample
from new_energy_sys.io_utils import ensure_dir


def parse_args() -> argparse.Namespace:
    """解析 HRRR 单文件提取测试命令行参数。"""

    parser = argparse.ArgumentParser(description="从单个 HRRR GRIB2 文件提取一个站点样本。")
    parser.add_argument("--grib", required=True, help="本地 HRRR GRIB2 文件路径。")
    parser.add_argument("--latitude", required=True, type=float, help="站点纬度。")
    parser.add_argument("--longitude", required=True, type=float, help="站点经度。")
    parser.add_argument("--output-csv", required=True, help="提取结果 CSV 输出路径。")
    parser.add_argument("--output-json", required=True, help="提取元数据 JSON 输出路径。")
    return parser.parse_args()


def main() -> None:
    """执行单文件 HRRR 提取并落盘 CSV 与 JSON 产物。"""
    args = parse_args()
    output_csv = Path(args.output_csv)
    output_json = Path(args.output_json)
    ensure_dir(output_csv.parent)
    ensure_dir(output_json.parent)

    result = extract_hrrr_point_sample(
        grib_path=args.grib,
        latitude=args.latitude,
        longitude=args.longitude,
    )
    result.frame.to_csv(output_csv, index=False)
    with output_json.open("w", encoding="utf-8") as handle:
        json.dump(result.metadata, handle, ensure_ascii=False, indent=2)

    print(f"HRRR sample CSV: {output_csv}")
    print(f"HRRR sample metadata: {output_json}")
    print(result.frame.to_string(index=False))


if __name__ == "__main__":
    main()
