import pytest

from bom_tool.adapters.mock_adapter import MockSupplierAdapter
from bom_tool.models import QueryStatus


@pytest.mark.asyncio
async def test_mock_adapter_returns_standard_result() -> None:
    adapter = MockSupplierAdapter()
    result = await adapter.search_by_mpn("STM32F103C8T6")

    assert result.status == QueryStatus.SUCCESS
    assert result.supplier == "mock"
    assert result.mpn == "STM32F103C8T6"
