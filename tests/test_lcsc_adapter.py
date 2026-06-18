from bom_tool.adapters.lcsc_adapter import LcscAdapter
from bom_tool.models import QueryStatus, SearchType


def test_lcsc_adapter_normalizes_nested_payload() -> None:
    adapter = LcscAdapter()
    payload = {
        "data": {
            "productList": [
                {
                    "productModel": "STM32F103C8T6",
                    "productCode": "C8734",
                    "brandName": "ST",
                    "encapStandard": "LQFP-48",
                    "productIntroEn": "MCU",
                    "stockNumber": "1200",
                    "productPrice": "8.5",
                    "pdfUrl": "https://example.com/datasheet.pdf",
                }
            ]
        }
    }

    item = adapter._pick_first_item(payload)
    assert item is not None

    result = adapter.standardize_data(
        "STM32F103C8T6", SearchType.MPN, adapter._normalize_item(item)
    )

    assert result.status == QueryStatus.SUCCESS
    assert result.supplier == "lcsc"
    assert result.mpn == "STM32F103C8T6"
    assert result.sku == "C8734"
    assert result.stock == 1200
    assert result.price_unit == 8.5


def test_lcsc_adapter_uses_versioned_cache_key() -> None:
    adapter = LcscAdapter()

    assert adapter.supplier_name == "lcsc"
    assert adapter.cache_key == "lcsc_html_v4"


async def test_lcsc_adapter_defaults_to_html_payload() -> None:
    adapter = LcscAdapter()
    calls: list[str] = []

    async def fake_api_payload(query: str):
        calls.append(f"api:{query}")
        raise AssertionError("API should not be called by default")

    async def fake_html_payload(query: str):
        calls.append(f"html:{query}")
        return {
            "data": {
                "productList": [
                    {
                        "productModel": "STM32F103C8T6",
                        "productCode": "C8734",
                        "stockNumber": "1200",
                    }
                ]
            }
        }

    adapter._fetch_api_payload = fake_api_payload
    adapter._fetch_html_payload = fake_html_payload

    result = await adapter.search_by_mpn("STM32F103C8T6")

    assert result.status == QueryStatus.SUCCESS
    assert result.sku == "C8734"
    assert calls == ["html:STM32F103C8T6"]


async def test_lcsc_adapter_falls_back_to_html_when_api_fails() -> None:
    adapter = LcscAdapter(prefer_api=True)
    calls: list[str] = []

    async def fake_api_payload(query: str):
        calls.append(f"api:{query}")
        raise RuntimeError("api unavailable")

    async def fake_html_payload(query: str):
        calls.append(f"html:{query}")
        return {
            "props": {
                "pageProps": {
                    "soData": {
                        "searchResult": {
                            "productRecordList": [
                                {
                                    "productVO": {
                                        "productModel": "STM32F103C8T6",
                                        "productCode": "C8734",
                                    }
                                }
                            ]
                        }
                    }
                }
            }
        }

    adapter._fetch_api_payload = fake_api_payload
    adapter._fetch_html_payload = fake_html_payload

    result = await adapter.search_by_mpn("STM32F103C8T6")

    assert result.status == QueryStatus.SUCCESS
    assert result.sku == "C8734"
    assert calls == ["api:STM32F103C8T6", "html:STM32F103C8T6"]


def test_lcsc_adapter_finds_deep_records_and_price_breaks() -> None:
    adapter = LcscAdapter()
    payload = {
        "result": {
            "productSearchResultVO": {
                "records": [
                    {
                        "productName": "LM358DR",
                        "productCode": "C7950",
                        "manufacturerName": "TI",
                        "productPackage": "SOIC-8",
                        "stockQuantity": "10,000",
                        "productPriceList": [
                            {"startQuantity": "1", "unitPrice": "0.45"},
                            {"startQuantity": "100", "unitPrice": "0.38"},
                        ],
                        "dataManualUrl": "//datasheet.example/lm358.pdf",
                    }
                ]
            }
        }
    }

    item = adapter._pick_first_item(payload)
    assert item is not None

    result = adapter.standardize_data("LM358DR", SearchType.MPN, adapter._normalize_item(item))

    assert result.status == QueryStatus.SUCCESS
    assert result.mpn == "LM358DR"
    assert result.sku == "C7950"
    assert result.brand == "TI"
    assert result.package == "SOIC-8"
    assert result.stock == 10000
    assert result.price_unit == 0.45
    assert result.price_breaks[1].quantity == 100
    assert result.datasheet_url == "https://datasheet.example/lm358.pdf"
    assert result.product_url == "https://so.szlcsc.com/global.html?k=C7950"


def test_lcsc_adapter_normalizes_current_lcsc_search_fields() -> None:
    adapter = LcscAdapter()
    item = {
        "productCode": "C8734",
        "productModel": "STM32F103C8T6",
        "productGradePlateName": "ST(意法半导体)",
        "encapsulationModel": "LQFP-48(7x7)",
        "stockNumber": 58393,
        "validStockNumber": 58393,
        "minBuyNumber": 1,
        "productPriceList": [
            {
                "startPurchasedNumber": 1,
                "endPurchasedNumber": 9,
                "productPrice": 8.85,
                "thePrice": 8.85,
            },
            {
                "startPurchasedNumber": 10,
                "endPurchasedNumber": 29,
                "productPrice": 7.69,
                "thePrice": 7.69,
            },
        ],
    }

    result = adapter.standardize_data(
        "STM32F103C8T6", SearchType.MPN, adapter._normalize_item(item)
    )

    assert result.brand == "ST(意法半导体)"
    assert result.stock == 58393
    assert result.moq == 1
    assert result.price_unit == 8.85
    assert result.price_breaks[0].quantity == 1
    assert result.price_breaks[1].unit_price == 7.69


def test_lcsc_adapter_picks_exact_item_from_next_data_payload() -> None:
    adapter = LcscAdapter()
    payload = {
        "props": {
            "pageProps": {
                "soData": {
                    "searchResult": {
                        "productRecordList": [
                            {
                                "productVO": {
                                    "productCode": "C111",
                                    "productModel": "STM32F103C8T6TR",
                                    "encapsulationModel": "LQFP-48",
                                }
                            },
                            {
                                "productVO": {
                                    "productCode": "C8734",
                                    "productModel": "STM32F103C8T6",
                                    "productNameEn": "ARM MCU",
                                    "encapsulationModel": "LQFP-48",
                                    "stockNumber": "1200",
                                }
                            },
                        ]
                    }
                }
            }
        }
    }

    item = adapter._pick_best_item("STM32F103C8T6", payload)
    assert item is not None

    result = adapter.standardize_data(
        "STM32F103C8T6", SearchType.MPN, adapter._normalize_item(item)
    )

    assert result.sku == "C8734"
    assert result.mpn == "STM32F103C8T6"
    assert result.description == "ARM MCU"


async def test_lcsc_adapter_skips_chinese_keywords_without_request() -> None:
    adapter = LcscAdapter()
    result = await adapter.search_by_mpn("芯片")

    assert result.status == QueryStatus.NOT_FOUND
    assert "Skipped" in (result.error_message or "")
