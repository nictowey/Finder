from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EbayOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    marketplace_id: str = Field(default="EBAY_US", pattern=r"^EBAY_[A-Z]{2,10}$")
    category_ids: list[str] = Field(default_factory=list, max_length=1)
    buying_options: list[Literal["FIXED_PRICE", "AUCTION", "BEST_OFFER"]] = Field(
        default_factory=lambda: ["FIXED_PRICE", "AUCTION"], min_length=1
    )
    sort: Literal["newlyListed", "endingSoonest", "price", "-price", "bestMatch"] = "newlyListed"
    page_size: int = Field(default=50, ge=1, le=200)
    max_pages: int = Field(default=2, ge=1, le=10000)
    fetch_details: bool = True
    aspect_filter: str | None = None

    @model_validator(mode="after")
    def check_bounds(self) -> "EbayOptions":
        if self.page_size * self.max_pages > 10000:
            raise ValueError("eBay search exposes at most 10,000 results per query")
        if any(not category.isdigit() for category in self.category_ids):
            raise ValueError("Category IDs must be numeric strings")
        if self.aspect_filter and not self.category_ids:
            raise ValueError("An aspect filter requires a category ID")
        return self
