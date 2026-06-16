"""Automate the Campaign Planning Agent with a Foundry **routine**.

A *routine* is a project-native automation rule in Foundry Agent Service: a
trigger (a cron schedule or one-shot timer) plus an action (invoke one agent).
This is the Foundry-native way to schedule a hosted agent — no Container Apps
Job, Logic App, or external scheduler. Foundry queues the invocation, runs the
agent, and stores a run record you can inspect.

Because a routine only *invokes* the agent, the briefing is emailed by the agent
itself through **Work IQ Mail** (see ``src/campaign_autopilot/workiq_email.py``).
Make sure the campaign agent has the Work IQ tool and an email instruction before
the scheduled run is useful.

Docs: https://learn.microsoft.com/azure/foundry/agents/how-to/use-routines

Routines are **preview**: the SDK is ``azure-ai-projects>=2.2.0`` and calls carry
the ``Foundry-Features: Routines=V1Preview`` header. Routines are available in a
subset of regions only — confirm your project's region (see the README).

Usage::

    # create or update the weekly routine (default action)
    python -m scripts.register_campaign_routine

    # pause / resume / fire-now / inspect recent runs
    python -m scripts.register_campaign_routine --disable
    python -m scripts.register_campaign_routine --enable
    python -m scripts.register_campaign_routine --test
    python -m scripts.register_campaign_routine --list-runs

Environment variables:
  AZURE_AI_PROJECT_ENDPOINT       Foundry project endpoint (required).
  AZURE_AI_CAMPAIGN_AGENT_NAME    Agent the routine invokes (default: campaign-agent).
  CAMPAIGN_ROUTINE_NAME           Routine name (default: campaign-weekly-briefing).
  CAMPAIGN_AUTOPILOT_SCHEDULE     Cron expression, UTC (default: "0 6 * * 1").
  CAMPAIGN_ROUTINE_TIME_ZONE      IANA time zone for the cron (default: UTC).
  CAMPAIGN_AUTOPILOT_PROMPT       Optional input sent to the agent on each run.
"""

from __future__ import annotations

import os
import sys

ROUTINE_NAME = os.getenv("CAMPAIGN_ROUTINE_NAME", "campaign-weekly-briefing")
AGENT_NAME = os.getenv("AZURE_AI_CAMPAIGN_AGENT_NAME", "campaign-agent")
SCHEDULE = os.getenv("CAMPAIGN_AUTOPILOT_SCHEDULE", "0 6 * * 1")
TIME_ZONE = os.getenv("CAMPAIGN_ROUTINE_TIME_ZONE", "UTC")
_TRIGGER_KEY = "weekly-briefing"


def build_schedule_trigger(cron: str, time_zone: str) -> dict:
    """Return a recurring (cron) schedule trigger definition."""
    return {
        _TRIGGER_KEY: {
            "type": "schedule",
            "cron_expression": cron,
            "time_zone": time_zone,
        }
    }


def build_action(agent_name: str) -> dict:
    """Return a Responses-API action that invokes the given agent."""
    return {
        "type": "invoke_agent_responses_api",
        "agent_name": agent_name,
    }


def _create_or_update(client) -> object:
    """Create/update the routine, tolerating SDKs that don't accept ``input``."""
    triggers = build_schedule_trigger(SCHEDULE, TIME_ZONE)
    action = build_action(AGENT_NAME)
    prompt = os.getenv("CAMPAIGN_AUTOPILOT_PROMPT", "").strip()

    kwargs = dict(
        routine_name=ROUTINE_NAME,
        description="Weekly competitor & margin briefing — emailed via Work IQ Mail.",
        enabled=True,
        triggers=triggers,
        action=action,
    )
    if prompt:
        # ``input`` is the per-invocation user message in the preview; some SDK
        # builds expose it as a top-level field. Fall back gracefully if not.
        try:
            return client.beta.routines.create_or_update(input=prompt, **kwargs)
        except TypeError:
            print("Note: this SDK build doesn't accept 'input'; creating without it. "
                  "Put the briefing+email instruction in the agent's instructions instead.")
    return client.beta.routines.create_or_update(**kwargs)


def create() -> None:
    from scripts.deploy_helpers import get_client, get_env

    get_env("AZURE_AI_PROJECT_ENDPOINT")
    client = get_client()
    routine = _create_or_update(client)
    name = getattr(routine, "name", ROUTINE_NAME)
    enabled = getattr(routine, "enabled", True)
    print(f"Routine '{name}' created/updated (enabled={enabled}).")
    print(f"  Trigger: schedule cron '{SCHEDULE}' ({TIME_ZONE})")
    print(f"  Action:  invoke_agent_responses_api -> '{AGENT_NAME}'")
    print("  Reminder: the agent must have the Work IQ Mail tool to send the email.")
    print(f"  Fire a test run now with: python -m scripts.register_campaign_routine --test")


def disable() -> None:
    from scripts.deploy_helpers import get_client, get_env

    get_env("AZURE_AI_PROJECT_ENDPOINT")
    client = get_client()
    routine = client.beta.routines.disable(ROUTINE_NAME)
    print(f"Routine '{ROUTINE_NAME}' disabled (enabled={getattr(routine, 'enabled', False)}).")


def enable() -> None:
    from scripts.deploy_helpers import get_client, get_env

    get_env("AZURE_AI_PROJECT_ENDPOINT")
    client = get_client()
    routine = client.beta.routines.enable(ROUTINE_NAME)
    print(f"Routine '{ROUTINE_NAME}' enabled (enabled={getattr(routine, 'enabled', True)}).")


def test_run() -> None:
    """Fire the routine once immediately (without waiting for the schedule)."""
    from scripts.deploy_helpers import get_client, get_env

    get_env("AZURE_AI_PROJECT_ENDPOINT")
    client = get_client()
    result = client.beta.routines.dispatch(
        routine_name=ROUTINE_NAME,
        payload={"type": "invoke_agent_responses_api"},
    )
    dispatch_id = getattr(result, "dispatch_id", None) or (
        result.get("dispatch_id") if isinstance(result, dict) else None
    )
    print(f"Test run queued for '{ROUTINE_NAME}'. dispatch_id={dispatch_id}")
    print("Inspect with: python -m scripts.register_campaign_routine --list-runs")


def list_runs() -> None:
    from scripts.deploy_helpers import get_client, get_env

    get_env("AZURE_AI_PROJECT_ENDPOINT")
    client = get_client()
    runs = client.beta.routines.list_runs(ROUTINE_NAME)
    print(f"Recent runs for '{ROUTINE_NAME}':")
    for run in runs:
        get = run.get if isinstance(run, dict) else (lambda k, d=None: getattr(run, k, d))
        print(
            f"  {get('id')}  status={get('status')}  phase={get('phase')}  "
            f"started={get('started_at')}  response={get('response_id')}"
        )


def main(argv: list[str] | None = None) -> int:
    args = set(argv if argv is not None else sys.argv[1:])
    try:
        if "--disable" in args:
            disable()
        elif "--enable" in args:
            enable()
        elif "--test" in args:
            test_run()
        elif "--list-runs" in args:
            list_runs()
        else:
            create()
    except Exception as exc:  # surface a clear message for the operator
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
