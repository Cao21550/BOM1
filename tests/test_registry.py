import pytest

from bom_tool.adapters.hqchip_adapter import HqchipAdapter
from bom_tool.adapters.registry import create_adapters
from bom_tool.models import QueryStatus


@pytest.mark.asyncio
async def test_search_link_adapters_return_search_urls() -> None:
    hqchip, mouser = create_adapters(["hqchip", "mouser"])

    mouser_result = await mouser.search_by_mpn("STM32F103C8T6")

    assert isinstance(hqchip, HqchipAdapter)
    assert mouser_result.status == QueryStatus.SUCCESS
    assert "mouser.com" in (mouser_result.product_url or "")
