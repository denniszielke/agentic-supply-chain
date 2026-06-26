"""Shared helpers for the campaign A365 autopilot deployment scripts.

These modules are Python ports of the PowerShell scripts in the Foundry
``foundry-autopilot-agent`` sample. Each step lives in its own file and exposes
a callable plus a ``__main__`` entry point so it can be run standalone, and they
are orchestrated together by ``scripts/deploy_campaign_autopilot.py``.

All Azure access tokens are acquired through the Azure CLI (``az account
get-access-token``) so the scripts use the same identity as the rest of the
repo's tooling. REST calls use ``requests`` (already a project dependency).
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Well-known constants (mirrors the foundry-autopilot-agent sample)
# ---------------------------------------------------------------------------

# Built-in Azure role definition ids.
COGNITIVE_SERVICES_USER_ROLE_ID = "a97b65f3-24c7-4388-baec-2e87135dc908"
ACR_PULL_ROLE_ID = "7f951dda-4ed3-4680-a7ca-43fe172d538d"
# First-party application ids used for OAuth2 permission grants.
APX_APP_ID = "5a807f24-c9de-44ee-a3a7-329e88a00ffc"
PROD_MCP_APP_ID = "ea9ffc3e-8a23-4a7d-836d-234d7c7565c1"

# Data-plane API versions.
AGENTS_API_VERSION = "2025-11-15-preview"

# Resource audiences for token acquisition.
AI_AZURE_RESOURCE = "https://ai.azure.com"
GRAPH_RESOURCE = "https://graph.microsoft.com/"
TEAMS_DEV_RESOURCE = "https://dev.teams.microsoft.com"

# Scope bundle granted to the blueprint service principal for M365 MCP servers.
MCP_OAUTH_SCOPE = (
    "McpServers.M365Admin.All McpServers.DASearch.All McpServers.WebSearch.All "
    "McpServers.Files.All AgentTools.MOSEvents.All McpServers.Admin365Graph.All "
    "McpServers.ERPAnalytics.All McpServers.DataverseCustom.All "
    "McpServers.Dataverse.All McpServers.D365Service.All McpServers.D365Sales.All "
    "McpServers.Management.All McpServersMetadata.Read.All McpServers.Developer.All "
    "McpServers.CopilotMCP.All McpServers.OneDriveSharepoint.All McpServers.Mail.All "
    "McpServers.Teams.All McpServers.Me.All McpServers.Calendar.All "
    "McpServers.SharepointLists.All McpServers.Knowledge.All McpServers.Excel.All "
    "McpServers.Word.All McpServers.PowerPoint.All"
)
APX_OAUTH_SCOPE = "AgentData.ReadWrite"


def repo_root() -> Path:
    """Return the repository root (two levels up from this file)."""
    return Path(__file__).resolve().parents[2]


def load_env() -> None:
    """Load ``./.env`` (written by ``azd up``) into the process environment."""
    env_path = repo_root() / ".env"
    load_dotenv(dotenv_path=env_path if env_path.exists() else None, override=False)


def az(*args: str, capture: bool = True, cwd: "str | Path | None" = None) -> str:
    """Run an ``az`` CLI command and return its stripped stdout.

    Raises ``subprocess.CalledProcessError`` on a non-zero exit code.
    """
    result = subprocess.run(
        ["az", *args],
        check=True,
        capture_output=capture,
        text=True,
        cwd=str(cwd) if cwd is not None else None,
    )
    return (result.stdout or "").strip()


def get_access_token(resource: str) -> str:
    """Acquire an access token for ``resource`` via the Azure CLI."""
    token = az(
        "account", "get-access-token",
        "--resource", resource,
        "--query", "accessToken",
        "-o", "tsv",
    )
    if not token:
        raise RuntimeError(f"Failed to acquire an access token for {resource}.")
    return token


def _host(endpoint: str) -> str:
    parsed = urlparse(endpoint if "://" in endpoint else f"https://{endpoint}")
    return parsed.netloc or parsed.path


@dataclass
class AutopilotConfig:
    """Resolved configuration for the campaign autopilot deployment."""

    subscription_id: str
    tenant_id: str
    resource_group: str
    location: str
    account_name: str
    project_name: str
    project_endpoint: str
    registry_login_server: str
    openai_endpoint: str
    model_deployment: str
    agent_name: str
    maib_name: str
    image_name: str

    @property
    def authority_endpoint(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}"

    @property
    def account_scope(self) -> str:
        return (
            f"/subscriptions/{self.subscription_id}/resourceGroups/"
            f"{self.resource_group}/providers/Microsoft.CognitiveServices/"
            f"accounts/{self.account_name}"
        )

    @property
    def project_resource_id(self) -> str:
        return f"{self.account_scope}/projects/{self.project_name}"

    @property
    def registry_name(self) -> str:
        return self.registry_login_server.split(".")[0]

    @property
    def registry_scope(self) -> str:
        return (
            f"/subscriptions/{self.subscription_id}/resourceGroups/"
            f"{self.resource_group}/providers/Microsoft.ContainerRegistry/"
            f"registries/{self.registry_name}"
        )

    @classmethod
    def from_env(cls) -> "AutopilotConfig":
        load_env()

        project_endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "").strip()
        if not project_endpoint:
            raise RuntimeError(
                "AZURE_AI_PROJECT_ENDPOINT is required (run `azd up` first)."
            )

        # The project endpoint looks like
        # https://<account>.services.ai.azure.com/api/projects/<project>
        host = _host(project_endpoint)
        account_name = os.getenv("AZURE_AI_ACCOUNT_NAME", "").strip() or host.split(".")[0]
        project_name = os.getenv("AZURE_AI_PROJECT_NAME", "").strip()
        if not project_name and "/projects/" in project_endpoint:
            project_name = project_endpoint.split("/projects/", 1)[1].split("/")[0]
        if not project_name:
            raise RuntimeError(
                "Could not resolve the project name. Set AZURE_AI_PROJECT_NAME."
            )

        registry = (
            os.getenv("AZURE_CONTAINER_REGISTRY_ENDPOINT", "").strip()
            or os.getenv("AZURE_REGISTRY", "").strip()
        )
        if not registry:
            raise RuntimeError(
                "AZURE_CONTAINER_REGISTRY_ENDPOINT (or AZURE_REGISTRY) is required."
            )

        resource_group = os.getenv("AZURE_RESOURCE_GROUP", "").strip()
        if not resource_group:
            env_name = os.getenv("AZURE_ENV_NAME", "").strip()
            resource_group = f"rg-{env_name}" if env_name else ""
        if not resource_group:
            raise RuntimeError("AZURE_RESOURCE_GROUP is required.")

        subscription_id = os.getenv("AZURE_SUBSCRIPTION_ID", "").strip() or az(
            "account", "show", "--query", "id", "-o", "tsv"
        )
        tenant_id = os.getenv("AZURE_TENANT_ID", "").strip() or az(
            "account", "show", "--query", "tenantId", "-o", "tsv"
        )
        location = os.getenv("AZURE_LOCATION", "").strip() or az(
            "group", "show", "-n", resource_group, "--query", "location", "-o", "tsv"
        )

        openai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
        if not openai_endpoint:
            openai_endpoint = f"https://{account_name}.openai.azure.com/"

        model_deployment = (
            os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME", "").strip()
            or os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "").strip()
            or "gpt-4.1-mini"
        )

        agent_name = os.getenv("AZURE_AI_CAMPAIGN_AGENT_NAME", "campaign-a365-agent").strip()
        maib_name = os.getenv("MAIB_NAME", "").strip() or f"{agent_name}-maib"
        image_name = os.getenv("CAMPAIGN_A365_IMAGE_NAME", "campaign-a365-agent").strip()

        return cls(
            subscription_id=subscription_id,
            tenant_id=tenant_id,
            resource_group=resource_group,
            location=location,
            account_name=account_name,
            project_name=project_name,
            project_endpoint=project_endpoint.rstrip("/"),
            registry_login_server=registry,
            openai_endpoint=openai_endpoint,
            model_deployment=model_deployment,
            agent_name=agent_name,
            maib_name=maib_name,
            image_name=image_name,
        )

    def summary(self) -> str:
        return json.dumps(
            {
                "subscription_id": self.subscription_id,
                "tenant_id": self.tenant_id,
                "resource_group": self.resource_group,
                "location": self.location,
                "account_name": self.account_name,
                "project_name": self.project_name,
                "registry_login_server": self.registry_login_server,
                "model_deployment": self.model_deployment,
                "agent_name": self.agent_name,
                "maib_name": self.maib_name,
            },
            indent=2,
        )
