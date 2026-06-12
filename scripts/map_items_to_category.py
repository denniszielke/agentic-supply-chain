"""Map uncategorized items to the best-matching category via vector search.

For every item in the retail-items index whose category_id is "uncategorized"
the script:
  1. Generates an embedding from the item's description_text (or name as fallback).
  2. Runs a nearest-neighbour vector search against the retail-categories index.
  3. Patches the item document with the winning category_id (and optionally the
     category name) when the best score meets the confidence threshold.
  4. Reports items that remain uncategorized after the search.

Environment variables (same as processor.py / create_category_items.py):
  AZURE_SEARCH_ENDPOINT                  — required
  AZURE_SEARCH_ADMIN_KEY                 — optional; falls back to DefaultAzureCredential
  AZURE_SEARCH_ITEM_INDEX_NAME           — default: retail-items
  AZURE_SEARCH_CATEGORY_INDEX_NAME       — default: retail-categories
  AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME — required for vector embedding
  FOUNDRY_MODELS_ENDPOINT                — optional; derived from AZURE_AI_PROJECT_ENDPOINT
  FOUNDRY_MODELS_API_KEY                 — optional; falls back to DefaultAzureCredential
  AZURE_AI_PROJECT_ENDPOINT              — used to derive FOUNDRY_MODELS_ENDPOINT

Usage:
  python scripts/map_items_to_category.py
  python scripts/map_items_to_category.py --dry-run          # print mappings, no update
  python scripts/map_items_to_category.py --threshold 0.85   # custom confidence threshold
  python scripts/map_items_to_category.py --batch-size 50    # items fetched per page
"""
from __future__ import annotations

import asyncio
import logging
import os
from urllib.parse import urlparse

from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_THRESHOLD = 0.82
_DEFAULT_BATCH_SIZE = 100
_DEFAULT_ITEM_INDEX = "retail-items"
_DEFAULT_CATEGORY_INDEX = "retail-categories"

# Number of top category candidates to retrieve per item (only the best is used,
# but logging shows the runner-up for transparency).
_TOP_K = 3

# Maximum embedding batch size to stay within API limits.
_EMBED_BATCH_SIZE = 256
_EMBED_MAX_RETRIES = 3
_EMBED_INITIAL_BACKOFF = 1.0


# ---------------------------------------------------------------------------
# Credential helper
# ---------------------------------------------------------------------------

class _ScopedCredential:
    """Wraps a TokenCredential to always request the Cognitive Services scope."""
    _SCOPE = "https://cognitiveservices.azure.com/.default"

    def __init__(self, inner) -> None:
        self._inner = inner

    def get_token(self, *_scopes, **kwargs):
        return self._inner.get_token(self._SCOPE, **kwargs)


# ---------------------------------------------------------------------------
# Search client factory
# ---------------------------------------------------------------------------

def _get_search_client(index_name: str) -> SearchClient:
    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
    if not endpoint:
        raise RuntimeError("AZURE_SEARCH_ENDPOINT is required")
    api_key = os.getenv("AZURE_SEARCH_ADMIN_KEY", "").strip()
    credential = AzureKeyCredential(api_key) if api_key else DefaultAzureCredential()
    return SearchClient(endpoint=endpoint, index_name=index_name, credential=credential)


# ---------------------------------------------------------------------------
# Embedding client + helper (mirrors create_category_items.py)
# ---------------------------------------------------------------------------

def _create_embedding_client(model: str):
    from agent_framework.foundry import FoundryEmbeddingClient  # type: ignore

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
        endpoint, model,
        "api_key" if api_key else "DefaultAzureCredential",
    )
    if api_key:
        return FoundryEmbeddingClient(model=model, endpoint=endpoint, api_key=api_key)
    return FoundryEmbeddingClient(
        model=model,
        endpoint=endpoint,
        credential=_ScopedCredential(DefaultAzureCredential()),
    )


