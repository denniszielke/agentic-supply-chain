"""
Retail flyer processor.

Pipeline
--------
1. Materialise each source (URL or local path) to a local working directory.
2. Split PDF files into per-page PNG images using PyMuPDF.
3. Run a sliding-window batch loop over the collected images, calling an Azure
   OpenAI vision model to incrementally extract supplier, category, and item
   data.
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
"""

from __future__ import annotations

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

import requests
from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.search.documents import SearchClient
from dotenv import load_dotenv
from openai import AzureOpenAI
from pydantic import BaseModel, Field

from src.shared.models import (
    Address,
    Attributes,
    Category,
    Conditions,
    Contact,
    IngestionMetadata,
    Item,
    OfferValidity,
    OpeningHour,
    Packaging,
    Pricing,
    Promotion,
    Supplier,
)

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------

_DEFAULT_WORK_DIR = "data/tempfiles"
_DEFAULT_BATCH_SIZE = 8
_DEFAULT_OVERLAP = 2
_DEFAULT_MODEL = "gpt-5.4-mini"

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
promotional flyer page images together with the CURRENT extraction state (JSON).

ONTOLOGY REFERENCE:
{ontology}

YOUR TASK:
Analyse ONLY the newly provided images and extend the current extraction state.

OUTPUT RULES:
- Return ONLY a single valid JSON object matching this exact structure:
  {{
    "supplier": {{ ... }},       // exactly one supplier object (or null if not yet determined)
    "categories": [ ... ],       // deduplicated list of all Category objects seen so far
    "items": [ ... ]             // deduplicated list of all Item objects seen so far
  }}
- Preserve ALL existing entries; only ADD or UPDATE (never delete) based on new images.
- Assign stable item_id values using the pattern: "{{supplier_id}}-{{slug}}-{{index}}"
  where slug is a lowercase-hyphenated product name and index is a counter.
