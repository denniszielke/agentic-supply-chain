"""Environment-driven configuration for the Campaign Autopilot.

Every value can be supplied through an environment variable so the job is
configured the same way whether it runs locally, in a container, or as an
Azure Container Apps Job. The agent itself reads its own configuration
(``AZURE_AI_PROJECT_ENDPOINT``, ``AZURE_SEARCH_ENDPOINT``, the pricing toolbox,
etc.) from :mod:`src.campaign_agent.agent`; the variables below only configure
the *autopilot wrapper* — scheduling metadata, the analysis prompt, and email
delivery.

Email / report variables
-------------------------
EMAIL_PROVIDER            Delivery transport: ``acs`` (default), ``smtp`` or ``file``.
EMAIL_RECIPIENTS          Comma/semicolon separated list of recipient addresses (required
                          unless the provider is ``file`` / running ``--dry-run``).
EMAIL_SENDER_ADDRESS      From address (required for ``acs`` and ``smtp``).
EMAIL_SUBJECT_PREFIX      Prefix prepended to every subject (default: ``[Campaign Autopilot]``).
AUTOPILOT_OUTPUT_DIR      Directory for the ``file`` provider / ``--dry-run`` output
                          (default: ``data/autopilot``).

Azure Communication Services (EMAIL_PROVIDER=acs)
-------------------------------------------------
ACS_CONNECTION_STRING     ACS connection string. If set it is used directly.
ACS_ENDPOINT              ACS endpoint (e.g. ``https://<name>.communication.azure.com``).
                          Used with ``DefaultAzureCredential`` when no connection string
                          is provided (keyless / managed-identity path).

SMTP (EMAIL_PROVIDER=smtp)
--------------------------
SMTP_HOST                 SMTP server host (required for smtp).
SMTP_PORT                 SMTP server port (default: 587).
SMTP_USERNAME             SMTP username (optional; enables login).
SMTP_PASSWORD             SMTP password (optional; used with SMTP_USERNAME).
SMTP_USE_TLS              ``true``/``false`` — issue STARTTLS before sending (default: true).

Report content
--------------
CAMPAIGN_AUTOPILOT_REPORT_TITLE  Human title shown in the email header
                                 (default: ``Weekly Competitor & Margin Briefing``).
CAMPAIGN_AUTOPILOT_PROMPT        The instruction sent to the campaign agent. A strong
                                 default is provided; override per category/persona/region.
CAMPAIGN_AUTOPILOT_SCHEDULE      Cron expression, recorded in the report footer and used by
                                 ``--loop`` (default: ``0 6 * * 1`` — Mondays 06:00 UTC).
CAMPAIGN_AGENT_ENDPOINT          Optional: deployed agent RESPONSES endpoint. When unset
                                 (recommended) the agent runs in-process.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional


DEFAULT_REPORT_TITLE = "Weekly Competitor & Margin Briefing"
DEFAULT_SUBJECT_PREFIX = "[Campaign Autopilot]"
DEFAULT_SCHEDULE = "0 6 * * 1"  # Mondays 06:00 UTC
DEFAULT_OUTPUT_DIR = "data/autopilot"

DEFAULT_PROMPT = (
    "Produce this week's competitor-and-margin briefing for the retail marketing team. "
    "Focus on the grocery categories under the most competitive pressure "
    "(for example milchprodukte-eier, fleisch-wurst and obst-gemuese). For each category: "
    "summarise what competitors are currently discounting (use search_competitor_promotions), "
    "compare it against our internal margin and weekly volume (use the pricing tools), and "
    "recommend two to three margin-aware promotion actions, each targeted at a shopping persona. "
    "Always optimise weekly gross margin, never propose a shelf price below the procurement-plus-"
    "logistics cost floor, and never reveal raw procurement cost. "
    "Return the briefing as well-structured GitHub-flavoured Markdown: start with a short "
    "'Executive summary' (3-4 sentences), then a table with the columns "
    "| Category | Competitor pressure | Recommended action | Persona | Forecast weekly margin impact |, "
    "and finish with a bulleted 'Key risks' section."
)


def _split_recipients(raw: str) -> List[str]:
    """Parse a comma/semicolon separated recipient list into clean addresses."""
    parts: List[str] = []
    for chunk in raw.replace(";", ",").split(","):
        addr = chunk.strip()
        if addr:
            parts.append(addr)
    return parts


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class AutopilotConfig:
    """Resolved configuration for a single autopilot run."""

    # Report content
    report_title: str = DEFAULT_REPORT_TITLE
    prompt: str = DEFAULT_PROMPT
    schedule: str = DEFAULT_SCHEDULE
    agent_endpoint: Optional[str] = None

    # Email delivery
    provider: str = "acs"
    recipients: List[str] = field(default_factory=list)
    sender_address: str = ""
    subject_prefix: str = DEFAULT_SUBJECT_PREFIX
    output_dir: str = DEFAULT_OUTPUT_DIR

    # Azure Communication Services
    acs_connection_string: str = ""
    acs_endpoint: str = ""

    # SMTP
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True

    @classmethod
    def from_env(cls) -> "AutopilotConfig":
        """Build the configuration from environment variables."""
        return cls(
            report_title=os.getenv("CAMPAIGN_AUTOPILOT_REPORT_TITLE", DEFAULT_REPORT_TITLE),
            prompt=os.getenv("CAMPAIGN_AUTOPILOT_PROMPT", DEFAULT_PROMPT),
            schedule=os.getenv("CAMPAIGN_AUTOPILOT_SCHEDULE", DEFAULT_SCHEDULE),
            agent_endpoint=(os.getenv("CAMPAIGN_AGENT_ENDPOINT", "").strip() or None),
            provider=os.getenv("EMAIL_PROVIDER", "acs").strip().lower(),
            recipients=_split_recipients(os.getenv("EMAIL_RECIPIENTS", "")),
            sender_address=os.getenv("EMAIL_SENDER_ADDRESS", "").strip(),
            subject_prefix=os.getenv("EMAIL_SUBJECT_PREFIX", DEFAULT_SUBJECT_PREFIX),
            output_dir=os.getenv("AUTOPILOT_OUTPUT_DIR", DEFAULT_OUTPUT_DIR),
            acs_connection_string=os.getenv("ACS_CONNECTION_STRING", "").strip(),
            acs_endpoint=os.getenv("ACS_ENDPOINT", "").strip(),
            smtp_host=os.getenv("SMTP_HOST", "").strip(),
            smtp_port=int(os.getenv("SMTP_PORT", "587") or "587"),
            smtp_username=os.getenv("SMTP_USERNAME", "").strip(),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            smtp_use_tls=_env_bool("SMTP_USE_TLS", True),
        )

    def validate(self) -> None:
        """Raise ``ValueError`` if the configuration cannot deliver email.

        The ``file`` provider needs no recipients (it writes to disk), which is
        also what ``--dry-run`` uses, so it is always valid.
        """
        if self.provider == "file":
            return
        if self.provider not in {"acs", "smtp"}:
            raise ValueError(
                f"Unknown EMAIL_PROVIDER '{self.provider}'. Use 'acs', 'smtp' or 'file'."
            )
        if not self.recipients:
            raise ValueError("EMAIL_RECIPIENTS is required (comma-separated addresses).")
        if not self.sender_address:
            raise ValueError("EMAIL_SENDER_ADDRESS is required for the 'acs' and 'smtp' providers.")
        if self.provider == "acs" and not (self.acs_connection_string or self.acs_endpoint):
            raise ValueError(
                "EMAIL_PROVIDER=acs requires ACS_CONNECTION_STRING or ACS_ENDPOINT."
            )
        if self.provider == "smtp" and not self.smtp_host:
            raise ValueError("EMAIL_PROVIDER=smtp requires SMTP_HOST.")
