"""CLI entry point for importing frontend display artifacts into MySQL."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from backend.app import database
from backend.app.db_importer import import_display_data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import New Energy Sys frontend display data into the MySQL application query database.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="SQLAlchemy URL, for example mysql+pymysql://user:password@127.0.0.1:3306/new_energy_sys?charset=utf8mb4.",
    )
    parser.add_argument(
        "--source-dir",
        default="data/processed/pvdaq_nsrdb_2020_2022",
        type=Path,
        help="Directory containing processed CSV/JSON/Parquet frontend artifacts.",
    )
    parser.add_argument(
        "--project-root",
        default=Path.cwd(),
        type=Path,
        help="Repository root used to resolve config files and store relative artifact paths.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Clear existing display tables before importing the current artifact set.",
    )
    parser.add_argument(
        "--chunk-size",
        default=5000,
        type=int,
        help="Rows per SQLAlchemy flush while importing large display tables.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.database_url:
        os.environ["NES_DATABASE_URL"] = args.database_url
        database.reset_engine_cache()

    engine = database.get_engine()
    summary = import_display_data(
        engine,
        source_dir=args.source_dir,
        project_root=args.project_root,
        replace=args.replace,
        chunk_size=args.chunk_size,
    )
    print(json.dumps({
        "rows_by_artifact": summary.rows_by_artifact,
        "registered_artifacts": summary.registered_artifacts,
        "total_rows": summary.total_rows,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
