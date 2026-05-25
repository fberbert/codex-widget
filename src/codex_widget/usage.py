from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_AUTH_FILE = Path.home() / ".codex" / "auth.json"
DEFAULT_BASE_URL = "https://chatgpt.com/backend-api"


class CodexUsageError(RuntimeError):
    pass


@dataclass(frozen=True)
class UsageWindow:
    used_percent: float | None
    reset_at: int | None
    window_seconds: int | None

    @property
    def remaining_percent(self) -> float | None:
        if self.used_percent is None:
            return None
        return max(0.0, 100.0 - self.used_percent)


@dataclass(frozen=True)
class CodexUsage:
    five_hour: UsageWindow | None
    weekly: UsageWindow | None
    plan_type: str | None


def load_auth(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise CodexUsageError(f"Auth file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CodexUsageError(f"Invalid auth file: {path}") from exc
    if not isinstance(data, dict):
        raise CodexUsageError("Auth file does not contain a JSON object")
    return data


def extract_access_token(auth: dict[str, Any]) -> str:
    tokens = auth.get("tokens")
    if isinstance(tokens, dict):
        for key in ("access_token", "accessToken"):
            value = tokens.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    raise CodexUsageError("Could not find tokens.access_token in auth.json")


def extract_account_id(auth: dict[str, Any]) -> str:
    tokens = auth.get("tokens")
    if isinstance(tokens, dict):
        for key in ("account_id", "accountId"):
            value = tokens.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        id_token = tokens.get("id_token") or tokens.get("idToken")
        if isinstance(id_token, str):
            account_id = _account_id_from_id_token(id_token)
            if account_id:
                return account_id

    raise CodexUsageError("Could not find chatgpt-account-id in auth.json")


def fetch_usage(access_token: str, account_id: str, base_url: str, timeout: float) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/wham/usage"
    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "chatgpt-account-id": account_id,
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        detail = _safe_error_body(exc)
        raise CodexUsageError(f"HTTP {exc.code} while fetching usage: {detail}") from exc
    except URLError as exc:
        raise CodexUsageError(f"Network error while fetching usage: {exc.reason}") from exc
    except TimeoutError as exc:
        raise CodexUsageError("Timed out while fetching usage") from exc

    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise CodexUsageError("Usage response is not valid JSON") from exc
    if not isinstance(data, dict):
        raise CodexUsageError("Usage response does not contain a JSON object")
    return data


def parse_usage_payload(payload: dict[str, Any], now: int | None = None) -> CodexUsage:
    now_epoch = int(time.time()) if now is None else now
    rate_limit = payload.get("rate_limit")
    if not isinstance(rate_limit, dict):
        return CodexUsage(five_hour=None, weekly=None, plan_type=_plan_type(payload))

    primary = _parse_window(rate_limit.get("primary_window"), now_epoch)
    secondary = _parse_window(rate_limit.get("secondary_window"), now_epoch)

    five_hour = None
    weekly = None
    for window in (primary, secondary):
        if window is None:
            continue
        if window.window_seconds == 604800:
            weekly = window
        elif window.window_seconds == 18000:
            five_hour = window

    if five_hour is None and primary is not None and primary.window_seconds != 604800:
        five_hour = primary
    if weekly is None and secondary is not None:
        weekly = secondary

    return CodexUsage(five_hour=five_hour, weekly=weekly, plan_type=_plan_type(payload))


def _account_id_from_id_token(id_token: str) -> str | None:
    parts = id_token.split(".")
    if len(parts) < 2:
        return None
    try:
        payload = parts[1] + ("=" * (-len(parts[1]) % 4))
        raw = base64.urlsafe_b64decode(payload.encode("ascii"))
        claims = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(claims, dict):
        return None

    auth_claims = claims.get("https://api.openai.com/auth")
    if isinstance(auth_claims, dict):
        value = auth_claims.get("chatgpt_account_id")
        if isinstance(value, str) and value.strip():
            return value.strip()

    value = claims.get("chatgpt_account_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _safe_error_body(exc: HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8", errors="replace").strip()
    except Exception:
        return exc.reason or "error without details"
    if not raw:
        return exc.reason or "error without details"
    return raw[:500]


def _parse_window(raw: Any, now: int) -> UsageWindow | None:
    if not isinstance(raw, dict):
        return None

    used_percent = _as_float(raw.get("used_percent"))
    reset_at = _as_int(raw.get("reset_at"))
    reset_after = _as_int(raw.get("reset_after_seconds"))
    if reset_at is None and reset_after is not None:
        reset_at = now + reset_after

    return UsageWindow(
        used_percent=used_percent,
        reset_at=reset_at,
        window_seconds=_as_int(raw.get("limit_window_seconds")),
    )


def _plan_type(payload: dict[str, Any]) -> str | None:
    value = payload.get("plan_type")
    return value if isinstance(value, str) and value else None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
