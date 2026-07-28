# campaign_autopilot — Foundry-native scheduled campaign briefings

Automates the [Campaign Planning Agent](../campaign_agent/) the **Foundry-native**
way: a Foundry **routine** triggers the agent on a schedule, and the agent emails
its briefing itself through **Work IQ Mail** (Microsoft 365 / Outlook). The
schedule and the send both live inside Foundry — no Container Apps Job, no Azure
Communication Services.

It is **purely additive** — it provides the Work IQ tool wiring and the routine
registration without modifying the campaign agent.

```
┌──────────────────┐   cron    ┌───────────────────────────┐   Work IQ A2A   ┌────────────┐
│ Foundry routine  │ ────────▶ │ Campaign Planning Agent   │ ──────────────▶ │ Outlook /  │
│ schedule trigger │  Mon 06:00│  • produce briefing       │   (OBO user)    │ M365 Mail  │
└──────────────────┘           │  • email it via Work IQ   │                 └─────┬──────┘
                               └───────────────────────────┘                       ▼
                                                                          📧 recipient inbox(es)
```

A routine only *invokes* the agent, so the **agent itself** sends the email
through the **Work IQ Mail** tool.

---

## Decision checklist (for the repo owner)

You need a Foundry **project** (this repo's is already a good fit — public
endpoint, supported region) plus the Microsoft 365 side wired up. Tick these:

- [ ] Foundry project is in a **routines-supported region** — _confirmed yes for this project_.
- [ ] Foundry project endpoint is **not VNet-restricted** — _confirmed: this repo's
      Foundry account sets `publicNetworkAccess: 'Enabled'` with no
      `virtualNetworkRules` (`infra/core/ai/ai-project.bicep`); the VNet only backs
      the Container Apps environment, not the project._ ✅
- [ ] A **Work IQ project connection** exists (Foundry → Tools → Work IQ), and you
      have its connection id → set `WORK_IQ_PROJECT_CONNECTION_ID`.
- [ ] **Entra Global Admin** granted admin consent for `WorkIQAgent.Ask`.
- [ ] Each identity the mail is sent on behalf of has a **Microsoft 365 Copilot licence**.
- [ ] **Decide the sender identity** for unattended runs — see
      [the one open question](#the-one-open-question-unattended-sending) below.

Then do the two steps: **(1)** add the Work IQ tool to the campaign agent,
**(2)** register the routine.

---

## Step 1 — Give the campaign agent the Work IQ Mail tool

Work IQ connects a Foundry agent to Microsoft 365 over A2A, on-behalf-of the
signed-in user. Add the tool to the campaign agent and tell it to email the
finished briefing. `workiq_email.py` provides both helpers, so it's a small,
opt-in change to `src/campaign_agent/agent.py`:

```python
from src.campaign_autopilot.workiq_email import build_workiq_tool, augmented_instructions

# where the agent is assembled (src/campaign_agent/agent.py):
agent = _chat_client.as_agent(
    name="campaign-planner",
    instructions=augmented_instructions(
        CAMPAIGN_AGENT_SYSTEM_PROMPT,
        recipients=["category-manager@aldi-sued.example"],
        report_title="Weekly Competitor & Margin Briefing",
    ),
    tools=[search_competitor_promotions, _pricing_tool, build_workiq_tool()],
)
```

`build_workiq_tool()` reads `WORK_IQ_PROJECT_CONNECTION_ID` and returns a
`WorkIQPreviewTool`. `augmented_instructions(...)` appends the email delivery
instruction to the agent's system prompt. No new dependency is needed — the
campaign agent already pins `azure-ai-projects==2.2.0`, which contains
`WorkIQPreviewTool`.

Docs: [Connect agents to Microsoft 365 with Work IQ](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/work-iq).

## Step 2 — Register the Foundry routine (the schedule)

```bash
# create/update the weekly schedule (default: Mondays 06:00 UTC)
python -m scripts.register_campaign_routine

# fire it once now (don't wait for the schedule), then inspect the run history
python -m scripts.register_campaign_routine --test
python -m scripts.register_campaign_routine --list-runs

# pause / resume without deleting it
python -m scripts.register_campaign_routine --disable
python -m scripts.register_campaign_routine --enable
```

The routine's action is `invoke_agent_responses_api` pointing at the campaign
agent; its input defaults to the standard briefing prompt (override with
`CAMPAIGN_AUTOPILOT_PROMPT`).

Routines need `azure-ai-projects>=2.2.0` (already pinned) and are **preview**
(calls carry `Foundry-Features: Routines=V1Preview`).

Docs: [Automate agents with routines](https://learn.microsoft.com/azure/foundry/agents/how-to/use-routines).

---

## Configuration

| Variable | Used by | Default | Description |
|---|---|---|---|
| `WORK_IQ_PROJECT_CONNECTION_ID` | Step 1 | — | Resource id of the Work IQ project connection (required to build the tool) |
| `EMAIL_RECIPIENTS` | Step 1 | — | Comma/semicolon separated recipients named in the email instruction |
| `CAMPAIGN_AUTOPILOT_REPORT_TITLE` | Step 1 | `Weekly Competitor & Margin Briefing` | Email subject/title |
| `AZURE_AI_PROJECT_ENDPOINT` | Step 2 | — | Foundry project endpoint (required) |
| `AZURE_AI_CAMPAIGN_AGENT_NAME` | Step 2 | `campaign-agent` | Agent the routine invokes |
| `CAMPAIGN_ROUTINE_NAME` | Step 2 | `campaign-weekly-briefing` | Routine name |
| `CAMPAIGN_AUTOPILOT_SCHEDULE` | Step 2 | `0 6 * * 1` | Cron (UTC) — Mondays 06:00 |
| `CAMPAIGN_ROUTINE_TIME_ZONE` | Step 2 | `UTC` | IANA time zone for the cron |
| `CAMPAIGN_AUTOPILOT_PROMPT` | Step 2 | _(standard briefing prompt)_ | Instruction sent to the agent each run |

---

## The one open question — unattended sending

Work IQ sends mail **on-behalf-of (OBO) the signed-in user**, so the email goes
*from that user's mailbox*. A **scheduled routine has no interactive user**, so
the sender identity for an unattended run is undefined until tested. This is the
one thing to validate in the tenant before relying on it:

- Which identity does the routine run as, and does it have an M365 Copilot licence?
- Does unattended Work IQ Mail work from the routine's agent identity, or does it
  need a dedicated **service / shared mailbox** to send as?

This code intentionally sets **no** sender address — it delegates entirely to
whatever identity Work IQ resolves at invocation (`build_workiq_tool()` only
passes the connection id; the routine action only names the agent). Verify with
`--test` and check `--list-runs` / the run's response.

## Can this be built without subscription access?

**Yes for the code; no for end-to-end validation.** The Work IQ tool wiring, the
routine script, and the tests are all written and unit-tested without any Azure
access. What requires the subscription (so it's on the repo owner) is registering
the routine against a live project, creating the Work IQ connection + admin
consent, and sending a real email — plus answering the unattended-sending
question above.

---

## Files

| File | Purpose |
|---|---|
| `workiq_email.py` | Build the `WorkIQPreviewTool` + the agent's email delivery instruction |
| `../../scripts/register_campaign_routine.py` | Create/manage the Foundry routine (`--test`, `--enable`, `--disable`, `--list-runs`) |
| `../../tests/test_register_campaign_routine.py` | Unit tests for the routine payloads and the Work IQ instruction (no Azure needed) |

## Tests

```bash
python -m unittest tests.test_register_campaign_routine -v
```