async def _embed(texts: list[str], model: str) -> list[list[float]] | None:
    """Generate embeddings in batches with retry on rate-limit / transient errors."""
    if not model or not texts:
        return None

    client = _create_embedding_client(model)
    resolved_endpoint = (
        os.getenv("FOUNDRY_MODELS_ENDPOINT")
        or "(derived from AZURE_AI_PROJECT_ENDPOINT)"
    )

    async def _call_batch(batch: list[str]) -> list[list[float]]:
        backoff = _EMBED_INITIAL_BACKOFF
        for attempt in range(1, _EMBED_MAX_RETRIES + 1):
            try:
                resp = await client.get_embeddings(batch)
                return [item.vector for item in resp]
            except HttpResponseError as exc:
                status = exc.status_code
                if status == 404:
                    logger.error(
                        "Embedding 404 — resource not found (config error, not retrying).\n"
                        "  endpoint=%r  model=%r\n  Detail: %s",
                        resolved_endpoint, model, exc,
                    )
                    raise
                if status == 429 or (status is not None and status >= 500):
                    if attempt < _EMBED_MAX_RETRIES:
                        retry_after = float(getattr(exc, "retry_after", None) or backoff)
                        logger.warning(
                            "Embedding HTTP %s on attempt %d/%d — retrying in %.1fs.",
                            status, attempt, _EMBED_MAX_RETRIES, retry_after,
                        )
                        await asyncio.sleep(retry_after)
                        backoff *= 2
                        continue
                    logger.warning(
                        "Embedding HTTP %s — exhausted %d retries, skipping batch.",
                        status, _EMBED_MAX_RETRIES,
                    )
                    raise
                logger.warning("Embedding HTTP %s (not retrying). Detail: %s", status, exc)
                raise
        raise RuntimeError("unreachable")

    try:
        results: list[list[float]] = []
        for start in range(0, len(texts), _EMBED_BATCH_SIZE):
            batch = texts[start: start + _EMBED_BATCH_SIZE]
            results.extend(await _call_batch(batch))
        return results
    except Exception as exc:
        logger.warning(
            "Embedding generation failed: %s\n  endpoint=%r  model=%r",
            exc, resolved_endpoint, model,
        )
        return None
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Fetch uncategorized items
# ---------------------------------------------------------------------------

def _fetch_uncategorized_items(
    client: SearchClient, batch_size: int
) -> list[dict]:
    """Return all items with category_id == 'uncategorized' from the index."""
    items: list[dict] = []
    skip = 0
    while True:
        results = list(
            client.search(
                search_text="*",
                filter="category_id eq 'uncategorized'",
                select=["id", "item_id", "name", "description_text", "category_id", "supplier_id"],
                top=batch_size,
                skip=skip,
            )
        )
        if not results:
            break
        items.extend(results)
        if len(results) < batch_size:
            break
        skip += batch_size
    return items


# ---------------------------------------------------------------------------
# Vector search for best category match
# ---------------------------------------------------------------------------

def _find_best_category(
    vector: list[float],
    category_client: SearchClient,
    top_k: int = _TOP_K,
) -> list[dict]:
    """Return the top-k category hits for *vector* from the category index."""
    vq = VectorizedQuery(
        vector=vector,
        k_nearest_neighbors=top_k,
        fields="embedding",
    )
    return list(
        category_client.search(
            search_text=None,
            vector_queries=[vq],
            select=["category_id", "name"],
            top=top_k,
        )
    )


# ---------------------------------------------------------------------------
# Core mapping logic
# ---------------------------------------------------------------------------

