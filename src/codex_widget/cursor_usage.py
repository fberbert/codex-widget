from __future__ import annotations

import base64
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://api2.cursor.sh"
DEFAULT_STATE_DB = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    / "Cursor"
    / "User"
    / "globalStorage"
    / "state.vscdb"
)
PERIOD_PATH = "/aiserver.v1.DashboardService/GetCurrentPeriodUsage"
PLAN_PATH = "/aiserver.v1.DashboardService/GetPlanInfo"
TOKEN_URL_PATH = "/oauth/token"
CLIENT_ID = "KbZUR41cY7W6zRSdpSUJ7I7mLYBKOCmB"
ACCESS_TOKEN_KEY = "cursorAuth/accessToken"
REFRESH_TOKEN_KEY = "cursorAuth/refreshToken"
MEMBERSHIP_KEY = "cursorAuth/stripeMembershipType"
LOGIN_HINT = "Sessão do Cursor expirada; abra o Cursor e faça login"


class CursorUsageError(RuntimeError):
    pass


class _AuthExpired(CursorUsageError):
    pass


@dataclass(frozen=True)
class CursorSession:
    access_token: str
    refresh_token: str | None
    plan: str | None


@dataclass(frozen=True)
class CursorUsage:
    plan: str | None
    total_percent_used: float
    api_percent_used: float | None
    spent_cents: int
    limit_cents: int | None
    cycle_end: int | None


def load_usage(
    *,
    state_db: Path = DEFAULT_STATE_DB,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 20.0,
    now: float | None = None,
) -> CursorUsage:
    session = read_session(state_db)
    token, refreshed = _active_token(session, base_url=base_url, timeout=timeout, now=now)
    try:
        period = _post_json(base_url, PERIOD_PATH, token, timeout)
    except _AuthExpired:
        if refreshed or not session.refresh_token:
            raise CursorUsageError(LOGIN_HINT) from None
        token = refresh_access_token(session.refresh_token, base_url=base_url, timeout=timeout)
        period = _post_json(base_url, PERIOD_PATH, token, timeout)
    plan = session.plan
    try:
        info = _post_json(base_url, PLAN_PATH, token, timeout)
    except CursorUsageError:
        info = None
    if isinstance(info, dict):
        plan = parse_plan_name(info) or plan
    return parse_period(period, plan=plan)


def read_session(state_db: Path = DEFAULT_STATE_DB) -> CursorSession:
    if not state_db.is_file():
        raise CursorUsageError(f"Banco do Cursor não encontrado em {state_db}")
    keys = (ACCESS_TOKEN_KEY, REFRESH_TOKEN_KEY, MEMBERSHIP_KEY)
    try:
        connection = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise CursorUsageError(f"Não foi possível abrir {state_db}: {exc}") from exc
    try:
        rows = connection.execute(
            f"SELECT key, value FROM ItemTable WHERE key IN ({','.join('?' for _ in keys)})",
            keys,
        ).fetchall()
    except sqlite3.Error as exc:
        raise CursorUsageError(f"Banco do Cursor ilegível: {exc}") from exc
    finally:
        connection.close()

    values = {key: _token_text(value) for key, value in rows}
    access_token = values.get(ACCESS_TOKEN_KEY)
    if not access_token:
        raise CursorUsageError(LOGIN_HINT)
    return CursorSession(
        access_token=access_token,
        refresh_token=values.get(REFRESH_TOKEN_KEY),
        plan=_plan_label(values.get(MEMBERSHIP_KEY)),
    )


def refresh_access_token(
    refresh_token: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 20.0,
) -> str:
    payload = _request_json(
        base_url.rstrip("/") + TOKEN_URL_PATH,
        method="POST",
        timeout=timeout,
        data=json.dumps(
            {
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": refresh_token,
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    if not isinstance(payload, dict) or payload.get("shouldLogout") is True:
        raise CursorUsageError(LOGIN_HINT)
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise CursorUsageError(LOGIN_HINT)
    return access_token.strip()


def parse_plan_name(payload: dict[str, Any]) -> str | None:
    info = payload.get("planInfo")
    if not isinstance(info, dict):
        return None
    name = info.get("planName")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def parse_period(payload: dict[str, Any], *, plan: str | None = None) -> CursorUsage:
    plan_usage = payload.get("planUsage")
    if not isinstance(plan_usage, dict):
        raise CursorUsageError("Resposta de uso do Cursor sem o plano atual")
    limit = _cents(plan_usage.get("limit"))
    spent = _cents(plan_usage.get("totalSpend"))
    if spent is None:
        spent = _cents(plan_usage.get("includedSpend"))
    if spent is None:
        spent = 0
    total = _number(plan_usage.get("totalPercentUsed"))
    if total is None and limit:
        total = spent / limit * 100
    if total is None:
        raise CursorUsageError("Resposta de uso do Cursor sem percentual do plano")
    return CursorUsage(
        plan=plan,
        total_percent_used=total,
        api_percent_used=_number(plan_usage.get("apiPercentUsed")),
        spent_cents=spent,
        limit_cents=limit,
        cycle_end=_epoch_seconds(payload.get("billingCycleEnd")),
    )


def jwt_expired(token: str, *, now: float | None = None, skew_seconds: int = 60) -> bool:
    parts = token.split(".")
    if len(parts) < 2:
        return False
    padding = "=" * (-len(parts[1]) % 4)
    try:
        raw = base64.urlsafe_b64decode(parts[1] + padding)
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    exp = payload.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        return False
    current = time.time() if now is None else now
    return float(exp) <= current + skew_seconds


def _active_token(
    session: CursorSession,
    *,
    base_url: str,
    timeout: float,
    now: float | None,
) -> tuple[str, bool]:
    if session.refresh_token and jwt_expired(session.access_token, now=now):
        return refresh_access_token(session.refresh_token, base_url=base_url, timeout=timeout), True
    return session.access_token, False


def _post_json(base_url: str, path: str, access_token: str, timeout: float) -> Any:
    return _request_json(
        base_url.rstrip("/") + path,
        method="POST",
        timeout=timeout,
        data=b"{}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
            "Accept": "application/json",
        },
    )


def _request_json(
    url: str,
    *,
    method: str,
    timeout: float,
    data: bytes | None,
    headers: dict[str, str],
) -> Any:
    request = Request(url, data=data, method=method, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise _AuthExpired(LOGIN_HINT) from exc
        raise CursorUsageError(f"Uso do Cursor indisponível (HTTP {exc.code})") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise CursorUsageError(f"Falha ao consultar o uso do Cursor: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CursorUsageError("Resposta de uso do Cursor não é JSON válido") from exc
    return payload


def _token_text(value: object) -> str | None:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            loaded = text[1:-1]
        if isinstance(loaded, str):
            text = loaded.strip()
    return text or None


def _plan_label(value: str | None) -> str | None:
    if value is None:
        return None
    label = value.replace("_", " ").replace("-", " ").strip()
    return label.title() if label else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _cents(value: Any) -> int | None:
    number = _number(value)
    if number is None:
        return None
    return int(round(number))


def _epoch_seconds(value: Any) -> int | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    if number > 10_000_000_000:
        number /= 1000
    return int(number)
