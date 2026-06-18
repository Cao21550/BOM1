import asyncio

import pytest

from bom_tool.adapters.base_adapter import BaseSupplierAdapter
from bom_tool.adapters.hqchip_adapter import HqchipAdapter
from bom_tool.core.task_manager import TaskManager, TaskProgress
from bom_tool.db.cache_db import CacheDB
from bom_tool.models import PartResult, QueryStatus, SearchType


class CountingAdapter(BaseSupplierAdapter):
    def __init__(self) -> None:
        super().__init__("counting")
        self.queries: list[str] = []

    async def search_by_mpn(self, mpn: str) -> PartResult:
        self.queries.append(mpn)
        await asyncio.sleep(0)
        return PartResult(
            supplier=self.supplier_name,
            query=mpn,
            search_type=SearchType.MPN,
            status=QueryStatus.SUCCESS,
            mpn=mpn,
        )

    async def search_by_sku(self, sku: str) -> PartResult:
        return await self.search_by_mpn(sku)


class FlakyAdapter(BaseSupplierAdapter):
    def __init__(self) -> None:
        super().__init__("flaky")
        self.queries: list[str] = []

    async def search_by_mpn(self, mpn: str) -> PartResult:
        self.queries.append(mpn)
        await asyncio.sleep(0)
        if self.queries.count(mpn) == 1:
            return PartResult(
                supplier=self.supplier_name,
                query=mpn,
                search_type=SearchType.MPN,
                status=QueryStatus.FAILED,
                error_message="temporary failure",
            )
        return PartResult(
            supplier=self.supplier_name,
            query=mpn,
            search_type=SearchType.MPN,
            status=QueryStatus.SUCCESS,
            mpn=mpn,
        )

    async def search_by_sku(self, sku: str) -> PartResult:
        return await self.search_by_mpn(sku)


class RetryConcurrencyAdapter(BaseSupplierAdapter):
    def __init__(self) -> None:
        super().__init__("retry_concurrency")
        self.counts: dict[str, int] = {}
        self.active = 0
        self.max_active_on_retry = 0

    async def search_by_mpn(self, mpn: str) -> PartResult:
        self.counts[mpn] = self.counts.get(mpn, 0) + 1
        is_retry = self.counts[mpn] > 1
        if is_retry:
            self.active += 1
            self.max_active_on_retry = max(self.max_active_on_retry, self.active)
        await asyncio.sleep(0.01)
        if is_retry:
            self.active -= 1
        return PartResult(
            supplier=self.supplier_name,
            query=mpn,
            search_type=SearchType.MPN,
            status=QueryStatus.SUCCESS if is_retry else QueryStatus.FAILED,
            mpn=mpn if is_retry else None,
        )

    async def search_by_sku(self, sku: str) -> PartResult:
        return await self.search_by_mpn(sku)


class SearchTypeRecordingAdapter(BaseSupplierAdapter):
    def __init__(self) -> None:
        super().__init__("recording")
        self.calls: list[tuple[str, str]] = []

    async def search_by_mpn(self, mpn: str) -> PartResult:
        self.calls.append(("mpn", mpn))
        await asyncio.sleep(0)
        return PartResult(
            supplier=self.supplier_name,
            query=mpn,
            search_type=SearchType.MPN,
            status=QueryStatus.SUCCESS,
            mpn=mpn,
        )

    async def search_by_sku(self, sku: str) -> PartResult:
        self.calls.append(("sku", sku))
        await asyncio.sleep(0)
        return PartResult(
            supplier=self.supplier_name,
            query=sku,
            search_type=SearchType.SKU,
            status=QueryStatus.SUCCESS,
            sku=sku,
        )


@pytest.mark.asyncio
async def test_task_manager_deduplicates_cleaned_queries() -> None:
    adapter = CountingAdapter()
    manager = TaskManager([adapter], max_concurrent=2)
    progress_events: list[TaskProgress] = []

    results = await manager.process_bom(
        ["ABC-TR", "ABC", "XYZ"],
        SearchType.MPN,
        progress_events.append,
    )

    assert sorted(adapter.queries) == ["ABC", "XYZ"]
    assert results["ABC-TR"][0].mpn == "ABC"
    assert results["ABC"][0].mpn == "ABC"
    assert results["XYZ"][0].mpn == "XYZ"
    assert progress_events[-1].total == 2
    assert progress_events[-1].completed == 2
    assert progress_events[-1].total_rows == 3
    assert progress_events[-1].completed_rows == 3
    assert progress_events[-1].deduplicated == 1
    assert progress_events[-1].cache_hits == 0


@pytest.mark.asyncio
async def test_task_manager_skips_non_mpn_rows() -> None:
    adapter = CountingAdapter()
    manager = TaskManager([adapter], max_concurrent=1)
    progress_events: list[TaskProgress] = []

    results = await manager.process_bom(
        ["元件名称", "芯片", "STM32F103C8T6"],
        SearchType.MPN,
        progress_events.append,
    )

    assert adapter.queries == ["STM32F103C8T6"]
    assert "元件名称" not in results
    assert "芯片" not in results
    assert results["STM32F103C8T6"][0].mpn == "STM32F103C8T6"
    assert progress_events[-1].total == 1
    assert progress_events[-1].total_rows == 1


