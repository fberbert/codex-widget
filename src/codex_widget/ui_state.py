from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_STATE_FILE = (
    Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    / "codex-widget"
    / "ui.json"
)


@dataclass(frozen=True)
class WidgetUiState:
    horizontal: bool = False
    orientation_locked: bool = False
    scale: float | None = None
    collapsed: bool = False
    screen_name: str | None = None
    offset_x: int | None = None
    offset_y: int | None = None
    width: int | None = None
    active_only: bool = False


def load_ui_state(path: Path = DEFAULT_STATE_FILE) -> WidgetUiState:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return WidgetUiState()
    if not isinstance(data, dict):
        return WidgetUiState()
    name = data.get("screen_name")
    return WidgetUiState(
        horizontal=data.get("horizontal") is True,
        orientation_locked=data.get("orientation_locked") is True,
        scale=_scale(data.get("scale")),
        collapsed=data.get("collapsed") is True,
        screen_name=name if isinstance(name, str) and name else None,
        offset_x=_coordinate(data.get("offset_x")),
        offset_y=_coordinate(data.get("offset_y")),
        width=_width(data.get("width")),
        active_only=data.get("active_only") is True,
    )


def save_ui_state(state: WidgetUiState, path: Path = DEFAULT_STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(
            {
                "horizontal": state.horizontal,
                "orientation_locked": state.orientation_locked,
                "scale": state.scale,
                "collapsed": state.collapsed,
                "screen_name": state.screen_name,
                "offset_x": state.offset_x,
                "offset_y": state.offset_y,
                "width": state.width,
                "active_only": state.active_only,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def _coordinate(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _width(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _scale(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None
