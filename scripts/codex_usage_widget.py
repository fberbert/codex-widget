#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

for path in (PROJECT_ROOT, SRC_DIR):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

from codex_widget.main import main


if __name__ == "__main__":
    raise SystemExit(main())
