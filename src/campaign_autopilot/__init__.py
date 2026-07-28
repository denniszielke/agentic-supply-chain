"""Campaign Autopilot — Foundry-native scheduled, emailed campaign briefings.

Automates the Campaign Planning Agent the Foundry-native way: a Foundry
**routine** triggers the agent on a schedule, and the agent emails its briefing
itself through **Work IQ Mail** (Microsoft 365 / Outlook). No Container Apps Job
and no Azure Communication Services — the schedule and the send both live inside
Foundry.

It is purely additive: it provides the Work IQ tool wiring and the routine
registration without modifying the campaign agent.

Modules:
  workiq_email   — build the Work IQ Mail tool and the agent's email instruction.
  (``scripts/register_campaign_routine.py`` registers/manages the Foundry routine.)
"""
