"""
Retail flyer processor.

Pipeline
--------
1. Materialise each source (URL or local path) to a local working directory.
2. Split PDF files into per-page PNG images using PyMuPDF.
3. Run a sliding-window batch loop over the collected images, calling a
   Microsoft Foundry vision model (via the agent-framework Foundry SDK) to
   incrementally extract supplier, category, and item data.
4. Persist the consolidated result as a JSON file and/or push it to Azure AI
   Search indexes.

Environment variables
---------------------
PROCESSING_WORK_DIR              Root directory for image artefacts (default: tempfiles, relative to cwd; set to an absolute path to override)
PROCESSING_BATCH_SIZE            Images per sliding-window batch (default: 8)
PROCESSING_OVERLAP               Overlapping images between consecutive batches (default: 2)
AZURE_AI_PROJECT_ENDPOINT        Azure AI Foundry project endpoint URL (required)
AZURE_OPENAI_CHAT_DEPLOYMENT_NAME  Vision model deployment name (default: gpt-5.4-mini)

Search indexing (required when --push-to-search is used)
AZURE_SEARCH_ENDPOINT            Azure AI Search service endpoint
AZURE_SEARCH_ADMIN_KEY           Admin API key (optional; falls back to DefaultAzureCredential)
AZURE_SEARCH_SUPPLIER_INDEX_NAME Supplier index name (default: retail-suppliers)
AZURE_SEARCH_CATEGORY_INDEX_NAME Category index name (default: retail-categories)
AZURE_SEARCH_ITEM_INDEX_NAME     Item index name (default: retail-items)
AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME  Embedding model for category/item vectors (optional; skips vectors if unset)
FOUNDRY_MODELS_ENDPOINT          Foundry inference endpoint for embeddings (optional; defaults to <project-host>/models)
FOUNDRY_MODELS_API_KEY           API key for the Foundry inference endpoint (optional; falls back to DefaultAzureCredential)
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.parse import urlparse

import re
import unicodedata

import requests
from agent_framework import Content, Message
from agent_framework.foundry import FoundryChatClient, FoundryEmbeddingClient
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError

from src.shared.models import (
    Category,
    Item,
    Supplier,
)

load_dotenv()

logger = logging.getLogger(__name__)


def _format_validation_error(exc: Exception) -> str:
    """Return a human-readable summary of a pydantic ValidationError.

    For each error, shows the field path and the invalid value that was
    received, making it easy to identify what the LLM returned incorrectly.
    """
    if not isinstance(exc, ValidationError):
        return str(exc)
    lines: list[str] = []
    for e in exc.errors():
        field = ".".join(str(loc) for loc in e.get("loc", []))
        msg = e.get("msg", "")
        input_val = e.get("input")
        lines.append(f"  {field}: {msg} — got {input_val!r}")
    return "\n".join(lines) if lines else str(exc)


def _safe_key(text: str) -> str:
    """Return a document-key-safe slug from *text*.

    Azure AI Search keys may only contain letters, digits, underscore (_),
    dash (-), or equal sign (=).  This function:
      1. Expands German umlauts to their ASCII digraphs (ä→ae, ö→oe, ü→ue, ß→ss).
      2. Applies NFKD unicode normalisation and drops any remaining non-ASCII.
      3. Lowercases and replaces every invalid character with a dash.
      4. Collapses consecutive dashes and strips leading/trailing dashes.
    """
    for src, dst in (
        ("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"),
        ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue"),
    ):
        text = text.replace(src, dst)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9\-_=]", "-", text.lower())
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "key"

# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------

_DEFAULT_WORK_DIR = "data/tempfiles"
_DEFAULT_BATCH_SIZE = 8
_DEFAULT_OVERLAP = 2
_DEFAULT_MODEL = "gpt-5.4-mini"
# Longest-edge pixel cap for images sent to the vision model. Keeps per-request
# payloads small enough to avoid API timeouts when batching multiple pages.
_MAX_IMAGE_DIMENSION = int(os.getenv("PROCESSING_MAX_IMAGE_DIMENSION", "1536"))

# ---------------------------------------------------------------------------
# Job data models
# ---------------------------------------------------------------------------


class JobInput(BaseModel):
    """Describes a single flyer extraction job."""

    supplier_id: str = Field(..., description="Stable business identifier for the supplier.")
    sources: List[str] = Field(
        ...,
        description="List of URLs or local file paths pointing to PDF or image files to process.",
    )
    output_path: str = Field(
        default="data/extraction-result.json",
        description="Path where the JSON extraction result will be written.",
    )


class ExtractionResult(BaseModel):
    """Consolidated output of a flyer extraction job."""

    supplier: Optional[Supplier] = None
    categories: List[Category] = Field(default_factory=list)
    items: List[Item] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal LLM response shape (raw JSON from the model)
# ---------------------------------------------------------------------------

_ONTOLOGY_PATH = Path(__file__).parent.parent / "shared" / "ontology.json"


def _load_ontology_summary() -> str:
    """Return a compact, prompt-safe summary of the ontology."""
    try:
        data = json.loads(_ONTOLOGY_PATH.read_text(encoding="utf-8"))
        lines = [f"Domain: {data.get('domain', '')} — {data.get('description', '')}"]
        for cls_name, cls_def in data.get("classes", {}).items():
            fields = ", ".join(cls_def.get("fields", {}).keys())
            lines.append(f"  {cls_name}: {cls_def.get('description', '')} Fields: [{fields}]")
        return "\n".join(lines)
    except Exception:
        return "(ontology not available)"


_SYSTEM_PROMPT = """
You are an expert retail data extraction engine. You will receive a batch of retail
promocional flyer page images together with a SUMMARY of what has already been
extracted from previous pages.

ONTOLOGY REFERENCE:
{ontology}

YOUR TASK:
Analyse ONLY the newly provided images and return ONLY the NEW or UPDATED entities
found in those images. Do NOT repeat entities that already exist unless you are
correcting them.

