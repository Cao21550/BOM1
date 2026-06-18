from __future__ import annotations

from bom_tool.adapters.base_adapter import BaseSupplierAdapter

SUPPLIER_CHOICES = {
    "lcsc": "立创商城",
    "hqchip": "华秋商城",
}


def create_adapters(names: list[str], lcsc_interval: float = 1.2) -> list[BaseSupplierAdapter]:
    adapters: list[BaseSupplierAdapter] = []
    for name in names:
        normalized_name = name.strip().lower()
        if normalized_name == "mock":
            from bom_tool.adapters.mock_adapter import MockSupplierAdapter

            adapters.append(MockSupplierAdapter())
        elif normalized_name in {"lcsc", "立创", "立创商城"}:
            from bom_tool.adapters.lcsc_adapter import LcscAdapter

            adapters.append(LcscAdapter(min_interval=lcsc_interval))
        elif normalized_name in {"hqchip", "华秋", "华秋商城"}:
            from bom_tool.adapters.hqchip_adapter import HqchipAdapter

            adapters.append(HqchipAdapter())
        else:
            raise ValueError(f"Unknown supplier adapter: {name}")
    return adapters
