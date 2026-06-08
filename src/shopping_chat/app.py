from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from src.shopping_chat.catalog import CatalogService
from src.shared.models import (
    Address,
    IngestionMetadata,
    OfferValidity,
    Supplier,
)
from src.shared.seed_data import seed_items

app = FastAPI(title="agentic-supply-chain MCP app")


def _seed_suppliers() -> List[Supplier]:
    start = date.today()
    end = start + timedelta(days=6)
    return [
        Supplier(
            supplier_id="rewe-berlin-week-24",
            brand="REWE",
            store_name="REWE Berlin Mitte",
            address=Address(
                street="Alexanderplatz 1",
                city="Berlin",
                postal_code="10178",
                country="DE",
            ),
            region="Berlin",
            offer_validity=OfferValidity(start_date=start, end_date=end),
            ingestion_metadata=IngestionMetadata(
                source_document="seed",
                ingestion_timestamp=datetime.now(timezone.utc),
            ),
        ),
        Supplier(
            supplier_id="aldi-berlin-week-24",
            brand="ALDI SÜD",
            store_name="ALDI Berlin Süd",
            address=Address(
                street="Leipziger Str. 30",
                city="Berlin",
                postal_code="10117",
                country="DE",
            ),
            region="Berlin",
            offer_validity=OfferValidity(start_date=start, end_date=end),
            ingestion_metadata=IngestionMetadata(
                source_document="seed",
                ingestion_timestamp=datetime.now(timezone.utc),
            ),
        ),
    ]


SEED_ITEMS = seed_items()

catalog = CatalogService(SEED_ITEMS)
suppliers = _seed_suppliers()


class SearchRequest(BaseModel):
    query: str


class CategoryRequest(BaseModel):
    category_id: str
    limit: int = 5


class SupplierRequest(BaseModel):
    supplier_id: str


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    ui_path = Path(__file__).parent / "templates" / "index.html"
    return ui_path.read_text(encoding="utf-8")


@app.post("/mcp/search")
def mcp_search(req: SearchRequest):
    return {"items": [item.model_dump() for item in catalog.search_by_description(req.query)]}


@app.post("/mcp/recommend")
def mcp_recommend(req: CategoryRequest):
    return {
        "items": [
            item.model_dump() for item in catalog.recommend_by_category(req.category_id, req.limit)
        ]
    }


@app.post("/mcp/inventory")
def mcp_inventory(req: SupplierRequest):
    return {"items": [item.model_dump() for item in catalog.inventory_by_supplier(req.supplier_id)]}


@app.get("/mcp/suppliers")
def mcp_suppliers():
    return {"suppliers": [supplier.model_dump() for supplier in suppliers]}
