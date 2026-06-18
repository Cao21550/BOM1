from __future__ import annotations

from urllib.parse import quote_plus

from bom_tool.adapters.base_adapter import BaseSupplierAdapter
from bom_tool.models import PartResult, QueryStatus, SearchType


class SearchLinkAdapter(BaseSupplierAdapter):
    def __init__(self, supplier_name: str, search_url_template: str, display_name: str) -> None:
        super().__init__(supplier_name)
        self.search_url_template = search_url_template
        self.display_name = display_name

    async def search_by_mpn(self, mpn: str) -> PartResult:
        return self._search_link_result(mpn, SearchType.MPN)

    async def search_by_sku(self, sku: str) -> PartResult:
        return self._search_link_result(sku, SearchType.SKU)

    def _search_link_result(self, query: str, search_type: SearchType) -> PartResult:
        keyword = query.strip()
        if not keyword:
            return self.not_found_result(query, search_type)

        return PartResult(
            supplier=self.supplier_name,
            query=keyword,
            search_type=search_type,
            status=QueryStatus.SUCCESS,
            mpn=keyword if search_type != SearchType.SKU else None,
            sku=keyword if search_type == SearchType.SKU else None,
            description=f"{self.display_name}暂未接入实时库存接口，请打开商品链接查看搜索结果。",
            product_url=self.search_url_template.format(keyword=quote_plus(keyword)),
            confidence=0.1,
        )
