from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List
from urllib.parse import urlparse

import requests

from src.shared.models import Item, Pricing


@dataclass
class ExtractedOffer:
    item_id: str
    name: str
    description_text: str
    category_id: str
    current_price: float


class FlyerIndexerJob:
    def __init__(self, output_path: Path) -> None:
        self.output_path = output_path

    def index_source(self, source: str, supplier_id: str) -> List[Item]:
        local_files = self._materialize_source(source)
        offers = self._extract_offers(local_files)
        items = [
            Item(
                item_id=offer.item_id,
                supplier_id=supplier_id,
                name=offer.name,
                description_text=offer.description_text,
                category_id=offer.category_id,
                pricing=Pricing(current_price=offer.current_price),
            )
            for offer in offers
        ]
        self._persist(items)
        return items

    def _materialize_source(self, source: str) -> List[Path]:
        parsed = urlparse(source)
        if parsed.scheme in {"http", "https"}:
            response = requests.get(source, timeout=30)
            response.raise_for_status()
            suffix = Path(parsed.path).suffix or ".html"
            temp = Path(tempfile.mkdtemp(prefix="flyer-source-")) / f"source{suffix}"
            temp.write_bytes(response.content)
            return [temp]

        source_path = Path(source)
        if source_path.is_dir():
            return [path for path in source_path.rglob("*") if path.is_file()]
        return [source_path]

    def _extract_offers(self, files: Iterable[Path]) -> List[ExtractedOffer]:
        extracted: List[ExtractedOffer] = []
        for idx, file in enumerate(files, start=1):
            # Hook for a visual model OCR/understanding pipeline.
            extracted.append(
                ExtractedOffer(
                    item_id=f"indexed-{idx}",
                    name=file.stem.replace("_", " "),
                    description_text=f"Indexed from {file.name}",
                    category_id="uncategorized",
                    current_price=0.0,
                )
            )
        return extracted

    def _persist(self, items: List[Item]) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [item.model_dump(mode="json") for item in items]
        self.output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Index flyer sources into normalized offer items")
    parser.add_argument("--source", required=True, help="Website URL, PDF/image file, or folder")
    parser.add_argument("--supplier-id", required=True, help="Supplier context ID")
    parser.add_argument(
        "--output",
        default="/tmp/workspace/denniszielke/agentic-supply-chain/data/indexed-items.json",
        help="Output JSON file",
    )
    args = parser.parse_args()

    job = FlyerIndexerJob(Path(args.output))
    items = job.index_source(source=args.source, supplier_id=args.supplier_id)
    print(f"Indexed {len(items)} offers for supplier {args.supplier_id}")


if __name__ == "__main__":
    main()
