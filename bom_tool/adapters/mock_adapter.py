from __future__ import annotations

import asyncio

from bom_tool.adapters.base_adapter import BaseSupplierAdapter
from bom_tool.models import PartResult, QueryStatus, SearchType


class MockSupplierAdapter(BaseSupplierAdapter):
    def __init__(self) -> None:
        super().__init__("mock")

    async def search_by_mpn(self, mpn: str) -> PartResult:
        await asyncio.sleep(0)
        if not mpn:
            return self.not_found_result(mpn, SearchType.MPN)
        return PartResult(
            supplier=self.supplier_name,
            query=mpn,
            search_type=SearchType.MPN,
            status=QueryStatus.SUCCESS,
            mpn=mpn,
            sku=f"MOCK-{mpn}",
            brand="MockBrand",
            description="Mock supplier result for development",
            stock=1000,
            price_unit=0.1,
            product_url="https://example.com/mock-part",
            confidence=1.0,
        )

    async def search_by_sku(self, sku: str) -> PartResult:
        await asyncio.sleep(0)
        if not sku:
            return self.not_found_result(sku, SearchType.SKU)
        return PartResult(
            supplier=self.supplier_name,
            query=sku,
            search_type=SearchType.SKU,
            status=QueryStatus.SUCCESS,
            mpn=sku.replace("MOCK-", ""),
            sku=sku,
            brand="MockBrand",
            description="Mock supplier result for development",
            stock=1000,
            price_unit=0.1,
            product_url="https://example.com/mock-part",
            confidence=1.0,
        )
