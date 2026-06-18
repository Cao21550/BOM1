from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class SearchType(StrEnum):
    MPN = "mpn"
    SKU = "sku"
    AUTO = "auto"


class QueryStatus(StrEnum):
    SUCCESS = "success"
    NOT_FOUND = "not_found"
    FAILED = "failed"


@dataclass(slots=True)
class PriceBreak:
    quantity: int
    unit_price: float


@dataclass(slots=True)
class PartResult:
    supplier: str
    query: str
    search_type: SearchType
    status: QueryStatus
    mpn: str | None = None
    sku: str | None = None
    brand: str | None = None
    package: str | None = None
    description: str | None = None
    stock: int | None = None
    moq: int | None = None
    price_unit: float | None = None
    price_breaks: list[PriceBreak] = field(default_factory=list)
    lead_time: str | None = None
    product_url: str | None = None
    datasheet_url: str | None = None
    confidence: float | None = None
    error_message: str | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PartResult:
        fetched_at_value = data.get("fetched_at")
        fetched_at = (
            datetime.fromisoformat(fetched_at_value)
            if fetched_at_value
            else datetime.now(timezone.utc)
        )
        return cls(
            supplier=data["supplier"],
            query=data["query"],
            search_type=SearchType(data["search_type"]),
            status=QueryStatus(data["status"]),
            mpn=data.get("mpn"),
            sku=data.get("sku"),
            brand=data.get("brand"),
            package=data.get("package"),
            description=data.get("description"),
            stock=data.get("stock"),
            moq=data.get("moq"),
            price_unit=data.get("price_unit"),
            price_breaks=[
                PriceBreak(quantity=item["quantity"], unit_price=item["unit_price"])
                for item in data.get("price_breaks", [])
                if isinstance(item, dict)
            ],
            lead_time=data.get("lead_time"),
            product_url=data.get("product_url"),
            datasheet_url=data.get("datasheet_url"),
            confidence=data.get("confidence"),
            error_message=data.get("error_message"),
            fetched_at=fetched_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "supplier": self.supplier,
            "query": self.query,
            "search_type": self.search_type.value,
            "status": self.status.value,
            "mpn": self.mpn,
            "sku": self.sku,
            "brand": self.brand,
            "package": self.package,
            "description": self.description,
            "stock": self.stock,
            "moq": self.moq,
            "price_unit": self.price_unit,
            "price_breaks": [
                {"quantity": price_break.quantity, "unit_price": price_break.unit_price}
                for price_break in self.price_breaks
            ],
            "lead_time": self.lead_time,
            "product_url": self.product_url,
            "datasheet_url": self.datasheet_url,
            "confidence": self.confidence,
            "error_message": self.error_message,
            "fetched_at": self.fetched_at.isoformat(),
        }
