from __future__ import annotations

import asyncio
import json
import random
import re
from typing import Any
from urllib.parse import quote_plus

import httpx

from bom_tool.adapters.base_adapter import BaseSupplierAdapter
from bom_tool.models import PartResult, PriceBreak, QueryStatus, SearchType
from bom_tool.utils import AsyncRateLimiter

# Maximum seconds to wait for a single exponential-backoff step
_RATE_LIMIT_RETRY_CAP = 120.0
_SEARCH_URL_TEMPLATE = "https://so.szlcsc.com/global.html?k={keyword}"


class LcscAdapter(BaseSupplierAdapter):
    CONTAINER_KEYS = (
        "data",
        "result",
        "page",
        "productSearchResultVO",
        "productSearchResult",
        "searchResult",
    )
    LIST_KEYS = (
        "productList",
        "productListVO",
        "records",
        "list",
        "items",
        "content",
        "rows",
    )
    PRODUCT_FIELD_KEYS = {
        "productModel",
        "productCode",
        "productNo",
        "lcscCode",
        "productName",
        "brandName",
        "stockNumber",
    }

    def __init__(
        self,
        timeout: float = 15.0,
        http2: bool = False,
        min_interval: float = 1.2,
        max_retries: int = 2,
        prefer_api: bool = False,
        use_api_fallback: bool = False,
    ) -> None:
        super().__init__("lcsc", cache_key="lcsc_html_v4")
        self.timeout = timeout
        self.http2 = http2
        self.max_retries = max(1, max_retries)
        self.prefer_api = prefer_api
        self.use_api_fallback = use_api_fallback
        self._rate_limiter = AsyncRateLimiter(min_interval=min_interval)
        self._request_lock = asyncio.Lock()
        self._api_client: httpx.AsyncClient | None = None
        self.search_url = "https://so.szlcsc.com/global.html"
        self.api_search_url = "https://wmsc.lcsc.com/ftps/wm/search/global"

    async def search_by_mpn(self, mpn: str) -> PartResult:
        return await self._search(mpn, SearchType.MPN)

    async def search_by_sku(self, sku: str) -> PartResult:
        return await self._search(sku, SearchType.SKU)

    async def _search(self, query: str, search_type: SearchType) -> PartResult:
        if not query:
            return self.not_found_result(query, search_type)
        if self._contains_chinese(query):
            return PartResult(
                supplier=self.supplier_name,
                query=query,
                search_type=search_type,
                status=QueryStatus.NOT_FOUND,
                error_message="Skipped non-MPN keyword containing Chinese characters",
            )

        payload: dict[str, Any] | None = None
        failure_details: list[str] = []

        if self.prefer_api:
            try:
                payload = await self._fetch_api_payload(query)
            except Exception as exc:
                failure_details.append(f"api: {type(exc).__name__}: {exc!r}")
        if payload is None:
            try:
                payload = await self._fetch_html_payload(query)
            except Exception as exc:
                failure_details.append(f"html: {type(exc).__name__}: {exc!r}")
        if payload is None and not self.prefer_api and self.use_api_fallback:
            try:
                payload = await self._fetch_api_payload(query)
            except Exception as exc:
                failure_details.append(f"api: {type(exc).__name__}: {exc!r}")

        if payload is None:
            return self._degraded_result(query, search_type, "; ".join(failure_details))

        item = self._pick_best_item(query, payload)
        if not item:
            return self._degraded_result(query, search_type, "No exact product item found")

        return self.standardize_data(query, search_type, self._normalize_item(item))

    # ── HTTP helpers ──────────────────────────────────────────────

    async def _fetch_html_payload(self, query: str) -> dict[str, Any]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.szlcsc.com/",
        }
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            if attempt > 1:
                await asyncio.sleep(attempt * 4 + random.uniform(0.5, 1.5))
            try:
                return await self._do_html_request(query, headers)
            except RuntimeError as exc:
                if "HTTP 429" in str(exc):
                    # Dedicated exponential-backoff series for rate limiting
                    recovered = await self._retry_after_rate_limit(query, headers)
                    if recovered is not None:
                        return recovered
                last_error = exc
            except Exception as exc:
                last_error = exc

        raise RuntimeError(f"html request failed after retries: {last_error!r}")

    async def _do_html_request(self, query: str, headers: dict[str, str]) -> dict[str, Any]:
        """Single HTML search request with a short-lived client."""
        await self._rate_limiter.wait()
        async with httpx.AsyncClient(
            timeout=self.timeout,
            headers=headers,
            http2=self.http2,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        ) as client:
            response = await client.get(self.search_url, params={"k": query})

        if response.status_code in {403, 429, 500, 502, 503, 504}:
            raise RuntimeError(f"html HTTP {response.status_code}")

        response.raise_for_status()
        match = re.search(
            r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            response.text,
            re.S,
        )
        if not match:
            title_match = re.search(
                r"<title[^>]*>(.*?)</title>",
                response.text,
                re.I | re.S,
            )
            title = title_match.group(1).strip() if title_match else ""
            if self._is_login_or_rate_limit_page(title, response.text):
                raise RuntimeError(f"html login/rate-limit page; title={title[:120]}")
            raise ValueError(f"html page has no __NEXT_DATA__; title={title[:120]}")
        return json.loads(match.group(1))

    async def _retry_after_rate_limit(
        self,
        query: str,
        headers: dict[str, str],
    ) -> dict[str, Any] | None:
        """Exponential backoff retry series after HTTP 429.

        Returns the payload on first success, or None if all sub-retries fail.
        """
        for step in range(2):
            wait = min(_RATE_LIMIT_RETRY_CAP, 4 * (2**step)) + random.uniform(0.5, 1.5)
            await asyncio.sleep(wait)
            try:
                return await self._do_html_request(query, headers)
            except RuntimeError as exc:
                if "HTTP 429" not in str(exc):
                    raise
        return None  # all sub-retries exhausted

    def _degraded_result(
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
            mpn=query if search_type != SearchType.SKU else None,
            sku=query if search_type == SearchType.SKU else None,
            product_url=_SEARCH_URL_TEMPLATE.format(keyword=quote_plus(query)),
            error_message=error_message,
            confidence=0.0,
        )

    async def _fetch_api_payload(self, query: str) -> dict[str, Any]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.szlcsc.com/",
        }
        params = {
            "keyword": query,
            "pageNum": 1,
            "pageSize": 30,
        }
        client = self._get_api_client(headers)
        async with self._request_lock:
            await self._rate_limiter.wait()
            response = await client.get(self.api_search_url, params=params)
        response.raise_for_status()
        return response.json()

    # ── Item selection & normalisation ────────────────────────────

    def _pick_best_item(self, query: str, payload: Any) -> dict[str, Any] | None:
        products = self._extract_product_items(payload)
        if not products:
            return None

        query_norm = self._normalize_match_text(query)
        exact: list[dict[str, Any]] = []
        fuzzy: list[dict[str, Any]] = []
        for product in products:
            candidates = [
                self._first(product, "productCode", "productNo", "sku", "lcscCode", "code"),
                self._first(
                    product,
                    "productModel",
                    "productCodeInSupplier",
                    "model",
                    "mpn",
                    "manufacturerPartNumber",
                    "productName",
                    "productNameEn",
                ),
            ]
            candidate_norms = [self._normalize_match_text(value) for value in candidates if value]
            if query_norm and query_norm in candidate_norms:
                exact.append(product)
            elif query_norm and any(query_norm in value for value in candidate_norms):
                fuzzy.append(product)

        return (exact or fuzzy or products)[0]

    def _extract_product_items(self, payload: Any) -> list[dict[str, Any]]:
        products: list[dict[str, Any]] = []
        self._collect_product_items(payload, products)
        return products

    def _collect_product_items(self, node: Any, out: list[dict[str, Any]]) -> None:
        if isinstance(node, dict):
            product = node.get("productVO")
            if isinstance(product, dict) and self._looks_like_product(product):
                out.append(product)
            elif self._looks_like_product(node):
                out.append(node)

            for value in node.values():
                self._collect_product_items(value, out)
            return
        if isinstance(node, list):
            for item in node:
                self._collect_product_items(item, out)

    def _pick_first_item(self, payload: Any) -> dict[str, Any] | None:
        if isinstance(payload, list):
            return self._first_product_from_list(payload)
        if not isinstance(payload, dict):
            return None

        if self._looks_like_product(payload):
            return payload

        for key in self.LIST_KEYS:
            item = self._pick_first_item(payload.get(key))
            if item:
                return item

        for key in self.CONTAINER_KEYS:
            item = self._pick_first_item(payload.get(key))
            if item:
                return item

        for value in payload.values():
            item = self._pick_first_item(value)
            if item:
                return item

        return None

    def _normalize_item(self, item: dict[str, Any]) -> dict[str, Any]:
        sku = self._first(item, "productCode", "productNo", "sku", "lcscCode", "code")
        product_url = self._normalize_url(self._first(item, "productUrl", "url", "productLink"))
        if not product_url and sku:
            product_url = f"https://so.szlcsc.com/global.html?k={quote_plus(str(sku))}"

        return {
            "manufacturer_part_number": self._first(
                item,
                "productModel",
                "productCodeInSupplier",
                "manufacturerPartNumber",
                "productName",
                "productNameEn",
                "mpn",
                "model",
                "catalogName",
            ),
            "item_code": sku,
            "brand": self._first(
                item,
                "brandName",
                "productGradePlateName",
                "productGradePlateNameWithoutHighlight",
                "brand",
                "manufacturer",
                "manufacturerName",
            ),
            "package": self._first(
                item,
                "encapStandard",
                "encapsulationModel",
                "package",
                "encapsulation",
                "productPackage",
            ),
            "description": self._first(
                item,
                "productNameEn",
                "productName",
                "productIntroEn",
                "productIntro",
                "productDesc",
                "description",
                "productDescription",
            ),
            "stock": self._to_int(
                self._first(
                    item,
                    "stockNumber",
                    "validStockNumber",
                    "stock",
                    "stockQuantity",
                    "totalStock",
                    "availableStock",
                )
            ),
            "moq": self._to_int(
                self._first(
                    item,
                    "minBuyNumber",
                    "minPacketUnit",
                    "minimumBuy",
                    "minimumOrderQuantity",
                    "moq",
                    "minimum",
                )
            ),
            "price_unit": self._normalize_unit_price(item),
            "price_breaks": self._normalize_price_breaks(item),
            "lead_time": self._first(item, "leadTime", "deliveryTime", "delivery"),
            "product_url": product_url,
            "datasheet_url": self._normalize_url(
                self._first(item, "pdfUrl", "datasheet", "datasheetUrl", "dataManualUrl")
            ),
            "confidence": 0.8,
        }

    def _first_product_from_list(self, values: list[Any]) -> dict[str, Any] | None:
        for value in values:
            if isinstance(value, dict) and self._looks_like_product(value):
                return value
        for value in values:
            item = self._pick_first_item(value)
            if item:
                return item
        return None

    def _looks_like_product(self, item: dict[str, Any]) -> bool:
        return any(key in item for key in self.PRODUCT_FIELD_KEYS)

    # ── Primitive helpers ─────────────────────────────────────────

    def _first(self, item: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            value = item.get(key)
            if value not in (None, ""):
                return value
        return None

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

    def _normalize_unit_price(self, item: dict[str, Any]) -> float | None:
        direct_price = self._to_float(
            self._first(item, "productPrice", "price", "unitPrice", "discountPrice", "minPrice")
        )
        if direct_price is not None:
            return direct_price

        price_breaks = self._normalize_price_breaks(item)
        if price_breaks:
            return price_breaks[0].unit_price
        return None

    def _normalize_price_breaks(self, item: dict[str, Any]) -> list[PriceBreak]:
        raw_prices = self._first(
            item,
            "priceBreaks",
            "productPriceList",
            "ladderPriceList",
            "productHkDollerPriceList",
            "prices",
            "priceList",
        )
        if not isinstance(raw_prices, list):
            return []

        price_breaks: list[PriceBreak] = []
        for raw_price in raw_prices:
            if not isinstance(raw_price, dict):
                continue
            quantity = self._to_int(
                self._first(
                    raw_price,
                    "quantity",
                    "qty",
                    "startQuantity",
                    "startQty",
                    "startPurchasedNumber",
                    "spNumber",
                    "num",
                )
            )
            unit_price = self._to_float(
                self._first(
                    raw_price,
                    "unitPrice",
                    "price",
                    "productPrice",
                    "thePrice",
                    "discountPrice",
                )
            )
            if quantity is not None and unit_price is not None:
                price_breaks.append(PriceBreak(quantity=quantity, unit_price=unit_price))

        return price_breaks

    def _normalize_url(self, value: Any) -> str | None:
        if value in (None, ""):
            return None
        url = str(value).strip()
        if url.startswith("//"):
            return f"https:{url}"
        if url.startswith("/"):
            return f"https://www.szlcsc.com{url}"
        return url

    def _normalize_match_text(self, value: Any) -> str:
        return "".join(
            char.lower()
            for char in str(value).strip()
            if char not in {" ", "-", "_", "/", "\\", "."}
        )

    def _contains_chinese(self, value: Any) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]", str(value)))

    def _is_login_or_rate_limit_page(self, title: str, body: str) -> bool:
        text = f"{title}\n{body[:1000]}".lower()
        return any(marker in text for marker in ("login", "rate limit"))

    # ── Client lifecycle ──────────────────────────────────────────

    def _get_api_client(self, headers: dict[str, str]) -> httpx.AsyncClient:
        if self._api_client is None:
            self._api_client = httpx.AsyncClient(
                timeout=self.timeout,
                headers=headers,
                http2=self.http2,
                limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
            )
        return self._api_client

    async def close(self) -> None:
        if self._api_client is not None:
            await self._api_client.aclose()
        self._api_client = None
