from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path

from azure.ai.projects import AIProjectClient
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
