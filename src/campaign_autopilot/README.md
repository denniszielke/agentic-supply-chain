# campaign_autopilot — Scheduled, emailed Campaign Planning Agent reports

## Overview

`campaign_autopilot` runs the [Campaign Planning Agent](../campaign_agent/) **on a
schedule** and emails its output as a nicely formatted report. It is the
"autopilot" use case: every Monday morning a supplier/category manager receives a
competitor-and-margin briefing in their inbox, with no one having to run anything
by hand.

It is **purely additive** — it imports and reuses `src/campaign_agent/agent.py`
rather than modifying it, so the agent and the autopilot can be developed and
deployed independently.

## Two automation approaches

There are two ways to run this on a schedule. Pick based on what your Foundry
project supports.

| | **A. Foundry-native** (recommended) | **B. Portable fallback** |
|---|---|---|
| Schedule | **Foundry routine** (`scripts/register_campaign_routine.py`) | Azure Container Apps **Job** (`scripts/deploy_campaign_autopilot.py`) |
| Email | Agent sends via **Work IQ Mail** (Microsoft 365 / Outlook) | This process sends via **ACS** or **SMTP** |
| Who emails | the **agent itself** (a Work IQ tool) | the autopilot wrapper (`email_sender.py`) |
| Best when | the project is in a routines region, **not VNet-restricted**, with M365 Copilot licences | Work IQ / routines preview isn't available to you (VNet, region, licensing) |

