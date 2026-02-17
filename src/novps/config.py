from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".novps"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_API_URL = "https://api.novps.io"


def load_config() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    return json.loads(CONFIG_FILE.read_text())


def save_config(config: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")


def get_token() -> str | None:
    return load_config().get("token")


def get_api_url() -> str:
    return os.environ.get("NOVPS_API_URL") or load_config().get("api_url") or DEFAULT_API_URL


DEFAULT_WS_URL = "wss://ws.novps.io"


def get_ws_url() -> str:
    return os.environ.get("NOVPS_WS_URL") or load_config().get("ws_url") or DEFAULT_WS_URL
