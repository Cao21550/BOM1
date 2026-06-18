from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from bom_tool.adapters.registry import create_adapters
from bom_tool.core.bom_pipeline import (
    DEFAULT_CACHE_TTL_HOURS,
    DEFAULT_OUTPUT_FIELDS,
    BomPipeline,
    BomPipelineConfig,
)
from bom_tool.core.task_manager import TaskProgress
from bom_tool.models import SearchType


def main() -> int:
    parser = argparse.ArgumentParser(description="Run BOM completion pipeline.")
    parser.add_argument("input", type=Path, help="Input .xlsx or .csv BOM file")
    parser.add_argument("output", type=Path, help="Output file path")
    parser.add_argument(
        "--search-column",
        required=True,
        help="Header name or 1-based column number",
    )
    parser.add_argument(
        "--search-type",
        choices=[item.value for item in SearchType],
        default=SearchType.MPN.value,
    )
    parser.add_argument(
        "--supplier",
        action="append",
        help="Supplier adapter name; repeatable. Supported: lcsc, hqchip. Defaults to lcsc",
    )
    parser.add_argument("--sheet", default=None, help="Excel sheet name")
    parser.add_argument("--max-concurrent", type=int, default=1)
    parser.add_argument(
        "--retry-max-concurrent",
        type=int,
        default=1,
        help="Retry concurrency for failed queries",
    )
    parser.add_argument("--field", action="append", help="Output field; can be repeated")
    parser.add_argument("--no-cache", action="store_true", help="Disable SQLite query cache")
    parser.add_argument("--cache-path", type=Path, default=None, help="SQLite cache path")
    parser.add_argument(
        "--cache-ttl-hours",
        type=int,
        default=DEFAULT_CACHE_TTL_HOURS,
        help="Cache TTL in hours",
    )
    parser.add_argument(
        "--fast-xlsx",
        action="store_true",
        help="Speed up .xlsx export by skipping copied cell styles for appended data",
    )
    parser.add_argument(
        "--lcsc-interval",
        type=float,
        default=1.2,
        help="LCSC rate limiter interval in seconds. Lower = faster but may trigger anti-bot (default 1.2)",
    )
    args = parser.parse_args()

    config = BomPipelineConfig(
        input_path=args.input,
        output_path=args.output,
        search_column=args.search_column,
        search_type=SearchType(args.search_type),
        output_fields=args.field or DEFAULT_OUTPUT_FIELDS,
        sheet_name=args.sheet,
        max_concurrent=args.max_concurrent,
        retry_max_concurrent=args.retry_max_concurrent,
        enable_cache=not args.no_cache,
        cache_path=args.cache_path,
        cache_ttl_hours=args.cache_ttl_hours,
        preserve_excel_styles=not args.fast_xlsx,
    )
    pipeline = BomPipeline(create_adapters(args.supplier or ["lcsc"], lcsc_interval=args.lcsc_interval))

    def report(progress: TaskProgress) -> None:
        print(
            f"progress rows={progress.completed_rows}/{progress.total_rows} "
            f"queries={progress.completed}/{progress.total} "
            f"success={progress.success} failed={progress.failed} "
            f"not_found={progress.not_found} cached={progress.cache_hits} "
            f"deduped={progress.deduplicated}"
        )

    result = asyncio.run(pipeline.run(config, report))
    print(f"exported: {result.output_path}")
    print(f"rows: {result.total_rows}")
    print(f"fields: {', '.join(result.exported_fields)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
