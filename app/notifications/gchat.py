from __future__ import annotations

import logging

import httpx

from ..config import get_settings
from ..models import NotificationKind
from .payload import NotifPayload

log = logging.getLogger(__name__)


def _build_card(payload: NotifPayload) -> dict:
    n = len(payload.slots)
    if payload.kind is NotificationKind.OPEN:
        header_title = (f"{n} créneaux ouverts 🎉" if payload.is_batch
                        else "Réservation ouverte 🎉")
    else:
        header_title = (f"Rappel — {n} créneaux encore libres" if payload.is_batch
                        else "Rappel — créneau encore libre")

    widgets: list[dict] = []
    for s in payload.slots:
        widgets.append({"decoratedText": {
            "topLabel": s.slot_date.strftime("%A %d %B %Y"),
            "text": f"{s.service_fr}" + (f" — {', '.join(s.times)}" if s.times else ""),
        }})

    buttons = [{"text": "Réserver", "onClick": {"openLink": {"url": payload.booking_url}}}]
    if payload.maps_url:
        buttons.append({"text": "Itinéraire",
                        "onClick": {"openLink": {"url": payload.maps_url}}})
    if payload.dashboard_url:
        buttons.append({"text": "Tableau de bord",
                        "onClick": {"openLink": {"url": payload.dashboard_url}}})
    widgets.append({"buttonList": {"buttons": buttons}})

    return {
        "cardsV2": [{
            "cardId": f"slots-{payload.kind.value}-{payload.slots[0].slot_date.isoformat()}-{n}",
            "card": {
                "header": {"title": header_title, "subtitle": "Lycée Hôtelier d'Occitanie"},
                "sections": [{"widgets": widgets}],
            },
        }],
        "text": payload.subject,
    }


def send_gchat(webhook_url: str, payload: NotifPayload) -> tuple[bool, str | None]:
    settings = get_settings()
    if settings.notifications_dry_run:
        log.info("[DRY-RUN gchat] webhook=%s subject=%s", webhook_url[:48] + "...", payload.subject)
        return True, None
    if not webhook_url:
        return False, "no webhook configured"
    try:
        r = httpx.post(webhook_url, json=_build_card(payload), timeout=15.0)
        if r.status_code >= 300:
            return False, f"http {r.status_code}: {r.text[:200]}"
        return True, None
    except Exception as e:
        log.exception("gchat send failed")
        return False, str(e)
