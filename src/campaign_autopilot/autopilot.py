"""Campaign Autopilot orchestrator and CLI.

Runs one analysis cycle: ask the Campaign Planning Agent for the report, render
it as an email, and deliver it. Designed to be the container entry point for an
Azure Container Apps *Job* (which provides the schedule), so the default mode is
a single run that then exits.

Usage
-----
    # one cycle, deliver via the configured provider (this is what the Job runs)
    python -m src.campaign_autopilot.autopilot --once

    # render to disk instead of sending — no Azure/SMTP needed
    python -m src.campaign_autopilot.autopilot --dry-run

    # fully offline: use the bundled sample report (no agent call at all)
    python -m src.campaign_autopilot.autopilot --dry-run --sample

    # run continuously on a cron schedule (local convenience; the Job uses --once)
    python -m src.campaign_autopilot.autopilot --loop --cron "0 6 * * 1"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Allow `python -m src.campaign_autopilot.autopilot` from the repo root.
_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

_env_path = _repo_root / ".env"
load_dotenv(dotenv_path=_env_path if _env_path.exists() else None)

from src.campaign_autopilot.config import AutopilotConfig  # noqa: E402
from src.campaign_autopilot.email_renderer import render_email  # noqa: E402
from src.campaign_autopilot.email_sender import SendResult, send_email  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logging.getLogger("azure").setLevel(logging.WARNING)
logger = logging.getLogger("campaign_autopilot")

_SAMPLE_PATH = Path(__file__).with_name("sample_report.md")


def _load_sample_report() -> str:
    """Return the bundled sample report for fully offline runs."""
    if not _SAMPLE_PATH.exists():
        raise FileNotFoundError(
            f"Sample report not found at {_SAMPLE_PATH}. The sample is a source-tree "
            "artefact for local/offline use and is intentionally not shipped in the image."
        )
    return _SAMPLE_PATH.read_text(encoding="utf-8")


def _report_metadata(config: AutopilotConfig, provider: str, source: str) -> dict[str, str]:
    import os

    model = (
        os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME")
        or os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")
        or "gpt-4.1-mini"
    )
    return {
        "Generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "Schedule": config.schedule,
        "Model": model,
        "Source": source,
        "Delivery": provider,
    }


async def _build_report(config: AutopilotConfig, use_sample: bool) -> str:
    if use_sample:
        logger.info("Using bundled sample report (offline mode).")
        return _load_sample_report()
    # Imported lazily so --sample / unit tests never require the agent's env.
    from src.campaign_autopilot.report import generate_report

    return await generate_report(config)


def run_once(
    config: AutopilotConfig,
    *,
    dry_run: bool = False,
    use_sample: bool = False,
) -> SendResult:
    """Execute a single autopilot cycle and return the delivery result."""
    provider = "file" if dry_run else config.provider
    if not dry_run:
        config.validate()

    source = "bundled sample" if use_sample else "campaign agent (in-process)"
    report_markdown = asyncio.run(_build_report(config, use_sample))

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    subject = f"{config.subject_prefix} {config.report_title} — {today}".strip()
    metadata = _report_metadata(config, provider, source)

    rendered = render_email(
        report_markdown,
        title=config.report_title,
        subtitle="Automated competitor & margin analysis",
        metadata=metadata,
    )

    result = send_email(config, subject, rendered, provider_override=provider)
    logger.info("Delivery complete via '%s': %s", result.provider, result.detail)
    return result


def _run_loop(config: AutopilotConfig, cron: str, dry_run: bool, use_sample: bool) -> None:
    """Run forever, executing one cycle at each cron occurrence (local convenience)."""
    try:
        from croniter import croniter
    except ImportError as exc:  # pragma: no cover - depends on optional package
        raise SystemExit(
            "--loop needs the 'croniter' package (pip install croniter), or deploy as a "
            "Container Apps Job which schedules --once for you."
        ) from exc

    logger.info("Autopilot loop started with cron '%s'. Ctrl+C to stop.", cron)
    while True:
        now = datetime.now(timezone.utc)
        next_run = croniter(cron, now).get_next(datetime)
        sleep_seconds = max(1.0, (next_run - now).total_seconds())
        logger.info("Next run at %s UTC (in %.0f s).", next_run.isoformat(), sleep_seconds)
        time.sleep(sleep_seconds)
        try:
            run_once(config, dry_run=dry_run, use_sample=use_sample)
        except Exception:  # pragma: no cover - keep the loop alive on failure
            logger.exception("Autopilot cycle failed; continuing to the next scheduled run.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Campaign Planning Agent autopilot.")
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit (default).")
    parser.add_argument("--loop", action="store_true", help="Run continuously on a cron schedule.")
    parser.add_argument("--cron", default=None, help="Cron expression for --loop (default: from config).")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render the email to disk instead of sending (forces the 'file' provider).",
    )
    parser.add_argument(
        "--sample",
        action="store_true",
        help="Use the bundled sample report instead of calling the agent (fully offline).",
    )
    parser.add_argument("--output-dir", default=None, help="Override AUTOPILOT_OUTPUT_DIR.")
    parser.add_argument("--recipients", default=None, help="Override EMAIL_RECIPIENTS (comma separated).")
    args = parser.parse_args(argv)

    config = AutopilotConfig.from_env()
    if args.output_dir:
        config.output_dir = args.output_dir
    if args.recipients:
        from src.campaign_autopilot.config import _split_recipients

        config.recipients = _split_recipients(args.recipients)

    if args.loop:
        cron = args.cron or config.schedule
        _run_loop(config, cron, dry_run=args.dry_run, use_sample=args.sample)
        return 0

    try:
        run_once(config, dry_run=args.dry_run, use_sample=args.sample)
    except Exception:
        logger.exception("Autopilot run failed.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
