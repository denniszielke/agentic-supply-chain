from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


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

    @field_validator("start_date", "end_date")
    @classmethod
    def _ensure_tz_aware(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class IngestionMetadata(BaseModel):
    source_document: str
    ingestion_timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @field_validator("ingestion_timestamp")
    @classmethod
    def _ensure_tz_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class Supplier(BaseModel):
    id: str = ""
    supplier_id: str
    brand: str
    store_name: str
    address: Address
    opening_hours: List[OpeningHour] = Field(default_factory=list)
    region: Optional[str] = None
    contact: Contact = Field(default_factory=Contact)

    @field_validator("contact", mode="before")
    @classmethod
    def _coerce_contact(cls, value):
        return Contact() if value is None else value

    @field_validator("opening_hours", mode="before")
    @classmethod
    def _coerce_opening_hours(cls, value):
        return [] if value is None else value

    @model_validator(mode="after")
    def _default_id(self) -> "Supplier":
        if not self.id:
            self.id = self.supplier_id
        return self


class Category(BaseModel):
    id: str = ""
    category_id: str
    name: str
    description_text: str
    semantic_tags: List[str] = Field(default_factory=list)
    embedding: List[float] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _default_description(cls, data):
        if isinstance(data, dict) and not data.get("description_text"):
            data["description_text"] = data.get("name") or data.get("category_id") or ""
        return data

    @field_validator("semantic_tags", "embedding", mode="before")
    @classmethod
    def _coerce_lists(cls, value):
        return [] if value is None else value

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

    @field_validator("bio", mode="before")
    @classmethod
    def _coerce_bio(cls, value):
        return False if value is None else value


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


def _extract_float(value) -> Optional[float]:
    """Extract the first numeric value from a string, or return None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = re.search(r"\d+[.,]\d+|\d+", value)
        if m:
            return float(m.group().replace(",", "."))
    return None


class Promotion(BaseModel):
    type: Optional[str] = None
    bonus_amount: Optional[float] = None
    coupon_required: bool = False

    @field_validator("coupon_required", mode="before")
    @classmethod
    def _coerce_coupon_required(cls, value):
        return False if value is None else value

    @field_validator("bonus_amount", mode="before")
    @classmethod
    def _coerce_bonus_amount(cls, value):
        return _extract_float(value)


class Conditions(BaseModel):
    deposit: Optional[float] = None
    availability: Optional[str] = None

    @field_validator("deposit", mode="before")
    @classmethod
    def _coerce_deposit(cls, value):
        return _extract_float(value)

    @field_validator("availability", mode="before")
    @classmethod
    def _coerce_availability(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            # LLM sometimes returns {"availability": "..."} instead of a plain string
            for v in value.values():
                if isinstance(v, str) and v:
                    return v
        return str(value) if value else None


class Item(BaseModel):
    id: str = ""
    item_id: str
    supplier_id: str
    name: str
    brand: Optional[str] = None
    description_text: str
    category_id: str
    source_ref: Optional[str] = None
    offer_validity: Optional[OfferValidity] = None
    attributes: Attributes = Field(default_factory=Attributes)
    packaging: Packaging = Field(default_factory=Packaging)
    pricing: Pricing
    promotion: Promotion = Field(default_factory=Promotion)
    conditions: Conditions = Field(default_factory=Conditions)
    embedding: List[float] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _default_description(cls, data):
        if isinstance(data, dict) and not data.get("description_text"):
            data["description_text"] = data.get("name") or data.get("item_id") or ""
        return data

    @field_validator("attributes", "packaging", "promotion", "conditions", mode="before")
    @classmethod
    def _coerce_nested(cls, value):
        return {} if value is None else value

    @field_validator("embedding", mode="before")
    @classmethod
    def _coerce_embedding(cls, value):
        return [] if value is None else value

    @model_validator(mode="after")
    def _default_id(self) -> "Item":
        if not self.id:
            self.id = self.item_id
        return self


CatalogBySupplier = Dict[str, List[Item]]
