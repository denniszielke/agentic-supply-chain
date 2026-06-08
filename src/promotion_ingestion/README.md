# promotion_ingestion — Flyer Indexing Job

## Overview

`promotion_ingestion` is a Python container job that downloads and indexes weekly retail promotional flyers (PDFs, images, or web pages) into the Azure AI Search index defined by this project.

The job follows a three-stage pipeline:

1. **Materialize** — download remote URLs or enumerate local files
2. **Extract** — parse offer tiles from each source (OCR / visual model hook)
3. **Persist** — write normalized `Item` records to a JSON output file or push directly to the search index

## Running locally

```bash
# from repository root
python -m src.promotion_ingestion.job \
    --source https://example.com/weekly-flyer.pdf \
    --supplier-id rewe-berlin-week-24 \
    --output data/indexed-items.json
```

Supported `--source` values:
- HTTP/HTTPS URL (PDF, image, or HTML page)
- Path to a local PDF or image file
- Path to a folder of images

## Container build

```bash
docker build -t promotion-ingestion -f src/promotion_ingestion/Dockerfile .
docker run promotion-ingestion \
    --source https://example.com/flyer.pdf \
    --supplier-id rewe-berlin-week-24
```

Or use the shared build script:

```bash
./scripts/build_containers.sh
```

## Environment variables

| Variable | Description |
|---|---|
| `AZURE_SEARCH_ENDPOINT` | Azure AI Search endpoint (optional — required for index push) |
| `AZURE_SEARCH_ADMIN_KEY` | Admin API key (optional — required for index push) |

## Key files

| File | Purpose |
|---|---|
| `job.py` | `FlyerIndexerJob` class and CLI entry point |
| `Dockerfile` | Container image definition |
| `requirements.txt` | Python dependencies |

## Extending the extraction pipeline

`FlyerIndexerJob._extract_offers()` is the hook point for a visual understanding model (e.g., GPT-4o Vision or Azure AI Document Intelligence). Replace the placeholder stub in `job.py` with your OCR/LLM call to extract structured `ExtractedOffer` objects from real flyer images.
