from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from urllib.parse import quote_plus

from curl_cffi.requests import AsyncSession

from bom_tool.adapters.base_adapter import BaseSupplierAdapter
from bom_tool.models import PartResult, SearchType
from bom_tool.utils import AsyncRateLimiter

_HTML_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en-US,en;q=0.5",
    "Referer": "https://www.hqchip.com/",
}
_API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.hqchip.com/",
}


class HqchipAdapter(BaseSupplierAdapter):
    def __init__(self, timeout: float = 8.0, min_interval: float = 1.5) -> None:
        super().__init__("hqchip", cache_key="hqchip_html_v1")
        self.timeout = timeout
        self.min_interval = min_interval
        self.search_url_template = "https://www.hqchip.com/search/{keyword}.html"
        self.api_search_url = "https://search.hqchip.com/search/v5/goods/detail"
        self._api_client: AsyncSession | None = None
        self._html_client: AsyncSession | None = None
        self._request_lock = asyncio.Lock()
        self._rate_limiter = AsyncRateLimiter(min_interval=min_interval)

    async def search_by_mpn(self, mpn: str) -> PartResult:
        return await self._search(mpn, SearchType.MPN)

    async def search_by_sku(self, sku: str) -> PartResult:
        return await self._search(sku, SearchType.SKU)

    async def _search(self, query: str, search_type: SearchType) -> PartResult:
        keyword = self._prepare_query(query)
        if not keyword:
            return self.not_found_result(query, search_type)

        item: dict[str, Any] | None = None
        failure_details: list[str] = []

        # HTML first (self-operated data is more accurate)
        try:
            payload = await self._fetch_html_payload(keyword)
            html_items = payload.get("PD") or []
            item = self._pick_best_item(keyword, html_items)
        except Exception as exc:
            failure_details.append(f"html: {type(exc).__name__}: {exc!r}")

        # API fallback when HTML returns nothing
        if not item:
            try:
                api_result = await self._fetch_api_payload(keyword)
                if api_result:
                    item = self._api_item_to_html_item(api_result)
            except Exception as exc:
                failure_details.append(f"api: {type(exc).__name__}: {exc!r}")

        if not item:
            message = "; ".join(failure_details) if failure_details else "No product found"
            return self.not_found_result(keyword, search_type)

        return self.standardize_data(keyword, search_type, self._normalize_item(item, keyword))

    # ── API (primary) ──────────────────────────────────────────────

    async def _fetch_api_payload(self, keyword: str) -> dict[str, Any] | None:
        """Query the HQChip goods detail API."""
        async with self._request_lock:
            await self._rate_limiter.wait()
            client = self._get_api_client()
            response = await client.get(
                self.api_search_url, params={"keyword": keyword}
            )
            response.raise_for_status()
        data: dict[str, Any] = response.json()
        if data.get("retCode") == 0 and data.get("result"):
            return data["result"]
        return None

    def _api_item_to_html_item(self, api_result: dict[str, Any]) -> dict[str, Any]:
        """Map API response fields to the field names expected by _normalize_item."""
        return {
            "ModelName": api_result.get("goodsName"),
            "goods_name": api_result.get("goodsName"),
            "brand_name": api_result.get("brandName"),
            "encap": api_result.get("packageType"),
            "Desc": api_result.get("goodsDesc"),
            "goods_desc": api_result.get("goodsDesc"),
            "store_number": self._to_int(api_result.get("stockQuantity")),
            "min_buynum": self._to_int(api_result.get("moq")),
            "highest_price": self._to_float(api_result.get("price")),
            "goods_no": str(api_result.get("goodsId", "")),
            "DocUrl": api_result.get("datasheetUrl"),
            "ModelNameUrl": api_result.get("goodsUrl"),
        }

    # ── HTML (fallback) ────────────────────────────────────────────

    async def _fetch_html_payload(self, keyword: str) -> dict[str, Any]:
        url = self.search_url_template.format(keyword=quote_plus(keyword))
        async with self._request_lock:
            await self._rate_limiter.wait()
            client = self._get_html_client()
            response = await client.get(url)
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

    # ── Item selection & normalisation ─────────────────────────────

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

    # ── Primitives ─────────────────────────────────────────────────

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

    _BRACKET_ANNOTATION = re.compile(r"[（(][^）)]*[）)]")

    @staticmethod
    def _prepare_query(query: str) -> str:
        """Strip whitespace and Chinese bracket annotations like （云汉）."""
        cleaned = query.strip()
        if not cleaned:
            return ""
        cleaned = HqchipAdapter._BRACKET_ANNOTATION.sub("", cleaned).strip()
        return cleaned

    # ── Client lifecycle ───────────────────────────────────────────

    def _get_api_client(self) -> AsyncSession:
        if self._api_client is None:
            self._api_client = AsyncSession(
                impersonate="chrome124",
                timeout=self.timeout,
                headers=_API_HEADERS,
            )
        return self._api_client

    def _get_html_client(self) -> AsyncSession:
        if self._html_client is None:
            self._html_client = AsyncSession(
                impersonate="chrome124",
                timeout=self.timeout,
                headers=_HTML_HEADERS,
            )
        return self._html_client

    async def close(self) -> None:
        if self._api_client is not None:
            await self._api_client.close()
            self._api_client = None
        if self._html_client is not None:
            await self._html_client.close()
            self._html_client = None
