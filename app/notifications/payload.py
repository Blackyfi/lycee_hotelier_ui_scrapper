from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from html import escape

from ..models import NotificationKind, Service
from .welcome import fmt_long

_SVC_FR = {Service.LUNCH: "Déjeuner", Service.DINNER: "Dîner"}


def fmt_date(d: date) -> str:
    """'Mardi 3 novembre 2026' — French regardless of the container's locale."""
    return f"{fmt_long(d).capitalize()} {d.year}"


@dataclass(frozen=True)
class SlotInfo:
    slot_date: date
    service: Service
    times: tuple[str, ...] = field(default_factory=tuple)
    dishes: tuple[str, ...] = field(default_factory=tuple)  # from the menu PDF, when published
    menu_url: str | None = None  # full menu (PDF / Acrobat link) the dishes came from

    @property
    def service_fr(self) -> str:
        return _SVC_FR.get(self.service, str(self.service.value))


@dataclass(frozen=True)
class NotifPayload:
    """A single notification covering one or more newly-opened (or still-open) slots."""

    kind: NotificationKind
    slots: tuple[SlotInfo, ...]
    booking_url: str
    unsubscribe_token: str | None = None  # if set, render the STOP-by-email footer
    address: str | None = None  # restaurant postal address (for Maps button)
    maps_url: str | None = None  # Google Maps link to render as a button

    def __post_init__(self):
        if not self.slots:
            raise ValueError("NotifPayload requires at least one slot")

    @property
    def menu_urls(self) -> list[str]:
        return list(dict.fromkeys(s.menu_url for s in self.slots if s.menu_url))

    @property
    def is_batch(self) -> bool:
        return len(self.slots) > 1

    @property
    def subject(self) -> str:
        if not self.is_batch:
            s = self.slots[0]
            d = fmt_date(s.slot_date)
            return f"[Lycée Hôtelier] {s.service_fr} disponible le {d}"
        n = len(self.slots)
        return f"[Lycée Hôtelier] {n} créneaux ouverts"

    def html(self) -> str:
        intro = "Une réservation vient d'ouvrir 🎉"
        if self.is_batch:
            intro = f"{len(self.slots)} créneaux viennent d'ouvrir 🎉"

        # Group slots by date for compact rendering
        by_date: dict[date, list[SlotInfo]] = {}
        for s in self.slots:
            by_date.setdefault(s.slot_date, []).append(s)

        blocks: list[str] = []
        for d in sorted(by_date.keys()):
            d_label = fmt_date(d)
            inner: list[str] = []
            for s in sorted(by_date[d], key=lambda x: x.service.value):
                chips = ""
                if s.times:
                    chips = (
                        '<div style="margin-top:4px">'
                        + "".join(
                            f'<span style="display:inline-block;padding:3px 7px;margin:2px;'
                            f"background:#fff3bf;border-radius:6px;font-family:monospace;"
                            f'font-size:13px">{t}</span>'
                            for t in s.times
                        )
                        + "</div>"
                    )
                else:
                    chips = (
                        '<div style="margin-top:4px;color:#666;font-size:13px">'
                        "Heures à confirmer sur la page de réservation</div>"
                    )
                menu = ""
                if s.dishes:
                    menu = (
                        '<div style="margin-top:6px;font-size:13px;color:#44403c">'
                        '<span style="font-size:12px;color:#78716c">🍽️ Au menu</span>'
                        '<ul style="margin:2px 0 0 0;padding-left:16px">'
                        + "".join(f'<li style="margin:1px 0">{escape(d)}</li>' for d in s.dishes)
                        + "</ul></div>"
                    )
                inner.append(f'<li style="margin:4px 0"><strong>{s.service_fr}</strong>{chips}{menu}</li>')
            blocks.append(
                f'<div style="margin:12px 0;padding:10px 12px;background:#fafaf9;'
                f'border-left:3px solid #f59e0b;border-radius:4px">'
                f'<div style="font-weight:600">{d_label}</div>'
                f'<ul style="margin:6px 0 0 0;padding-left:18px">{"".join(inner)}</ul>'
                f"</div>"
            )

        maps_button = ""
        if self.maps_url:
            addr_line = (
                f'<span style="color:#57534e;font-size:13px;margin-left:8px">{self.address}</span>'
                if self.address
                else ""
            )
            maps_button = (
                f'<a href="{self.maps_url}" '
                f'style="display:inline-block;padding:10px 16px;margin-left:8px;'
                f"background:#fff;color:#1c1917;border:1px solid #d6d3d1;"
                f'text-decoration:none;border-radius:6px;font-weight:600">'
                f"📍 Ouvrir dans Google Maps</a>" + addr_line
            )

        menu_links = "".join(
            f'<p style="margin:10px 0 0 0;font-size:13px">📄 <a href="{escape(u)}" style="color:#b45309">'
            "Voir le menu complet</a></p>"
            for u in self.menu_urls
        )

        unsubscribe_html = ""
        if self.unsubscribe_token:
            unsubscribe_html = (
                '<p style="color:#888;font-size:12px;margin-top:12px">'
                "Pour arrêter ces notifications, envoyez un <strong>nouvel email</strong> "
                "(pas une réponse — le sujet de réponse commence par <em>Re:</em>) à cette adresse "
                "avec pour sujet :<br>"
                f'<code style="background:#f5f5f4;padding:2px 6px;border-radius:4px">'
                f"STOP {self.unsubscribe_token}</code><br>"
                "Pour les réactiver, même principe avec : "
                f'<code style="background:#f5f5f4;padding:2px 6px;border-radius:4px">'
                f"START {self.unsubscribe_token}</code>"
                "</p>"
            )
        return f"""<!doctype html>
<html><body style="font-family:system-ui,sans-serif;max-width:560px;color:#1c1917">
  <p>{intro}</p>
  {"".join(blocks)}
  {menu_links}
  <p style="margin-top:18px"><a href="{self.booking_url}"
        style="display:inline-block;padding:10px 16px;background:#f1c40f;color:#000;
               text-decoration:none;border-radius:6px;font-weight:600">
    Réserver maintenant
  </a>{maps_button}</p>
  <hr style="margin-top:24px;border:none;border-top:1px solid #e7e5e4">
  <p style="color:#888;font-size:12px">
    Notification automatique du watcher Restaurant d'Application — Lycée Hôtelier d'Occitanie.
  </p>
  {unsubscribe_html}
</body></html>"""

    def text(self) -> str:
        intro = "Réservation ouverte"
        if self.is_batch:
            intro = f"{len(self.slots)} créneaux viennent d'ouvrir"
        lines = [intro, ""]
        by_date: dict[date, list[SlotInfo]] = {}
        for s in self.slots:
            by_date.setdefault(s.slot_date, []).append(s)
        for d in sorted(by_date.keys()):
            lines.append(fmt_date(d))
            for s in sorted(by_date[d], key=lambda x: x.service.value):
                t = f"  {s.service_fr}"
                if s.times:
                    t += f": {', '.join(s.times)}"
                lines.append(t)
                lines.extend(f"      - {d}" for d in s.dishes)
            lines.append("")
        lines.extend(f"Menu complet: {u}" for u in self.menu_urls)
        lines.append(f"Réserver: {self.booking_url}")
        if self.maps_url:
            if self.address:
                lines.append(f"Adresse: {self.address}")
            lines.append(f"Google Maps: {self.maps_url}")
        if self.unsubscribe_token:
            lines.append("")
            lines.append("Pour arrêter: envoyez un NOUVEL email (pas une réponse) à cette adresse")
            lines.append(f"avec pour sujet: STOP {self.unsubscribe_token}")
            lines.append(f"Pour réactiver: même principe avec   START {self.unsubscribe_token}")
        return "\n".join(lines) + "\n"