OUTPUT RULES:
- Return ONLY a single valid JSON object matching this exact structure:
  {{
    "supplier": {{ ... }},       // supplier object with offer_validity (document-level start/end dates)
                                 // ONLY if newly determined or corrected this batch; otherwise null
    "categories": [ ... ],       // ONLY categories newly seen or updated in THIS batch's images
    "items": [ ... ]             // ONLY items newly seen or updated in THIS batch's images
  }}
- Do NOT re-emit entities already listed in the existing-state summary; emit only deltas.
- Assign stable item_id values using the pattern: "{{supplier_id}}-{{YYYY-MM-DD}}-{{slug}}-{{index}}"
  where YYYY-MM-DD is today's date, slug is a lowercase ASCII-only hyphenated product name
  (no umlauts or special characters — write ae/oe/ue instead of ä/ö/ü),
  and index continues from the highest existing index for that slug.
- All category_id values must be lowercase, hyphen-separated slugs.
- Every item's category_id must reference either an existing category_id or one you
  include in this batch's "categories" array.
- For each NEW category write an EXTENSIVE description_text (3-5 sentences) covering:
    * What product types and sub-types belong to this category
    * Typical brands, packaging forms, and usage contexts
    * Distinguishing characteristics versus adjacent categories
  The description is used as the vector embedding — richer text produces better search results.
- Use null for optional fields that cannot be extracted from the images.
- OFFER VALIDITY — each item MUST include an "offer_validity" field:
    * DEFAULT: copy the document-level validity from supplier.offer_validity
      (start_date / end_date of the overall flyer period).
    * OVERRIDE: if the flyer explicitly shows a NARROWER validity for an individual item
      (e.g. "nur am Samstag", "nur am Wochenende", "nur Do.",
      a date range visually highlighted on that item or page that is shorter than the
      document range), set offer_validity to that narrower window instead.
    * The document-level validity (for use as default) is provided in the existing-state
      summary under "document_validity".
- NUMERIC FIELDS — always output a plain JSON number (not a string) for:
    * conditions.deposit: decimal only, e.g. 0.25 or 3.00. Strip any surrounding text
      such as "Pfand", "zzgl. Pfand", "inkl. Pfand" etc.
    * promotion.bonus_amount: decimal only. For bundle deals like "4+1 gratis" use null
      and encode the deal description in promotion.type (e.g. "4+1 gratis").
    * pricing fields (current_price, original_price, unit_price, discount_percentage):
      plain numbers only, no currency symbols, units, or percent signs.
- PACKAGING — extract into the "packaging" object using GERMAN terminology:
    * unit_type: the content unit as a lowercase German word — use exactly one of:
        "gramm", "kilogramm", "milligramm", "liter", "milliliter", "zentiliter",
        "stueck", "portion", "meter", "zentimeter" — do NOT use abbreviations like "g", "kg", "ml".
    * quantity: the numeric amount only (e.g. 500 for "500g", 0.75 for "0,75l", 300 for "300g").
    * packaging_type: the container/format as a lowercase German word, e.g.:
        "packung", "flasche", "dose", "becher", "beutel", "glas", "tube",
        "karton", "faltschachtel", "tüte", "netz", "schale", "kanister" etc.
    Examples:
      "500g Packung"   → unit_type="gramm",      quantity=500,  packaging_type="packung"
      "0,75l Flasche"  → unit_type="liter",       quantity=0.75, packaging_type="flasche"
      "300g Dose"      → unit_type="gramm",       quantity=300,  packaging_type="dose"
      "6 Stück"        → unit_type="stueck",      quantity=6,    packaging_type=null
      "250ml Becher"   → unit_type="milliliter",  quantity=250,  packaging_type="becher"
- PRICING — populate ALL applicable sub-fields:
    * current_price: the main shelf/promo price shown, as a plain number.
    * original_price: if a price is visually crossed-through or shown as "statt X.XX",
      set original_price to that number.
    * discount_percentage: if the flyer shows a relative discount such as "-17%" or
      "17% gespart", set discount_percentage=17 (plain number, no % sign).
      Do NOT set this when only an original_price is shown — only set it when a
      percentage is explicitly printed.
    * unit_price + unit_reference: the normalised comparison price printed alongside
      the item (e.g. "1 kg = 7,50", "1 l = 5,53", "(100g = 0,83)").
      - unit_price: the numeric value only (e.g. 7.5, 5.53).
      - unit_reference: the reference unit in lowercase German —
          "kg", "liter", "100g", "100ml", "stueck" etc., exactly as implied by the label.
    Examples:
      "2,49 €  (-17%)  1 kg = 4,98"  → current_price=2.49, discount_percentage=17,
                                         unit_price=4.98, unit_reference="kg"
      "3,99 € statt 4,79 €"          → current_price=3.99, original_price=4.79
      "0,99 €  (1 l = 1,32)"         → current_price=0.99, unit_price=1.32, unit_reference="liter"
- DATE PARSING (German locale):
    * Dates on German flyers are written as DD.MM or DD.MM.YYYY — the first number is
      the DAY, the second is the MONTH. Never swap them.
    * Weekday names (Mo, Di, Mi, Do, Fr, Sa, So) printed alongside a date are ground
      truth: cross-check the weekday against the calendar to confirm the day/month are
      the right way around. If they disagree, trust the weekday and correct the date.
    * Assume the current year unless a year is explicitly printed.
    * Output all dates as ISO-8601 UTC strings (YYYY-MM-DDT00:00:00Z).
- ITEM SCOPE — extract ONLY classical in-store retail products:
    * Food & beverages, fresh produce, meat & dairy, frozen food, bakery, snacks
    * Household goods, cleaning products, personal care, pet supplies
    * Small appliances, kitchenware, textiles, DIY supplies, garden products
    * Toys, stationery, seasonal/promotional merchandise
    * DO NOT extract: vacation packages, travel offers, mobile/data contracts,
      insurance products, financial services, or any non-physical service.
      If a page contains only such offers, return an empty "items" array for that batch.
