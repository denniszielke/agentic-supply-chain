"""Deploy the Campaign Autopilot as a scheduled Azure Container Apps Job.

The autopilot runs the Campaign Planning Agent on a cron schedule and emails the
result. This script builds its image in ACR and deploys the job via
``infra/core/host/job.bicep``, wiring in both the campaign agent's runtime
configuration (Foundry project, AI Search, pricing toolbox) and the autopilot's
email configuration. Sensitive values (ACS connection string, SMTP password) are
deployed as Container App *secrets* rather than plain env vars.

Run it after ``azd up`` has provisioned the infrastructure and the pricing
toolbox/campaign agent have been set up (so the in-process agent can reach them).

Usage::

    # build the image in ACR, then deploy the scheduled job
    python -m scripts.deploy_campaign_autopilot --build

    # deploy only (image already in ACR, uses :latest or the TAG env var)
    python -m scripts.deploy_campaign_autopilot

Environment variables (populated from ``.env`` after ``azd up``):
  AZURE_RESOURCE_GROUP                    target resource group (required)
  AZURE_REGISTRY                          ACR login server (required)
  AZURE_CONTAINER_APPS_ENVIRONMENT_NAME   Container Apps environment (required)
  AZURE_IDENTITY_NAME                     user-assigned managed identity (required)
  AZURE_AI_PROJECT_ENDPOINT               Foundry project endpoint (required)
  TAG                                     image tag to deploy (default: latest)

Email / report variables (see src/campaign_autopilot/config.py):
  EMAIL_PROVIDER, EMAIL_RECIPIENTS, EMAIL_SENDER_ADDRESS, EMAIL_SUBJECT_PREFIX,
  ACS_CONNECTION_STRING, ACS_ENDPOINT,
  SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_USE_TLS,
  CAMPAIGN_AUTOPILOT_REPORT_TITLE, CAMPAIGN_AUTOPILOT_PROMPT,
  CAMPAIGN_AUTOPILOT_SCHEDULE (cron, default "0 6 * * 1"),
  PRICING_TOOLBOX_NAME, TOOLBOX_MCP_ENDPOINT, PRICING_MCP_URL
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.deploy_helpers import build_image, get_env, shared_agent_env

APP_NAME = os.getenv("CAMPAIGN_AUTOPILOT_APP_NAME", "campaign-autopilot")
_DOCKERFILE = "src/campaign_autopilot/Dockerfile"
_DEFAULT_SCHEDULE = "0 6 * * 1"

# Values that must never be deployed as plain-text env vars.
_SENSITIVE_KEYS = {"ACS_CONNECTION_STRING", "SMTP_PASSWORD"}

# Autopilot-specific env vars forwarded to the job (when set in the environment).
_AUTOPILOT_ENV_KEYS = (
    "EMAIL_PROVIDER",
    "EMAIL_RECIPIENTS",
    "EMAIL_SENDER_ADDRESS",
    "EMAIL_SUBJECT_PREFIX",
    "AUTOPILOT_OUTPUT_DIR",
    "ACS_CONNECTION_STRING",
    "ACS_ENDPOINT",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "SMTP_USE_TLS",
    "CAMPAIGN_AUTOPILOT_REPORT_TITLE",
    "CAMPAIGN_AUTOPILOT_PROMPT",
    "CAMPAIGN_AUTOPILOT_SCHEDULE",
    "PRICING_TOOLBOX_NAME",
    "TOOLBOX_MCP_ENDPOINT",
    "PRICING_MCP_URL",
)


def _registry_name(login_server: str) -> str:
    return login_server.removesuffix(".azurecr.io")


def _identity_client_id(resource_group: str, identity_name: str) -> str:
    """Return the client id of the user-assigned identity (for DefaultAzureCredential)."""
    result = subprocess.run(
        [
            "az", "identity", "show",
            "-g", resource_group,
            "-n", identity_name,
            "--query", "clientId",
            "-o", "tsv",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def build() -> str:
    """Build the campaign-autopilot image in ACR (timestamp tag + :latest)."""
    registry = get_env("AZURE_REGISTRY")
    source_path = Path(__file__).resolve().parents[1]
    dockerfile = str(source_path / _DOCKERFILE)
    return build_image(registry, APP_NAME, source_path, dockerfile=dockerfile)


def _collect_env() -> dict[str, str]:
    """Assemble the full env dict for the job: agent config + autopilot config."""
    project_endpoint = get_env("AZURE_AI_PROJECT_ENDPOINT")
    env_vars = dict(shared_agent_env(project_endpoint))

    resource_group = get_env("AZURE_RESOURCE_GROUP")
    identity_name = get_env("AZURE_IDENTITY_NAME")
    # DefaultAzureCredential inside the container needs the identity's client id.
    env_vars["AZURE_CLIENT_ID"] = _identity_client_id(resource_group, identity_name)

    for key in _AUTOPILOT_ENV_KEYS:
        value = os.getenv(key, "")
        if value:
            env_vars[key] = value

    return env_vars


def _split_env_and_secrets(env_vars: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """Split a flat env dict into a job env array and a secrets array.

    Sensitive keys become Container App secrets and are referenced from the env
    via ``secretRef``; everything else is a plain ``{name, value}`` entry.
    """
    env_array: list[dict] = []
    secrets_array: list[dict] = []
    for name, value in env_vars.items():
        if not value:
            continue
        if name in _SENSITIVE_KEYS:
            secret_name = name.lower().replace("_", "-")
            secrets_array.append({"name": secret_name, "value": value})
            env_array.append({"name": name, "secretRef": secret_name})
        else:
            env_array.append({"name": name, "value": value})
    return env_array, secrets_array


def deploy(tag: str | None = None) -> None:
    resource_group = get_env("AZURE_RESOURCE_GROUP")
    registry = get_env("AZURE_REGISTRY")
    environment_name = get_env("AZURE_CONTAINER_APPS_ENVIRONMENT_NAME")
    identity_name = get_env("AZURE_IDENTITY_NAME")
    tag = tag or os.getenv("TAG", "latest")
    cron = os.getenv("CAMPAIGN_AUTOPILOT_SCHEDULE", _DEFAULT_SCHEDULE)

    image_ref = f"{registry}/{APP_NAME}:{tag}"
    env_array, secrets_array = _split_env_and_secrets(_collect_env())

    job_bicep = Path(__file__).resolve().parents[1] / "infra" / "core" / "host" / "job.bicep"

    params = [
        f"name={APP_NAME}",
        f"containerAppsEnvironmentName={environment_name}",
        f"containerRegistryName={_registry_name(registry)}",
        f"identityName={identity_name}",
        f"imageName={image_ref}",
        f"envJson={json.dumps(env_array)}",
        f"secretsJson={json.dumps(secrets_array)}",
        f"cronExpression={cron}",
    ]

    print(f"==> Deploying Container Apps Job '{APP_NAME}' (image {image_ref}, cron '{cron}')")
    subprocess.run(
        [
            "az", "deployment", "group", "create",
            "--resource-group", resource_group,
            "--name", f"deploy-{APP_NAME}-{tag}",
            "--template-file", str(job_bicep),
            "--parameters", *params,
        ],
        check=True,
    )

    print(f"\nCampaign Autopilot job '{APP_NAME}' deployed (schedule: {cron}).")
    print("Trigger a one-off run now with:")
    print(f"  az containerapp job start -g {resource_group} -n {APP_NAME}")


if __name__ == "__main__":
    do_build = "--build" in sys.argv
    built_tag: str | None = None
    if do_build:
        built_tag = build()
    deploy(tag=built_tag)
