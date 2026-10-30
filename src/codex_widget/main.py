from __future__ import annotations

import argparse
import os
import signal
import sys
from decimal import Decimal, InvalidOperation
from datetime import date
from pathlib import Path

from PyQt6 import QtCore, QtWidgets

from .config import default_config_path, load_config
from .desktop import ensure_desktop_integration, find_icon, load_window_icon, running_executable
from .layer_shell import use_layer_shell
from .deepseek import (
    DEFAULT_BASE_URL as DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_STATE_FILE as DEFAULT_DEEPSEEK_STATE_FILE,
)
from .dotenv import resolve_secret
from .usage import DEFAULT_AUTH_FILE, DEFAULT_BASE_URL

from .widget import CodexUsageWidget


def _decimal_arg(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"valor decimal inválido: {value}") from exc


def _date_arg(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"data inválida (use AAAA-MM-DD): {value}") from exc


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
    parser.add_argument("--openai-admin-key", default=None)
    parser.add_argument(
        "--openai-credits-purchased",
        type=_decimal_arg,
        default=None,
        help="Créditos da OpenAI comprados; o saldo exibido é esse valor menos o custo acumulado.",
    )
    parser.add_argument(
        "--openai-credits-since",
        type=_date_arg,
        default=None,
        help="Data (AAAA-MM-DD) a partir da qual o custo é somado para o saldo da OpenAI.",
    )
    args = parser.parse_args(argv)
    use_layer_shell()

    config = load_config(args.config_file)
    deepseek_api_key = (
        args.deepseek_api_key
        or resolve_secret("DEEPSEEK_API_KEY")
        or config.deepseek_api_key
    )
    deepseek_base_url = (
        args.deepseek_base_url
        or resolve_secret("DEEPSEEK_BASE_URL")
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
    openai_admin_key = (
        args.openai_admin_key
        or resolve_secret("OPENAI_ADMIN_API_KEY", "OPENAI_ADMIN_KEY")
        or config.openai_admin_key
    )
    openai_credits_purchased = (
        args.openai_credits_purchased
        if args.openai_credits_purchased is not None
        else config.openai_credits_purchased
    )
    openai_credits_since = args.openai_credits_since or config.openai_credits_since

    app = QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName("codex-widget")
    app.setApplicationDisplayName("Codex Widget")
    app.setDesktopFileName("codex-widget")
    try:
        ensure_desktop_integration(executable=running_executable(), icon=find_icon())
    except OSError:
        pass
    icon = load_window_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
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
        openai_admin_key=openai_admin_key,
        openai_credits_purchased=openai_credits_purchased,
        openai_credits_since=openai_credits_since,
    )
    _install_interrupt_handler(app)
    widget.show()
    return app.exec()


def _install_interrupt_handler(app: QtWidgets.QApplication) -> None:
    """Quit from Ctrl+C without raising inside a Qt slot.

    ``QApplication.exec`` runs in C++, so the interpreter only notices the
    signal when a Python callback runs. The pulse timer provides that callback.
    A second Ctrl+C exits immediately: destroying a running fetch thread aborts
    the process.
    """

    interrupts = {"count": 0}

    def handle_sigint(*_args: object) -> None:
        interrupts["count"] += 1
        if interrupts["count"] > 1:
            os._exit(0)
        app.quit()

    signal.signal(signal.SIGINT, handle_sigint)
    pulse = QtCore.QTimer(app)
    pulse.setInterval(200)
    pulse.timeout.connect(lambda: None)
    pulse.start()


if __name__ == "__main__":
    raise SystemExit(main())
