from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .chrome_cookies import ChromeCookie, cookie_header, load_cookies

DEFAULT_BASE_URL = "https://platform.xiaomimimo.com"
DEFAULT_DOMAIN = "platform.xiaomimimo.com"


class MiMoUsageError(RuntimeError):
    pass


@dataclass(frozen=True)
class MiMoSnapshot:
    currency: str
    balance: Decimal
    cash: Decimal
    gift: Decimal
    recharged: Decimal | None
    total_cost: Decimal
    month_cost: Decimal
    total_tokens: int


def load_snapshot(
    *,
    profile: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 20.0,
) -> MiMoSnapshot:
    cookies = load_cookies(DEFAULT_DOMAIN, profile=profile)
    return fetch_snapshot(cookies, base_url=base_url, timeout=timeout)


def fetch_snapshot(
    cookies: tuple[ChromeCookie, ...],
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 20.0,
) -> MiMoSnapshot:
    header = cookie_header(cookies)
    balance = _get_json(base_url, "/api/v1/balance", header, timeout)
    usage = _get_json(base_url, "/api/v1/usage", header, timeout)
    try:
        recharge = _get_json(base_url, "/api/v1/accumulatedRechargeAmount", header, timeout)
    except MiMoUsageError:
        recharge = None
    return parse_snapshot(balance, usage, recharge)


def parse_snapshot(
    balance: dict[str, Any],
    usage: dict[str, Any],
    recharge: dict[str, Any] | None = None,
) -> MiMoSnapshot:
    currency = _text(balance.get("currency")) or "USD"
    token_usage = usage.get("tokenUsage")
    cost_usage = usage.get("costUsage")
    if not isinstance(token_usage, dict) or not isinstance(cost_usage, dict):
        raise MiMoUsageError("Resposta de uso do MiMo sem tokenUsage/costUsage")

    return MiMoSnapshot(
        currency=currency.upper(),
        balance=_decimal(balance.get("balance")),
        cash=_decimal(balance.get("cashBalance")),
        gift=_decimal(balance.get("giftBalance")),
        recharged=(
            _decimal(recharge.get("accumulatedRechargeAmount"))
            if isinstance(recharge, dict)
            else None
        ),
        total_cost=_decimal(cost_usage.get("totalCost")),
        month_cost=_decimal(cost_usage.get("currentMonthCost")),
        total_tokens=_as_int(token_usage.get("totalToken")),
    )


def _get_json(base_url: str, path: str, cookie: str, timeout: float) -> dict[str, Any]:
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        headers={
            "Cookie": cookie,
            "Accept": "application/json",
            "User-Agent": "codex-widget/0.1",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise MiMoUsageError(
                "Sessão do Chrome expirada para platform.xiaomimimo.com; faça login novamente"
            ) from exc
        raise MiMoUsageError(f"HTTP {exc.code} ao consultar o MiMo") from exc
    except URLError as exc:
        raise MiMoUsageError(f"Erro de rede ao consultar o MiMo: {exc.reason}") from exc
    except TimeoutError as exc:
        raise MiMoUsageError("Tempo esgotado ao consultar o MiMo") from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise MiMoUsageError("Resposta do MiMo não é JSON válido") from exc
    if not isinstance(payload, dict):
        raise MiMoUsageError("Resposta do MiMo não contém um objeto JSON")
    if payload.get("code") not in (0, None):
        message = _text(payload.get("message")) or "erro desconhecido"
        raise MiMoUsageError(f"MiMo retornou erro: {message}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise MiMoUsageError(f"Resposta do MiMo sem data em {path}")
    return data


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
