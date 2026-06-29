"""Telemetry bootstrap for the shopping simulator.

Instruments the workflow with the **Microsoft OpenTelemetry** distro and exports
spans to Application Insights so the deployed workflow can be registered and
observed as a Foundry **external agent**. Every span carries the
``gen_ai.agent.id`` attribute that Foundry matches against the registration's
``otel_agent_id``.

This must run **before** any agent-framework imports, so call
:func:`setup_telemetry` at the very top of the process. A no-op when
``APPLICATIONINSIGHTS_CONNECTION_STRING`` is unset.

See: https://learn.microsoft.com/azure/foundry/agents/how-to/register-external-agent
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

AGENT_NAME = os.environ.get("AGENT_NAME", "shopping-simulator")
OTEL_AGENT_ID = os.environ.get("OTEL_AGENT_ID", f"{AGENT_NAME}-v1")


def setup_telemetry() -> None:
    """Export Agent Framework traces to Application Insights via Microsoft OTel."""
    conn = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "").strip()
    if not conn:
        logger.info("APPLICATIONINSIGHTS_CONNECTION_STRING not set — telemetry disabled.")
        return

    # GenAI semantic-convention opt-ins, set before any framework imports.
    os.environ.setdefault("AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING", "true")
    os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental")
    os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "SPAN_AND_EVENT")

    try:
        from microsoft.opentelemetry import use_microsoft_opentelemetry

        use_microsoft_opentelemetry(
            enable_azure_monitor=True,
            azure_monitor_connection_string=conn,
            sampling_ratio=1.0,
            instrumentation_options={
                "fastapi": {"enabled": True},
                "openai": {
                    "enabled": True,
                    "agent_id": OTEL_AGENT_ID,
                    "agent_name": AGENT_NAME,
                },
            },
        )
        logger.info(
            "Microsoft OpenTelemetry → Application Insights enabled (gen_ai.agent.id=%s).",
            OTEL_AGENT_ID,
        )
    except Exception as exc:  # pragma: no cover - telemetry must never block startup
        logger.warning("Telemetry setup failed (%s); continuing without it.", exc)
