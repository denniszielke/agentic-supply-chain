# promotion_ingestion — Flyer Processor

## Overview

`promotion_ingestion` is a Python container job that downloads weekly retail promotional flyers (PDFs or images), extracts structured supplier, category, and product data using a GPT-4o vision model, and persists the results as JSON and/or pushes them directly into Azure AI Search indexes.

### Pipeline

1. **Materialise** — download HTTP/HTTPS sources or use local file paths into a configurable working directory
2. **Split** — convert each PDF page into a PNG image using PyMuPDF
3. **Extract** — run a sliding-window batch loop over the collected images, calling the Azure OpenAI vision model; each batch receives the accumulated extraction state and incrementally extends it
4. **Output** — write `{ supplier, categories, items }` as a JSON file, push documents to the three Azure AI Search indexes (`retail-suppliers`, `retail-categories`, `retail-items`), or both

The extraction is guided by the domain ontology in [`src/shared/ontology.json`](../shared/ontology.json).

## Running locally

```bash
# from repository root
python -m src.promotion_ingestion.processor \
    --supplier-id <supplier-id> \
    --source https://example.com/weekly-flyer.pdf \
    --source data/local-flyer.pdf \
    --output data/extraction-result.json
```

To push directly to Azure AI Search instead of (or in addition to) writing JSON:

```bash
python -m src.promotion_ingestion.processor \
    --supplier-id <supplier-id> \
    --source https://example.com/weekly-flyer.pdf \
    --push-to-search
```

Use both flags together to write JSON **and** index:

```bash
python -m src.promotion_ingestion.processor \
    --supplier-id <supplier-id> \
    --source https://example.com/weekly-flyer.pdf \
    --output data/extraction-result.json \
    --push-to-search
```

`--source` can be repeated for multiple PDFs or images. Supported formats: PDF, PNG, JPG, JPEG, WebP.

## Container build

```bash
docker build -t promotion-ingestion -f src/promotion_ingestion/Dockerfile .
docker run --env-file .env promotion-ingestion \
    --supplier-id <supplier-id> \
    --source https://example.com/flyer.pdf
```

Or use the shared build script:

```bash
./scripts/build_containers.sh "${AZURE_ENV_NAME}"
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `PROCESSING_WORK_DIR` | `/tmp/agentic-supply-chain` | Root directory for image artefacts |
| `PROCESSING_BATCH_SIZE` | `8` | Images per sliding-window batch |
| `PROCESSING_OVERLAP` | `2` | Overlapping images between batches |
| `AZURE_AI_PROJECT_ENDPOINT` | — | Azure AI Foundry project endpoint URL (required) |
| `AZURE_OPENAI_CHAT_DEPLOYMENT_NAME` | `gpt-4o` | Vision model deployment name |
| `OPENAI_API_VERSION` | `2025-01-01-preview` | Azure OpenAI API version |

### Search indexing (required for `--push-to-search`)

| Variable | Default | Description |
|---|---|---|
| `AZURE_SEARCH_ENDPOINT` | — | Azure AI Search service endpoint |
| `AZURE_SEARCH_ADMIN_KEY` | — | Admin API key (falls back to `DefaultAzureCredential` if absent) |
| `AZURE_SEARCH_SUPPLIER_INDEX_NAME` | `retail-suppliers` | Target supplier index |
| `AZURE_SEARCH_CATEGORY_INDEX_NAME` | `retail-categories` | Target category index |
| `AZURE_SEARCH_ITEM_INDEX_NAME` | `retail-items` | Target item index |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME` | — | Embedding model for category/item vectors (optional; skips vectors if unset) |

## Key files

| File | Purpose |
|---|---|
| `processor.py` | `FlyerProcessor` pipeline, `JobInput` / `ExtractionResult` models, CLI entry point |
| `Dockerfile` | Container image definition |
| `requirements.txt` | Python dependencies |
| `../shared/ontology.json` | Domain ontology used as LLM context |
| `../shared/models.py` | Pydantic models: `Supplier`, `Category`, `Item` and related types |
