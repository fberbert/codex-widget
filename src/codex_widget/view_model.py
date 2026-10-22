from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .claude import ClaudeUsage, ClaudeWindow
from .deepseek import DeepSeekBalance, SpendSnapshot, format_money
from .mimo import MiMoSnapshot
from .usage import UsageWindow


@dataclass(frozen=True)
class UsageCardModel:
    title: str
    percent_remaining: int
    reset_text: str


@dataclass(frozen=True)
class MetricCardModel:
    title: str
    primary_label: str
    primary_value: str
    secondary_label: str
    secondary_value: str
    detail_text: str
    footnote: str
    available: bool


def build_card_models(
    *,
    short_window: UsageWindow | None,
    weekly: UsageWindow | None,
) -> tuple[UsageCardModel, ...]:
    cards: list[UsageCardModel] = []

    if short_window is not None:
        cards.append(
            UsageCardModel(
                title=_window_title(short_window),
                percent_remaining=_remaining_percent(short_window),
                reset_text=_reset_text(short_window, include_date=False),
            )
        )

    cards.append(
        UsageCardModel(
            title="Limite de uso semanal",
            percent_remaining=_remaining_percent(weekly),
            reset_text=_reset_text(weekly, include_date=True),
        )
    )
    return tuple(cards)


def build_deepseek_card(
    *,
    balance: DeepSeekBalance,
    spend: SpendSnapshot,
    baseline: Decimal | None = None,
) -> MetricCardModel:
    spent_total = spend.spent + (baseline if baseline is not None else Decimal("0"))
    since = (
        datetime.fromtimestamp(spend.since).astimezone()
        if spend.since is not None
        else None
    )

    if not balance.is_available:
        footnote = "Conta sem saldo disponível para uso"
    elif since is not None:
        footnote = f"Monitorado desde {since:%d/%m/%Y %H:%M}"
    else:
        footnote = "Monitorando…"

    return MetricCardModel(
        title="DeepSeek",
        primary_label="Saldo",
        primary_value=format_money(balance.total, balance.currency),
        secondary_label="Gasto total" if baseline is not None else "Gasto monitorado",
        secondary_value=format_money(spent_total, balance.currency),
        detail_text=(
            f"Concedido {format_money(balance.granted, balance.currency)}"
            f" · Recarregado {format_money(balance.topped_up, balance.currency)}"
        ),
        footnote=footnote,
        available=balance.is_available,
    )


def build_mimo_card(snapshot: MiMoSnapshot) -> MetricCardModel:
    detail = f"Mês {format_money(snapshot.month_cost, snapshot.currency)}"
    if snapshot.recharged is not None:
        detail = (
            f"Recarregado {format_money(snapshot.recharged, snapshot.currency)}"
            f" · {detail}"
        )

    return MetricCardModel(
        title="MiMo",
        primary_label="Saldo",
        primary_value=format_money(snapshot.balance, snapshot.currency),
        secondary_label="Gasto total",
        secondary_value=format_money(snapshot.total_cost, snapshot.currency),
        detail_text=detail,
        footnote=f"Sessão do Chrome · {_token_text(snapshot.total_tokens)}",
        available=snapshot.balance > 0,
    )


def build_claude_card(usage: ClaudeUsage) -> MetricCardModel:
    detail = f"Assinatura {usage.plan}" if usage.plan else "Assinatura Claude Code"
    return MetricCardModel(
        title="Claude Code",
        primary_label="Janela de 5 horas",
        primary_value=_claude_window_text(usage.five_hour),
        secondary_label="Janela semanal",
        secondary_value=_claude_window_text(usage.seven_day),
        detail_text=detail,
        footnote=_claude_resets(usage),
        available=usage.five_hour is not None or usage.seven_day is not None,
    )


def _claude_window_text(window: ClaudeWindow | None) -> str:
    if window is None:
        return "Indisponível"
    remaining = max(0, min(100, int(round(100 - window.utilization))))
    return f"{remaining}% restantes"


def _claude_resets(usage: ClaudeUsage) -> str:
    parts: list[str] = []
    for label, window in (("5h", usage.five_hour), ("7d", usage.seven_day)):
        if window is None or window.resets_at is None:
            continue
        dt = datetime.fromtimestamp(window.resets_at).astimezone()
        parts.append(f"{label} {dt:%d/%m %H:%M}")
    return " · ".join(parts) if parts else "Redefinição indisponível"


def _token_text(tokens: int) -> str:
    if tokens >= 1_000_000:
        text = f"{tokens / 1_000_000:.1f}".replace(".", ",")
        return f"{text}M tokens"
    return f"{tokens} tokens"


def _window_title(window: UsageWindow) -> str:
    seconds = window.window_seconds
    if seconds is not None and seconds > 0 and seconds % 3600 == 0:
        hours = seconds // 3600
        unit = "hora" if hours == 1 else "horas"
        return f"Limite de uso de {hours} {unit}"
    return "Limite de uso por janela"


def _remaining_percent(window: UsageWindow | None) -> int:
    if window is None or window.remaining_percent is None:
        return 0
    return max(0, min(100, int(round(window.remaining_percent))))


def _reset_text(window: UsageWindow | None, *, include_date: bool) -> str:
    if window is None or window.reset_at is None:
        return "Redefinição indisponível"

    dt = datetime.fromtimestamp(window.reset_at).astimezone()
    if include_date:
        months = (
            "jan.",
            "fev.",
            "mar.",
            "abr.",
            "mai.",
            "jun.",
            "jul.",
            "ago.",
            "set.",
            "out.",
            "nov.",
            "dez.",
        )
        return f"Redefinição {dt.day} de {months[dt.month - 1]} de {dt.year} {dt:%H:%M}"
    return f"Redefinição {dt:%H:%M}"
