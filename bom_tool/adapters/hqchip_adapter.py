from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import quote_plus

import httpx

from bom_tool.adapters.base_adapter import BaseSupplierAdapter
from bom_tool.models import PartResult, SearchType
from bom_tool.utils import AsyncRateLimiter


class HqchipAdapter(BaseSupplierAdapter):
    def __init__(self, timeout: float = 15.0, min_interval: float = 2.0) -> None:
        super().__init__("hqchip", cache_key="hqchip_html_v1")
        self.timeout = timeout
        self.min_interval = min_interval
        self.search_url_template = "https://www.hqchip.com/search/{keyword}.html"
        self._client: httpx.AsyncClient | None = None
        self._request_lock = asyncio.Lock()
        self._rate_limiter = AsyncRateLimiter(min_interval=min_interval)

    async def search_by_mpn(self, mpn: str) -> PartResult:
        return await self._search(mpn, SearchType.MPN)

    async def search_by_sku(self, sku: str) -> PartResult:
        return await self._search(sku, SearchType.SKU)

    async def _search(self, query: str, search_type: SearchType) -> PartResult:
        keyword = query.strip()
        if not keyword:
            return self.not_found_result(query, search_type)

        try:
            payload = await self._fetch_payload(keyword)
        except Exception as exc:
            message = f"HQChip request failed: {type(exc).__name__}: {exc!r}"
            return self.failed_result(keyword, search_type, message)

        item = self._pick_best_item(keyword, payload.get("PD") or [])
        if not item:
            return self.not_found_result(keyword, search_type)

        return self.standardize_data(keyword, search_type, self._normalize_item(item, keyword))

    async def _fetch_payload(self, keyword: str) -> dict[str, Any]:
        url = self.search_url_template.format(keyword=quote_plus(keyword))
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.hqchip.com/",
        }
        async with self._request_lock:
            await self._rate_limiter.wait()
            response = await self._get_client(headers).get(url)
            response.raise_for_status()

        return self._extract_self_json(response.text)

    def _extract_self_json(self, html: str) -> dict[str, Any]:
        marker = "SelfJson : "
        start = html.find(marker)
        if start < 0:
            raise ValueError("SelfJson not found in HQChip search page")
        start += len(marker)
        if start >= len(html) or html[start] != "{":
            raise ValueError("SelfJson object start not found")

        end = self._find_json_object_end(html, start)
        if end is None:
            raise ValueError("SelfJson object end not found")
        return json.loads(html[start:end])

    def _find_json_object_end(self, text: str, start: int) -> int | None:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return index + 1
        return None

    def _pick_best_item(self, query: str, items: list[Any]) -> dict[str, Any] | None:
        products = [item for item in items if isinstance(item, dict)]
        if not products:
            return None

        query_norm = self._normalize_match_text(query)
        exact: list[dict[str, Any]] = []
        fuzzy: list[dict[str, Any]] = []
        for product in products:
            candidates = [
                product.get("ModelName"),
                product.get("goods_name"),
                product.get("origin_goods_name"),
                product.get("urlName"),
                product.get("goods_no"),
            ]
            candidate_norms = [self._normalize_match_text(value) for value in candidates if value]
            if query_norm and query_norm in candidate_norms:
                exact.append(product)
            elif query_norm and any(query_norm in value for value in candidate_norms):
                fuzzy.append(product)

        return (exact or fuzzy or products)[0]

    def _normalize_item(self, item: dict[str, Any], keyword: str) -> dict[str, Any]:
        product_url = self._normalize_url(
            item.get("ModelNameUrl")
        ) or self.search_url_template.format(keyword=quote_plus(keyword))
        return {
            "manufacturer_part_number": item.get("ModelName") or item.get("goods_name"),
            "item_code": item.get("goods_no") or item.get("goodsNo") or item.get("erp_goods_sn"),
            "brand": item.get("brand_name") or item.get("BrandName") or item.get("brand_cn"),
            "package": item.get("encap") or item.get("package"),
            "description": item.get("Desc") or item.get("goods_desc") or item.get("goods_sn"),
            "stock": self._to_int(item.get("store_number") or item.get("spot_number")),
            "moq": self._to_int(item.get("min_buynum") or item.get("SPQ") or item.get("spq")),
            "price_unit": self._to_float(item.get("highest_price")),
            "product_url": product_url,
            "datasheet_url": self._normalize_url(item.get("DocUrl")),
            "confidence": 0.85,
        }

    def _normalize_url(self, value: Any) -> str | None:
        if value in (None, ""):
            return None
        url = str(value).strip()
        if url.startswith("//"):
            return f"https:{url}"
        if url.startswith("/"):
            return f"https://www.hqchip.com{url}"
        return url

    def _to_int(self, value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(float(str(value).replace(",", "")))
        except ValueError:
            return None

    def _to_float(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(str(value).replace(",", ""))
        except ValueError:
            return None

    def _normalize_match_text(self, value: Any) -> str:
        return "".join(
            char.lower()
            for char in str(value).strip()
            if char not in {" ", "-", "_", "/", "\\", "."}
        )

    def _get_client(self, headers: dict[str, str]) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers=headers,
                follow_redirects=True,
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
