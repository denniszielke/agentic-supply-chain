"""Migrate the retail-suppliers Azure AI Search index to the multi-location schema.

This script performs four steps in sequence:

  1. **Export** — Download every document from the current (flat, single-store)
     supplier index and write them to a local JSON file.
  2. **Convert** — Group the flat documents by brand and convert each group into
     a single new-format supplier document whose ``locations`` list contains one
     entry per old flat document.
  3. **Delete** — Drop the old index.
  4. **Recreate & Import** — Create the new index (with the ``locations``
     ComplexField schema) and upload the converted documents.

Usage
-----
    python scripts/migrate_supplier_index.py [OPTIONS]

Options
-------
  --export-file PATH     JSON file to write/read during the migration
                         (default: data/supplier_migration_export.json).
  --index-name NAME      Name of the supplier index
                         (default: AZURE_SEARCH_SUPPLIER_INDEX_NAME env var,
                         fallback: retail-suppliers).
  --export-only          Export old data to JSON and stop; skip delete/recreate.
  --import-only          Skip export/delete and only recreate + import from an
                         existing --export-file.
  --dry-run              Print what would happen without making any changes.

Environment variables required
-------------------------------
  AZURE_SEARCH_ENDPOINT   — e.g. https://<service>.search.windows.net
  AZURE_SEARCH_ADMIN_KEY  — admin API key (optional; falls back to
                            DefaultAzureCredential when not set)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from itertools import islice
from pathlib import Path
from typing import Dict, Iterator, List

from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from dotenv import load_dotenv

load_dotenv()

# Allow running from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.create_search_index import _build_supplier_fields, _hnsw_vector_search, _semantic_search  # noqa: E402
from azure.search.documents.indexes.models import SearchIndex  # noqa: E402

logger = logging.getLogger(__name__)

_UPLOAD_BATCH_SIZE = 100


# ---------------------------------------------------------------------------
# Azure credential helpers
# ---------------------------------------------------------------------------


def _get_credential():
    api_key = os.getenv("AZURE_SEARCH_ADMIN_KEY", "").strip()
    return AzureKeyCredential(api_key) if api_key else DefaultAzureCredential()


def _get_index_client() -> SearchIndexClient:
    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
    if not endpoint:
        raise RuntimeError("AZURE_SEARCH_ENDPOINT is required")
    return SearchIndexClient(endpoint=endpoint, credential=_get_credential())


def _get_search_client(index_name: str) -> SearchClient:
    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
    if not endpoint:
        raise RuntimeError("AZURE_SEARCH_ENDPOINT is required")
    return SearchClient(endpoint=endpoint, index_name=index_name, credential=_get_credential())


# ---------------------------------------------------------------------------
# Step 1 — Export
# ---------------------------------------------------------------------------


def export_old_index(index_name: str, export_file: Path, dry_run: bool = False) -> list[dict]:
    """Download all documents from *index_name* and write them to *export_file*.

    Returns the list of raw document dicts.
    """
    print(f"[export] Fetching all documents from index '{index_name}' …")
    if dry_run:
        print("[export] DRY RUN — no data will be downloaded.")
        return []

    client = _get_search_client(index_name)
    docs: list[dict] = []
    results = client.search(search_text="*", select="*", include_total_count=True)
    total = results.get_count()
    for doc in results:
        # Remove search-internal metadata keys
        clean = {k: v for k, v in doc.items() if not k.startswith("@search.")}
        docs.append(clean)

    export_file.parent.mkdir(parents=True, exist_ok=True)
    export_file.write_text(json.dumps(docs, indent=2, default=str), encoding="utf-8")
    print(f"[export] Exported {len(docs)} / {total} document(s) → {export_file}")
    return docs


# ---------------------------------------------------------------------------
# Step 2 — Convert old flat format → new multi-location format
# ---------------------------------------------------------------------------


def _old_doc_to_location(doc: dict) -> dict:
    """Extract the store-location fields from an old flat supplier document."""
    store_id = doc.get("id") or doc.get("supplier_id") or "store-unknown"
    return {
        "store_id": store_id,
        "store_name": doc.get("store_name") or doc.get("brand") or store_id,
        "region": doc.get("region"),
        "address_street": doc.get("address_street"),
        "address_city": doc.get("address_city"),
        "address_postal_code": doc.get("address_postal_code"),
        "address_country": doc.get("address_country", "DE"),
        "address_geo_lat": doc.get("address_geo_lat"),
        "address_geo_lon": doc.get("address_geo_lon"),
        "opening_hours": doc.get("opening_hours") or [],
        "contact_phone": doc.get("contact_phone"),
        "contact_website": doc.get("contact_website"),
    }


def convert_to_new_format(old_docs: List[dict]) -> List[Dict]:
    """Group old flat supplier documents by brand and merge into new-format docs.

    Multiple old documents that share the same ``brand`` value (e.g. several
    store flyers from the same chain) are merged into a single new-format
    supplier document with one ``StoreLocation`` per old document.

    The new document ``id`` is derived from the *first* old document's
    ``supplier_id`` for that brand group (or from the brand slug when no
    supplier_id is present).
    """
    # Group by brand (fall back to supplier_id, then id)
    groups: dict[str, list[dict]] = {}
    for doc in old_docs:
        brand = (doc.get("brand") or doc.get("supplier_id") or doc.get("id") or "unknown").strip()
        groups.setdefault(brand, []).append(doc)

    new_docs: list[dict] = []
    for brand, docs in groups.items():
        representative = docs[0]
        supplier_id = representative.get("supplier_id") or representative.get("id") or brand
        locations = [_old_doc_to_location(d) for d in docs]
        new_docs.append(
            {
                "id": supplier_id,
                "supplier_id": supplier_id,
                "brand": brand,
                "locations": locations,
            }
        )

    print(
        f"[convert] {len(old_docs)} old document(s) → "
        f"{len(new_docs)} new supplier document(s) "
        f"(with {sum(len(d['locations']) for d in new_docs)} total location(s))"
    )
    return new_docs


# ---------------------------------------------------------------------------
# Step 3 — Delete old index
# ---------------------------------------------------------------------------


def delete_index(index_name: str, dry_run: bool = False) -> None:
    """Delete *index_name* from Azure AI Search."""
    print(f"[delete] Deleting index '{index_name}' …")
    if dry_run:
        print("[delete] DRY RUN — index will not be deleted.")
        return

    client = _get_index_client()
    try:
        client.delete_index(index_name)
        print(f"[delete] Index '{index_name}' deleted.")
    except ResourceNotFoundError:
        print(f"[delete] Index '{index_name}' did not exist — nothing to delete.")


# ---------------------------------------------------------------------------
# Step 4a — Create new index
# ---------------------------------------------------------------------------


def create_new_index(index_name: str, dry_run: bool = False) -> None:
    """Create the supplier index with the new multi-location schema."""
    print(f"[create] Creating index '{index_name}' with multi-location schema …")
    if dry_run:
        print("[create] DRY RUN — index will not be created.")
        return

    index_client = _get_index_client()
    index = SearchIndex(
        name=index_name,
        fields=_build_supplier_fields(),
        semantic_search=_semantic_search(
            config_name="supplier-semantic",
            title_field="brand",
            content_fields=["supplier_id"],
            keyword_fields=["brand"],
        ),
    )
    result = index_client.create_or_update_index(index)
    print(f"[create] Index '{result.name}' created.")


# ---------------------------------------------------------------------------
# Step 4b — Import converted documents
# ---------------------------------------------------------------------------


def _batched(iterable, n: int) -> Iterator[list]:
    it = iter(iterable)
    while batch := list(islice(it, n)):
        yield batch


def import_new_documents(index_name: str, new_docs: List[Dict], dry_run: bool = False) -> None:
    """Upload *new_docs* to *index_name* in batches."""
    print(f"[import] Uploading {len(new_docs)} document(s) to '{index_name}' …")
    if dry_run:
        print("[import] DRY RUN — documents will not be uploaded.")
        return

    client = _get_search_client(index_name)
    uploaded = 0
    for batch in _batched(new_docs, _UPLOAD_BATCH_SIZE):
        result = client.merge_or_upload_documents(batch)
        succeeded = sum(1 for r in result if r.succeeded)
        uploaded += succeeded
        if succeeded < len(batch):
            failed = [r for r in result if not r.succeeded]
            for f in failed:
                logger.warning("[import] Failed to upload document key=%r: %s", f.key, f.error_message)

    print(f"[import] Upload complete — {uploaded} / {len(new_docs)} document(s) indexed.")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def migrate(
    index_name: str,
    export_file: Path,
    *,
    export_only: bool = False,
    import_only: bool = False,
    dry_run: bool = False,
) -> None:
    """Run the full migration pipeline."""
    if import_only:
        if not export_file.exists():
            raise FileNotFoundError(
                f"--import-only requires an existing export file, but '{export_file}' was not found."
            )
        print(f"[migrate] --import-only: loading documents from '{export_file}'")
        old_docs = json.loads(export_file.read_text(encoding="utf-8"))
        new_docs = convert_to_new_format(old_docs)
        delete_index(index_name, dry_run=dry_run)
        create_new_index(index_name, dry_run=dry_run)
        import_new_documents(index_name, new_docs, dry_run=dry_run)
        return

    # Step 1 — Export
    old_docs = export_old_index(index_name, export_file, dry_run=dry_run)
    if export_only:
        print("[migrate] --export-only: stopping after export.")
        return

    # Step 2 — Convert
    if dry_run:
        print("[convert] DRY RUN — conversion skipped.")
        new_docs: list[dict] = []
    else:
        old_docs_loaded = json.loads(export_file.read_text(encoding="utf-8"))
        new_docs = convert_to_new_format(old_docs_loaded)

    # Step 3 — Delete old index
    delete_index(index_name, dry_run=dry_run)

    # Step 4 — Recreate and import
    create_new_index(index_name, dry_run=dry_run)
    import_new_documents(index_name, new_docs, dry_run=dry_run)

    print("[migrate] Migration complete.")


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description="Migrate the retail-suppliers index to the multi-location schema."
    )
    parser.add_argument(
        "--export-file",
        default="data/supplier_migration_export.json",
        help="Path for the export JSON file (default: data/supplier_migration_export.json).",
    )
    parser.add_argument(
        "--index-name",
        default=None,
        help="Supplier index name (default: AZURE_SEARCH_SUPPLIER_INDEX_NAME env var or 'retail-suppliers').",
    )
    parser.add_argument(
        "--export-only",
        action="store_true",
        help="Export old data to JSON and stop; do not delete or recreate the index.",
    )
    parser.add_argument(
        "--import-only",
        action="store_true",
        help="Skip export; delete the old index and recreate + import from --export-file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without making any changes to Azure AI Search.",
    )
    args = parser.parse_args()

    if args.export_only and args.import_only:
        parser.error("--export-only and --import-only are mutually exclusive.")

    index_name = args.index_name or os.getenv("AZURE_SEARCH_SUPPLIER_INDEX_NAME", "retail-suppliers")
    export_file = Path(args.export_file)

    migrate(
        index_name=index_name,
        export_file=export_file,
        export_only=args.export_only,
        import_only=args.import_only,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
