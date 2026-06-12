from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


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


def _ensure_tz_aware(value: Optional[datetime]) -> Optional[datetime]:
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
    def _ensure_tz(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class Supplier(BaseModel):
    """Flat supplier document.

    Nested address / contact objects and the opening-hours collection have been
    flattened into prefixed scalar fields so the index contains no composite
    (ComplexField) types. ``opening_hours`` is a simple string collection where
    each entry is formatted as ``"<day> <open>-<close>"``.
    """

    id: str = ""
    supplier_id: str
    brand: str
    store_name: str
    region: Optional[str] = None

    address_street: Optional[str] = None
    address_city: Optional[str] = None
    address_postal_code: Optional[str] = None
    address_country: str = "DE"
    address_geo_lat: Optional[float] = None
    address_geo_lon: Optional[float] = None

    opening_hours: List[str] = Field(default_factory=list)

    contact_phone: Optional[str] = None
    contact_website: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _flatten(cls, data):
        """Accept legacy nested input (address/contact/opening_hours) and flatten it."""
        if not isinstance(data, dict):
            return data
        data = dict(data)

        addr = data.pop("address", None)
        if isinstance(addr, dict):
            data.setdefault("address_street", addr.get("street"))
            data.setdefault("address_city", addr.get("city"))
            data.setdefault("address_postal_code", addr.get("postal_code"))
            if addr.get("country"):
                data.setdefault("address_country", addr.get("country"))
            geo = addr.get("geo")
            if isinstance(geo, dict):
                data.setdefault("address_geo_lat", geo.get("lat"))
                data.setdefault("address_geo_lon", geo.get("lon"))

        contact = data.pop("contact", None)
        if isinstance(contact, dict):
            data.setdefault("contact_phone", contact.get("phone"))
            data.setdefault("contact_website", contact.get("website"))

        oh = data.get("opening_hours")
        if isinstance(oh, list):
            flat: list[str] = []
            for entry in oh:
                if isinstance(entry, dict):
                    day = (entry.get("day") or "").strip()
                    open_ = (entry.get("open") or "").strip()
                    close = (entry.get("close") or "").strip()
                    hours = f"{open_}-{close}".strip("-")
                    flat.append(f"{day} {hours}".strip())
                elif isinstance(entry, str):
                    flat.append(entry)
            data["opening_hours"] = flat
        return data

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


# Nested object name → tuple of its sub-field names. Used to flatten legacy
# nested item payloads (LLM output, existing JSON files) into prefixed scalars.
_ITEM_NESTED: dict[str, tuple[str, ...]] = {
    "attributes": ("origin", "quality_grade", "bio", "animal_welfare"),
    "packaging": ("unit_type", "quantity", "packaging_type"),
    "pricing": (
        "current_price",
        "currency",
        "original_price",
        "discount_percentage",
        "unit_price",
        "unit_reference",
    ),
    "promotion": ("type", "bonus_amount", "coupon_required"),
    "conditions": ("deposit", "availability"),
    "offer_validity": ("start_date", "end_date"),
}


class Item(BaseModel):
    """Flat item / offer document.

    All previously nested objects (attributes, packaging, pricing, promotion,
    conditions, offer_validity) have been flattened into ``<group>_<field>``
    scalar fields so the search index contains no composite types.
    """

    id: str = ""
    item_id: str
    supplier_id: str
    name: str
    brand: Optional[str] = None
    description_text: str
    category_id: str
    source_ref: Optional[str] = None

    # attributes_*
    attributes_origin: Optional[str] = None
    attributes_quality_grade: Optional[str] = None
    attributes_bio: bool = False
    attributes_animal_welfare: Optional[str] = None

    # packaging_*
    packaging_unit_type: Optional[str] = None
    packaging_quantity: Optional[float] = None
    packaging_packaging_type: Optional[str] = None

    # pricing_*
    pricing_current_price: float
    pricing_currency: str = "EUR"
    pricing_original_price: Optional[float] = None
    pricing_discount_percentage: Optional[float] = None
    pricing_unit_price: Optional[float] = None
    pricing_unit_reference: Optional[str] = None

    # promotion_*
    promotion_type: Optional[str] = None
    promotion_bonus_amount: Optional[float] = None
    promotion_coupon_required: bool = False

    # conditions_*
    conditions_deposit: Optional[float] = None
    conditions_availability: Optional[str] = None

    # offer_validity_*
    offer_validity_start_date: Optional[datetime] = None
    offer_validity_end_date: Optional[datetime] = None

    embedding: List[float] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _flatten(cls, data):
        """Accept legacy nested input and flatten it into prefixed scalar fields."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for prefix, subfields in _ITEM_NESTED.items():
            nested = data.pop(prefix, None)
            if isinstance(nested, dict):
                for sub in subfields:
                    flat_key = f"{prefix}_{sub}"
                    if sub in nested and data.get(flat_key) is None:
                        data[flat_key] = nested[sub]
        if not data.get("description_text"):
            data["description_text"] = data.get("name") or data.get("item_id") or ""
        return data

    @field_validator("attributes_bio", "promotion_coupon_required", mode="before")
    @classmethod
    def _coerce_bool(cls, value):
        return False if value is None else value

    @field_validator("promotion_bonus_amount", "conditions_deposit", mode="before")
    @classmethod
    def _coerce_float(cls, value):
        return _extract_float(value)

    @field_validator("conditions_availability", mode="before")
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

    @field_validator("offer_validity_start_date", "offer_validity_end_date", mode="after")
    @classmethod
    def _tz_aware(cls, value: Optional[datetime]) -> Optional[datetime]:
        return _ensure_tz_aware(value)

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
