"""Shopping Tour Agent — agent-framework edition.

Uses three Azure AI Search context providers:

  1. AGENTIC provider (supply-chain-kb knowledge base)
     Multi-hop reasoning for shopping plan creation and promotion search.
     knowledge_base_output_mode = "extractive_data"
     retrieval_reasoning_effort  = "medium"

  2. SEMANTIC provider — retail-items index
     Fast hybrid search for cross-retailer product price comparison.

  3. SEMANTIC provider — retail-categories index
     Fast hybrid search for category resolution and alternative products.

Environment variables:
  AZURE_SEARCH_ENDPOINT                   — required
  AZURE_SEARCH_ADMIN_KEY                  — optional; falls back to DefaultAzureCredential
  AZURE_SEARCH_KNOWLEDGE_BASE_NAME        — default: supply-chain-kb
  AZURE_SEARCH_ITEM_INDEX_NAME            — default: retail-items
  AZURE_SEARCH_CATEGORY_INDEX_NAME        — default: retail-categories
  AZURE_AI_PROJECT_ENDPOINT               — Azure AI Foundry project endpoint (required)
  AZURE_OPENAI_CHAT_DEPLOYMENT_NAME       — model deployment name
  AZURE_AI_MODEL_DEPLOYMENT_NAME          — fallback model name
  AZURE_OPENAI_ENDPOINT                   — Azure OpenAI endpoint for embeddings
  AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME  — embedding model for hybrid semantic search
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from agent_framework import Agent
from agent_framework.azure import AzureAISearchContextProvider
from agent_framework.foundry import FoundryChatClient
from agent_framework.openai import OpenAIEmbeddingClient
from azure.identity.aio import DefaultAzureCredential
from dotenv import load_dotenv

# Allow standalone execution from project root
_src_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_src_root))

_env_path = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(dotenv_path=_env_path if _env_path.exists() else None)

from src.shared.prompts import SHOPPING_TOUR_AGENT_INSTRUCTIONS  # noqa: E402

logging.basicConfig(level=logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_SEARCH_ENDPOINT = os.environ["AZURE_SEARCH_ENDPOINT"]
_SEARCH_API_KEY = os.getenv("AZURE_SEARCH_ADMIN_KEY", "").strip() or None

_KB_NAME = os.getenv("AZURE_SEARCH_KNOWLEDGE_BASE_NAME", "supply-chain-kb")
_ITEM_INDEX = os.getenv("AZURE_SEARCH_ITEM_INDEX_NAME", "retail-items")
_CATEGORY_INDEX = os.getenv("AZURE_SEARCH_CATEGORY_INDEX_NAME", "retail-categories")

_PROJECT_ENDPOINT = os.environ["AZURE_AI_PROJECT_ENDPOINT"]
_MODEL = (
    os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME")
    or os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")
    or "gpt-4.1-mini"
)
_AOAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
_EMBEDDING_MODEL = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME", "").strip()

SYSTEM_PROMPT = SHOPPING_TOUR_AGENT_INSTRUCTIONS


# ---------------------------------------------------------------------------
# Semantic provider with full-field extraction
# ---------------------------------------------------------------------------

class _FlatFieldContextProvider(AzureAISearchContextProvider):
    """Semantic provider that surfaces every flat scalar field, not just strings.

    The base ``AzureAISearchContextProvider._extract_document_text`` keeps only
    ``str`` fields, which silently drops the numeric pricing/packaging fields
    (``pricing_current_price``, ``packaging_quantity``, ``conditions_deposit``,
    ``offer_validity_*`` …). The data model is fully flat, so we override the
    extraction to include all non-embedding, non-metadata fields as
    ``key: value`` pairs.
    """

    _SKIP_FIELDS = frozenset({"embedding"})

    def _extract_document_text(self, doc: dict, doc_id: str | None = None) -> str:  # type: ignore[override]
        parts: list[str] = []
        for key, value in doc.items():
            if key.startswith("@") or key in self._SKIP_FIELDS or value is None:
                continue
            parts.append(f"{key}: {value}")
        text = " | ".join(parts)
        if doc_id and text:
            return f"[Source: {doc_id}] {text}"
        return text


# ---------------------------------------------------------------------------
# Context provider factories
# ---------------------------------------------------------------------------

def _make_embedding_client(credential: DefaultAzureCredential) -> OpenAIEmbeddingClient | None:
    """Return an embedding client for hybrid search, or None if not configured."""
    if _AOAI_ENDPOINT and _EMBEDDING_MODEL:
        return OpenAIEmbeddingClient(
            azure_endpoint=_AOAI_ENDPOINT,
            model=_EMBEDDING_MODEL,
            credential=credential,
        )
    return None


def _make_agentic_provider(credential: DefaultAzureCredential) -> AzureAISearchContextProvider:
    """Agentic KB provider — multi-hop reasoning across all three indexes.

    Uses extractive_data output mode so the agent receives raw field values
    (prices, dates, IDs) rather than synthesised prose, making it easier to
    run numerical comparisons and build the shopping plan.
    Effort is set to medium for a good accuracy/latency balance.
    """
    return AzureAISearchContextProvider(
        source_id="kb_promotions",
        endpoint=_SEARCH_ENDPOINT,
        api_key=_SEARCH_API_KEY,
        credential=credential if not _SEARCH_API_KEY else None,
        mode="agentic",
        knowledge_base_name=_KB_NAME,
        knowledge_base_output_mode="extractive_data",
        retrieval_reasoning_effort="medium",
    )


def _make_semantic_item_provider(
    credential: DefaultAzureCredential,
    embedding_client: OpenAIEmbeddingClient | None,
) -> AzureAISearchContextProvider:
    """Semantic provider for retail-items — fast cross-retailer product comparison."""
    return _FlatFieldContextProvider(
        source_id="semantic_items",
        endpoint=_SEARCH_ENDPOINT,
        index_name=_ITEM_INDEX,
        api_key=_SEARCH_API_KEY,
        credential=credential if not _SEARCH_API_KEY else None,
        mode="semantic",
        top_k=20,
        embedding_function=embedding_client,
        vector_field_name="embedding" if embedding_client else None,
    )


def _make_semantic_category_provider(
    credential: DefaultAzureCredential,
    embedding_client: OpenAIEmbeddingClient | None,
) -> AzureAISearchContextProvider:
    """Semantic provider for retail-categories — category resolution and alternatives."""
    return _FlatFieldContextProvider(
        source_id="semantic_categories",
        endpoint=_SEARCH_ENDPOINT,
        index_name=_CATEGORY_INDEX,
        api_key=_SEARCH_API_KEY,
        credential=credential if not _SEARCH_API_KEY else None,
        mode="semantic",
        top_k=20,
        embedding_function=embedding_client,
        vector_field_name="embedding" if embedding_client else None,
    )


# ---------------------------------------------------------------------------
# Agent runner
# ---------------------------------------------------------------------------

def make_providers(
    credential: DefaultAzureCredential,
) -> tuple[
    AzureAISearchContextProvider,
    AzureAISearchContextProvider,
    AzureAISearchContextProvider,
    OpenAIEmbeddingClient | None,
]:
    """Build the three Azure AI Search context providers and the embedding client.

    Returns ``(kb_provider, item_provider, category_provider, embedding_client)``.
    The providers are async context managers — the caller is responsible for
    entering and exiting them (e.g. via ``async with`` or an ``AsyncExitStack``).
    """
    embedding_client = _make_embedding_client(credential)
    kb_provider = _make_agentic_provider(credential)
    item_provider = _make_semantic_item_provider(credential, embedding_client)
    category_provider = _make_semantic_category_provider(credential, embedding_client)
    return kb_provider, item_provider, category_provider, embedding_client


async def run_agent(user_input: str, stream: bool = True) -> str:
    """Run one agent turn and return the complete response text.

    Three context providers are active simultaneously:
    - kb_promotions   (agentic, medium effort, extractive_data) — KB planning
    - semantic_items  (semantic, hybrid) — product price comparison
    - semantic_categories (semantic, hybrid) — category / alternative lookup
    """
    credential = DefaultAzureCredential()
    kb_provider, item_provider, category_provider, embedding_client = make_providers(credential)

    try:
        async with (
            kb_provider,
            item_provider,
            category_provider,
            Agent(
                client=FoundryChatClient(
                    project_endpoint=_PROJECT_ENDPOINT,
                    model=_MODEL,
                    credential=credential,
                ),
                name="ShoppingTourAgent",
                instructions=SYSTEM_PROMPT,
                context_providers=[kb_provider, item_provider, category_provider],
            ) as agent,
        ):
            chunks: list[str] = []
            async for chunk in agent.run(user_input, stream=stream):
                if chunk.text:
                    chunks.append(chunk.text)
                    if stream:
                        print(chunk.text, end="", flush=True)
                for content in chunk.contents:
                    if getattr(content, "annotations", None):
                        logger.debug("Sources: %s", content.annotations)
            return "".join(chunks)
    finally:
        # Close async transports to avoid "Unclosed client session" warnings.
        close = getattr(embedding_client, "close", None)
        if close is not None:
            result = close()
            if asyncio.iscoroutine(result):
                await result
        await credential.close()


# ---------------------------------------------------------------------------
# Interactive / hosted entry point
# ---------------------------------------------------------------------------

async def _interactive() -> None:
    print("Shopping Tour Agent  —  type your shopping list, or 'quit' to exit.\n")
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_input or user_input.lower() in {"quit", "exit"}:
            break
        print("Agent: ", end="", flush=True)
        await run_agent(user_input, stream=True)
        print()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Shopping Tour Agent")
    parser.add_argument("--query", default="", help="Single non-interactive query")
    args = parser.parse_args()

    if args.query:
        async def _once() -> None:
            print("Agent: ", end="", flush=True)
            await run_agent(args.query, stream=True)
            print()
        asyncio.run(_once())
    else:
        asyncio.run(_interactive())

