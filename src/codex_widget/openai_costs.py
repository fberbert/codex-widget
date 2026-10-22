from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .dotenv import default_env_file, resolve_secret

DEFAULT_BASE_URL = "https://api.openai.com"
COSTS_PATH = "/v1/organization/costs"
MAX_BUCKETS = 180
USER_AGENT = "codex-widget/0.1"


class OpenAICostsError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenAICosts:
    currency: str
    today: Decimal
    month: Decimal
    tracked: Decimal
    tracked_since: int


def resolve_admin_key(admin_key: str | None = None) -> str:
    value = (admin_key or "").strip() or resolve_secret("OPENAI_ADMIN_API_KEY", "OPENAI_ADMIN_KEY")
    if not value:
        raise OpenAICostsError(
            "Admin key da OpenAI não configurada "
            f"(--openai-admin-key, OPENAI_ADMIN_API_KEY em {default_env_file()} "
            "ou openai_admin_key no config)"
        )
    return value


def load_costs(
    admin_key: str,
    *,
    credits_since: date | None = None,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 20.0,
    now: int | None = None,
) -> OpenAICosts:
    now_epoch = int(time.time()) if now is None else now
    moment = datetime.fromtimestamp(now_epoch).astimezone()
    today_start = int(moment.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    month_start = int(
        moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    )
    tracked_since = (
        int(datetime.combine(credits_since, datetime.min.time()).astimezone().timestamp())
        if credits_since is not None
        else month_start
    )

    buckets = _fetch_buckets(
        admin_key,
        start_time=min(tracked_since, month_start),
        base_url=base_url,
        timeout=timeout,
    )
    return summarize(
        buckets,
        today_start=today_start,
        month_start=month_start,
        tracked_since=tracked_since,
    )


def summarize(
    buckets: list[dict[str, Any]],
    *,
    today_start: int,
    month_start: int,
    tracked_since: int,
) -> OpenAICosts:
    currency = "USD"
    today = month = tracked = Decimal("0")
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        start = _as_int(bucket.get("start_time"))
        results = bucket.get("results")
        if start is None or not isinstance(results, list):
            continue
        amount = Decimal("0")
        for result in results:
            if not isinstance(result, dict):
                continue
            raw = result.get("amount")
            if not isinstance(raw, dict):
                continue
            amount += _decimal(raw.get("value"))
            code = raw.get("currency")
            if isinstance(code, str) and code.strip():
                currency = code.strip().upper()
        if start >= tracked_since:
            tracked += amount
        if start >= month_start:
            month += amount
        if start >= today_start:
            today += amount
    return OpenAICosts(
        currency=currency,
        today=today,
        month=month,
        tracked=tracked,
        tracked_since=tracked_since,
    )


def _fetch_buckets(
    admin_key: str,
    *,
    start_time: int,
    base_url: str,
    timeout: float,
) -> list[dict[str, Any]]:
    buckets: list[dict[str, Any]] = []
    page: str | None = None
    while True:
        query: dict[str, Any] = {
            "start_time": start_time,
            "bucket_width": "1d",
            "limit": MAX_BUCKETS,
        }
        if page:
            query["page"] = page
        payload = _get_json(f"{base_url.rstrip('/')}{COSTS_PATH}?{urlencode(query)}", admin_key, timeout)
        data = payload.get("data")
        if not isinstance(data, list):
            raise OpenAICostsError("Resposta de custos da OpenAI sem data")
        buckets.extend(item for item in data if isinstance(item, dict))
        page = payload.get("next_page") if payload.get("has_more") is True else None
        if not isinstance(page, str) or not page:
            return buckets


def _get_json(url: str, admin_key: str, timeout: float) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {admin_key}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise OpenAICostsError(
                "Chave da OpenAI sem permissão de custos; use uma Admin key com escopo api.usage.read"
            ) from exc
        raise OpenAICostsError(f"HTTP {exc.code} ao consultar custos da OpenAI") from exc
    except URLError as exc:
        raise OpenAICostsError(f"Erro de rede ao consultar custos da OpenAI: {exc.reason}") from exc
    except TimeoutError as exc:
        raise OpenAICostsError("Tempo esgotado ao consultar custos da OpenAI") from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenAICostsError("Resposta de custos da OpenAI não é JSON válido") from exc
    if not isinstance(payload, dict):
        raise OpenAICostsError("Resposta de custos da OpenAI não contém um objeto JSON")
    return payload


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
