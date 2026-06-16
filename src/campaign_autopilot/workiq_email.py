"""Work IQ Mail integration for the Campaign Planning Agent.

This is the **Foundry-native** delivery path: instead of this Python process
sending email, the *campaign agent itself* sends the briefing through
**Work IQ** (Microsoft 365 / Outlook Mail), and a Foundry **routine** triggers
the agent on a schedule. See ``scripts/register_campaign_routine.py``.

Work IQ connects a Foundry agent to Microsoft 365 (mail, calendar, Teams, files)
over the A2A protocol, on-behalf-of the signed-in user. The agent is given a
``WorkIQPreviewTool`` and an instruction telling it to email the finished
briefing through Work IQ Outlook Mail.

Docs: https://learn.microsoft.com/azure/foundry/agents/how-to/tools/work-iq

Important preview constraints (see the README):
  * Work IQ is preview; the Foundry project must **not** be VNet-restricted.
  * Each user the mail is sent on behalf of needs a Microsoft 365 Copilot licence.
  * A Foundry connection to the Work IQ endpoint and Entra admin consent for
    ``WorkIQAgent.Ask`` are required.

Environment variables:
  WORK_IQ_PROJECT_CONNECTION_ID   Fully-qualified resource id of the Work IQ
                                  project connection (required to build the tool).
  EMAIL_RECIPIENTS                Comma/semicolon separated recipients for the
                                  emailed briefing.
  CAMPAIGN_AUTOPILOT_REPORT_TITLE Title used in the email subject/body.
"""

from __future__ import annotations

import os
from typing import Any, List

DEFAULT_REPORT_TITLE = "Weekly Competitor & Margin Briefing"

# Default analysis prompt sent to the campaign agent on each scheduled run. The
# agent produces this briefing and then emails it via Work IQ Mail (see the
# delivery instruction below). Override with CAMPAIGN_AUTOPILOT_PROMPT.
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


WORKIQ_EMAIL_INSTRUCTION_TEMPLATE = """\

---
DELIVERY INSTRUCTION (Work IQ Mail)
After you have produced the briefing above, send it as an email using the
Work IQ Outlook Mail tool:
  * To: {recipients}
  * Subject: {title} — <today's date>
  * Body: the full briefing, formatted as readable HTML (keep the executive
    summary, the table, and the key-risks list).
Send exactly one email. Do not include raw procurement cost in the email.
Confirm in your final response that the email was sent and to whom.
"""


def email_instruction(recipients: List[str], report_title: str) -> str:
    """Return the prompt fragment instructing the agent to email via Work IQ."""
    return WORKIQ_EMAIL_INSTRUCTION_TEMPLATE.format(
        recipients=", ".join(recipients) if recipients else "(configure EMAIL_RECIPIENTS)",
        title=report_title,
    )


def augmented_instructions(base_instructions: str, recipients: List[str], report_title: str) -> str:
    """Append the Work IQ email delivery instruction to an agent's system prompt.

    Use this when wiring Work IQ into the campaign agent so the agent both
    *produces* the briefing and *emails* it in a single run.
    """
    return base_instructions.rstrip() + "\n" + email_instruction(recipients, report_title)


def email_instruction_from_env() -> str:
    """Build the email instruction from environment configuration."""
    recipients = _split_recipients(os.getenv("EMAIL_RECIPIENTS", ""))
    title = os.getenv("CAMPAIGN_AUTOPILOT_REPORT_TITLE", DEFAULT_REPORT_TITLE)
    return email_instruction(recipients, title)


def build_workiq_tool(connection_id: str | None = None) -> Any:
    """Build the ``WorkIQPreviewTool`` for attaching to the campaign agent.

    Imports the SDK lazily so this module is importable (and unit-testable)
    without ``azure-ai-projects>=2.2.0`` installed. Pass ``connection_id`` or set
    ``WORK_IQ_PROJECT_CONNECTION_ID``.
    """
    conn = (connection_id or os.getenv("WORK_IQ_PROJECT_CONNECTION_ID", "")).strip()
    if not conn:
        raise ValueError(
            "WORK_IQ_PROJECT_CONNECTION_ID is required to build the Work IQ tool "
            "(the fully-qualified resource id of the Work IQ project connection)."
        )
    from azure.ai.projects.models import WorkIQPreviewTool

    return WorkIQPreviewTool(project_connection_id=conn)
