from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bom_tool.adapters.base_adapter import BaseSupplierAdapter
from bom_tool.core.file_parser import read_preview_and_search_rows
from bom_tool.core.file_writer import fill_xlsx_data, write_csv_data
from bom_tool.core.task_manager import ProgressCallback, TaskManager
from bom_tool.db.cache_db import CacheDB
from bom_tool.models import PartResult, QueryStatus, SearchType

DEFAULT_OUTPUT_FIELDS = [
    "status",
    "query",
    "mpn",
    "sku",
    "brand",
    "package",
    "description",
]
DEFAULT_CACHE_TTL_HOURS = 24 * 7
DEFAULT_CACHE_PATH = Path(".cache") / "bom_cache.sqlite3"

SUPPLIER_LABELS = {
    "lcsc": "立创商城",
    "hqchip": "华秋商城",
    "mock": "模拟商城",
}

FIELD_LABELS = {
    "supplier": "供应商",
    "query": "查询关键字",
    "search_type": "查询方式",
    "status": "状态",
    "mpn": "型号",
    "sku": "商城编号",
    "brand": "品牌",
    "package": "封装",
    "description": "描述",
    "stock": "库存",
    "moq": "最小起订量",
    "price_unit": "单价",
    "product_url": "商品链接",
    "datasheet_url": "数据手册",
    "confidence": "匹配置信度",
    "error_message": "错误信息",
}


@dataclass(slots=True)
class BomPipelineConfig:
    input_path: Path
    output_path: Path
    search_column: str
    search_type: SearchType = SearchType.MPN
    output_fields: list[str] | None = None
    sheet_name: str | None = None
    header_row: int | None = None
    max_concurrent: int = 1
    retry_max_concurrent: int = 1
    enable_cache: bool = True
    cache_path: Path | None = None
    cache_ttl_hours: int = DEFAULT_CACHE_TTL_HOURS
    preserve_excel_styles: bool = True
    retry_failed: bool = True


@dataclass(slots=True)
class BomPipelineResult:
    output_path: Path
    total_rows: int
    exported_fields: list[str]
    row_results: dict[int, dict[str, Any]]


@dataclass(slots=True)
class QueryTaskRecord:
    row_number: int
    original_keyword: str
    normalized_query: str
    search_type: SearchType
    cache_hits: int
    supplier_count: int

    @property
    def needs_network(self) -> bool:
        return self.cache_hits < self.supplier_count

    @property
    def status(self) -> str:
        return "cached" if not self.needs_network else "needs_network"


@dataclass(slots=True)
class BomPrecheckResult:
    total_rows: int
    searchable_rows: int
    unique_queries: int
    deduplicated: int
    cached_adapter_results: int
    total_adapter_results: int
    network_adapter_results: int
    task_records: list[QueryTaskRecord]


