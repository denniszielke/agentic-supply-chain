from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


class GeoLocation(BaseModel):
    lat: float
    lon: float


class Address(BaseModel):
    street: str
    city: str
    postal_code: str
    country: str = "DE"
    geo: Optional[GeoLocation] = None


class OpeningHour(BaseModel):
    day: str
    open: str
    close: str


class Contact(BaseModel):
    phone: Optional[str] = None
    website: Optional[str] = None


class OfferValidity(BaseModel):
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


class IngestionMetadata(BaseModel):
    source_document: str
    ingestion_timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class Supplier(BaseModel):
    id: str = ""
    supplier_id: str
    brand: str
    store_name: str
    address: Address
    opening_hours: List[OpeningHour] = Field(default_factory=list)
    region: Optional[str] = None
    contact: Contact = Field(default_factory=Contact)
    offer_validity: Optional[OfferValidity] = None
    ingestion_metadata: IngestionMetadata

    @model_validator(mode="after")
    def _default_id(self) -> "Supplier":
        if not self.id:
            self.id = self.supplier_id
        return self


class Category(BaseModel):
    id: str = ""
    category_id: str
    name: str
    parent_category_id: Optional[str] = None
    description_text: str
    semantic_tags: List[str] = Field(default_factory=list)
    embedding: List[float] = Field(default_factory=list)

    @model_validator(mode="after")
    def _default_id(self) -> "Category":
        if not self.id:
            self.id = self.category_id
        return self


class Attributes(BaseModel):
    origin: Optional[str] = None
    quality_grade: Optional[str] = None
    bio: bool = False
    animal_welfare: Optional[str] = None


class Packaging(BaseModel):
    unit_type: Optional[str] = None
    quantity: Optional[float] = None
    packaging_type: Optional[str] = None


class Pricing(BaseModel):
    current_price: float
    currency: str = "EUR"
    original_price: Optional[float] = None
    discount_percentage: Optional[float] = None
    unit_price: Optional[float] = None
    unit_reference: Optional[str] = None


class Promotion(BaseModel):
    type: Optional[str] = None
    bonus_amount: Optional[float] = None
    coupon_required: bool = False


class Conditions(BaseModel):
    deposit: Optional[float] = None
    availability: Optional[str] = None


class Item(BaseModel):
    id: str = ""
    item_id: str
    supplier_id: str
    name: str
    brand: Optional[str] = None
    description_text: str
    category_id: str
    attributes: Attributes = Field(default_factory=Attributes)
    packaging: Packaging = Field(default_factory=Packaging)
    pricing: Pricing
    promotion: Promotion = Field(default_factory=Promotion)
    conditions: Conditions = Field(default_factory=Conditions)
    embedding: List[float] = Field(default_factory=list)

    @model_validator(mode="after")
    def _default_id(self) -> "Item":
        if not self.id:
            self.id = self.item_id
        return self


CatalogBySupplier = Dict[str, List[Item]]
