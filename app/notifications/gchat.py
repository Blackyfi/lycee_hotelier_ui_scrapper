from __future__ import annotations

import logging
from html import escape

import httpx

from ..config import get_settings
from .payload import NotifPayload, fmt_date

log = logging.getLogger(__name__)


def _build_card(payload: NotifPayload) -> dict:
    n = len(payload.slots)
    header_title = f"{n} créneaux ouverts 🎉" if payload.is_batch else "Réservation ouverte 🎉"

    widgets: list[dict] = []
    for s in payload.slots:
        widgets.append(
            {
                "decoratedText": {
                    "topLabel": fmt_date(s.slot_date),
                    "text": f"{s.service_fr}" + (f" — {', '.join(s.times)}" if s.times else ""),
                }
            }
        )
        if s.dishes:
            dishes = "<br>".join(f"• {escape(d)}" for d in s.dishes)
            widgets.append({"textParagraph": {"text": f'<font color="#57534e">{dishes}</font>'}})

    buttons = [{"text": "Réserver", "onClick": {"openLink": {"url": payload.booking_url}}}]
    if payload.maps_url:
        buttons.append({"text": "Itinéraire", "onClick": {"openLink": {"url": payload.maps_url}}})
    for url in payload.menu_urls[:2]:
        buttons.append({"text": "Menu complet", "onClick": {"openLink": {"url": url}}})
    widgets.append({"buttonList": {"buttons": buttons}})

    return {
        "cardsV2": [
            {
                "cardId": f"slots-{payload.kind.value}-{payload.slots[0].slot_date.isoformat()}-{n}",
                "card": {
                    "header": {
                        "title": header_title,
                        "subtitle": "Lycée Hôtelier d'Occitanie",
                    },
                    "sections": [{"widgets": widgets}],
                },
            }
        ],
        "text": payload.subject,
    }


def send_gchat(webhook_url: str, payload: NotifPayload) -> tuple[bool, str | None]:
    return post_gchat(webhook_url, _build_card(payload))


def post_gchat(webhook_url: str, message: dict) -> tuple[bool, str | None]:
    """POST a ready-made Chat message (cardsV2 + text) to an incoming webhook."""
    settings = get_settings()
    if settings.notifications_dry_run:
        log.info(
            "[DRY-RUN gchat] webhook=%s subject=%s",
            webhook_url[:48] + "...",
            message.get("text"),
        )
        return True, None
    if not webhook_url:
        return False, "no webhook configured"
    try:
        r = httpx.post(webhook_url, json=message, timeout=15.0)
        if r.status_code >= 300:
            return False, f"http {r.status_code}: {r.text[:200]}"
        return True, None
    except Exception as e:
        log.exception("gchat send failed")
        return False, str(e)
