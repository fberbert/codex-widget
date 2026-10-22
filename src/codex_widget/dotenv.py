from __future__ import annotations

import os
from pathlib import Path


def default_env_file() -> Path:
    override = os.environ.get("CODEX_WIDGET_ENV_FILE")
    return Path(override).expanduser() if override else Path.home() / ".env"


def load_env_file(path: Path | None = None) -> dict[str, str]:
    """Read `KEY=value` pairs from a shell-style env file.

    GUI launchers do not inherit the shell environment, so credentials exported
    from `~/.env` are invisible to the widget unless the file is read directly.
    """
    target = default_env_file() if path is None else path
    try:
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}

    values: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        name, separator, value = line.partition("=")
        if not separator:
            continue
        name = name.strip()
        if not name.isidentifier():
            continue
        values[name] = _unquote(value.strip())
    return values


def resolve_secret(*names: str, env_file: Path | None = None) -> str | None:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value

    values = load_env_file(env_file)
    for name in names:
        value = values.get(name, "").strip()
        if value:
            return value
    return None


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value
