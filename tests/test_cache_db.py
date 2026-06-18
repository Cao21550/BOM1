from bom_tool.db.cache_db import CacheDB
from bom_tool.models import SearchType


def test_cache_round_trip(workspace_tmp_path) -> None:
    cache = CacheDB(workspace_tmp_path / "cache.sqlite3")
    payload = {"status": "success", "stock": 100}

    try:
        cache.set("mock", SearchType.MPN, "ABC", payload)

        assert cache.get("mock", SearchType.MPN, "ABC") == payload
    finally:
        cache.close()


def test_cache_persists_after_reopen(workspace_tmp_path) -> None:
    cache_path = workspace_tmp_path / "cache.sqlite3"
    payload = {"status": "success", "stock": 100}

    cache = CacheDB(cache_path)
    cache.set("mock", SearchType.MPN, "ABC", payload)
    cache.close()

    reopened = CacheDB(cache_path)
    try:
        assert reopened.get("mock", SearchType.MPN, "ABC") == payload
    finally:
        reopened.close()


def test_cache_clear_removes_cached_rows(workspace_tmp_path) -> None:
    cache = CacheDB(workspace_tmp_path / "cache.sqlite3")
    payload = {"status": "success", "stock": 100}

    try:
        cache.set("mock", SearchType.MPN, "ABC", payload)
        cache.clear()

        assert cache.get("mock", SearchType.MPN, "ABC") is None
    finally:
        cache.close()


def test_cache_get_many_and_set_many(workspace_tmp_path) -> None:
    cache = CacheDB(workspace_tmp_path / "cache.sqlite3")
    first_payload = {"status": "success", "stock": 100}
    second_payload = {"status": "not_found"}

    try:
        cache.set_many(
            [
                ("mock", SearchType.MPN, "ABC", first_payload),
                ("mock", SearchType.SKU, "C123", second_payload),
            ]
        )

        results = cache.get_many(
            [
                ("mock", SearchType.MPN, "ABC"),
                ("mock", SearchType.SKU, "C123"),
                ("mock", SearchType.MPN, "MISSING"),
            ]
        )

        assert results[("mock", SearchType.MPN, "ABC")] == first_payload
        assert results[("mock", SearchType.SKU, "C123")] == second_payload
        assert ("mock", SearchType.MPN, "MISSING") not in results
    finally:
        cache.close()
