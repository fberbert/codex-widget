from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .claude import ClaudeUsage, ClaudeWindow
from .cursor_usage import CursorUsage
from .deepseek import DeepSeekBalance, SpendSnapshot, format_money
from .kiro import KiroUsage
from .mimo import MiMoSnapshot
from .openai_costs import OpenAICosts
from .usage import CodexUsage, UsageWindow


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
    primary_percent: int | None = None
    secondary_percent: int | None = None


def build_codex_card(usage: CodexUsage) -> MetricCardModel:
    plan = usage.plan_type.strip().capitalize() if usage.plan_type else None
    return MetricCardModel(
        title="Codex",
        primary_label=_window_label(usage.short_window),
        primary_value=_window_text(usage.short_window),
        primary_percent=_window_percent(usage.short_window),
        secondary_label="Janela semanal",
        secondary_value=_window_text(usage.weekly),
        secondary_percent=_window_percent(usage.weekly),
        detail_text=f"Assinatura {plan}" if plan else "Limites de uso do Codex",
        footnote=_codex_resets(usage),
        available=usage.short_window is not None or usage.weekly is not None,
    )


def _window_percent(window: UsageWindow | None) -> int | None:
    if window is None or window.remaining_percent is None:
        return None
    return max(0, min(100, int(round(window.remaining_percent))))


def _window_text(window: UsageWindow | None) -> str:
    percent = _window_percent(window)
    return "Indisponível" if percent is None else f"{percent}% restantes"


def _window_label(window: UsageWindow | None) -> str:
    seconds = window.window_seconds if window is not None else None
    if seconds is not None and seconds > 0 and seconds % 3600 == 0:
        hours = seconds // 3600
        unit = "hora" if hours == 1 else "horas"
        return f"Janela de {hours} {unit}"
    return "Janela curta"


def _codex_resets(usage: CodexUsage) -> str:
    parts: list[str] = []
    if usage.short_window is not None and usage.short_window.reset_at is not None:
        short = datetime.fromtimestamp(usage.short_window.reset_at).astimezone()
        parts.append(f"5h {short:%H:%M}")
    if usage.weekly is not None and usage.weekly.reset_at is not None:
        weekly = datetime.fromtimestamp(usage.weekly.reset_at).astimezone()
        parts.append(f"7d {weekly:%d/%m %H:%M}")
    return " · ".join(parts) if parts else "Redefinição indisponível"


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


def build_openai_card(
    costs: OpenAICosts,
    *,
    credits_purchased: Decimal | None = None,
) -> MetricCardModel:
    since = datetime.fromtimestamp(costs.tracked_since).astimezone()
    today = format_money(costs.today, costs.currency)
    if credits_purchased is not None:
        balance = credits_purchased - costs.tracked
        return MetricCardModel(
            title="OpenAI",
            primary_label="Saldo",
            primary_value=format_money(balance, costs.currency),
            secondary_label="Gasto no mês",
            secondary_value=format_money(costs.month, costs.currency),
            detail_text=(
                f"Hoje {today}"
                f" · Desde {since:%d/%m/%Y} {format_money(costs.tracked, costs.currency)}"
            ),
            footnote=(
                f"Créditos informados {format_money(credits_purchased, costs.currency)}"
                " · custos da organização"
            ),
            available=balance > 0,
        )

    return MetricCardModel(
        title="OpenAI",
        primary_label="Gasto no mês",
        primary_value=format_money(costs.month, costs.currency),
        secondary_label="Gasto hoje",
        secondary_value=today,
        detail_text=f"Período desde {since:%d/%m/%Y}",
        footnote="Custos da organização (Admin key)",
        available=True,
    )


def build_claude_card(usage: ClaudeUsage) -> MetricCardModel:
    detail = f"Assinatura {usage.plan}" if usage.plan else "Assinatura Claude Code"
    return MetricCardModel(
        title="Claude Code",
        primary_label="Janela de 5 horas",
        primary_value=_claude_window_text(usage.five_hour),
        primary_percent=_claude_window_percent(usage.five_hour),
        secondary_label="Janela semanal",
        secondary_value=_claude_window_text(usage.seven_day),
        secondary_percent=_claude_window_percent(usage.seven_day),
        detail_text=detail,
        footnote=_claude_resets(usage),
        available=usage.five_hour is not None or usage.seven_day is not None,
    )


def build_cursor_card(usage: CursorUsage) -> MetricCardModel:
    total_remaining = _remaining_percent(usage.total_percent_used)
    api_remaining = _remaining_percent(usage.api_percent_used)
    detail = f"Plano {usage.plan}" if usage.plan else "Uso do Cursor"
    return MetricCardModel(
        title="Cursor",
        primary_label="Uso do plano",
        primary_value=_percent_text(total_remaining),
        primary_percent=total_remaining,
        secondary_label="Uso da API",
        secondary_value=_percent_text(api_remaining),
        secondary_percent=api_remaining,
        detail_text=detail,
        footnote=_cursor_footnote(usage),
        available=total_remaining is not None and total_remaining > 0,
    )


def _remaining_percent(percent_used: float | None) -> int | None:
    if percent_used is None:
        return None
    return max(0, min(100, int(round(100 - percent_used))))


def _percent_text(percent: int | None) -> str:
    return "Indisponível" if percent is None else f"{percent}% restantes"


def _cursor_footnote(usage: CursorUsage) -> str:
    spent = Decimal(usage.spent_cents) / Decimal(100)
    parts = [format_money(spent, "USD")]
    if usage.limit_cents is not None:
        limit = Decimal(usage.limit_cents) / Decimal(100)
        parts[0] = f"{parts[0]} de {format_money(limit, 'USD')}"
    if usage.cycle_end is not None:
        renewal = datetime.fromtimestamp(usage.cycle_end).astimezone()
        parts.append(f"renova {renewal:%d/%m}")
    return " · ".join(parts)


def build_kiro_card(usage: KiroUsage) -> MetricCardModel:
    percent = usage.percent_remaining
    footnote_parts: list[str] = []
    if usage.next_reset is not None:
        reset = datetime.fromtimestamp(usage.next_reset).astimezone()
        footnote_parts.append(f"Renova em {reset:%d/%m/%Y}")
    if usage.overage_enabled:
        overage = (
            f"excedente {_credits(usage.overage_used)} de {_credits(usage.overage_cap)}"
            f" · {format_money(usage.overage_charges, usage.currency)}"
        )
    else:
        overage = "excedente desativado"
    footnote_parts.append(overage)

    return MetricCardModel(
        title="Kiro",
        primary_label="Créditos restantes",
        primary_value=f"{percent}% restantes",
        primary_percent=percent,
        secondary_label="Créditos usados",
        secondary_value=f"{_credits(usage.used)} de {_credits(usage.limit)}",
        detail_text=f"Plano {usage.plan}" if usage.plan else "Assinatura Kiro",
        footnote=" · ".join(footnote_parts),
        available=percent > 0,
    )


def _credits(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.1"))
    if quantized == quantized.to_integral_value():
        text = f"{int(quantized):,}".replace(",", ".")
    else:
        text = f"{quantized:,.1f}".replace(",", "@").replace(".", ",").replace("@", ".")
    return text



def _claude_window_percent(window: ClaudeWindow | None) -> int | None:
    if window is None:
        return None
    return max(0, min(100, int(round(100 - window.utilization))))


def _claude_window_text(window: ClaudeWindow | None) -> str:
    percent = _claude_window_percent(window)
    return "Indisponível" if percent is None else f"{percent}% restantes"


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


