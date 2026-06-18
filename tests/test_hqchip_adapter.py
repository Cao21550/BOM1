from bom_tool.adapters.hqchip_adapter import HqchipAdapter
from bom_tool.models import QueryStatus, SearchType


def test_hqchip_adapter_extracts_self_json_and_normalizes_product() -> None:
    adapter = HqchipAdapter()
    html = """
    <script>
    var searchConfig = {
        SelfJson : {"PD":[{"ModelName":"STM32F103C8T6","goods_name":"STM32F103C8T6","goods_no":"IC0343565","brand_name":"ST","encap":"LQFP-48(7x7)","store_number":"10922","highest_price":6.5,"ModelNameUrl":"https://item.hqchip.com/2500269839.html","Desc":"32位MCU微控制器","DocUrl":"//file.huaqiu.com/test.pdf","min_buynum":1}],"status":0},
        search_id: ""
    };
    </script>
    """

    payload = adapter._extract_self_json(html)
    item = adapter._pick_best_item("STM32F103C8T6", payload["PD"])
    assert item is not None

    result = adapter.standardize_data(
        "STM32F103C8T6",
        SearchType.MPN,
        adapter._normalize_item(item, "STM32F103C8T6"),
    )

    assert result.status == QueryStatus.SUCCESS
    assert result.mpn == "STM32F103C8T6"
    assert result.sku == "IC0343565"
    assert result.brand == "ST"
    assert result.package == "LQFP-48(7x7)"
    assert result.stock == 10922
    assert result.price_unit == 6.5
    assert result.datasheet_url == "https://file.huaqiu.com/test.pdf"
