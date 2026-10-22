from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from PyQt6 import QtWidgets

from .config import default_config_path, load_config
from .deepseek import (
    DEFAULT_BASE_URL as DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_STATE_FILE as DEFAULT_DEEPSEEK_STATE_FILE,
)
from .usage import DEFAULT_AUTH_FILE, DEFAULT_BASE_URL

from .widget import CodexUsageWidget


def _decimal_arg(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"valor decimal inválido: {value}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Floating desktop widget for Codex usage.")
    parser.add_argument("--auth-file", type=Path, default=Path(os.environ.get("CODEX_AUTH_FILE", DEFAULT_AUTH_FILE)))
    parser.add_argument("--base-url", default=os.environ.get("CODEX_USAGE_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--refresh-seconds", type=int, default=60)
    parser.add_argument("--config-file", type=Path, default=default_config_path())
    parser.add_argument("--deepseek-api-key", default=None)
    parser.add_argument("--deepseek-base-url", default=None)
    parser.add_argument(
        "--deepseek-state-file",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--deepseek-spent-baseline",
        type=_decimal_arg,
        default=None,
        help="Gasto DeepSeek já realizado antes do monitoramento (somado ao gasto observado).",
    )
    parser.add_argument(
        "--mimo-chrome-profile",
        default=None,
        help="Perfil do Chrome com a sessão do console MiMo (padrão: detectar automaticamente).",
    )
    parser.add_argument(
        "--claude-chrome-profile",
        default=None,
        help="Perfil do Chrome com a sessão de claude.ai (padrão: detectar automaticamente).",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config_file)
    deepseek_api_key = (
        args.deepseek_api_key
        or os.environ.get("DEEPSEEK_API_KEY")
        or config.deepseek_api_key
    )
    deepseek_base_url = (
        args.deepseek_base_url
        or os.environ.get("DEEPSEEK_BASE_URL")
        or config.deepseek_base_url
        or DEFAULT_DEEPSEEK_BASE_URL
    )
    deepseek_state_file = (
        args.deepseek_state_file
        or config.deepseek_state_file
        or DEFAULT_DEEPSEEK_STATE_FILE
    )
    deepseek_spent_baseline = (
        args.deepseek_spent_baseline
        if args.deepseek_spent_baseline is not None
        else config.deepseek_spent_baseline
    )
    mimo_chrome_profile = args.mimo_chrome_profile or config.mimo_chrome_profile
    claude_chrome_profile = args.claude_chrome_profile or config.claude_chrome_profile

    app = QtWidgets.QApplication(sys.argv[:1])
    widget = CodexUsageWidget(
        auth_file=args.auth_file,
        base_url=args.base_url,
        refresh_seconds=args.refresh_seconds,
        deepseek_api_key=deepseek_api_key,
        deepseek_base_url=deepseek_base_url,
        deepseek_state_file=deepseek_state_file,
        deepseek_spent_baseline=deepseek_spent_baseline,
        mimo_chrome_profile=mimo_chrome_profile,
        claude_chrome_profile=claude_chrome_profile,
    )
    widget.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
