from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .usage import UsageWindow


@dataclass(frozen=True)
class UsageCardModel:
    title: str
    percent_remaining: int
    reset_text: str


def build_card_models(
    *,
    five_hour: UsageWindow | None,
    weekly: UsageWindow | None,
) -> tuple[UsageCardModel, UsageCardModel]:
    return (
        UsageCardModel(
            title="Limite de uso de 5 horas",
            percent_remaining=_remaining_percent(five_hour),
            reset_text=_reset_text(five_hour, include_date=False),
        ),
        UsageCardModel(
            title="Limite de uso semanal",
            percent_remaining=_remaining_percent(weekly),
            reset_text=_reset_text(weekly, include_date=True),
        ),
    )


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
