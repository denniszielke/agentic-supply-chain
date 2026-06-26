"""Build the campaign A365 agent image using a REMOTE Azure Container Registry
build (``az acr build``) — no local Docker required.

Python port of ``build-docker-image-acr.ps1``. The build context is the agent
package directory (``src/campaign_a365_agent``) and the Dockerfile lives at
``foundry-infra/Dockerfile`` inside it. The blueprint client id, authority,
tenant, Azure OpenAI endpoint and model deployment are passed as build args so
they are baked into the image's environment (matching the sample).

Run standalone (requires AGENT_IDENTITY_BLUEPRINT_ID in the environment):

    AGENT_IDENTITY_BLUEPRINT_ID=<id> python -m scripts.autopilot.build_image
"""

from __future__ import annotations

import os
import subprocess

from .common import AutopilotConfig, az, repo_root


def build_image(blueprint_client_id: str, config: AutopilotConfig | None = None) -> str:
    """Build & push the agent image via ACR Build. Returns the image reference."""
    config = config or AutopilotConfig.from_env()
    if not blueprint_client_id:
        raise ValueError("blueprint_client_id is required for the image build.")

    registry_name = config.registry_login_server.split(".")[0]
    context_path = repo_root() / "src" / "campaign_a365_agent"
    image_ref = f"{config.image_name}:latest"

    print(f"==> Building {image_ref} via ACR Build in registry: {registry_name}")
    # Run with the build context as CWD so `az acr build` resolves the relative
    # --file path (its local existence check is CWD-relative) and the context
    # `.` correctly — the Dockerfile's `COPY .` expects the agent package dir.
    az(
        "acr", "build",
        "--registry", registry_name,
        "--image", image_ref,
        "--platform", "linux/amd64",
        "--file", "foundry-infra/Dockerfile",
        "--build-arg", f"BLUEPRINT_CLIENT_ID={blueprint_client_id}",
        "--build-arg", f"AUTHORITY_ENDPOINT={config.authority_endpoint}",
        "--build-arg", f"TENANT_ID={config.tenant_id}",
        "--build-arg", f"AZURE_OPENAI_ENDPOINT={config.openai_endpoint}",
        "--build-arg", f"MODEL_DEPLOYMENT={config.model_deployment}",
        ".",
        capture=False,
        cwd=context_path,
    )

    full_ref = f"{config.registry_login_server}/{image_ref}"
    print(f"==> Image built and pushed: {full_ref}")
    return full_ref


def main() -> int:
    blueprint_client_id = os.getenv("AGENT_IDENTITY_BLUEPRINT_ID", "").strip()
    if not blueprint_client_id:
        print("❌ AGENT_IDENTITY_BLUEPRINT_ID is required (run provision_infra first).")
        return 1
    try:
        build_image(blueprint_client_id)
    except subprocess.CalledProcessError as ex:
        print(f"❌ ACR build failed: {ex}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