- All category_id values must be lowercase, hyphen-separated slugs.
- Every item's category_id must reference a category_id in the "categories" array.
- Use null for optional fields that cannot be extracted from the images.
- Do NOT wrap the JSON in markdown fences.
- Do NOT include ingestion_metadata in your output; it is added by the processor.
""".strip()

_TASK_PROMPT = """
INSTRUCTIONS FOR THIS BATCH:
1. Parse the supplied current extraction state (first text block).
2. Analyse the newly provided images for additional or updated content.
3. Return the COMPLETE UPDATED extraction state as JSON (no fences, no comments).
""".strip()


# ---------------------------------------------------------------------------
# OpenAI client factory
# ---------------------------------------------------------------------------


def _create_openai_client() -> AzureOpenAI:
    """Return an Azure OpenAI client.

    Priority:
    1. ``AZURE_AI_PROJECT_ENDPOINT`` — derives the Azure AI account root URL and
       uses the ``https://ai.azure.com/.default`` token scope.
    2. ``AZURE_OPENAI_ENDPOINT`` — uses the direct Azure OpenAI endpoint with
       the ``https://cognitiveservices.azure.com/.default`` scope.
    ``AZURE_OPENAI_API_KEY`` is used instead of DefaultAzureCredential when set.
    """
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "").strip() or None
    api_version = os.getenv("OPENAI_API_VERSION", "2025-01-01-preview")

    project_endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "").strip()
    if project_endpoint:
        # Derive account root (scheme + host) from the project endpoint so that
        # AzureOpenAI can construct the standard /openai/ base URL correctly.
        # e.g. https://ai-account-xxx.services.ai.azure.com/api/projects/name
        #   -> https://ai-account-xxx.services.ai.azure.com
        parsed = urlparse(project_endpoint)
        account_root = f"{parsed.scheme}://{parsed.netloc}"
        if api_key:
            return AzureOpenAI(
                azure_endpoint=account_root, api_key=api_key, api_version=api_version
            )
        token_provider = get_bearer_token_provider(
            DefaultAzureCredential(), "https://ai.azure.com/.default"
        )
        return AzureOpenAI(
            azure_endpoint=account_root,
            azure_ad_token_provider=token_provider,
            api_version=api_version,
        )

    direct_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
    if direct_endpoint:
        if api_key:
            return AzureOpenAI(
                azure_endpoint=direct_endpoint, api_key=api_key, api_version=api_version
            )
        token_provider = get_bearer_token_provider(
            DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
        )
        return AzureOpenAI(
            azure_endpoint=direct_endpoint,
            azure_ad_token_provider=token_provider,
            api_version=api_version,
        )

    raise RuntimeError(
        "No Azure OpenAI endpoint configured. "
        "Set AZURE_AI_PROJECT_ENDPOINT or AZURE_OPENAI_ENDPOINT."
    )


# ---------------------------------------------------------------------------
# Search index pusher
# ---------------------------------------------------------------------------

_EMBEDDING_BATCH_SIZE = 256  # Azure OpenAI embeddings API limit per request


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
        self._openai_client = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def push(self, result: ExtractionResult) -> None:
        """Upload all entities in *result* to their respective search indexes."""
        if result.supplier:
            doc = result.supplier.model_dump(mode="json")
            self._supplier_client.merge_or_upload_documents([doc])
            logger.info("Indexed supplier '%s'.", result.supplier.supplier_id)

        if result.categories:
            embeddings = self._embed([c.description_text or c.name for c in result.categories])
            cat_docs = []
            for i, cat in enumerate(result.categories):
                doc = cat.model_dump(mode="json")
                if embeddings:
                    doc["embedding"] = embeddings[i]
                cat_docs.append(doc)
            self._category_client.merge_or_upload_documents(cat_docs)
            logger.info("Indexed %d categories.", len(cat_docs))

        if result.items:
            embeddings = self._embed([it.description_text or it.name for it in result.items])
            item_docs = []
            for i, item in enumerate(result.items):
                doc = item.model_dump(mode="json")
                if embeddings:
                    doc["embedding"] = embeddings[i]
                item_docs.append(doc)
            self._item_client.merge_or_upload_documents(item_docs)
            logger.info("Indexed %d items.", len(item_docs))

        print(
            f"Indexed to AI Search: 1 supplier, {len(result.categories)} categories, "
            f"{len(result.items)} items."
        )

    # ------------------------------------------------------------------
    # Embedding helpers
    # ------------------------------------------------------------------

    def _get_openai_client(self):
        if self._openai_client is None:
            self._openai_client = _create_openai_client()
        return self._openai_client

    def _embed(self, texts: list[str]) -> list[list[float]] | None:
        """Generate embeddings in batches; returns None if no model is configured."""
        if not self._embedding_model or not texts:
            return None
        try:
            client = self._get_openai_client()
            results: list[list[float]] = []
            for start in range(0, len(texts), _EMBEDDING_BATCH_SIZE):
                batch = texts[start: start + _EMBEDDING_BATCH_SIZE]
                resp = client.embeddings.create(model=self._embedding_model, input=batch)
                results.extend(item.embedding for item in resp.data)
            return results
        except Exception as exc:
            logger.warning("Embedding generation failed, skipping vectors: %s", exc)
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

    def process(self, job: JobInput, push_to_search: bool = False, output_path: str | None = "data/extraction-result.json") -> ExtractionResult:
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
        result = self._extract_from_batches(image_files, job)

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
        self._finalise(result, push_to_search, output_path)
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
                out_path = dest_dir / f"{stem}-{page.number:03d}.png"
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

    def _extract_from_batches(self, image_files: list[Path], job: JobInput) -> ExtractionResult:
        """Drive the sliding-window extraction and return a merged result."""
        current_state = json.dumps({"supplier": None, "categories": [], "items": []})
        system_prompt = _SYSTEM_PROMPT.format(ontology=self._ontology_summary)

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
            messages = self._build_messages(system_prompt, batch, current_state)
            response = self._client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=0,
                max_tokens=16384,
            )
            raw = response.choices[0].message.content or ""
            raw = raw.replace("```json", "").replace("```", "").strip()
            current_state = raw
            logger.info("Batch %d processed.", batch_idx + 1)

        return self._parse_extraction_result(current_state, job)

    # ------------------------------------------------------------------
    # Message construction
    # ------------------------------------------------------------------

    def _build_messages(
        self, system_prompt: str, batch: list[Path], current_state: str
    ) -> list[dict]:
        content_blocks: list[dict] = [
            {"type": "text", "text": _TASK_PROMPT + "\n\nCURRENT STATE:\n" + current_state}
        ]
        for img_path in batch:
            b64 = self._image_to_base64(img_path)
            mime = "image/jpeg" if img_path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
            content_blocks.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "low"},
                }
            )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content_blocks},
        ]

    @staticmethod
    def _image_to_base64(image_path: Path) -> str:
        try:
            from PIL import Image  # type: ignore

            with Image.open(image_path) as img:
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
        source_doc = ", ".join(job.sources)

        # -- Supplier --
        supplier: Optional[Supplier] = None
        raw_supplier = data.get("supplier")
        if raw_supplier and isinstance(raw_supplier, dict):
            raw_supplier.setdefault("supplier_id", job.supplier_id)
            raw_supplier.setdefault("brand", job.supplier_id)
            raw_supplier.setdefault("store_name", raw_supplier.get("brand", job.supplier_id))
            raw_supplier.setdefault("address", {})
            raw_supplier["ingestion_metadata"] = {
                "source_document": source_doc,
                "ingestion_timestamp": now.isoformat(),
            }
            addr = raw_supplier.get("address") or {}
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
                logger.warning("Could not parse Supplier: %s", exc)

        # Fallback: if LLM returned nothing for supplier, create minimal stub
        if supplier is None:
            supplier = Supplier(
                supplier_id=job.supplier_id,
                brand=job.supplier_id,
                store_name=job.supplier_id,
                address=Address(street="", city="", postal_code=""),
                ingestion_metadata=IngestionMetadata(
                    source_document=source_doc, ingestion_timestamp=now
                ),
            )

        # -- Categories --
        categories: list[Category] = []
        seen_cat_ids: set[str] = set()
        for raw_cat in data.get("categories") or []:
            if not isinstance(raw_cat, dict):
                continue
            cat_id = raw_cat.get("category_id", "")
            if not cat_id or cat_id in seen_cat_ids:
                continue
            seen_cat_ids.add(cat_id)
            raw_cat.setdefault("description_text", raw_cat.get("name", cat_id))
            try:
                categories.append(Category.model_validate(raw_cat))
            except Exception as exc:
                logger.warning("Skipping invalid category %r: %s", cat_id, exc)

        # -- Items --
        items: list[Item] = []
        seen_item_ids: set[str] = set()
        for raw_item in data.get("items") or []:
            if not isinstance(raw_item, dict):
                continue
            item_id = raw_item.get("item_id", "")
            if not item_id or item_id in seen_item_ids:
                continue
            seen_item_ids.add(item_id)
            raw_item["supplier_id"] = job.supplier_id
            raw_item.setdefault("description_text", raw_item.get("name", item_id))
            pricing = raw_item.get("pricing") or {}
            if not pricing.get("current_price"):
                pricing["current_price"] = 0.0
            raw_item["pricing"] = pricing
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
                logger.warning("Skipping invalid item %r: %s", item_id, exc)

        return ExtractionResult(supplier=supplier, categories=categories, items=items)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _finalise(
        self,
        result: ExtractionResult,
        push_to_search: bool,
        output_path: str | None,
    ) -> None:
        """Write JSON and/or push to Azure AI Search based on active flags."""
        if output_path:
            self._persist(result, Path(output_path))
        if push_to_search:
            SearchIndexPusher().push(result)
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
    # Azure OpenAI client factory
    # ------------------------------------------------------------------

    def _build_client(self):
        return _create_openai_client()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

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
    processor.process(job, push_to_search=args.push_to_search, output_path=output_path)


if __name__ == "__main__":
    main()
