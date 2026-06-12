"""Ingest all PDF flyer files from data/files/ into Azure AI Search.

Supplier IDs are derived from filenames using the project naming conventions:
  AldiSued-Jun8.pdf     → aldi-sued
  AldiNord-Jun15.pdf    → aldi-nord
  EdekaPienka-Jun8.pdf  → edeka-pienka
  kaufland-Jun11.pdf    → kaufland
  rewe_2026_wk24_...    → rewe

Run from the repo root:
  python scripts/ingest_all.py [--files-dir data/files] [--output-dir data] [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Filename → supplier-id mapping
# ---------------------------------------------------------------------------

# Explicit overrides take priority over the auto-derived name.
_EXPLICIT_SUPPLIER_IDS: dict[str, str] = {
    # add overrides here if auto-detection ever needs a nudge
    # e.g. "SomeWeirdName": "correct-supplier-id",
}

# Tokens that signal the end of the store-name portion of a filename.
# Anything that looks like a date, week number, version, or store-code is a suffix.
_SUFFIX_PATTERN = re.compile(
    r"(?i)^("
    r"\d{4}"            # year: 2026
    r"|\d{1,2}$"        # bare day number at end of token
    r"|wk\d+"           # week: wk24
    r"|v\d+"            # version: v1
    r"|[a-z]{3}\d*$"    # month abbreviation: Jun8, Jun, Jan …
    r"|\d{6,}"          # long numeric code: 840417
    r")$"
)


def _camel_to_kebab(text: str) -> str:
    """Convert CamelCase text to kebab-case.  'AldiSued' → 'aldi-sued'."""
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1-\2", text)
    text = re.sub(r"([a-z\d])([A-Z])", r"\1-\2", text)
    return text.lower()


def supplier_id_from_filename(path: Path) -> str:
    """Derive a stable supplier-id slug from a PDF filename."""
    stem = path.stem  # filename without extension

    # Split on dashes or underscores
    tokens = re.split(r"[-_]", stem)

    # Collect tokens until we hit a date / version suffix
    name_tokens: list[str] = []
    for token in tokens:
        if _SUFFIX_PATTERN.match(token):
            break
        name_tokens.append(token)

    if not name_tokens:
        name_tokens = tokens[:1]  # fallback: use first token

    # Join and convert CamelCase → kebab
    raw = "-".join(name_tokens)
    supplier_id = _camel_to_kebab(raw).strip("-")

    # Apply explicit override if present
    return _EXPLICIT_SUPPLIER_IDS.get(supplier_id, supplier_id)


def output_name_from_filename(path: Path) -> str:
    """Derive an output JSON filename from the PDF filename.

    AldiSued-Jun8.pdf → aldi-sued-jun8.json
    rewe_2026_wk24_840417_v1.pdf → rewe-2026-wk24.json  (keeps year+week only)
    """
    stem = path.stem
    tokens = re.split(r"[-_]", stem)

    # Keep only the store prefix and up to two date/version tokens for readability
    name_tokens: list[str] = []
    date_tokens: list[str] = []
    in_suffix = False
    for token in tokens:
        if not in_suffix and _SUFFIX_PATTERN.match(token):
            in_suffix = True
        if in_suffix:
            # Only keep short, human-readable date tokens (skip long numeric codes / version)
            if re.match(r"(?i)^(wk\d+|[a-z]{3}\d*|\d{4})$", token):
                date_tokens.append(token.lower())
                if len(date_tokens) >= 2:
                    break
        else:
            name_tokens.append(_camel_to_kebab(token))

    parts = name_tokens + date_tokens
    return "-".join(p.strip("-") for p in parts if p) + ".json"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest all flyer PDFs into Azure AI Search.")
    parser.add_argument(
        "--files-dir",
        default="data/files",
        help="Directory containing PDF flyer files (default: data/files).",
    )
    parser.add_argument(
        "--output-dir",
        default="data",
        help="Directory for output JSON files (default: data).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the commands that would be run without executing them.",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="Skip --push-to-search (write JSON only).",
    )
    args = parser.parse_args()

    files_dir = Path(args.files_dir)
    output_dir = Path(args.output_dir)
    pdfs = sorted(files_dir.glob("*.pdf"))

    if not pdfs:
        print(f"No PDF files found in {files_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(pdfs)} PDF file(s) in {files_dir}\n")

    failed: list[Path] = []

    for pdf in pdfs:
        supplier_id = supplier_id_from_filename(pdf)
        output_file = output_dir / output_name_from_filename(pdf)

        cmd = [
            sys.executable, "-m", "src.promotion_ingestion.processor",
            "--supplier-id", supplier_id,
            "--source", str(pdf),
            "--output", str(output_file),
        ]
        if not args.no_push:
            cmd.append("--push-to-search")

        print(f"{'[DRY RUN] ' if args.dry_run else ''}Processing: {pdf.name}")
        print(f"  supplier-id : {supplier_id}")
        print(f"  output      : {output_file}")

        if args.dry_run:
            print(f"  command     : {' '.join(cmd)}\n")
            continue

        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"  ERROR: processor exited with code {result.returncode}\n", file=sys.stderr)
            failed.append(pdf)
        else:
            print(f"  Done.\n")

    if failed:
        print(f"\n{len(failed)} file(s) failed:", file=sys.stderr)
        for f in failed:
            print(f"  {f}", file=sys.stderr)
        sys.exit(1)
    elif not args.dry_run:
        print("All files processed successfully.")


if __name__ == "__main__":
    main()