- Do NOT wrap the JSON in markdown fences.
- Do NOT include ingestion_metadata in your output; it is added by the processor.
""".strip()

_TASK_PROMPT = """
INSTRUCTIONS FOR THIS BATCH:
1. Review the existing-state summary (first text block) to know what already exists.
2. Analyse the newly provided images for additional or updated content.
3. Return ONLY the new/updated entities from THIS batch as JSON (no fences, no comments).
""".strip()


# ---------------------------------------------------------------------------
# Foundry client factories
# ---------------------------------------------------------------------------


def _create_chat_client(model: str) -> FoundryChatClient:
    """Return a Foundry chat client bound to *model*.

    Resolves the Foundry project endpoint from ``AZURE_AI_PROJECT_ENDPOINT``
    (or ``FOUNDRY_PROJECT_ENDPOINT``) and authenticates with
    ``DefaultAzureCredential``.
    """
    project_endpoint = (
        os.getenv("AZURE_AI_PROJECT_ENDPOINT", "").strip()
        or os.getenv("FOUNDRY_PROJECT_ENDPOINT", "").strip()
    )
    if not project_endpoint:
        raise RuntimeError(
            "No Foundry project endpoint configured. "
            "Set AZURE_AI_PROJECT_ENDPOINT or FOUNDRY_PROJECT_ENDPOINT."
        )
    return FoundryChatClient(
        project_endpoint=project_endpoint,
        model=model,
        credential=DefaultAzureCredential(),
    )


class _ScopedCredential:
    """Wraps a TokenCredential to always request a specific OAuth2 scope.

    The Azure AI Services inference endpoint (*.services.ai.azure.com/models)
    requires the ``https://cognitiveservices.azure.com/.default`` scope, but
    FoundryEmbeddingClient may request a different audience, causing 401s.
    This shim intercepts ``get_token`` and substitutes the correct scope.
    """
    _SCOPE = "https://cognitiveservices.azure.com/.default"

    def __init__(self, inner) -> None:
        self._inner = inner

    def get_token(self, *_scopes, **kwargs):
        return self._inner.get_token(self._SCOPE, **kwargs)


def _create_embedding_client(model: str) -> FoundryEmbeddingClient:
    """Return a Foundry embedding client for *model*.

    ``FoundryEmbeddingClient`` uses the AI Services inference path
    ``{endpoint}/models/{model}/embeddings``, NOT the Azure OpenAI path
    ``/openai/deployments/{model}/embeddings``.  Using the wrong host produces
    a 404.

    Endpoint resolution order (first non-empty wins):
      1. ``FOUNDRY_MODELS_ENDPOINT`` — explicit override.
      2. ``/models`` path on the ``AZURE_AI_PROJECT_ENDPOINT`` host
         (e.g. ``https://<account>.services.ai.azure.com/models``).

    Credential resolution order:
      1. ``FOUNDRY_MODELS_API_KEY``
      2. ``DefaultAzureCredential``
    """
    endpoint = os.getenv("FOUNDRY_MODELS_ENDPOINT", "").strip()
    if not endpoint:
        project_endpoint = (
            os.getenv("AZURE_AI_PROJECT_ENDPOINT", "").strip()
            or os.getenv("FOUNDRY_PROJECT_ENDPOINT", "").strip()
        )
        if project_endpoint:
            parsed = urlparse(project_endpoint)
            endpoint = f"{parsed.scheme}://{parsed.netloc}/models"

    api_key = os.getenv("FOUNDRY_MODELS_API_KEY", "").strip() or None
    logger.info(
        "Embedding client: endpoint=%r  model=%r  auth=%s",
        endpoint,
        model,
        "api_key" if api_key else "DefaultAzureCredential",
    )
    if api_key:
        return FoundryEmbeddingClient(model=model, endpoint=endpoint, api_key=api_key)
    return FoundryEmbeddingClient(
        model=model, endpoint=endpoint, credential=_ScopedCredential(DefaultAzureCredential())
    )


# ---------------------------------------------------------------------------
# Search index pusher
# ---------------------------------------------------------------------------

_EMBEDDING_BATCH_SIZE = 256  # Azure OpenAI embeddings API limit per request
_EMBED_MAX_RETRIES = 3          # max attempts before giving up
_EMBED_INITIAL_BACKOFF = 1.0    # seconds; doubles on each retry (1 → 2 → 4)


class SearchIndexPusher:
    """Pushes an ExtractionResult to the three Azure AI Search indexes."""

    def __init__(self) -> None:
        endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
        if not endpoint:
            raise RuntimeError("AZURE_SEARCH_ENDPOINT is required for --push-to-search")

        api_key = os.getenv("AZURE_SEARCH_ADMIN_KEY", "").strip()
        credential: AzureKeyCredential | DefaultAzureCredential = (
            AzureKeyCredential(api_key) if api_key else DefaultAzureCredential()
        )

        supplier_index = os.getenv("AZURE_SEARCH_SUPPLIER_INDEX_NAME", "retail-suppliers")
        category_index = os.getenv("AZURE_SEARCH_CATEGORY_INDEX_NAME", "retail-categories")
        item_index = os.getenv("AZURE_SEARCH_ITEM_INDEX_NAME", "retail-items")

        self._supplier_client = SearchClient(endpoint, supplier_index, credential)
        self._category_client = SearchClient(endpoint, category_index, credential)
        self._item_client = SearchClient(endpoint, item_index, credential)
        self._embedding_model = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME", "").strip()
        self._embedding_client: FoundryEmbeddingClient | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def push(self, result: ExtractionResult) -> None:
        """Upload all entities in *result* to their respective search indexes."""
        if result.supplier:
            doc = result.supplier.model_dump(mode="json")
            self._supplier_client.merge_or_upload_documents([doc])
            logger.info("Indexed supplier '%s'.", result.supplier.supplier_id)

        # ── Step 1: deduplicate extracted categories against the index ───────
        id_remap: dict[str, str] = {}
        confirmed_category_ids: set[str] = set()
        if result.categories:
            new_categories, id_remap = await self._deduplicate_categories(result.categories)
            confirmed_category_ids.update(id_remap.values())  # existing ids we remapped to
            if new_categories:
                embeddings = await self._embed(
                    [c.description_text or c.name for c in new_categories]
                )
                cat_docs = []
                for i, cat in enumerate(new_categories):
                    doc = cat.model_dump(mode="json")
                    if embeddings:
                        doc["embedding"] = embeddings[i]
                    cat_docs.append(doc)
                self._category_client.merge_or_upload_documents(cat_docs)
                confirmed_category_ids.update(c.category_id for c in new_categories)
                logger.info(
                    "Indexed %d categories (%d remapped to existing).",
                    len(cat_docs),
                    len(result.categories) - len(new_categories),
                )
            else:
                logger.info("All %d categories already exist in the index — remapped.", len(result.categories))

        if result.items:
            # ── Step 2: apply category remap from deduplication ───────────────
            if id_remap:
                for item in result.items:
                    if item.category_id in id_remap:
                        logger.debug(
                            "Item '%s': remapping category '%s' → '%s'.",
                            item.item_id,
                            item.category_id,
                            id_remap[item.category_id],
                        )
                        item.category_id = id_remap[item.category_id]

            # ── Step 3: item-level category resolution ────────────────────────
            # For each item whose category is not yet confirmed in the index,
            # search for the closest existing category. If none is found above
            # the threshold the extracted category is pushed as a new document.
            item_cat_remap = await self._resolve_item_categories(
                result.items, confirmed_category_ids, result.categories
            )
            if item_cat_remap:
                for item in result.items:
                    if item.category_id in item_cat_remap:
                        item.category_id = item_cat_remap[item.category_id]

            embeddings = await self._embed(
                [it.description_text or it.name for it in result.items]
            )
            item_docs = []
            for i, item in enumerate(result.items):
                doc = item.model_dump(mode="json")
                if embeddings:
                    doc["embedding"] = embeddings[i]
                item_docs.append(doc)
            self._item_client.merge_or_upload_documents(item_docs)
            logger.info("Indexed %d items.", len(item_docs))

        if self._embedding_client is not None:
            await self._embedding_client.close()
            self._embedding_client = None

        print(
            f"Indexed to AI Search: 1 supplier, {len(result.categories)} categories, "
            f"{len(result.items)} items."
        )

    # ------------------------------------------------------------------
    # Embedding helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Category deduplication
    # ------------------------------------------------------------------

    _CATEGORY_SIMILARITY_THRESHOLD = 0.92   # cosine similarity above which we treat as duplicate
    _ITEM_CATEGORY_SEARCH_THRESHOLD = 0.88    # threshold for item-level category resolution

    async def _deduplicate_categories(
        self, categories: list[Category]
    ) -> tuple[list[Category], dict[str, str]]:
        """Return (new_categories, id_remap) against the search index.

        For each candidate category an embedding is generated from its
        description_text and a vector search is run against the existing index.
        If the top hit scores above the similarity threshold the candidate is
        considered a near-duplicate: it is omitted from new_categories and an
        entry is added to id_remap so callers can repoint items to the existing
        category_id.

        When no embedding model is configured all categories are returned
        unchanged and id_remap is empty — deduplication is best-effort.
        """
        if not self._embedding_model:
            return categories, {}

        descriptions = [c.description_text or c.name for c in categories]
        vectors = await self._embed(descriptions)
        if not vectors:
            return categories, {}

        new_categories: list[Category] = []
        id_remap: dict[str, str] = {}
        for cat, vec in zip(categories, vectors):
            vq = VectorizedQuery(
                vector=vec,
                k_nearest_neighbors=1,
                fields="embedding",
            )
            try:
                hits = list(
                    self._category_client.search(
                        search_text=None,
                        vector_queries=[vq],
                        select=["category_id", "name"],
                        top=1,
                    )
                )
            except Exception as exc:
                logger.warning("Category dedup search failed for '%s': %s", cat.category_id, exc)
                new_categories.append(cat)
                continue

            if hits:
                score = hits[0].get("@search.score", 0.0)
                if score >= self._CATEGORY_SIMILARITY_THRESHOLD:
                    existing_id = hits[0].get("category_id", cat.category_id)
                    logger.info(
                        "Category '%s' matches existing '%s' (score=%.3f) — remapping.",
                        cat.category_id,
                        existing_id,
                        score,
                    )
                    id_remap[cat.category_id] = existing_id
                    continue
            new_categories.append(cat)

        return new_categories, id_remap

    async def _resolve_item_categories(
        self,
        items: list[Item],
        confirmed_category_ids: set[str],
        extracted_categories: list[Category],
    ) -> dict[str, str]:
        """Resolve item category_ids not yet confirmed in the search index.

        For each unique category_id referenced by items that is not already
        known to exist in the index, performs a batched vector search against
        the category index using the extracted category's description_text.

        - Score >= _ITEM_CATEGORY_SEARCH_THRESHOLD  → remap to the existing category.
        - Score below threshold and category definition available → push as new category.
        - No definition available → leave as-is (logged as a warning).

        Returns a remap dict {unconfirmed_category_id -> confirmed_category_id}.
        """
        if not self._embedding_model:
            return {}

        unconfirmed_cat_ids = sorted({
            item.category_id for item in items
            if item.category_id not in confirmed_category_ids
        })
        if not unconfirmed_cat_ids:
            return {}

        logger.info(
            "Resolving %d unconfirmed item categor%s against the index.",
            len(unconfirmed_cat_ids),
            "y" if len(unconfirmed_cat_ids) == 1 else "ies",
        )

        cat_lookup: dict[str, Category] = {c.category_id: c for c in extracted_categories}
        texts = [
            cat_lookup[cid].description_text if cid in cat_lookup else cid
            for cid in unconfirmed_cat_ids
        ]

        vectors = await self._embed(texts)
        if not vectors:
            return {}

        remap: dict[str, str] = {}
        new_categories_to_push: list[tuple[Category, list[float]]] = []

        for cat_id, vec in zip(unconfirmed_cat_ids, vectors):
            vq = VectorizedQuery(
                vector=vec,
                k_nearest_neighbors=1,
                fields="embedding",
            )
            try:
                hits = list(
                    self._category_client.search(
                        search_text=None,
                        vector_queries=[vq],
                        select=["category_id", "name"],
                        top=1,
                    )
                )
            except Exception as exc:
                logger.warning(
                    "Item category resolution search for '%s' failed: %s — will push as new.",
                    cat_id, exc,
                )
                if cat_id in cat_lookup:
                    new_categories_to_push.append((cat_lookup[cat_id], vec))
                continue

            if hits:
                score = hits[0].get("@search.score", 0.0)
                if score >= self._ITEM_CATEGORY_SEARCH_THRESHOLD:
                    existing_id = hits[0].get("category_id", cat_id)
                    logger.info(
                        "Item category '%s' → remapped to existing '%s' (score=%.3f).",
                        cat_id, existing_id, score,
                    )
                    remap[cat_id] = existing_id
                    continue

            # No suitable match — push the extracted category as a new index document.
            if cat_id in cat_lookup:
                logger.info(
                    "Item category '%s' has no index match (best score below threshold) — pushing as new.",
                    cat_id,
                )
                new_categories_to_push.append((cat_lookup[cat_id], vec))
            else:
                logger.warning(
                    "Item category '%s' has no index match and no extracted definition — leaving as-is.",
                    cat_id,
                )

        if new_categories_to_push:
            docs = []
            for cat, vec in new_categories_to_push:
                doc = cat.model_dump(mode="json")
                doc["embedding"] = vec
                docs.append(doc)
            self._category_client.merge_or_upload_documents(docs)
            logger.info(
                "Pushed %d new categor%s from item-level resolution.",
                len(docs),
                "y" if len(docs) == 1 else "ies",
            )

        return remap

    def _get_embedding_client(self) -> FoundryEmbeddingClient:
        if self._embedding_client is None:
            self._embedding_client = _create_embedding_client(self._embedding_model)
        return self._embedding_client

    async def _embed(self, texts: list[str]) -> list[list[float]] | None:
        """Generate embeddings in batches with retry on rate-limit / transient errors.

        HTTP status handling:
          429  — rate limit: retry with exponential back-off (up to _EMBED_MAX_RETRIES).
          5xx  — transient server error: same retry behaviour.
          404  — resource not found: configuration error, logged clearly, not retried.
          other — logged as warning, not retried.
        Returns None when embeddings cannot be generated (vectors skipped).
        """
        if not self._embedding_model or not texts:
            return None

        resolved_endpoint = (
            os.getenv("FOUNDRY_MODELS_ENDPOINT")
            or "(derived from AZURE_AI_PROJECT_ENDPOINT)"
        )

        async def _call_batch(batch: list[str]) -> list[list[float]]:
            backoff = _EMBED_INITIAL_BACKOFF
            for attempt in range(1, _EMBED_MAX_RETRIES + 1):
                try:
                    client = self._get_embedding_client()
                    resp = await client.get_embeddings(batch)
                    return [item.vector for item in resp]
                except HttpResponseError as exc:
                    status = exc.status_code
                    if status == 404:
                        logger.error(
                            "Embedding 404 — resource not found (config error, not retrying).\n"
                            "  endpoint=%r  model=%r\n"
                            "  Check that the deployment name and endpoint path are correct.\n"
                            "  Detail: %s",
                            resolved_endpoint,
                            self._embedding_model,
                            exc,
                        )
                        raise
                    if status == 429 or (status is not None and status >= 500):
                        if attempt < _EMBED_MAX_RETRIES:
                            retry_after = float(
                                getattr(exc, "retry_after", None) or backoff
                            )
                            logger.warning(
                                "Embedding HTTP %s on attempt %d/%d — retrying in %.1fs.\n"
                                "  endpoint=%r  model=%r  detail: %s",
                                status,
                                attempt,
                                _EMBED_MAX_RETRIES,
                                retry_after,
                                resolved_endpoint,
                                self._embedding_model,
                                exc,
                            )
                            await asyncio.sleep(retry_after)
                            backoff *= 2
                            continue
                        logger.warning(
                            "Embedding HTTP %s — exhausted %d retries, skipping vectors.\n"
                            "  endpoint=%r  model=%r  detail: %s",
                            status,
                            _EMBED_MAX_RETRIES,
                            resolved_endpoint,
                            self._embedding_model,
                            exc,
                        )
                        raise
                    # Non-retryable HTTP error
                    logger.warning(
                        "Embedding HTTP %s (not retrying).\n"
                        "  endpoint=%r  model=%r  detail: %s",
                        status,
                        resolved_endpoint,
                        self._embedding_model,
                        exc,
                    )
                    raise
            raise RuntimeError("unreachable")

        try:
            results: list[list[float]] = []
            for start in range(0, len(texts), _EMBEDDING_BATCH_SIZE):
                batch = texts[start: start + _EMBEDDING_BATCH_SIZE]
                results.extend(await _call_batch(batch))
            return results
        except Exception as exc:
            logger.warning(
                "Embedding generation failed, skipping vectors: %s\n"
                "  endpoint=%r  model=%r",
                exc,
                resolved_endpoint,
                self._embedding_model,
            )
            return None


# ---------------------------------------------------------------------------
# Processor
# ---------------------------------------------------------------------------


class FlyerProcessor:
    """
    Extracts structured retail data from flyer PDFs or images using a
    sliding-window vision model loop.
    """

    def __init__(self) -> None:
        self.work_dir = Path(os.getenv("PROCESSING_WORK_DIR", _DEFAULT_WORK_DIR))
        self.batch_size = int(os.getenv("PROCESSING_BATCH_SIZE", str(_DEFAULT_BATCH_SIZE)))
        self.overlap = int(os.getenv("PROCESSING_OVERLAP", str(_DEFAULT_OVERLAP)))
        self.model_name = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME", _DEFAULT_MODEL)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._client = self._build_client()
        self._ontology_summary = _load_ontology_summary()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process(self, job: JobInput, push_to_search: bool = False, output_path: str | None = "data/extraction-result.json") -> ExtractionResult:
        """Run the full extraction pipeline for *job* and return the result."""
        session_dir = self.work_dir / job.supplier_id
        session_dir.mkdir(parents=True, exist_ok=True)

        # 1. Materialise sources and split PDFs into images
        image_files: list[Path] = []
        for source in job.sources:
            local_path = self._materialise_source(source, session_dir)
            if local_path.suffix.lower() == ".pdf":
                image_files.extend(self._split_pdf(local_path, session_dir))
            elif local_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                image_files.append(local_path)
            else:
                logger.warning("Skipping unsupported file type: %s", local_path)

        image_files = sorted(set(image_files))
        if not image_files:
            raise RuntimeError("No image files to process after materialising sources.")

        logger.info("Processing %d image(s) for supplier '%s'.", len(image_files), job.supplier_id)

        # 2. Run sliding-window extraction
        result = await self._extract_from_batches(image_files, job)

        logger.info(
            "Extraction complete: supplier=%s, categories=%d, items=%d",
            result.supplier.supplier_id if result.supplier else "None",
            len(result.categories),
            len(result.items),
        )
        print(
            f"Extraction complete: 1 supplier, {len(result.categories)} categories, "
            f"{len(result.items)} items."
        )

        # 3. Output
        await self._finalise(result, push_to_search, output_path)
        return result

    # ------------------------------------------------------------------
    # Source materialisation
    # ------------------------------------------------------------------

    def _materialise_source(self, source: str, dest_dir: Path) -> Path:
        """Download a URL or copy a local path into *dest_dir*."""
        parsed = urlparse(source)
        if parsed.scheme in {"http", "https"}:
            resp = requests.get(source, timeout=60)
            resp.raise_for_status()
            suffix = Path(parsed.path).suffix or ".bin"
            dest = dest_dir / f"{uuid.uuid4().hex}{suffix}"
            dest.write_bytes(resp.content)
            logger.info("Downloaded %s → %s", source, dest)
            return dest

        local = Path(source)
        if not local.exists():
            raise FileNotFoundError(f"Source file not found: {source}")
        return local

    # ------------------------------------------------------------------
    # PDF splitting
    # ------------------------------------------------------------------

    def _split_pdf(self, pdf_path: Path, dest_dir: Path) -> list[Path]:
        """Split each page of *pdf_path* into a PNG image in *dest_dir*."""
        try:
            import pymupdf  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "pymupdf is required for PDF processing. "
                "Install it with: pip install pymupdf"
            ) from exc

        stem = pdf_path.stem
        page_images: list[Path] = []
        doc = pymupdf.open(str(pdf_path))
        try:
            for page in doc:
                pix = page.get_pixmap()
                out_path = dest_dir / f"{stem}-{page.number + 1:03d}.png"
                pix.save(str(out_path))
                page_images.append(out_path)
        finally:
            doc.close()

        logger.info("Split %s into %d page images.", pdf_path.name, len(page_images))
        return sorted(page_images)

    # ------------------------------------------------------------------
    # Sliding-window helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sliding_windows(files: list[Path], batch_size: int, overlap: int) -> Iterable[list[Path]]:
        if batch_size <= 0:
            raise ValueError("batch_size must be > 0")
        if overlap < 0:
            raise ValueError("overlap must be >= 0")
        if overlap >= batch_size:
            raise ValueError("overlap must be < batch_size")
        step = batch_size - overlap
        start = 0
        while start < len(files):
            batch = files[start: start + batch_size]
            if not batch:
                break
            yield batch
            if start + batch_size >= len(files):
                break
            start += step

    # ------------------------------------------------------------------
    # Extraction loop
    # ------------------------------------------------------------------

    async def _extract_from_batches(self, image_files: list[Path], job: JobInput) -> ExtractionResult:
        """Drive the sliding-window extraction and return a merged result.

        Each batch returns only the entities newly seen or updated in its own
        images (a delta). Deltas are accumulated and deduplicated in Python, so
        per-batch model output stays small and a single truncated or malformed
        batch never discards results from earlier batches.
        """
        system_prompt = _SYSTEM_PROMPT.format(ontology=self._ontology_summary)
        merged: dict = {"supplier": None, "categories": {}, "items": {}}
        batch_idx = -1

        try:
            for batch_idx, batch in enumerate(
                self._sliding_windows(image_files, self.batch_size, self.overlap)
            ):
                logger.info(
                    "Batch %d/%d: %d image(s) [%s … %s]",
                    batch_idx + 1,
                    -1,
                    len(batch),
                    batch[0].name,
                    batch[-1].name,
                )
                state_summary = self._summarise_state(merged)
                messages = self._build_messages(system_prompt, batch, state_summary)
                response = await self._client.get_response(
                    messages,
                    options={"temperature": 0, "max_tokens": 16384},
                )
                raw = (response.text or "").replace("```json", "").replace("```", "").strip()
                delta = self._loads_resilient(raw)
                if delta is None:
                    logger.warning(
                        "Batch %d produced unparseable output — skipping its delta.",
                        batch_idx + 1,
                    )
                    continue
                # Annotate items with their source batch images so _parse_extraction_result
                # can populate source_ref without losing per-batch page information.
                for raw_item in (delta.get("items") or []):
                    if isinstance(raw_item, dict) and "_batch_images" not in raw_item:
                        raw_item["_batch_images"] = [p.name for p in batch]
                self._merge_delta(merged, delta)
                logger.info(
                    "Batch %d processed (running totals: %d categories, %d items).",
                    batch_idx + 1,
                    len(merged["categories"]),
                    len(merged["items"]),
                )
        except KeyboardInterrupt:
            logger.warning(
                "Interrupted after batch %d — saving partial results.", batch_idx + 1
            )

        consolidated = {
            "supplier": merged["supplier"],
            "categories": list(merged["categories"].values()),
            "items": list(merged["items"].values()),
        }
        return self._parse_extraction_result(json.dumps(consolidated), job)

    # ------------------------------------------------------------------
    # Delta merging
    # ------------------------------------------------------------------

    @staticmethod
    def _summarise_state(merged: dict) -> str:
        """Return a compact JSON summary of accumulated state for model context.

        Only identifiers (and category/item names) are included so the model can
        avoid duplicates and continue id numbering without re-sending full
        objects on every batch.
        """
        doc_validity: dict | None = None
        raw_supplier = merged.get("supplier")
        if isinstance(raw_supplier, dict):
            ov = raw_supplier.get("offer_validity")
            if isinstance(ov, dict) and (ov.get("start_date") or ov.get("end_date")):
                doc_validity = ov
        summary = {
            "supplier_determined": raw_supplier is not None,
            "document_validity": doc_validity,
            "existing_category_ids": sorted(merged["categories"].keys()),
            "existing_item_ids": sorted(merged["items"].keys()),
        }
        return json.dumps(summary, ensure_ascii=False)

    @staticmethod
    def _merge_delta(merged: dict, delta: dict) -> None:
        """Merge a single batch's delta into the accumulated state in place."""
        raw_supplier = delta.get("supplier")
        if isinstance(raw_supplier, dict):
            if isinstance(merged["supplier"], dict):
                merged["supplier"].update(raw_supplier)
            else:
                merged["supplier"] = raw_supplier

        for raw_cat in delta.get("categories") or []:
            if isinstance(raw_cat, dict):
                cat_id = raw_cat.get("category_id")
                if cat_id:
                    merged["categories"][cat_id] = raw_cat

        for raw_item in delta.get("items") or []:
            if isinstance(raw_item, dict):
                item_id = raw_item.get("item_id")
                if item_id:
                    merged["items"][item_id] = raw_item

    @staticmethod
    def _source_ref_from_images(image_names: list[str]) -> str:
        """Return a single concrete page reference for a batch of page-image filenames.

        Image names follow the pattern ``{pdf_stem}-{page:03d}.{ext}`` produced by
        ``_split_pdf``.  Because per-item page attribution is unavailable, the middle
        image in the batch is used as the representative page — this gives a deterministic
        single reference rather than an unbounded list.  For direct image inputs (no
        three-digit page suffix) the raw filename is returned as-is.
        """
        if not image_names:
            return ""
        page_pattern = re.compile(r"^(.+)-(\d{3})\.[^.]+$")
        # Pick the middle image as the representative page.
        representative = image_names[len(image_names) // 2]
        m = page_pattern.match(representative)
        if not m:
            return representative
        stem, page_str = m.group(1), m.group(2)
        page_num = int(page_str)  # filename already contains 1-based page number
        return f"{stem}.pdf p.{page_num}"

    @staticmethod
    def _loads_resilient(raw: str) -> Optional[dict]:
        """Parse model JSON output, salvaging a truncated trailing object/array.

        Returns None only if nothing usable can be recovered.
        """
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # Salvage: trim to the last balanced closing brace of the top-level object.
        last_brace = raw.rfind("}")
        while last_brace != -1:
            candidate = raw[: last_brace + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                last_brace = raw.rfind("}", 0, last_brace)
        return None


    # ------------------------------------------------------------------
    # Message construction
    # ------------------------------------------------------------------

    def _build_messages(
        self, system_prompt: str, batch: list[Path], state_summary: str
    ) -> list[Message]:
        """Return the system + user Messages for a single batch."""
        contents: list[Content] = [
            Content.from_text(
                text=_TASK_PROMPT + "\n\nEXISTING STATE SUMMARY:\n" + state_summary
            )
        ]
        for img_path in batch:
            b64 = self._image_to_base64(img_path)
            mime = "image/jpeg" if img_path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
            contents.append(
                Content.from_uri(uri=f"data:{mime};base64,{b64}", media_type=mime)
            )
        return [
            Message(role="system", contents=[Content.from_text(text=system_prompt)]),
            Message(role="user", contents=contents),
        ]

    @staticmethod
    def _image_to_base64(image_path: Path) -> str:
        try:
            from PIL import Image  # type: ignore

            with Image.open(image_path) as img:
                if _MAX_IMAGE_DIMENSION > 0 and max(img.size) > _MAX_IMAGE_DIMENSION:
                    img.thumbnail(
                        (_MAX_IMAGE_DIMENSION, _MAX_IMAGE_DIMENSION),
                        Image.LANCZOS,
                    )
                buf = BytesIO()
                img.save(buf, format="PNG")
                return base64.b64encode(buf.getvalue()).decode("utf-8")
        except ImportError:
            # Fallback: read raw bytes without PIL
            return base64.b64encode(image_path.read_bytes()).decode("utf-8")

    # ------------------------------------------------------------------
    # Result parsing
    # ------------------------------------------------------------------

    def _parse_extraction_result(self, raw_json: str, job: JobInput) -> ExtractionResult:
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse LLM output as JSON: %s", exc)
            return ExtractionResult()

        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")
        safe_supplier = _safe_key(job.supplier_id)
        source_doc = ", ".join(job.sources)

        # -- Supplier --
        supplier: Optional[Supplier] = None
        doc_offer_validity: Optional[dict] = None
        raw_supplier = data.get("supplier")
        if raw_supplier and isinstance(raw_supplier, dict):
            raw_supplier.setdefault("supplier_id", job.supplier_id)
            raw_supplier.setdefault("brand", job.supplier_id)
            # Capture document-level validity before stripping it from the Supplier model
            ov = raw_supplier.pop("offer_validity", None)
            if isinstance(ov, dict) and (ov.get("start_date") or ov.get("end_date")):
                doc_offer_validity = ov
            # ingestion_metadata is no longer part of the Supplier model; discard if present
            raw_supplier.pop("ingestion_metadata", None)
            # If the LLM returned flat single-store fields, the Supplier model's
            # _coerce_legacy validator will wrap them into a locations entry.
            # We only pre-build a location dict when the LLM gave an explicit address block.
            if "address" in raw_supplier and isinstance(raw_supplier["address"], dict):
                addr = raw_supplier["address"]
                raw_supplier["address"] = {
                    "street": addr.get("street", ""),
                    "city": addr.get("city", ""),
                    "postal_code": addr.get("postal_code", ""),
                    "country": addr.get("country", "DE"),
                    "geo": addr.get("geo"),
                }
            try:
                supplier = Supplier.model_validate(raw_supplier)
            except Exception as exc:
                logger.warning(
                    "Could not parse Supplier %r (brand=%r):\n%s",
                    raw_supplier.get("supplier_id", ""),
                    raw_supplier.get("brand", ""),
                    _format_validation_error(exc),
                )

        # Fallback: if LLM returned nothing for supplier, create minimal stub
        if supplier is None:
            supplier = Supplier(
                supplier_id=job.supplier_id,
                brand=job.supplier_id,
            )

        # -- Categories --
        categories: list[Category] = []
        seen_cat_ids: set[str] = set()
        for raw_cat in data.get("categories") or []:
            if not isinstance(raw_cat, dict):
                continue
            cat_id = _safe_key(raw_cat.get("category_id", "") or raw_cat.get("name", ""))
            if not cat_id or cat_id in seen_cat_ids:
                continue
            raw_cat["category_id"] = cat_id
            raw_cat["id"] = cat_id  # prevent LLM-supplied unsanitised id from becoming the search key
            seen_cat_ids.add(cat_id)
            raw_cat.setdefault("description_text", raw_cat.get("name", cat_id))
            try:
                categories.append(Category.model_validate(raw_cat))
            except Exception as exc:
                logger.warning(
                    "Skipping invalid category %r (name=%r):\n%s",
                    cat_id,
                    raw_cat.get("name", ""),
                    _format_validation_error(exc),
                )

        # -- Items --
        items: list[Item] = []
        seen_item_ids: set[str] = set()
        for raw_item in data.get("items") or []:
            if not isinstance(raw_item, dict):
                continue
            # Build a canonical, key-safe item_id that always embeds supplier + date.
            # Take the slug portion from the LLM-generated id or fall back to the name.
            llm_id = _safe_key(raw_item.get("item_id", "") or raw_item.get("name", ""))
            # Strip a pre-existing supplier prefix so we don't double it up.
            if llm_id.startswith(safe_supplier + "-"):
                llm_id = llm_id[len(safe_supplier) + 1:]
            # Strip a pre-existing date prefix (YYYY-MM-DD) if the model already added it.
            llm_id = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", llm_id)
            item_id = f"{safe_supplier}-{date_str}-{llm_id}" if llm_id else f"{safe_supplier}-{date_str}-item"
            if not item_id or item_id in seen_item_ids:
                continue
            seen_item_ids.add(item_id)
            raw_item["item_id"] = item_id
            raw_item["id"] = item_id  # prevent LLM-supplied unsanitised id from becoming the search key
            raw_item["supplier_id"] = job.supplier_id
            batch_images: list[str] = raw_item.pop("_batch_images", None) or []
            raw_item["source_ref"] = (
                self._source_ref_from_images(batch_images)
                if batch_images
                else Path(job.sources[0]).name if job.sources else ""
            )
            raw_item.setdefault("description_text", raw_item.get("name", item_id))
            pricing = raw_item.get("pricing") or {}
            if not pricing.get("current_price"):
                pricing["current_price"] = 0.0
            raw_item["pricing"] = pricing
            # Apply document-level validity as default when item has no specific offer_validity
            item_ov = raw_item.get("offer_validity")
            if not (isinstance(item_ov, dict) and (item_ov.get("start_date") or item_ov.get("end_date"))):
                raw_item["offer_validity"] = doc_offer_validity
            # Ensure category reference exists; fall back to 'uncategorized'
            if raw_item.get("category_id") not in seen_cat_ids:
                raw_item["category_id"] = "uncategorized"
                if "uncategorized" not in seen_cat_ids:
                    seen_cat_ids.add("uncategorized")
                    categories.append(
                        Category(
                            category_id="uncategorized",
                            name="Uncategorized",
                            description_text="Items without an assigned category.",
                        )
                    )
            try:
                items.append(Item.model_validate(raw_item))
            except Exception as exc:
                logger.warning(
                    "Skipping invalid item %r (name=%r):\n%s",
                    item_id,
                    raw_item.get("name", ""),
                    _format_validation_error(exc),
                )

        return ExtractionResult(supplier=supplier, categories=categories, items=items)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def _finalise(
        self,
        result: ExtractionResult,
        push_to_search: bool,
        output_path: str | None,
    ) -> None:
        """Write JSON and/or push to Azure AI Search based on active flags."""
        if output_path:
            self._persist(result, Path(output_path))
        if push_to_search:
            await SearchIndexPusher().push(result)
        if not output_path and not push_to_search:
            logger.warning("No output target configured — result discarded.")

    @staticmethod
    def _persist(result: ExtractionResult, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "supplier": result.supplier.model_dump(mode="json") if result.supplier else None,
            "categories": [c.model_dump(mode="json") for c in result.categories],
            "items": [i.model_dump(mode="json") for i in result.items],
        }
        output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    # ------------------------------------------------------------------
    # Foundry chat client factory
    # ------------------------------------------------------------------

    def _build_client(self) -> FoundryChatClient:
        return _create_chat_client(self.model_name)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # Suppress verbose HTTP request/response logs from Azure SDK and httpx.
    for _noisy in (
        "azure.core.pipeline.policies.http_logging_policy",
        "azure.identity",
        "httpx",
    ):
        logging.getLogger(_noisy).setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(description="Extract structured retail data from flyer sources.")
    parser.add_argument("--supplier-id", required=True, help="Stable supplier identifier.")
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        required=True,
        metavar="URL_OR_PATH",
        help="URL or path to a PDF/image file. Repeat for multiple sources.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Output JSON file path. Defaults to 'data/extraction-result.json' when "
            "--push-to-search is not set. Pass an explicit path to always write JSON "
            "regardless of other flags."
        ),
    )
    parser.add_argument(
        "--push-to-search",
        action="store_true",
        default=False,
        help=(
            "Push extracted entities directly to Azure AI Search indexes "
            "(requires AZURE_SEARCH_ENDPOINT and related env vars)."
        ),
    )
    args = parser.parse_args()

    # Default to JSON output when push-to-search is not requested
    output_path: str | None = args.output
    if output_path is None and not args.push_to_search:
        output_path = "data/extraction-result.json"

    job = JobInput(supplier_id=args.supplier_id, sources=args.sources, output_path=output_path or "")
    processor = FlyerProcessor()
    try:
        asyncio.run(
            processor.process(job, push_to_search=args.push_to_search, output_path=output_path)
        )
    except KeyboardInterrupt:
        print("\nInterrupted by user.", flush=True)
        raise SystemExit(130)


if __name__ == "__main__":
    main()
