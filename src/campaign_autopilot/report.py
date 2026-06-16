"""Run the Campaign Planning Agent and return its Markdown report.

By default the existing agent defined in :mod:`src.campaign_agent.agent` is run
**in-process** — the autopilot simply imports the already-assembled ``agent``
object and calls it. This reuses the exact agent Dennis built (its prompt,
skills framing, competitor-search tool and pricing toolbox) without forking or
modifying it, and means the autopilot works whether or not a hosted-agent
endpoint has been deployed.

Importing :mod:`src.campaign_agent.agent` constructs the Foundry chat client and
pricing tool at import time, so it requires the campaign agent's own environment
to be configured (``AZURE_AI_PROJECT_ENDPOINT`` etc.). The import is therefore
deferred into :func:`generate_report` so that unit tests and ``--sample`` runs,
which never call it, do not need any Azure configuration.
"""

from __future__ import annotations

import logging

from src.campaign_autopilot.config import AutopilotConfig

logger = logging.getLogger(__name__)


async def _run_in_process(prompt: str) -> str:
    """Run the campaign agent in-process and collect its full text output."""
    # Imported lazily: building the agent needs the campaign agent's env vars.
    from src.campaign_agent.agent import agent

    text_parts: list[str] = []
    runner = agent.run(prompt, stream=True)

    # ``agent.run`` may return an async iterator (streaming) or a coroutine that
    # resolves to a response object. Support both shapes defensively.
    if hasattr(runner, "__aiter__"):
        async for chunk in runner:
            chunk_text = getattr(chunk, "text", None)
            if chunk_text:
                text_parts.append(chunk_text)
    else:
        response = await runner
        text_parts.append(getattr(response, "text", str(response)))

    return "".join(text_parts).strip()


async def generate_report(config: AutopilotConfig) -> str:
    """Produce the Markdown analysis for this run by invoking the campaign agent."""
    if config.agent_endpoint:
        # Remote invocation against a deployed hosted agent is a planned
        # extension. Until it is wired, fall back to the robust in-process path
        # rather than silently doing nothing.
        logger.warning(
            "CAMPAIGN_AGENT_ENDPOINT is set (%s) but remote invocation is not yet "
            "implemented; running the agent in-process instead.",
            config.agent_endpoint,
        )

    logger.info("Running campaign agent in-process for the autopilot report.")
    report = await _run_in_process(config.prompt)
    if not report:
        raise RuntimeError("The campaign agent returned an empty report.")
    return report
