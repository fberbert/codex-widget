from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://codewhisperer.us-east-1.amazonaws.com"
DEFAULT_TOKEN_DB = Path.home() / ".local" / "share" / "kiro-cli" / "data.sqlite3"
TOKEN_KEY = "kirocli:odic:token"
TARGET = "AmazonCodeWhispererService.GetUsageLimits"
LOGIN_HINT = "Sessão do Kiro expirada, rode `kiro-cli login`"


class KiroUsageError(RuntimeError):
    pass


@dataclass(frozen=True)
class KiroUsage:
    plan: str | None
    resource: str
    used: Decimal
    limit: Decimal
    overage_enabled: bool
    overage_used: Decimal
    overage_cap: Decimal
    overage_charges: Decimal
    currency: str
    next_reset: int | None

    @property
    def percent_remaining(self) -> int:
        remaining = (self.limit - self.used) / self.limit * 100
        return max(0, min(100, int(remaining)))


def load_usage(
    *,
    token_db: Path = DEFAULT_TOKEN_DB,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 20.0,
) -> KiroUsage:
    return fetch_usage(read_token(token_db), base_url=base_url, timeout=timeout)


def read_token(token_db: Path = DEFAULT_TOKEN_DB) -> str:
    if not token_db.exists():
        raise KiroUsageError(f"Banco do Kiro CLI não encontrado em {token_db}")
    try:
        connection = sqlite3.connect(f"file:{token_db}?mode=ro", uri=True)
    except sqlite3.Error as exc:  # pragma: no cover - depends on filesystem state
        raise KiroUsageError(f"Não foi possível abrir {token_db}: {exc}") from exc
    try:
        row = connection.execute(
            "SELECT value FROM auth_kv WHERE key = ?", (TOKEN_KEY,)
        ).fetchone()
    except sqlite3.Error as exc:
        raise KiroUsageError(f"Banco do Kiro CLI ilegível: {exc}") from exc
    finally:
        connection.close()

    if row is None:
        raise KiroUsageError(LOGIN_HINT)
    try:
        token = json.loads(row[0])
    except (TypeError, json.JSONDecodeError) as exc:
        raise KiroUsageError("Token do Kiro CLI não é JSON válido") from exc
    if not isinstance(token, dict):
        raise KiroUsageError("Token do Kiro CLI em formato inesperado")

    expires_at = _expiry(token.get("expires_at"))
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        raise KiroUsageError(LOGIN_HINT)

    access_token = token.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise KiroUsageError(LOGIN_HINT)
    return access_token


def fetch_usage(
    access_token: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 20.0,
) -> KiroUsage:
    request = Request(
        base_url.rstrip("/") + "/",
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/x-amz-json-1.0",
            "X-Amz-Target": TARGET,
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise KiroUsageError(LOGIN_HINT) from exc
        raise KiroUsageError(f"Uso do Kiro indisponível (HTTP {exc.code})") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise KiroUsageError(f"Falha ao consultar o uso do Kiro: {exc}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise KiroUsageError("Resposta de uso do Kiro não é JSON válido") from exc
    if not isinstance(payload, dict):
        raise KiroUsageError("Resposta de uso do Kiro em formato inesperado")
    return parse_usage(payload)


def parse_usage(payload: dict[str, Any]) -> KiroUsage:
    breakdown = _credit_breakdown(payload.get("usageBreakdownList"))
    limit = _decimal(breakdown.get("usageLimitWithPrecision"), breakdown.get("usageLimit"))
    if limit <= 0:
        raise KiroUsageError("Limite de créditos do Kiro inválido")

    overage = payload.get("overageConfiguration")
    status = overage.get("overageStatus") if isinstance(overage, dict) else None
    subscription = payload.get("subscriptionInfo")
    plan = subscription.get("subscriptionTitle") if isinstance(subscription, dict) else None

    return KiroUsage(
        plan=plan.strip() if isinstance(plan, str) and plan.strip() else None,
        resource=_resource_name(breakdown),
        used=_decimal(
            breakdown.get("currentUsageWithPrecision"), breakdown.get("currentUsage")
        ),
        limit=limit,
        overage_enabled=isinstance(status, str) and status.upper() == "ENABLED",
        overage_used=_decimal(
            breakdown.get("currentOveragesWithPrecision"), breakdown.get("currentOverages")
        ),
        overage_cap=_decimal(
            breakdown.get("overageCapWithPrecision"), breakdown.get("overageCap")
        ),
        overage_charges=_decimal(breakdown.get("overageCharges")),
        currency=str(breakdown.get("currency") or "USD").upper(),
        next_reset=_epoch(breakdown.get("nextDateReset") or payload.get("nextDateReset")),
    )


def _credit_breakdown(raw: Any) -> dict[str, Any]:
    entries = [entry for entry in raw if isinstance(entry, dict)] if isinstance(raw, list) else []
    if not entries:
        raise KiroUsageError("Resposta de uso do Kiro sem usageBreakdownList")
    for entry in entries:
        if str(entry.get("resourceType") or "").upper() == "CREDIT":
            return entry
    return entries[0]


def _resource_name(breakdown: dict[str, Any]) -> str:
    for key in ("displayNamePlural", "displayName"):
        value = breakdown.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Credits"


def _decimal(*values: Any) -> Decimal:
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            continue
    return Decimal("0")


def _epoch(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _expiry(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
