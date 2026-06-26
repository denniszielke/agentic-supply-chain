"""Grant the existing Foundry project identity the roles a hosted agent needs.

Python port of ``project-roles.bicep``. A Foundry *autopilot* agent runs under
the project's system-assigned managed identity, which needs:

  * **AcrPull** on the container registry — to pull the agent image.
  * **Cognitive Services User** on the account — to call model deployments.

``azd up`` in this repo already grants AcrPull to the project identity, so the
bicep version failed with ``RoleAssignmentExists`` (the assignment exists under
a different name). Doing it here with ``az role assignment create`` lets us treat
"already exists" as success and stay idempotent across re-runs.

Run standalone:

    python -m scripts.autopilot.grant_project_roles
"""

from __future__ import annotations

import subprocess

from .common import (
    ACR_PULL_ROLE_ID,
    COGNITIVE_SERVICES_USER_ROLE_ID,
    AutopilotConfig,
    az,
)


def _project_principal_id(config: AutopilotConfig) -> str:
    principal_id = az(
        "rest",
        "--method", "get",
        "--url",
        f"https://management.azure.com{config.project_resource_id}"
        "?api-version=2025-04-01-preview",
        "--query", "identity.principalId",
        "-o", "tsv",
    )
    if not principal_id:
        raise RuntimeError(
            "Could not resolve the project's system-assigned identity principal id."
        )
    return principal_id


def _assign_role(principal_id: str, role_id: str, scope: str, label: str) -> None:
    print(f"==> Granting {label} to project identity on {scope}")
    try:
        az(
            "role", "assignment", "create",
            "--assignee-object-id", principal_id,
            "--assignee-principal-type", "ServicePrincipal",
            "--role", role_id,
            "--scope", scope,
            capture=False,
        )
        print(f"==> {label} assignment created.")
    except subprocess.CalledProcessError as ex:
        # The CLI returns non-zero when the assignment already exists.
        print(f"==> {label} may already exist (ignoring): {ex}")


def grant_project_roles(config: AutopilotConfig | None = None) -> None:
    """Grant AcrPull + Cognitive Services User to the project identity."""
    config = config or AutopilotConfig.from_env()
    principal_id = _project_principal_id(config)
    print(f"==> Project system identity principal id: {principal_id}")
    _assign_role(principal_id, ACR_PULL_ROLE_ID, config.registry_scope, "AcrPull")
    _assign_role(
        principal_id,
        COGNITIVE_SERVICES_USER_ROLE_ID,
        config.account_scope,
        "Cognitive Services User",
    )


def main() -> int:
    try:
        grant_project_roles()
    except subprocess.CalledProcessError as ex:
        print(f"❌ Role assignment failed: {ex}")
        return 1
    except Exception as ex:  # noqa: BLE001
        print(f"❌ Role assignment failed: {ex}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
