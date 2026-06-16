"""Pluggable email transport for the Campaign Autopilot.

Three providers are supported, selected by :attr:`AutopilotConfig.provider`:

  * ``acs``  — Azure Communication Services Email. Uses a connection string when
    provided, otherwise the ACS endpoint with ``DefaultAzureCredential`` (the
    keyless / managed-identity path that matches the rest of the repo).
  * ``smtp`` — any SMTP server (stdlib ``smtplib``); STARTTLS and login optional.
  * ``file`` — writes the rendered email to disk instead of sending. This is what
    ``--dry-run`` uses, so the whole pipeline can be exercised with no Azure
    resources and nothing actually leaves the machine.

The ACS and SMTP SDK imports are deliberately *lazy* (inside the functions) so
this module — and the unit tests — import cleanly without those packages.
"""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

from src.campaign_autopilot.config import AutopilotConfig
from src.campaign_autopilot.email_renderer import RenderedEmail

logger = logging.getLogger(__name__)


@dataclass
class SendResult:
    """Outcome of a delivery attempt."""

    provider: str
    detail: str
    recipients: list[str]


def send_email(
    config: AutopilotConfig,
    subject: str,
    rendered: RenderedEmail,
    *,
    provider_override: Optional[str] = None,
) -> SendResult:
    """Deliver the rendered email using the configured (or overridden) provider."""
    provider = (provider_override or config.provider).strip().lower()
    if provider == "file":
        return _send_file(config, subject, rendered)
    if provider == "smtp":
        return _send_smtp(config, subject, rendered)
    if provider == "acs":
        return _send_acs(config, subject, rendered)
    raise ValueError(f"Unknown email provider '{provider}'.")


def _send_file(config: AutopilotConfig, subject: str, rendered: RenderedEmail) -> SendResult:
    """Write the email to disk (used for --dry-run and offline verification)."""
    out_dir = Path(config.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    html_path = out_dir / f"campaign-report-{stamp}.html"
    text_path = out_dir / f"campaign-report-{stamp}.txt"
    html_path.write_text(rendered.html, encoding="utf-8")
    text_path.write_text(
        f"Subject: {subject}\nTo: {', '.join(config.recipients) or '(none)'}\n\n{rendered.text}",
        encoding="utf-8",
    )
    logger.info("Wrote email preview to %s", html_path)
    return SendResult(provider="file", detail=str(html_path), recipients=config.recipients)


def _send_smtp(config: AutopilotConfig, subject: str, rendered: RenderedEmail) -> SendResult:
    """Send via SMTP with an HTML body and a plain-text alternative."""
    message = EmailMessage()
    message["From"] = config.sender_address
    message["To"] = ", ".join(config.recipients)
    message["Subject"] = subject
    message.set_content(rendered.text)
    message.add_alternative(rendered.html, subtype="html")

    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=60) as server:
        if config.smtp_use_tls:
            server.starttls()
        if config.smtp_username:
            server.login(config.smtp_username, config.smtp_password)
        server.send_message(message)

    detail = f"sent via {config.smtp_host}:{config.smtp_port}"
    logger.info("SMTP email %s to %s", detail, config.recipients)
    return SendResult(provider="smtp", detail=detail, recipients=config.recipients)


def _send_acs(config: AutopilotConfig, subject: str, rendered: RenderedEmail) -> SendResult:
    """Send via Azure Communication Services Email."""
    from azure.communication.email import EmailClient

    if config.acs_connection_string:
        client = EmailClient.from_connection_string(config.acs_connection_string)
    else:
        from azure.identity import DefaultAzureCredential

        client = EmailClient(config.acs_endpoint, DefaultAzureCredential())

    message = {
        "senderAddress": config.sender_address,
        "content": {
            "subject": subject,
            "plainText": rendered.text,
            "html": rendered.html,
        },
        "recipients": {
            "to": [{"address": addr} for addr in config.recipients],
        },
    }

    poller = client.begin_send(message)
    result = poller.result()
    # The SDK returns a mapping with at least an "id"; status may vary by version.
    message_id = result.get("id") if isinstance(result, dict) else getattr(result, "id", "")
    status = result.get("status") if isinstance(result, dict) else getattr(result, "status", "")
    detail = f"acs message id={message_id} status={status}"
    logger.info("ACS email %s to %s", detail, config.recipients)
    return SendResult(provider="acs", detail=detail, recipients=config.recipients)
