from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from bom_tool.adapters.base_adapter import BaseSupplierAdapter
from bom_tool.core.data_cleaner import clean_mpn
from bom_tool.db.cache_db import CacheDB
from bom_tool.models import PartResult, QueryStatus, SearchType

ProgressCallback = Callable[["TaskProgress"], None | Awaitable[None]]
DEFAULT_CACHE_TTL_HOURS = 24 * 7
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")
_NON_MPN_KEYWORDS = {
    "元件名称",
    "芯片",
    "物料",
    "备注",
    "型号",
    "器件型号",
    "part number",
    "mpn",
}
_LCSC_SKU_PATTERN = re.compile(r"^C\d+$", re.IGNORECASE)
_HQCHIP_SKU_PATTERN = re.compile(r"^(?:IC|HQ|Hq|ic|hq)?\d{5,}$")


@dataclass(slots=True)
class TaskProgress:
    total: int
    completed: int = 0
    total_rows: int = 0
    completed_rows: int = 0
    success: int = 0
    failed: int = 0
    not_found: int = 0
    cache_hits: int = 0
    deduplicated: int = 0


@dataclass(slots=True)
class QueryPlan:
    keyword_list: list[str]
    query_to_keywords: dict[tuple[str, SearchType], list[str]]

    @property
    def unique_queries(self) -> list[tuple[str, SearchType]]:
        return list(self.query_to_keywords)

    @property
    def searchable_rows(self) -> int:
        return sum(len(values) for values in self.query_to_keywords.values())

    @property
    def deduplicated(self) -> int:
        return self.searchable_rows - len(self.query_to_keywords)