class BomPipeline:
    def __init__(self, adapters: list[BaseSupplierAdapter]) -> None:
        if not adapters:
            raise ValueError("At least one supplier adapter is required")
        self.adapters = adapters

    async def run(
        self,
        config: BomPipelineConfig,
        progress_callback: ProgressCallback | None = None,
    ) -> BomPipelineResult:
        output_fields = config.output_fields or DEFAULT_OUTPUT_FIELDS
        preview, search_rows = read_preview_and_search_rows(
            config.input_path,
            config.search_column,
            sheet_name=config.sheet_name,
        )

        cache = (
            CacheDB(config.cache_path or DEFAULT_CACHE_PATH)
            if config.enable_cache
            else None
        )
        try:
            manager = TaskManager(
                self.adapters,
                config.max_concurrent,
                retry_max_concurrent=config.retry_max_concurrent,
                cache=cache,
                cache_ttl_hours=config.cache_ttl_hours,
            )
            query_rows = [
                row
                for row in search_rows
                if manager._resolve_keyword(row.keyword, config.search_type)[0]
            ]
            keyword_results = await manager.process_bom(
                [row.keyword for row in query_rows],
                config.search_type,
                progress_callback,
                retry_failed=config.retry_failed,
            )
        finally:
            if cache is not None:
                cache.close()

        row_results, exported_fields = self._build_row_results(
            query_rows,
            keyword_results,
            output_fields,
        )
        output_path = self._write_output(config, row_results, exported_fields, preview.header_row)

        return BomPipelineResult(
            output_path=output_path,
            total_rows=len(query_rows),
            exported_fields=exported_fields,
            row_results=row_results,
        )

    def _build_row_results(
        self,
        search_rows: list[Any],
        keyword_results: dict[str, list[PartResult]],
        output_fields: list[str],
    ) -> tuple[dict[int, dict[str, Any]], list[str]]:
        exported_fields = [
            self._output_header(adapter.supplier_name, field_name)
            for adapter in self.adapters
            for field_name in output_fields
        ]
        row_results: dict[int, dict[str, Any]] = {}

        for search_row in search_rows:
            values: dict[str, Any] = {}
            results_by_supplier = {
                result.supplier: result for result in keyword_results.get(search_row.keyword, [])
            }
            for adapter in self.adapters:
                result = results_by_supplier.get(adapter.supplier_name)
                result_data = (
                    result.to_dict() if result else self._missing_result(adapter.supplier_name)
                )
                for field_name in output_fields:
                    header = self._output_header(adapter.supplier_name, field_name)
                    values[header] = result_data.get(field_name)
            row_results[search_row.row_number] = values

        return row_results, exported_fields

    def _write_output(
        self,
        config: BomPipelineConfig,
        row_results: dict[int, dict[str, Any]],
        exported_fields: list[str],
        detected_header_row: int,
    ) -> Path:
        suffix = config.input_path.suffix.lower()
        if suffix == ".xlsx":
            return fill_xlsx_data(
                config.input_path,
                config.output_path,
                row_results,
                exported_fields,
                config.sheet_name,
                config.header_row or detected_header_row,
                config.preserve_excel_styles,
            )
        if suffix == ".csv":
            return write_csv_data(
                config.input_path,
                config.output_path,
                row_results,
                exported_fields,
                config.header_row or detected_header_row,
            )
        raise ValueError(f"Unsupported file format: {suffix}")

    def _missing_result(self, supplier_name: str) -> dict[str, Any]:
        return {
            "supplier": supplier_name,
            "status": QueryStatus.NOT_FOUND.value,
        }

    def _output_header(self, supplier_name: str, field_name: str) -> str:
        supplier_label = SUPPLIER_LABELS.get(supplier_name, supplier_name)
        field_label = FIELD_LABELS.get(field_name, field_name)
        return f"{supplier_label}_{field_label}"

    def precheck(self, config: BomPipelineConfig) -> BomPrecheckResult:
        _, search_rows = read_preview_and_search_rows(
            config.input_path,
            config.search_column,
            sheet_name=config.sheet_name,
        )
        cache = (
            CacheDB(config.cache_path or DEFAULT_CACHE_PATH)
            if config.enable_cache
            else None
        )
        try:
            manager = TaskManager(
                self.adapters,
                config.max_concurrent,
                retry_max_concurrent=config.retry_max_concurrent,
                cache=cache,
                cache_ttl_hours=config.cache_ttl_hours,
            )
            keywords = [row.keyword for row in search_rows]
            plan = manager.build_query_plan(keywords, config.search_type)
            cache_counts = manager.count_cached_adapter_results(plan.unique_queries)
        finally:
            if cache is not None:
                cache.close()

        supplier_count = len(self.adapters)
        task_records: list[QueryTaskRecord] = []
        for search_row in search_rows:
            normalized_query, effective_search_type = manager._resolve_keyword(
                search_row.keyword,
                config.search_type,
            )
            if not normalized_query:
                continue
            task_records.append(
                QueryTaskRecord(
                    row_number=search_row.row_number,
                    original_keyword=search_row.keyword,
                    normalized_query=normalized_query,
                    search_type=effective_search_type,
                    cache_hits=cache_counts.get((normalized_query, effective_search_type), 0),
                    supplier_count=supplier_count,
                )
            )

        total_adapter_results = len(plan.unique_queries) * supplier_count
        cached_adapter_results = sum(cache_counts.values())
        return BomPrecheckResult(
            total_rows=len(search_rows),
            searchable_rows=plan.searchable_rows,
            unique_queries=len(plan.unique_queries),
            deduplicated=plan.deduplicated,
            cached_adapter_results=cached_adapter_results,
            total_adapter_results=total_adapter_results,
            network_adapter_results=max(0, total_adapter_results - cached_adapter_results),
            task_records=task_records,
        )


def write_query_task_table(
    task_records: list[QueryTaskRecord],
    output_path: str | Path,
) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as target_file:
        writer = csv.writer(target_file)
        writer.writerow(
            [
                "row_number",
                "original_keyword",
                "normalized_query",
                "search_type",
                "cache_hits",
                "supplier_count",
                "needs_network",
                "status",
            ]
        )
        for record in task_records:
            writer.writerow(
                [
                    record.row_number,
                    record.original_keyword,
                    record.normalized_query,
                    record.search_type.value,
                    record.cache_hits,
                    record.supplier_count,
                    "yes" if record.needs_network else "no",
                    record.status,
                ]
            )
    return output
