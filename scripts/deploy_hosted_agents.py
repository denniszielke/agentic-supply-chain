from __future__ import annotations

import os
from pathlib import Path

from azure.ai.projects.models import (
    AgentEndpoint,
    AgentEndpointProtocol,
    AgentProtocol,
    HostedAgentDefinition,
    ProtocolVersionRecord,
)

from scripts.deploy_helpers import build_image, get_client


def deploy() -> None:
    project_endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT")
    registry = os.getenv("AZURE_CONTAINER_REGISTRY_ENDPOINT")
    if not project_endpoint or not registry:
        print(
            "Skipping hosted agent deployment: AZURE_AI_PROJECT_ENDPOINT and "
            "AZURE_CONTAINER_REGISTRY_ENDPOINT are required."
        )
        return

    agent_name = os.getenv("AZURE_AI_HOSTED_AGENT_NAME", "shopping-agent")
    model_deployment_name = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4.1-mini")
    aoai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    openai_api_version = os.getenv("OPENAI_API_VERSION", "2024-05-01-preview")

    client = get_client()
    source_path = Path(__file__).resolve().parents[1]
    dockerfile = str(source_path / "src" / "shopping_agent" / "Dockerfile")
    image_tag = build_image(registry, agent_name, source_path, dockerfile=dockerfile)

    env_vars = {
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
        "AZURE_OPENAI_ENDPOINT": aoai_endpoint,
        "OPENAI_API_VERSION": openai_api_version,
    }
    env_vars = {k: v for k, v in env_vars.items() if v}

    protocols = [
        ProtocolVersionRecord(protocol=AgentProtocol.RESPONSES, version="1.0.0"),
    ]
    client.agents.create_version(
        agent_name=agent_name,
        description="Shopping planner hosted agent",
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

    endpoint_config = AgentEndpoint(
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


if __name__ == "__main__":
    deploy()
