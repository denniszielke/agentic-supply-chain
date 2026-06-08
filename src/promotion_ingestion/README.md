# promotion_ingestion — Flyer Processor

## Overview

`promotion_ingestion` is a Python container job that downloads weekly retail promotional flyers (PDFs or images), extracts structured supplier, category, and product data using a GPT-5 vision model, and persists the results as JSON ready for indexing into Azure AI Search.

### Pipeline

1. **Materialise** — download HTTP/HTTPS sources or use local file paths into a configurable working directory
2. **Split** — convert each PDF page into a PNG image using PyMuPDF
3. **Extract** — run a sliding-window batch loop over the collected images, calling the Azure OpenAI vision model; each batch receives the accumulated extraction state and incrementally extends it
4. **Persist** — write `{ supplier, categories, items }` as a single JSON file

The extraction is guided by the domain ontology in [`src/shared/ontology.json`](../shared/ontology.json).

## Running locally

```bash
# from repository root
python -m src.promotion_ingestion.processor \
    --supplier-id rewe-berlin-week-24 \
    --source https://example.com/weekly-flyer.pdf \
    --source data/local-flyer.pdf \
    --output data/extraction-result.json
```

`--source` can be repeated for multiple PDFs or images. Supported formats: PDF, PNG, JPG, JPEG, WebP.

## Container build

```bash
docker build -t promotion-ingestion -f src/promotion_ingestion/Dockerfile .
docker run --env-file .env promotion-ingestion \
    --supplier-id rewe-berlin-week-24 \
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
| `AZURE_OPENAI_ENDPOINT` | — | Azure OpenAI endpoint URL |
| `AZURE_OPENAI_API_KEY` | — | API key (falls back to `DefaultAzureCredential` if absent) |
| `AZURE_OPENAI_CHAT_DEPLOYMENT_NAME` | `gpt-4o` | Vision model deployment name |
| `OPENAI_API_VERSION` | `2025-01-01-preview` | Azure OpenAI API version |

## Key files

| File | Purpose |
|---|---|
| `processor.py` | `FlyerProcessor` pipeline, `JobInput` / `ExtractionResult` models, CLI entry point |
| `Dockerfile` | Container image definition |
| `requirements.txt` | Python dependencies |
| `../shared/ontology.json` | Domain ontology used as LLM context |
| `../shared/models.py` | Pydantic models: `Supplier`, `Category`, `Item` and related types |
