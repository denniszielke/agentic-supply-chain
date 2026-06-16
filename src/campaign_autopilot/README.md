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
| `autopilot.py` | Orchestrator + CLI entry point (`--once`, `--dry-run`, `--sample`, `--loop`) |
| `report.py` | Runs the Campaign Planning Agent in-process and returns its Markdown |
| `email_renderer.py` | Markdown → styled HTML + plain-text email (pure, unit-tested) |
| `email_sender.py` | Pluggable transport: `acs` / `smtp` / `file` |
| `config.py` | Env-driven `AutopilotConfig` |
| `sample_report.md` | Bundled sample output for fully offline demos/tests |
| `Dockerfile` | Job image (runs `autopilot.py --once`) |
| `../../infra/core/host/job.bicep` | Container Apps Job (schedule trigger) |
| `../../scripts/deploy_campaign_autopilot.py` | Build image + deploy the job |

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
