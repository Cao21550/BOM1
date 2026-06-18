from bom_tool.ui.main_window import select_preferred_search_header


def test_select_preferred_search_header_prefers_supplier_model_with_suffix() -> None:
    headers = [
        "序号",
        "元件名称",
        "值",
        "元件描述",
        "供应商完整型号_b",
        "报价型号",
    ]

    assert select_preferred_search_header(headers) == "供应商完整型号_b"


def test_select_preferred_search_header_uses_quoted_model_before_name() -> None:
    headers = ["序号", "元件名称", "报价型号", "备注"]

    assert select_preferred_search_header(headers) == "报价型号"