class TaskManager:
    def __init__(
        self,
        adapters: Iterable[BaseSupplierAdapter],
        max_concurrent: int = 5,
        retry_max_concurrent: int = 1,
        cache: CacheDB | None = None,
        cache_ttl_hours: int = DEFAULT_CACHE_TTL_HOURS,
    ) -> None:
        self.adapters = list(adapters)
        self.max_concurrent = max(1, max_concurrent)
        self.retry_max_concurrent = max(1, retry_max_concurrent)
        self.semaphore = asyncio.Semaphore(self.max_concurrent)
        self.cache = cache
        self.cache_ttl_hours = cache_ttl_hours
        self._cancelled = False
        self._prefetched_cache: dict[tuple[str, SearchType, str], PartResult] = {}
        self._pending_cache_rows: list[tuple[str, SearchType, str, dict[str, Any]]] = []
        self._batch_cache_enabled = False

    def cancel(self) -> None:
        self._cancelled = True

    async def process_single_item(
        self,
        keyword: str,
        search_type: SearchType = SearchType.MPN,
    ) -> list[PartResult]:
        query, effective_search_type = self._resolve_keyword(keyword, search_type)
        async with self.semaphore:
            results, _ = await self._search_query(query, effective_search_type)
            return results

    async def process_bom(
        self,
        keywords: Iterable[str],
        search_type: SearchType = SearchType.MPN,
        progress_callback: ProgressCallback | None = None,
        retry_failed: bool = True,
    ) -> dict[str, list[PartResult]]:
        plan = self.build_query_plan(keywords, search_type)
        query_to_keywords = plan.query_to_keywords
        unique_queries = plan.unique_queries
        progress = TaskProgress(
            total=len(unique_queries),
            total_rows=plan.searchable_rows,
            deduplicated=plan.deduplicated,
        )
        results: dict[str, list[PartResult]] = {}
        query_results: dict[tuple[str, SearchType], list[PartResult]] = {}
        progress_lock = asyncio.Lock()

        async def process_query(query_key: tuple[str, SearchType], is_retry: bool = False) -> None:
            query, effective_search_type = query_key
            async with self.semaphore:
                item_results, cache_hits = await self._search_query(query, effective_search_type)
            async with progress_lock:
                query_results[query_key] = item_results
                for keyword in query_to_keywords[query_key]:
                    results[keyword] = item_results
                progress.completed += 1
                if not is_retry:
                    progress.completed_rows += len(query_to_keywords[query_key])
                progress.cache_hits += cache_hits
                self._refresh_progress_counts(progress, query_results.values())

                if progress_callback:
                    callback_result = progress_callback(progress)
                    if callback_result:
                        await callback_result

        async def worker(queue: asyncio.Queue[tuple[tuple[str, SearchType], bool] | None]) -> None:
            while not self._cancelled:
                item = await queue.get()
                try:
                    if item is None:
                        return
                    await process_query(item[0], item[1])
                finally:
                    queue.task_done()

        async def run_queries(
            queries: list[tuple[str, SearchType]],
            is_retry: bool = False,
            worker_limit: int | None = None,
        ) -> None:
            queue: asyncio.Queue[tuple[tuple[str, SearchType], bool] | None] = asyncio.Queue()
            for query in queries:
                queue.put_nowait((query, is_retry))

            worker_count = min(worker_limit or self.max_concurrent, len(queries))
            for _ in range(worker_count):
                queue.put_nowait(None)

            pending = [asyncio.create_task(worker(queue)) for _ in range(worker_count)]
            try:
                if pending:
                    await asyncio.gather(*pending)
            finally:
                for task in pending:
                    if not task.done():
                        task.cancel()

        try:
            self._prefetch_cache(unique_queries)
            await run_queries(unique_queries)
            retry_queries = [
                query_key
                for query_key, item_results in query_results.items()
                if self._should_retry_results(item_results)
            ]
            if retry_failed and retry_queries and not self._cancelled:
                progress.total += len(retry_queries)
                await run_queries(
                    retry_queries,
                    is_retry=True,
                    worker_limit=self.retry_max_concurrent,
                )
        finally:
            try:
                self._flush_pending_cache_rows()
            finally:
                await self._close_adapters()

        return results

    def build_query_plan(
        self,
        keywords: Iterable[str],
        search_type: SearchType = SearchType.MPN,
    ) -> QueryPlan:
        keyword_list = [str(keyword) for keyword in keywords if str(keyword).strip()]
        query_to_keywords: dict[tuple[str, SearchType], list[str]] = {}
        for keyword in keyword_list:
            query, effective_search_type = self._resolve_keyword(keyword, search_type)
            if query:
                query_to_keywords.setdefault((query, effective_search_type), []).append(keyword)
        return QueryPlan(keyword_list=keyword_list, query_to_keywords=query_to_keywords)

    def count_cached_adapter_results(
        self,
        query_keys: list[tuple[str, SearchType]],
    ) -> dict[tuple[str, SearchType], int]:
        if self.cache is None or not query_keys:
            return {}

        cache_keys = self._cache_keys_for_queries(query_keys)
        payloads = self.cache.get_many(cache_keys, self.cache_ttl_hours)
        counts: dict[tuple[str, SearchType], int] = {}
        for supplier, search_type, query in payloads:
            _ = supplier
            counts[(query, search_type)] = counts.get((query, search_type), 0) + 1
        return counts

    async def _search_query(
        self,
        query: str,
        search_type: SearchType,
    ) -> tuple[list[PartResult], int]:
        results: list[PartResult | None] = []
        tasks: list[asyncio.Task[PartResult]] = []
        task_indexes: list[int] = []
        cache_hits = 0

        for adapter in self.adapters:
            cached = self._get_cached_result(adapter, query, search_type)
            if cached:
                results.append(cached)
                cache_hits += 1
                continue

            results.append(None)
            task_indexes.append(len(results) - 1)
            tasks.append(asyncio.create_task(self._search_adapter(adapter, query, search_type)))

        if tasks:
            fresh_results = await asyncio.gather(*tasks)
            for index, result in zip(task_indexes, fresh_results, strict=True):
                results[index] = result
                self._set_cached_result(result)

        return [result for result in results if result is not None], cache_hits

    async def _search_adapter(
        self,
        adapter: BaseSupplierAdapter,
        query: str,
        search_type: SearchType,
    ) -> PartResult:
        try:
            if search_type == SearchType.SKU:
                return await adapter.search_by_sku(query)
            return await adapter.search_by_mpn(query)
        except Exception as exc:
            return adapter.failed_result(query, search_type, str(exc))

    def _normalize_keyword(self, keyword: str, search_type: SearchType) -> str:
        return self._resolve_keyword(keyword, search_type)[0]

    def _resolve_keyword(self, keyword: str, search_type: SearchType) -> tuple[str, SearchType]:
        if search_type == SearchType.AUTO:
            raw_keyword = keyword.strip()
            if self._looks_like_sku(raw_keyword):
                return raw_keyword, SearchType.SKU
            cleaned = clean_mpn(keyword)
            if self._looks_like_non_mpn_keyword(cleaned):
                return "", SearchType.MPN
            return cleaned, SearchType.MPN
        if search_type in {SearchType.MPN, SearchType.AUTO}:
            cleaned = clean_mpn(keyword)
            if self._looks_like_non_mpn_keyword(cleaned):
                return "", SearchType.MPN
            return cleaned, SearchType.MPN
        return keyword.strip(), SearchType.SKU

    def _looks_like_sku(self, keyword: str) -> bool:
        normalized = keyword.strip()
        return bool(
            _LCSC_SKU_PATTERN.fullmatch(normalized)
            or _HQCHIP_SKU_PATTERN.fullmatch(normalized)
        )

    def _looks_like_non_mpn_keyword(self, keyword: str) -> bool:
        normalized = keyword.strip().lower()
        return normalized in _NON_MPN_KEYWORDS or bool(_CJK_PATTERN.search(keyword))

    def _refresh_progress_counts(
        self,
        progress: TaskProgress,
        result_groups: Iterable[list[PartResult]],
    ) -> None:
        progress.success = 0
        progress.failed = 0
        progress.not_found = 0
        for item_results in result_groups:
            if any(result.status == QueryStatus.SUCCESS for result in item_results):
                progress.success += 1
            elif any(result.status == QueryStatus.FAILED for result in item_results):
                progress.failed += 1
            else:
                progress.not_found += 1

    def _should_retry_results(self, item_results: list[PartResult]) -> bool:
        return not any(result.status == QueryStatus.SUCCESS for result in item_results) and any(
            result.status == QueryStatus.FAILED for result in item_results
        )

    async def _close_adapters(self) -> None:
        for adapter in self.adapters:
            close = getattr(adapter, "close", None)
            if close is None:
                continue
            close_result = close()
            if close_result:
                await close_result

    def _get_cached_result(
        self,
        adapter: BaseSupplierAdapter,
        query: str,
        search_type: SearchType,
    ) -> PartResult | None:
        if self.cache is None:
            return None
        cache_key = getattr(adapter, "cache_key", adapter.supplier_name)
        prefetched = self._prefetched_cache.get((cache_key, search_type, query))
        if prefetched is not None:
            return prefetched

        payload = self.cache.get(cache_key, search_type, query, self.cache_ttl_hours)
        if not payload:
            return None
        return PartResult.from_dict(payload)

    def _set_cached_result(self, result: PartResult) -> None:
        if self.cache is None:
            return
        if result.status == QueryStatus.FAILED:
            return
        cache_supplier = self._cache_supplier_for_result(result)
        payload = result.to_dict()
        if self._batch_cache_enabled:
            self._pending_cache_rows.append(
                (cache_supplier, result.search_type, result.query, payload)
            )
            return
        self.cache.set(cache_supplier, result.search_type, result.query, payload)

    def _prefetch_cache(self, query_keys: list[tuple[str, SearchType]]) -> None:
        self._prefetched_cache = {}
        self._pending_cache_rows = []
        self._batch_cache_enabled = self.cache is not None and bool(query_keys)
        if self.cache is None or not query_keys:
            return

        cache_keys = [
            *self._cache_keys_for_queries(query_keys)
        ]
        payloads = self.cache.get_many(cache_keys, self.cache_ttl_hours)
        self._prefetched_cache = {
            key: PartResult.from_dict(payload)
            for key, payload in payloads.items()
        }

    def _flush_pending_cache_rows(self) -> None:
        if self.cache is None or not self._pending_cache_rows:
            return
        self.cache.set_many(self._pending_cache_rows)
        self._pending_cache_rows = []

    def _cache_supplier_for_result(self, result: PartResult) -> str:
        for adapter in self.adapters:
            if adapter.supplier_name == result.supplier:
                return getattr(adapter, "cache_key", adapter.supplier_name)
        return result.supplier

    def _cache_keys_for_queries(
        self,
        query_keys: list[tuple[str, SearchType]],
    ) -> list[tuple[str, SearchType, str]]:
        return [
            (getattr(adapter, "cache_key", adapter.supplier_name), search_type, query)
            for query, search_type in query_keys
            for adapter in self.adapters
        ]
