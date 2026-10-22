from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .chrome_cookies import ChromeCookie, ChromeCookieError, cookie_header, load_cookies

WEB_BASE_URL = "https://claude.ai"
WEB_DOMAIN = "claude.ai"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"


class ClaudeUsageError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClaudeWindow:
    utilization: float
    resets_at: int | None


@dataclass(frozen=True)
class ClaudeUsage:
    five_hour: ClaudeWindow | None
    seven_day: ClaudeWindow | None
    plan: str | None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


_OPENER = build_opener(_NoRedirect())


def load_usage(*, profile: str | None = None, timeout: float = 20.0) -> ClaudeUsage:
    try:
        cookies = load_cookies(WEB_DOMAIN, profile=profile)
    except ChromeCookieError as exc:
        raise ClaudeUsageError(f"Sessão do Claude no Chrome indisponível: {exc}") from exc
    return fetch_web_usage(cookies, timeout=timeout)


def fetch_web_usage(cookies: tuple[ChromeCookie, ...], *, timeout: float = 20.0) -> ClaudeUsage:
    session = cookie_header(cookies)
    organizations = _get_json("/api/organizations", session, timeout)
    if not isinstance(organizations, list):
        raise ClaudeUsageError("Resposta de organizações do Claude inválida")

    chat_org = next(
        (
            org for org in organizations
            if isinstance(org, dict)
            and isinstance(org.get("uuid"), str)
            and isinstance(org.get("capabilities"), list)
            and "chat" in org["capabilities"]
        ),
        None,
    )
    if chat_org is None:
        raise ClaudeUsageError("Nenhuma organização de chat do Claude disponível nesta sessão")

    usage = _get_json(f"/api/organizations/{quote(chat_org['uuid'], safe='')}/usage", session, timeout)
    if not isinstance(usage, dict):
        raise ClaudeUsageError("Resposta de uso do Claude inválida")
    if usage.get("type") == "error":
        raise ClaudeUsageError("A organização do Claude não autorizou a consulta de uso")
    return parse_usage(usage, plan=_plan_label(chat_org))


def _get_json(path: str, session: str, timeout: float) -> Any:
    request = Request(
        f"{WEB_BASE_URL}{path}",
        headers={
            "Cookie": session,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308, 401, 403):
            raise ClaudeUsageError("Sessão do Claude no Chrome expirada; abra claude.ai e faça login") from exc
        if exc.code == 429:
            raise ClaudeUsageError("Consulta de uso do Claude temporariamente limitada (HTTP 429)") from exc
        raise ClaudeUsageError(f"HTTP {exc.code} ao consultar o Claude") from exc
    except URLError as exc:
        raise ClaudeUsageError(f"Erro de rede ao consultar o Claude: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ClaudeUsageError("Tempo esgotado ao consultar o Claude") from exc

    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClaudeUsageError("Resposta de uso do Claude não é JSON válido") from exc


def parse_usage(payload: dict[str, Any], *, plan: str | None = None) -> ClaudeUsage:
    five_hour = _parse_window(payload.get("five_hour"))
    seven_day = _parse_window(payload.get("seven_day"))
    if five_hour is None and seven_day is None:
        raise ClaudeUsageError("Resposta do Claude Code sem janelas de uso")
    return ClaudeUsage(five_hour=five_hour, seven_day=seven_day, plan=plan)


def _parse_window(raw: Any) -> ClaudeWindow | None:
    if not isinstance(raw, dict):
        return None
    utilization = _as_float(raw.get("utilization"))
    if utilization is None:
        return None
    return ClaudeWindow(
        utilization=utilization,
        resets_at=_iso_epoch(raw.get("resets_at")),
    )


def _plan_label(organization: dict[str, Any]) -> str | None:
    tier = organization.get("rate_limit_tier")
    if isinstance(tier, str):
        if "max_20x" in tier:
            return "Max 20x"
        if "max_5x" in tier:
            return "Max 5x"
        if "max" in tier:
            return "Max"
    label = organization.get("plan_display_name")
    return label.strip() if isinstance(label, str) and label.strip() else None


def _iso_epoch(value: Any) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
