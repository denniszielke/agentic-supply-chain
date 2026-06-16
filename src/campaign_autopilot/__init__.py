"""Campaign Autopilot — scheduled, emailed Campaign Planning Agent reports.

This package runs the existing :mod:`src.campaign_agent` on a schedule (as an
Azure Container Apps *Job*) and emails its output as a nicely formatted report.

It is purely additive: it *imports and reuses* the campaign agent rather than
modifying it, so the two can be developed and deployed independently.

Modules:
  config          — env-driven :class:`AutopilotConfig`.
  report          — run the campaign agent and return its Markdown output.
  email_renderer  — turn the Markdown report into a styled HTML + plain-text email.
  email_sender    — pluggable email transport (Azure Communication Services / SMTP / file).
  autopilot       — orchestrator and CLI entry point (``python -m src.campaign_autopilot.autopilot``).
"""
