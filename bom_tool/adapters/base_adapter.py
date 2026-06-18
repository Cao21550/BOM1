from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from bom_tool.models import PartResult, QueryStatus, SearchType


class BaseSupplierAdapter(ABC):
    supplier_name: str
    cache_key: str

    def __init__(self, supplier_name: str, cache_key: str | None = None) -> None:
        self.supplier_name = supplier_name
        self.cache_key = cache_key or supplier_name

    @abstractmethod
    async def search_by_mpn(self, mpn: str) -> PartResult:
        raise NotImplementedError

    @abstractmethod
    async def search_by_sku(self, sku: str) -> PartResult:
        raise NotImplementedError

    def failed_result(
        self,
        query: str,
        search_type: SearchType,
        error_message: str,
    ) -> PartResult:
        return PartResult(
            supplier=self.supplier_name,
            query=query,
            search_type=search_type,
            status=QueryStatus.FAILED,
            error_message=error_message,
        )

    def not_found_result(self, query: str, search_type: SearchType) -> PartResult:
        return PartResult(
            supplier=self.supplier_name,
            query=query,
            search_type=search_type,
            status=QueryStatus.NOT_FOUND,
        )

    def standardize_data(
        self,
        query: str,
        search_type: SearchType,
        raw_data: dict[str, Any],
    ) -> PartResult:
        return PartResult(
            supplier=self.supplier_name,
            query=query,
            search_type=search_type,
            status=QueryStatus.SUCCESS,
            mpn=raw_data.get("manufacturer_part_number") or raw_data.get("mpn"),
            sku=raw_data.get("item_code") or raw_data.get("sku"),
            brand=raw_data.get("brand"),
            package=raw_data.get("package"),
            description=raw_data.get("description"),
            stock=raw_data.get("stock_quantity") or raw_data.get("stock"),
            moq=raw_data.get("moq"),
            price_unit=raw_data.get("unit_price") or raw_data.get("price_unit"),
            price_breaks=raw_data.get("price_breaks") or [],
            lead_time=raw_data.get("lead_time"),
            product_url=raw_data.get("product_url"),
            datasheet_url=raw_data.get("datasheet") or raw_data.get("datasheet_url"),
            confidence=raw_data.get("confidence"),
        )