async def map_items(
    dry_run: bool = False,
    threshold: float = _DEFAULT_THRESHOLD,
    batch_size: int = _DEFAULT_BATCH_SIZE,
) -> None:
    item_index = os.getenv("AZURE_SEARCH_ITEM_INDEX_NAME", _DEFAULT_ITEM_INDEX)
    category_index = os.getenv("AZURE_SEARCH_CATEGORY_INDEX_NAME", _DEFAULT_CATEGORY_INDEX)
    embedding_model = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME", "").strip()

    if not embedding_model:
        raise RuntimeError(
            "AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME is required for vector-based category mapping."
        )

    item_client = _get_search_client(item_index)
    category_client = _get_search_client(category_index)

    # 1. Fetch all uncategorized items
    print(f"Fetching uncategorized items from '{item_index}' …")
    items = _fetch_uncategorized_items(item_client, batch_size)
    if not items:
        print("No uncategorized items found — nothing to do.")
        return
    print(f"Found {len(items)} uncategorized item(s).")

    # 2. Build embedding texts (prefer description_text, fall back to name)
    texts = [
        (item.get("description_text") or "").strip() or item.get("name", item.get("id", ""))
        for item in items
    ]

    # 3. Generate embeddings
    print(f"Generating embeddings via model '{embedding_model}' …")
    vectors = await _embed(texts, embedding_model)
    if vectors is None:
        raise RuntimeError("Embedding generation failed — cannot continue.")
    print(f"Embeddings generated for {len(vectors)} item(s).")

    # 4. For each item, search for best matching category
    mapped: list[dict] = []       # items successfully mapped above threshold
    unmapped: list[dict] = []     # items whose best score is below threshold

    for item, vector in zip(items, vectors):
        try:
            hits = _find_best_category(vector, category_client)
        except Exception as exc:
            logger.warning("Category search failed for item '%s': %s", item.get("id"), exc)
            unmapped.append(item)
            continue

        if not hits:
            logger.warning("No category candidates returned for item '%s'.", item.get("id"))
            unmapped.append(item)
            continue

        best = hits[0]
        best_score = best.get("@search.score", 0.0)
        best_cat_id = best.get("category_id", "")
        best_cat_name = best.get("name", "")

        # Log runner-up for transparency
        runner_up_info = ""
        if len(hits) > 1:
            runner_up = hits[1]
            runner_up_info = (
                f"  runner-up: '{runner_up.get('category_id')}' "
                f"(score={runner_up.get('@search.score', 0.0):.4f})"
            )

        if best_score >= threshold:
            logger.info(
                "MAPPED  '%s' → '%s' (score=%.4f)%s",
                item.get("name", item.get("id")),
                best_cat_id,
                best_score,
                f"\n{runner_up_info}" if runner_up_info else "",
            )
            mapped.append({
                "id": item["id"],
                "item_name": item.get("name", ""),
                "category_id": best_cat_id,
                "category_name": best_cat_name,
                "score": best_score,
            })
        else:
            logger.info(
                "BELOW THRESHOLD  '%s' — best: '%s' (score=%.4f < %.4f)%s",
                item.get("name", item.get("id")),
                best_cat_id,
                best_score,
                threshold,
                f"\n{runner_up_info}" if runner_up_info else "",
            )
            unmapped.append(item)

    # 5. Report summary
    print(f"\n── Mapping results ──────────────────────────────")
    print(f"  Mapped (above threshold {threshold}): {len(mapped)}")
    print(f"  Unmapped (below threshold):           {len(unmapped)}")

    if dry_run:
        print("\n── DRY RUN — proposed mappings (not written) ──")
        for entry in mapped:
            print(
                f"  [{entry['score']:.4f}]  {entry['item_name']!r:50s}  →  {entry['category_id']}"
            )
        if unmapped:
            print("\n── Items that would remain uncategorized ──")
            for item in unmapped:
                print(f"  {item.get('name', item.get('id'))!r}")
        return

    # 6. Update items in the search index
    if not mapped:
        print("Nothing to update.")
        return

    print(f"\nUpdating {len(mapped)} item(s) in '{item_index}' …")
    patch_docs = [
        {"id": entry["id"], "category_id": entry["category_id"]}
        for entry in mapped
    ]

    # merge_or_upload_documents in batches of 1000 (Azure Search limit)
    _UPLOAD_BATCH = 1000
    succeeded_total = 0
    failed_total = 0
    for start in range(0, len(patch_docs), _UPLOAD_BATCH):
        batch = patch_docs[start: start + _UPLOAD_BATCH]
        result = item_client.merge_or_upload_documents(batch)
        succeeded = sum(1 for r in result if r.succeeded)
        failed = len(result) - succeeded
        succeeded_total += succeeded
        failed_total += failed
        if failed:
            for r in result:
                if not r.succeeded:
                    logger.error(
                        "Failed to update item '%s': %s", r.key, r.error_message
                    )

    print(f"Update complete: {succeeded_total} succeeded, {failed_total} failed.")

    if unmapped:
        print(f"\n── {len(unmapped)} item(s) remain uncategorized ──")
        for item in unmapped:
            print(f"  {item.get('name', item.get('id'))!r}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    for _noisy in (
        "azure.core.pipeline.policies.http_logging_policy",
        "azure.identity",
        "httpx",
    ):
        logging.getLogger(_noisy).setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(
        description=(
            "Map items with category_id='uncategorized' to the best-matching "
            "category using vector similarity search."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print proposed mappings to stdout without updating the index.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=_DEFAULT_THRESHOLD,
        metavar="SCORE",
        help=(
            f"Minimum cosine similarity score to accept a category match "
            f"(default: {_DEFAULT_THRESHOLD})."
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=_DEFAULT_BATCH_SIZE,
        metavar="N",
        help=f"Number of items fetched per search page (default: {_DEFAULT_BATCH_SIZE}).",
    )
    args = parser.parse_args()

    try:
        asyncio.run(
            map_items(
                dry_run=args.dry_run,
                threshold=args.threshold,
                batch_size=args.batch_size,
            )
        )
    except KeyboardInterrupt:
        print("\nInterrupted by user.", flush=True)
        raise SystemExit(130)


if __name__ == "__main__":
    main()
