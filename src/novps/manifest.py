from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_ENV_LINE_PATTERN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


class ManifestError(Exception):
    pass


def _substitute(value: Any, env: dict[str, str]) -> Any:
    if isinstance(value, str):
        missing: list[str] = []

        def replace(match: re.Match[str]) -> str:
            var_name = match.group(1)
            if var_name not in env:
                missing.append(var_name)
                return ""
            return env[var_name]

        result = _VAR_PATTERN.sub(replace, value)
        if missing:
            raise ManifestError(f"Undefined environment variable(s): {', '.join(sorted(set(missing)))}")
        return result
    if isinstance(value, list):
        return [_substitute(item, env) for item in value]
    if isinstance(value, dict):
        return {k: _substitute(v, env) for k, v in value.items()}
    return value


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        inner = value[1:-1]
        if value[0] == '"':
            return inner.encode("utf-8").decode("unicode_escape")
        return inner
    return value


def load_env_file(path: str | Path) -> dict[str, str]:
    """Parse a .env file into a dict.

    Supported: KEY=value, KEY="value", KEY='value', export KEY=value.
    Blank lines and `#` comments are skipped. Inline `#` after an unquoted value starts a comment.
    """
    p = Path(path)
    if not p.exists():
        raise ManifestError(f"Env file not found: {path}")

    result: dict[str, str] = {}
    for lineno, raw in enumerate(p.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _ENV_LINE_PATTERN.match(line)
        if not match:
            raise ManifestError(f"{path}:{lineno}: invalid syntax")
        key, value = match.group(1), match.group(2)
        stripped = value.strip()
        if stripped and stripped[0] not in ("'", '"'):
            comment_index = stripped.find(" #")
            if comment_index != -1:
                stripped = stripped[:comment_index].rstrip()
        result[key] = _unquote(stripped)
    return result


def load_manifest(
    path: str | Path,
    env: dict[str, str] | None = None,
    env_file: str | Path | None = None,
) -> dict:
    """Load YAML manifest from file, substitute ${VAR} references.

    Substitution sources, in order of precedence (later wins):
      1. `env_file` contents (if provided)
      2. `env` param if provided, else `os.environ`

    Returns a dict ready to send to PUT /public-api/apps/{app_name}/apply.
    """
    p = Path(path)
    if not p.exists():
        raise ManifestError(f"Manifest file not found: {path}")
    try:
        raw = yaml.safe_load(p.read_text())
    except yaml.YAMLError as e:
        raise ManifestError(f"Failed to parse YAML: {e}") from e

    if raw is None:
        raise ManifestError("Manifest is empty")
    if not isinstance(raw, dict):
        raise ManifestError("Manifest must be a mapping with 'resources' and 'envs' keys")

    base_env: dict[str, str] = {}
    if env_file is not None:
        base_env.update(load_env_file(env_file))
    base_env.update(env if env is not None else os.environ)

    data = _substitute(raw, base_env)

    resources = data.get("resources")
    if not isinstance(resources, list) or len(resources) == 0:
        raise ManifestError("Manifest must contain at least one resource under 'resources'")

    envs = data.get("envs") or []
    if not isinstance(envs, list):
        raise ManifestError("'envs' must be a list")

    return {"resources": resources, "envs": envs}


def resource_names(manifest: dict) -> list[str]:
    return [r.get("name", "") for r in manifest.get("resources", [])]