> **Why both?** Dennis asked for the Foundry-native shape (routine + Work IQ
> Mail) because that's how a Foundry-hosted agent should be automated. But both
> Foundry routines **and** Work IQ are **preview** features with real constraints
> (see [limitations](#preview-limitations--open-questions)). The ACA Job + ACS
> path is kept as a portable fallback that works without those previews — and it
> doubles as the way to demo the report offline.

## Can we build this without access to the subscription?

**Yes for the code; no for end-to-end validation.** All of this — the routine
registration script, the Work IQ tool wiring, the bicep, the deploy scripts, the
renderer and its tests — is written and unit-tested **without** any Azure
subscription. What *requires* the subscription (so it's on the repo owner to run)
is the actual deployment and a live test:

- registering the routine against a real Foundry project,
- creating the Work IQ connection + Entra admin consent and sending a real email,
- confirming the project's region/VNet/licensing satisfy the preview
  requirements.

The offline path (`--dry-run --sample`) lets anyone preview the email formatting
with no Azure at all.

---

## Approach A — Foundry-native (routine + Work IQ Mail)

```
┌──────────────────┐   cron    ┌───────────────────────────┐   Work IQ A2A   ┌────────────┐
│ Foundry routine  │ ────────▶ │ Campaign Planning Agent   │ ──────────────▶ │ Outlook /  │
│ schedule trigger │  Mon 06:00│  • produce briefing       │   (OBO user)    │ M365 Mail  │
└──────────────────┘           │  • email it via Work IQ   │                 └─────┬──────┘
                               └───────────────────────────┘                       ▼
                                                                          📧 recipient inbox(es)
```

A Foundry **routine** triggers the agent on a schedule; the routine only
*invokes* the agent, so the **agent itself** sends the email through the
**Work IQ Mail** tool. No Container Apps Job, no ACS.

### 1. Give the campaign agent the Work IQ Mail tool

Work IQ connects a Foundry agent to Microsoft 365 over A2A, on-behalf-of the
signed-in user. Add the tool to the campaign agent and tell it to email the
finished briefing. `src/campaign_autopilot/workiq_email.py` provides both helpers:

```python
from src.campaign_autopilot.workiq_email import build_workiq_tool, augmented_instructions

# when building the campaign agent (src/campaign_agent/agent.py):
tools = [search_competitor_promotions, _pricing_tool, build_workiq_tool()]
instructions = augmented_instructions(
    CAMPAIGN_AGENT_SYSTEM_PROMPT,
    recipients=["category-manager@aldi-sued.example"],
    report_title="Weekly Competitor & Margin Briefing",
)
```

This is the only change to the agent and it's opt-in (gated on
`WORK_IQ_PROJECT_CONNECTION_ID`). Prereqs (subscription owner): a Work IQ project
connection, Entra admin consent for `WorkIQAgent.Ask`, and an M365 Copilot
licence for each recipient identity. See
[Connect agents to Microsoft 365 with Work IQ](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/work-iq).

### 2. Register the routine

```bash
# create/update the weekly schedule (default: Mondays 06:00 UTC)
python -m scripts.register_campaign_routine

# fire it once now (don't wait for the schedule), then inspect runs
python -m scripts.register_campaign_routine --test
python -m scripts.register_campaign_routine --list-runs

# pause / resume
python -m scripts.register_campaign_routine --disable
python -m scripts.register_campaign_routine --enable
```

Config: `CAMPAIGN_ROUTINE_NAME`, `AZURE_AI_CAMPAIGN_AGENT_NAME` (default
`campaign-agent`), `CAMPAIGN_AUTOPILOT_SCHEDULE` (cron, default `0 6 * * 1`),
`CAMPAIGN_ROUTINE_TIME_ZONE` (default `UTC`). Routines need
`azure-ai-projects>=2.2.0` (already pinned) and are **preview**
(`Foundry-Features: Routines=V1Preview`).

### Preview limitations & open questions

- **Routines are region-limited** in preview (East US, East US 2, West US,
  West US 2, West Central US, North Central US, Sweden Central, Japan East at the
  time of writing). Confirm your project's region first.
- **Work IQ does not support VNet-restricted projects.** This repo's infra uses a
  VNet, so the Foundry-native email path needs a non-VNet project (or use the
  fallback).
- **Unattended OBO.** Work IQ runs on-behalf-of the signed-in user; a *scheduled*
  routine has no interactive user. Whether a routine-invoked agent can send Work
  IQ Mail unattended must be validated against your tenant — this is the main
  open question for the Foundry-native path. The fallback (ACS) has no such
  dependency.

---

## Approach B — Portable fallback (Container Apps Job + ACS/SMTP)

It is **purely additive** — imports and reuses `src/campaign_agent/agent.py`.

```
┌──────────────────────────┐     cron      ┌──────────────────────────────┐
│ Azure Container Apps Job │ ─────────────▶ │ campaign_autopilot (--once)  │
│  triggerType: Schedule   │  e.g. Mon 06:00│  1. run Campaign Agent        │
└──────────────────────────┘                │  2. render Markdown → email   │
                                            │  3. send (ACS / SMTP)         │
                                            └──────────────┬───────────────┘
                                                           ▼
                                              📧 recipient inbox(es)
```

### How it works

1. **Run the agent.** The job imports the assembled `agent` from
   `src/campaign_agent/agent.py` and calls it in-process with a configurable
   prompt (default: a weekly competitor-and-margin briefing). This reuses the
   agent's exact prompt, skills framing, competitor-search tool and pricing
   toolbox — and works whether or not a hosted-agent endpoint is deployed.
2. **Render the email.** The agent returns GitHub-flavoured Markdown (executive
   summary, a table, a risk list). `email_renderer.py` converts it into a styled,
   inline-CSS HTML body plus a plain-text alternative.
3. **Deliver it.** `email_sender.py` sends via the configured provider — Azure
   Communication Services (default), SMTP, or `file` (writes to disk for dry runs).

### Why a Container Apps Job

The whole repo deploys to Azure Container Apps via ACR builds. A Container Apps
**Job** with a `Schedule` trigger is the native "run on a cron, then exit"
primitive in that stack: it scales to zero between runs and reuses the same
managed identity and ACR as the other services.

---

## Key files

| File | Purpose |
|---|---|
| `workiq_email.py` | **(A)** Work IQ Mail tool builder + email instruction for the agent |
| `../../scripts/register_campaign_routine.py` | **(A)** Create/manage the Foundry routine (schedule) |
| `autopilot.py` | **(B)** Orchestrator + CLI (`--once`, `--dry-run`, `--sample`, `--loop`) |
| `report.py` | **(B)** Runs the Campaign Planning Agent in-process and returns its Markdown |
| `email_renderer.py` | Markdown → styled HTML + plain-text email (pure, unit-tested) |
| `email_sender.py` | **(B)** Pluggable transport: `acs` / `smtp` / `file` |
| `config.py` | Env-driven `AutopilotConfig` |
| `sample_report.md` | Bundled sample output for fully offline demos/tests |
| `Dockerfile` | **(B)** Job image (runs `autopilot.py --once`) |
| `../../infra/core/host/job.bicep` | **(B)** Container Apps Job (schedule trigger) |
| `../../scripts/deploy_campaign_autopilot.py` | **(B)** Build image + deploy the job |

_(A) = Foundry-native approach · (B) = portable fallback._

---

## Run locally

From the repository root, with `./.env` populated (see [environment variables](#environment-variables)).

### Fully offline (no Azure, no email) — see the formatting instantly

Uses the bundled sample report and writes the rendered email to disk:

```bash
python -m src.campaign_autopilot.autopilot --dry-run --sample
# writes data/autopilot/campaign-report-*.html  (open it in a browser)
```

### Dry run against the real agent (renders to disk, sends nothing)

Runs the actual Campaign Planning Agent, then writes the email to disk instead of
sending. Requires the agent's own env (`AZURE_AI_PROJECT_ENDPOINT`,
`AZURE_SEARCH_ENDPOINT`, pricing toolbox/MCP) but **no** email configuration:

```bash
python -m src.campaign_autopilot.autopilot --dry-run
```

### Send a real email (one cycle)

```bash
export EMAIL_PROVIDER=smtp                  # or acs
export EMAIL_RECIPIENTS="manager@example.com"
export EMAIL_SENDER_ADDRESS="autopilot@example.com"
export SMTP_HOST=smtp.example.com
python -m src.campaign_autopilot.autopilot --once
```

### Local schedule loop (dev convenience)

```bash
pip install croniter
python -m src.campaign_autopilot.autopilot --loop --cron "0 6 * * 1"
```

> In production the **Container Apps Job** provides the schedule and runs
> `--once`; `--loop` is only for running it continuously on your own machine.

---

## Environment variables

The agent reads its own config (Foundry project, AI Search, pricing toolbox) — see
[`src/campaign_agent`](../campaign_agent/). The variables below configure the
autopilot wrapper.

### Email / report

| Variable | Required | Default | Description |
|---|---|---|---|
| `EMAIL_PROVIDER` | | `acs` | `acs`, `smtp`, or `file` |
| `EMAIL_RECIPIENTS` | for acs/smtp | — | Comma/semicolon separated recipient addresses |
| `EMAIL_SENDER_ADDRESS` | for acs/smtp | — | From address |
| `EMAIL_SUBJECT_PREFIX` | | `[Campaign Autopilot]` | Prefix for every subject |
| `AUTOPILOT_OUTPUT_DIR` | | `data/autopilot` | Where `file`/`--dry-run` writes the email |
| `CAMPAIGN_AUTOPILOT_REPORT_TITLE` | | `Weekly Competitor & Margin Briefing` | Email header title |
| `CAMPAIGN_AUTOPILOT_PROMPT` | | _(strong default)_ | The instruction sent to the agent |
| `CAMPAIGN_AUTOPILOT_SCHEDULE` | | `0 6 * * 1` | Cron (UTC); used by the Job and `--loop` |
| `CAMPAIGN_AGENT_ENDPOINT` | | — | Optional deployed endpoint (falls back to in-process) |

### Azure Communication Services (`EMAIL_PROVIDER=acs`)

| Variable | Required | Description |
|---|---|---|
| `ACS_CONNECTION_STRING` | one of these | ACS connection string (used directly) |
| `ACS_ENDPOINT` | one of these | ACS endpoint; used with managed identity / `DefaultAzureCredential` |

### SMTP (`EMAIL_PROVIDER=smtp`)

| Variable | Required | Default | Description |
|---|---|---|---|
| `SMTP_HOST` | ✅ | — | SMTP server host |
| `SMTP_PORT` | | `587` | SMTP server port |
| `SMTP_USERNAME` | | — | Username (enables login) |
| `SMTP_PASSWORD` | | — | Password |
| `SMTP_USE_TLS` | | `true` | Issue STARTTLS before sending |

---

## Deploy to Azure (for the subscription owner)

> These steps run in **Dennis's** subscription. The image builds in ACR (no local
> Docker) and the job slots into the existing Container Apps environment and
> managed identity. All variables come from `./.env` (written by `azd up`).

### 1. Choose an email transport

**Option A — Azure Communication Services (recommended, keyless).**

```bash
# Create an ACS + Email Communication Service and a managed (Azure) sender domain,
# then link them. Replace <rg> and names as needed.
az communication create -g <rg> -n acs-aldi --location global --data-location europe
az communication email create -g <rg> -n acs-email-aldi --location global --data-location europe
az communication email domain create -g <rg> --email-service-name acs-email-aldi \
    --name AzureManagedDomain --domain-management AzureManaged
# Grant the existing user-assigned identity the "Communication and Email Service Owner"
# role on the ACS resource so DefaultAzureCredential can send without keys.
```

Set in `./.env`:

```
EMAIL_PROVIDER=acs
ACS_ENDPOINT=https://acs-aldi.communication.azure.com
EMAIL_SENDER_ADDRESS=DoNotReply@<your-azure-managed-domain>.azurecomm.net
EMAIL_RECIPIENTS=category-manager@aldi-sued.example
```

(Or simply set `ACS_CONNECTION_STRING=` instead of `ACS_ENDPOINT` to use a key.)

**Option B — SMTP.** Set `EMAIL_PROVIDER=smtp` plus the `SMTP_*` variables.

### 2. Build the image and deploy the scheduled job

```bash
# build in ACR (timestamp tag + :latest) and deploy the Container Apps Job
python -m scripts.deploy_campaign_autopilot --build

# deploy only (image already in ACR; uses :latest or the TAG env var)
python -m scripts.deploy_campaign_autopilot
```

The deploy script forwards the campaign agent's runtime config **and** the
autopilot email config into the job. Sensitive values (`ACS_CONNECTION_STRING`,
`SMTP_PASSWORD`) are deployed as Container App **secrets**, not plain env vars.

### 3. Set / change the schedule

The cron comes from `CAMPAIGN_AUTOPILOT_SCHEDULE` (default `0 6 * * 1` — Mondays
06:00 UTC). Change it and re-run the deploy, or update the job directly:

```bash
az containerapp job update -g <rg> -n campaign-autopilot \
    --cron-expression "0 6 * * 1"
```

### 4. Trigger a one-off run now (don't wait for the schedule)

```bash
az containerapp job start -g <rg> -n campaign-autopilot
# inspect the run
az containerapp job execution list -g <rg> -n campaign-autopilot -o table
```

---

## Tests

The renderer and config are pure and need no Azure:

```bash
python -m unittest discover -s tests -v
```
