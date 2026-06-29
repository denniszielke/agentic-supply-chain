"""Serve the shopping simulator workflow on the Agent Framework DevUI.

Runs the multi-agent shopping simulator workflow behind the DevUI on the public
container port, with Application Insights telemetry enabled.

    python -m src.shopping_simulations.server
"""
from __future__ import annotations

import logging
import os
import secrets

from src.shopping_simulations.telemetry import setup_telemetry

# Instrument BEFORE importing the agent framework so spans are captured.
setup_telemetry()

from src.shopping_simulations.agents import workflow  # noqa: E402

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def main() -> None:
    from agent_framework.devui import serve

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger = logging.getLogger(__name__)

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8080"))

    # DevUI refuses no-auth on non-loopback hosts. On a public bind (e.g. the
    # Container App, HOST=0.0.0.0) enable bearer auth with DEVUI_AUTH_TOKEN, or
    # auto-generate and log a token. On loopback, allow no-auth local dev.
    is_loopback = host.lower() in _LOOPBACK_HOSTS
    auth_enabled = not is_loopback
    auth_token = os.getenv("DEVUI_AUTH_TOKEN") or None
    if auth_enabled and not auth_token:
        auth_token = secrets.token_urlsafe(32)
        logger.info("=" * 70)
        logger.info("DevUI bearer token (set DEVUI_AUTH_TOKEN to pin it):")
        logger.info("   %s", auth_token)
        logger.info("Call with header: Authorization: Bearer <token>")
        logger.info("=" * 70)

    logger.info("Starting Shopping Simulator workflow DevUI on http://%s:%s", host, port)
    serve(
        entities=[workflow],
        host=host,
        port=port,
        auto_open=False,
        auth_enabled=auth_enabled,
        auth_token=auth_token,
    )


if __name__ == "__main__":
    main()
