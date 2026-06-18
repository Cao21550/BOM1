import pytest

from bom_tool.adapters.hqchip_adapter import HqchipAdapter
from bom_tool.adapters.registry import create_adapters
from bom_tool.models import QueryStatus


@pytest.mark.asyncio
async def test_create_adapters_returns_hqchip() -> None:
    hqchip, = create_adapters(["hqchip"])

    assert isinstance(hqchip, HqchipAdapter)
