from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    AgentEndpointConfig,
    AgentEndpointProtocol,
    AgentProtocol,
    HostedAgentDefinition,
    ProtocolVersionRecord,
)
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv(override=True)


def get_env(name: str, required: bool = True, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value or ""


def get_client() -> AIProjectClient:
    return AIProjectClient(
        endpoint=get_env("AZURE_AI_PROJECT_ENDPOINT"),
        credential=DefaultAzureCredential(),
        allow_preview=True,
    )


def build_image(
    registry: str,
    image_name: str,
    context_path: Path,
    dockerfile: str | None = None,
) -> str:
    registry_name = registry.removesuffix(".azurecr.io")
    build_tag = datetime.now().strftime("%Y%m%d%H%M%S")
    image_tag = f"{registry}/{image_name}:{build_tag}"
    cmd = [
        "az",
        "acr",
        "build",
        "--registry",
        registry_name,
        "--image",
        image_tag,
        "--platform",
        "linux/amd64",
    ]
    if dockerfile:
        cmd += ["--file", dockerfile]
    cmd.append(str(context_path))
    subprocess.run(cmd, check=True)
    return image_tag


def _registry_name(login_server: str) -> str:
    """Strip the .azurecr.io suffix to get the bare ACR resource name."""
    return login_server.removesuffix(".azurecr.io")


def get_container_app_fqdn(resource_group: str, app_name: str) -> str:
    """Return the ingress FQDN of a deployed Container App (empty if none)."""
    result = subprocess.run(
        [
            "az", "containerapp", "show",
            "--resource-group", resource_group,
            "--name", app_name,
            "--query", "properties.configuration.ingress.fqdn",
            "--output", "tsv",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def deploy_container_app(
    *,
    app_name: str,
    image_name: str,
    port: int,
    external: bool,
    env_vars: dict[str, str],
    tag: str | None = None,
) -> str:
    """Deploy a single Container App via ``app.bicep`` and return its FQDN.

    Reads ``AZURE_RESOURCE_GROUP``, ``AZURE_REGISTRY``,
    ``AZURE_CONTAINER_APPS_ENVIRONMENT_NAME`` and ``AZURE_IDENTITY_NAME`` from
    the environment. ``tag`` defaults to the ``TAG`` env var or ``latest``.
    """
    resource_group = get_env("AZURE_RESOURCE_GROUP")
    registry = get_env("AZURE_REGISTRY")
    environment_name = get_env("AZURE_CONTAINER_APPS_ENVIRONMENT_NAME")
    identity_name = get_env("AZURE_IDENTITY_NAME")
    tag = tag or os.getenv("TAG", "latest")
    app_bicep = Path(__file__).resolve().parents[1] / "infra" / "core" / "host" / "app.bicep"

    image_ref = f"{registry}/{image_name}:{tag}"
    env_json = json.dumps(
        [{"name": k, "value": v} for k, v in env_vars.items() if v]
    )

    print(f"==> Deploying Container App '{app_name}' with image {image_ref}")
    subprocess.run(
        [
            "az", "deployment", "group", "create",
            "--resource-group", resource_group,
            "--template-file", str(app_bicep),
            "--parameters",
            f"name={app_name}",
            f"containerAppsEnvironmentName={environment_name}",
            f"containerRegistryName={_registry_name(registry)}",
            f"identityName={identity_name}",
            f"imageName={image_ref}",
            f"targetPort={port}",
            f"external={'true' if external else 'false'}",
            f"envJson={env_json}",
        ],
        check=True,
    )
    return get_container_app_fqdn(resource_group, app_name)


def shared_agent_env(project_endpoint: str) -> dict[str, str]:
    """Environment variables common to every Foundry hosted agent."""
    model_deployment_name = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4.1-mini")
    return {
        "AZURE_SEARCH_ENDPOINT": os.getenv("AZURE_SEARCH_ENDPOINT", ""),
        "AZURE_SEARCH_SUPPLIER_INDEX_NAME": os.getenv("AZURE_SEARCH_SUPPLIER_INDEX_NAME", "retail-suppliers"),
        "AZURE_SEARCH_CATEGORY_INDEX_NAME": os.getenv("AZURE_SEARCH_CATEGORY_INDEX_NAME", "retail-categories"),
        "AZURE_SEARCH_ITEM_INDEX_NAME": os.getenv("AZURE_SEARCH_ITEM_INDEX_NAME", "retail-items"),
        "AZURE_SEARCH_ADMIN_KEY": os.getenv("AZURE_SEARCH_ADMIN_KEY", ""),
        "APPLICATIONINSIGHTS_CONNECTION_STRING": os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", ""),
        "AZURE_AI_PROJECT_ENDPOINT": project_endpoint,
        "AZURE_AI_PROJECT_ID": os.getenv("AZURE_AI_PROJECT_ID", ""),
        "AZURE_AI_PROJECT_NAME": os.getenv("AZURE_AI_PROJECT_NAME", ""),
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": model_deployment_name,
        "AZURE_OPENAI_CHAT_DEPLOYMENT_NAME": os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME", model_deployment_name),
        "AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME": os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME", "text-embedding-3-small"),
        "AZURE_OPENAI_ENDPOINT": os.getenv("AZURE_OPENAI_ENDPOINT", ""),
        "OPENAI_API_VERSION": os.getenv("OPENAI_API_VERSION", "2024-05-01-preview"),
    }


def deploy_hosted_agent(
    client: AIProjectClient,
    *,
    agent_name: str,
    description: str,
    registry: str,
    project_endpoint: str,
    dockerfile_rel: str,
    extra_env: dict[str, str] | None = None,
) -> None:
    """Build the agent image and create/patch a Foundry hosted agent version."""
    source_path = Path(__file__).resolve().parents[1]
    dockerfile = str(source_path / dockerfile_rel)
    image_tag = build_image(registry, agent_name, source_path, dockerfile=dockerfile)

    env_vars = {**shared_agent_env(project_endpoint), **(extra_env or {})}
    env_vars = {k: v for k, v in env_vars.items() if v}

    protocols = [
        ProtocolVersionRecord(protocol=AgentProtocol.RESPONSES, version="1.0.0"),
    ]
    client.agents.create_version(
        agent_name=agent_name,
        description=description,
        definition=HostedAgentDefinition(
            container_protocol_versions=protocols,
            cpu="1",
            memory="2Gi",
            image=image_tag,
            environment_variables=env_vars,
        ),
        metadata={"enableVnextExperience": "true"},
        headers={"Foundry-Features": "HostedAgents=V1Preview"},
    )

    endpoint_config = AgentEndpointConfig(
        protocols=[
            AgentEndpointProtocol.RESPONSES,
            AgentEndpointProtocol.A2A,
            AgentEndpointProtocol.INVOCATIONS,
        ],
    )
    client.beta.agents.patch_agent_details(
        agent_name=agent_name,
        agent_endpoint=endpoint_config,
    )
    print(f"Hosted agent '{agent_name}' deployed from source.")
