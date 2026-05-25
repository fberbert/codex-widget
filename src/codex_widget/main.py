from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from PyQt6 import QtWidgets

from .usage import DEFAULT_AUTH_FILE, DEFAULT_BASE_URL

from .widget import CodexUsageWidget


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Floating desktop widget for Codex usage.")
    parser.add_argument("--auth-file", type=Path, default=Path(os.environ.get("CODEX_AUTH_FILE", DEFAULT_AUTH_FILE)))
    parser.add_argument("--base-url", default=os.environ.get("CODEX_USAGE_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--refresh-seconds", type=int, default=60)
    args = parser.parse_args(argv)

    app = QtWidgets.QApplication(sys.argv[:1])
    widget = CodexUsageWidget(
        auth_file=args.auth_file,
        base_url=args.base_url,
        refresh_seconds=args.refresh_seconds,
    )
    widget.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
