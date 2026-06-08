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


def build_image(registry: str, image_name: str, context_path: Path) -> str:
    registry_name = registry.split(".")[0]
    build_tag = datetime.now().strftime("%Y%m%d%H%M%S")
    image_tag = f"{registry}/{image_name}:{build_tag}"
    subprocess.run(
        [
            "az",
            "acr",
            "build",
            "--registry",
            registry_name,
            "--image",
            image_tag,
            "--platform",
            "linux/amd64",
            str(context_path),
        ],
        check=True,
    )
    return image_tag