@pytest.mark.asyncio
async def test_task_manager_auto_search_type_detects_sku_and_mpn() -> None:
    adapter = SearchTypeRecordingAdapter()
    manager = TaskManager([adapter], max_concurrent=1)

    results = await manager.process_bom(["C8734", "STM32F103C8T6-TR"], SearchType.AUTO)

    assert adapter.calls == [("sku", "C8734"), ("mpn", "STM32F103C8T6")]
    assert results["C8734"][0].search_type == SearchType.SKU
    assert results["STM32F103C8T6-TR"][0].search_type == SearchType.MPN


@pytest.mark.asyncio
async def test_task_manager_retries_failed_queries_once() -> None:
    adapter = FlakyAdapter()
    manager = TaskManager([adapter], max_concurrent=1)
    progress_events: list[TaskProgress] = []

    results = await manager.process_bom(["ABC"], SearchType.MPN, progress_events.append)

    assert adapter.queries == ["ABC", "ABC"]
    assert results["ABC"][0].status == QueryStatus.SUCCESS
    assert results["ABC"][0].mpn == "ABC"
    assert progress_events[-1].total == 2
    assert progress_events[-1].completed == 2
    assert progress_events[-1].success == 1
    assert progress_events[-1].failed == 0


@pytest.mark.asyncio
async def test_task_manager_can_defer_failed_retries() -> None:
    adapter = FlakyAdapter()
    manager = TaskManager([adapter], max_concurrent=1)

    results = await manager.process_bom(["ABC"], SearchType.MPN, retry_failed=False)

    assert adapter.queries == ["ABC"]
    assert results["ABC"][0].status == QueryStatus.FAILED


@pytest.mark.asyncio
async def test_task_manager_uses_serial_retry_concurrency() -> None:
    adapter = RetryConcurrencyAdapter()
    manager = TaskManager([adapter], max_concurrent=2, retry_max_concurrent=1)

    results = await manager.process_bom(["ABC", "XYZ"], SearchType.MPN)

    assert results["ABC"][0].status == QueryStatus.SUCCESS
    assert results["XYZ"][0].status == QueryStatus.SUCCESS
    assert adapter.counts == {"ABC": 2, "XYZ": 2}
    assert adapter.max_active_on_retry == 1


@pytest.mark.asyncio
async def test_task_manager_uses_cache_between_runs(workspace_tmp_path) -> None:
    cache = CacheDB(workspace_tmp_path / "cache.sqlite3")
    first_adapter = CountingAdapter()
    first_manager = TaskManager([first_adapter], max_concurrent=1, cache=cache)

    try:
        await first_manager.process_bom(["ABC"], SearchType.MPN)

        second_adapter = CountingAdapter()
        second_manager = TaskManager([second_adapter], max_concurrent=1, cache=cache)
        progress_events: list[TaskProgress] = []
        results = await second_manager.process_bom(["ABC"], SearchType.MPN, progress_events.append)

        assert first_adapter.queries == ["ABC"]
        assert second_adapter.queries == []
        assert results["ABC"][0].mpn == "ABC"
        assert progress_events[-1].cache_hits == 1
    finally:
        cache.close()


@pytest.mark.asyncio
async def test_task_manager_batch_prefetches_and_writes_cache(workspace_tmp_path) -> None:
    class CountingCache(CacheDB):
        def __init__(self, path) -> None:
            super().__init__(path)
            self.get_many_calls = 0
            self.set_many_calls = 0

        def get_many(self, keys, ttl_hours=24):
            self.get_many_calls += 1
            return super().get_many(keys, ttl_hours)

        def set_many(self, rows) -> None:
            self.set_many_calls += 1
            super().set_many(rows)

    cache = CountingCache(workspace_tmp_path / "cache.sqlite3")
    adapter = CountingAdapter()
    manager = TaskManager([adapter], max_concurrent=1, cache=cache)

    try:
        await manager.process_bom(["ABC", "XYZ"], SearchType.MPN)

        assert cache.get_many_calls == 1
        assert cache.set_many_calls == 1
        assert cache.get("counting", SearchType.MPN, "ABC") is not None
        assert cache.get("counting", SearchType.MPN, "XYZ") is not None
    finally:
        cache.close()


@pytest.mark.asyncio
async def test_task_manager_uses_adapter_cache_key_for_hqchip(workspace_tmp_path) -> None:
    cache = CacheDB(workspace_tmp_path / "cache.sqlite3")
    try:
        cache.set(
            "hqchip",
            SearchType.MPN,
            "ABC",
            {
                "supplier": "hqchip",
                "query": "ABC",
                "search_type": "mpn",
                "status": "success",
                "description": "华秋商城暂未接入实时库存接口",
            },
        )

        class FakeHqchipAdapter(HqchipAdapter):
            async def search_by_mpn(self, mpn: str) -> PartResult:
                return PartResult(
                    supplier=self.supplier_name,
                    query=mpn,
                    search_type=SearchType.MPN,
                    status=QueryStatus.SUCCESS,
                    mpn=mpn,
                    stock=123,
                )

        manager = TaskManager([FakeHqchipAdapter()], max_concurrent=1, cache=cache)
        results = await manager.process_bom(["ABC"], SearchType.MPN)

        assert results["ABC"][0].stock == 123
        assert results["ABC"][0].description != "华秋商城暂未接入实时库存接口"
    finally:
        cache.close()
