from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WidgetConfig:
    deepseek_api_key: str | None = None
    deepseek_base_url: str | None = None
    deepseek_state_file: Path | None = None
    deepseek_spent_baseline: Decimal | None = None
    mimo_chrome_profile: str | None = None
    claude_chrome_profile: str | None = None


def default_config_path() -> Path:
    override = os.environ.get("CODEX_WIDGET_CONFIG")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / "codex-widget" / "config.json"


def load_config(path: Path | None = None) -> WidgetConfig:
    target = default_config_path() if path is None else path
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return WidgetConfig()
    if not isinstance(raw, dict):
        return WidgetConfig()
    return WidgetConfig(
        deepseek_api_key=_string(raw.get("deepseek_api_key")),
        deepseek_base_url=_string(raw.get("deepseek_base_url")),
        deepseek_state_file=_path(raw.get("deepseek_state_file")),
        deepseek_spent_baseline=_decimal(raw.get("deepseek_spent_baseline")),
        mimo_chrome_profile=_string(raw.get("mimo_chrome_profile")),
        claude_chrome_profile=_string(raw.get("claude_chrome_profile")),
    )


def _string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _path(value: Any) -> Path | None:
    text = _string(value)
    return Path(text).expanduser() if text else None


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
