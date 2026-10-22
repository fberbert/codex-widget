from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import default_config_path

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_STATE_FILE = (
    Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    / "codex-widget"
    / "deepseek-spend.json"
)

_CURRENCY_SYMBOLS = {
    "USD": "US$",
    "CNY": "CN¥",
    "EUR": "€",
    "BRL": "R$",
}


class DeepSeekUsageError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeepSeekBalance:
    currency: str
    total: Decimal
    granted: Decimal
    topped_up: Decimal
    is_available: bool


@dataclass(frozen=True)
class SpendSnapshot:
    spent: Decimal
    topups: Decimal
    since: int | None
    tracking: bool


def resolve_api_key(api_key: str | None = None) -> str:
    value = (api_key or os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not value:
        raise DeepSeekUsageError(
            "DeepSeek API key não configurada "
            f"(--deepseek-api-key, DEEPSEEK_API_KEY ou {default_config_path()})"
        )
    return value


def fetch_balance(
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 20.0,
) -> DeepSeekBalance:
    request = Request(
        f"{base_url.rstrip('/')}/user/balance",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        raise DeepSeekUsageError(f"HTTP {exc.code} ao consultar saldo DeepSeek") from exc
    except URLError as exc:
        raise DeepSeekUsageError(f"Erro de rede ao consultar saldo DeepSeek: {exc.reason}") from exc
    except TimeoutError as exc:
        raise DeepSeekUsageError("Tempo esgotado ao consultar saldo DeepSeek") from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise DeepSeekUsageError("Resposta de saldo DeepSeek não é JSON válido") from exc
    if not isinstance(payload, dict):
        raise DeepSeekUsageError("Resposta de saldo DeepSeek não contém um objeto JSON")
    return parse_balance_payload(payload)


def parse_balance_payload(payload: dict[str, Any]) -> DeepSeekBalance:
    infos = payload.get("balance_infos")
    if not isinstance(infos, list) or not infos:
        raise DeepSeekUsageError("Resposta de saldo DeepSeek sem balance_infos")

    entries = [entry for entry in infos if isinstance(entry, dict)]
    if not entries:
        raise DeepSeekUsageError("Resposta de saldo DeepSeek sem entradas válidas")

    usd = next((entry for entry in entries if _currency(entry) == "USD"), None)
    chosen = usd or entries[0]
    return DeepSeekBalance(
        currency=_currency(chosen),
        total=_decimal(chosen.get("total_balance")),
        granted=_decimal(chosen.get("granted_balance")),
        topped_up=_decimal(chosen.get("topped_up_balance")),
        is_available=payload.get("is_available") is True,
    )


def format_money(amount: Decimal, currency: str) -> str:
    text = f"{amount:,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    symbol = _CURRENCY_SYMBOLS.get(currency.upper(), currency.upper())
    return f"{symbol} {text}"


class SpendTracker:
    """Accumulates spend from balance drops, persisted across widget restarts.

    DeepSeek's API exposes the current balance only, so spend is inferred from
    decreases between observations. Top-ups (balance increases) are recorded
    separately and never counted as negative spend.
    """

    def __init__(self, path: Path = DEFAULT_STATE_FILE) -> None:
        self._path = path

    def record(self, balance: DeepSeekBalance, now: int | None = None) -> SpendSnapshot:
        timestamp = int(time.time()) if now is None else now
        state = self._load()

        spent = Decimal("0")
        topups = Decimal("0")
        since = timestamp
        tracking = False

        if state is not None and state.get("currency") == balance.currency:
            spent = _decimal(state.get("spent"))
            topups = _decimal(state.get("topups"))
            since = _as_int(state.get("since")) or timestamp
            tracking = True
            delta = _decimal(state.get("last_total")) - balance.total
            if delta > 0:
                spent += delta
            elif delta < 0:
                topups += -delta

        self._save(
            {
                "currency": balance.currency,
                "last_total": str(balance.total),
                "spent": str(spent),
                "topups": str(topups),
                "since": since,
            }
        )
        return SpendSnapshot(spent=spent, topups=topups, since=since, tracking=tracking)

    def _load(self) -> dict[str, Any] | None:
        try:
            with self._path.open("r", encoding="utf-8") as handle:
                state = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None
        return state if isinstance(state, dict) else None

    def _save(self, state: dict[str, Any]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_name(f"{self._path.name}.tmp")
            temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
            temporary.replace(self._path)
        except OSError as exc:
            raise DeepSeekUsageError(f"Não foi possível gravar o estado de gasto DeepSeek: {exc}") from exc


def _currency(entry: dict[str, Any]) -> str:
    value = entry.get("currency")
    return value.upper() if isinstance(value, str) and value.strip() else "USD"


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
