"""Welcome calendar: everything bookable from today, sent once to a new (or returning) subscriber.

Pure rendering only (no DB): the dispatcher builds a WelcomeCalendar from AvailableSlot rows
and hands it to send_email / post_gchat.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import date, timedelta
from html import escape
from pathlib import Path
from threading import Lock

from ..models import Service

GIF_DIR = Path(__file__).resolve().parent.parent / "assets" / "gifs"
MAX_LIST_WEEKS = 8  # the detailed list stops here; the month grid always covers the full range
GCHAT_MAX_ITEMS = 10

_JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_JOURS_COURTS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
_MOIS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]  # fmt: skip
_MOIS_COURTS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
_SVC_SHORT = {Service.LUNCH: "Déj.", Service.DINNER: "Dîn."}
_SVC_LETTER = {Service.LUNCH: "M", Service.DINNER: "S"}
_SVC_ORDER = {Service.LUNCH: 0, Service.DINNER: 1}

# Email palette (matches payload.py)
_OK, _FULL, _FULL_BG, _NONE, _MUTED, _LINE = "#16a34a", "#dc2626", "#fee2e2", "#f5f5f4", "#78716c", "#e7e5e4"
_CHIP = (
    "display:inline-block;padding:1px 6px;margin:1px 3px 1px 0;border-radius:5px;font-family:monospace;font-size:12px;"
)
_CHIP_OPEN = _CHIP + "background:#fff3bf;"
_CHIP_FULL = _CHIP + f"background:{_FULL_BG};color:{_FULL};border:1px dashed {_FULL};text-decoration:line-through;"


def fmt_long(d: date) -> str:
    return f"{_JOURS[d.weekday()]} {d.day} {_MOIS[d.month - 1]}"


def fmt_short(d: date) -> str:
    return f"{_JOURS_COURTS[d.weekday()]} {d.day} {_MOIS_COURTS[d.month - 1]}"


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


_last_gif: str | None = None
_gif_lock = Lock()


def pick_gif() -> Path | None:
    """Random GIF from the curated, re-encoded pool, never the same one twice in a row."""
    global _last_gif
    pool = sorted(GIF_DIR.glob("*.gif"))
    if not pool:
        return None
    with _gif_lock:
        choices = [p for p in pool if p.name != _last_gif] or pool
        gif = secrets.choice(choices)
        _last_gif = gif.name
    return gif


@dataclass(frozen=True)
class CalSlot:
    slot_date: date
    service: Service
    times: tuple[str, ...]
    is_open: bool  # False = was bookable, has since disappeared from the calendar ("complet")


@dataclass(frozen=True)
class WelcomeCalendar:
    name: str
    today: date
    slots: tuple[CalSlot, ...]  # from today onward, already filtered by the user's preference
    booking_url: str
    unsubscribe_token: str | None = None
    address: str | None = None
    maps_url: str | None = None
    gif_cid: str | None = None  # Content-ID of the inline GIF attached to the email
    services: tuple[Service, ...] = field(default=(Service.LUNCH, Service.DINNER))

    def __post_init__(self):
        if not self.open_slots:
            raise ValueError("WelcomeCalendar requires at least one open slot")

    @property
    def open_slots(self) -> list[CalSlot]:
        return [s for s in self.slots if s.is_open]

    @property
    def full_slots(self) -> list[CalSlot]:
        return [s for s in self.slots if not s.is_open]

    @property
    def subject(self) -> str:
        n = len(self.open_slots)
        last = self.open_slots[-1].slot_date
        plural = "x" if n > 1 else ""
        s = "s" if n > 1 else ""
        return f"[Lycée Hôtelier] Bienvenue · {n} créneau{plural} réservable{s} d'ici le {last.day} {_MOIS[last.month - 1]}"

    # ─── HTML pieces ──────────────────────────────────────────────────────
    def _badge(self, slot: CalSlot | None, svc: Service) -> str:
        base = "display:inline-block;width:18px;height:14px;line-height:14px;border-radius:3px;font-size:9px;font-weight:700;text-align:center;margin-right:2px;"
        if slot is None:
            return f'<span style="{base}"></span>'
        if slot.is_open:
            return f'<span style="{base}background:{_OK};color:#fff">{_SVC_LETTER[svc]}</span>'
        return (
            f'<span style="{base}background:{_FULL_BG};color:{_FULL};border:1px dashed {_FULL};'
            f'line-height:12px;width:16px;height:12px;text-decoration:line-through">{_SVC_LETTER[svc]}</span>'
        )

    def _months_html(self) -> str:
        by_key = {(s.slot_date, s.service): s for s in self.slots}
        cols = 7 if any(s.slot_date.weekday() >= 5 for s in self.slots) else 5
        heads = "".join(
            f'<th style="font-size:11px;color:{_MUTED};font-weight:500;padding:0">{j[0].upper()}</th>'
            for j in _JOURS[:cols]
        )
        first, last = self.slots[0].slot_date, self.slots[-1].slot_date
        blocks: list[str] = []
        m = date(first.year, first.month, 1)
        while m <= last:
            nxt = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
            rows: list[str] = []
            wk = _monday(m)
            while wk < nxt:
                cells: list[str] = []
                for i in range(cols):
                    d = wk + timedelta(days=i)
                    if d.month != m.month:
                        cells.append("<td></td>")
                        continue
                    num_color = _MUTED if d >= self.today else "#d6d3d1"
                    badges = "".join(self._badge(by_key.get((d, svc)), svc) for svc in self.services)
                    cells.append(
                        f'<td style="padding:0;height:36px;vertical-align:top">'
                        f'<div style="background:{_NONE};border-radius:5px;padding:2px 3px;height:32px">'
                        f'<div style="font-size:11px;color:{num_color}">{d.day}</div>'
                        f'<div style="white-space:nowrap">{badges}</div></div></td>'
                    )
                rows.append(f"<tr>{''.join(cells)}</tr>")
                wk += timedelta(days=7)
            blocks.append(
                '<div style="display:inline-block;vertical-align:top;width:262px;margin:0 10px 12px 0">'
                f'<div style="font-weight:600;font-size:14px;margin-bottom:4px">{_MOIS[m.month - 1].capitalize()} {m.year}</div>'
                '<table role="presentation" style="border-collapse:separate;border-spacing:3px;width:100%;table-layout:fixed">'
                f"<tr>{heads}</tr>{''.join(rows)}</table></div>"
            )
            m = nxt
        legend = (
            f'<div style="font-size:12px;color:{_MUTED};margin-bottom:8px">'
            f'<span style="display:inline-block;width:14px;height:10px;background:{_OK};border-radius:2px"></span> ouvert &nbsp; '
            f'<span style="display:inline-block;width:12px;height:8px;background:{_FULL_BG};border:1px dashed {_FULL};border-radius:2px"></span> complet &nbsp; '
            "M = déjeuner · S = dîner" + (" &nbsp;(pas de service le week-end)" if cols == 5 else "") + "</div>"
        )
        return legend + "".join(blocks)

    def _service_line(self, s: CalSlot) -> str:
        tag = f'<span style="font-size:11px;color:{_MUTED};margin-right:4px">{_SVC_SHORT[s.service]}</span>'
        if s.is_open:
            chips = "".join(f'<span style="{_CHIP_OPEN}">{escape(t)}</span>' for t in s.times) or (
                f'<span style="font-size:12px;color:{_MUTED}">horaires sur le site</span>'
            )
        else:
            chips = "".join(f'<span style="{_CHIP_FULL}">{escape(t)}</span>' for t in s.times) or (
                f'<span style="{_CHIP_FULL}font-family:inherit;text-decoration:none">complet</span>'
            )
            chips += f'<span style="font-size:11px;color:{_FULL};margin-left:2px">complet</span>' if s.times else ""
        return f"<div>{tag}{chips}</div>"

    def _weeks(self) -> tuple[list[tuple[date, dict[date, list[CalSlot]]]], int]:
        weeks: dict[date, dict[date, list[CalSlot]]] = {}
        for s in self.slots:
            weeks.setdefault(_monday(s.slot_date), {}).setdefault(s.slot_date, []).append(s)
        ordered = sorted(weeks.items())
        hidden = sum(1 for _, days in ordered[MAX_LIST_WEEKS:] for lst in days.values() for s in lst if s.is_open)
        return ordered[:MAX_LIST_WEEKS], hidden

    def _list_html(self) -> str:
        shown, hidden = self._weeks()
        blocks: list[str] = []
        for monday, days in shown:
            rows = []
            for d in sorted(days):
                lines = "".join(self._service_line(s) for s in sorted(days[d], key=lambda x: _SVC_ORDER[x.service]))
                rows.append(
                    f'<tr><td style="padding:3px 4px;width:70px;white-space:nowrap;vertical-align:top;'
                    f'border-top:1px solid {_LINE}">{_JOURS_COURTS[d.weekday()]} {d.day}</td>'
                    f'<td style="padding:3px 4px;border-top:1px solid {_LINE}">{lines}</td></tr>'
                )
            blocks.append(
                '<div style="margin:10px 0;padding:8px 10px;background:#fafaf9;border-left:3px solid #f59e0b;border-radius:4px">'
                f'<div style="font-weight:600;font-size:13px;margin-bottom:4px">Semaine du {monday.day} {_MOIS[monday.month - 1]}</div>'
                f'<table role="presentation" style="border-collapse:collapse;width:100%;font-size:13px">{"".join(rows)}</table></div>'
            )
        more = (
            f'<p style="font-size:13px;color:{_MUTED};font-style:italic">+ {hidden} autres créneaux plus loin, '
            "visibles dans le calendrier ci-dessus.</p>"
            if hidden
            else ""
        )
        return "".join(blocks) + more

    def html(self) -> str:
        open_slots, full = self.open_slots, self.full_slots
        n_days = len({s.slot_date for s in open_slots})
        stat = f"display:inline-block;margin:0 6px 6px 0;padding:6px 10px;background:#fafaf9;border:1px solid {_LINE};border-radius:6px;font-size:13px"
        stats = (
            f'<span style="{stat}"><b>{len(open_slots)}</b> services ouverts</span>'
            f'<span style="{stat}"><b>{n_days}</b> jours</span>'
            + (f'<span style="{stat}"><b>{len(full)}</b> déjà complets</span>' if full else "")
            + f'<span style="{stat}">Prochain : <b>{fmt_short(open_slots[0].slot_date)}</b></span>'
        )
        gif = (
            f'<div style="text-align:center;margin:14px 0 4px"><img src="cid:{self.gif_cid}" width="200" alt="" '
            'style="border-radius:8px;max-width:100%;height:auto"></div>'
            if self.gif_cid
            else ""
        )
        h4 = f"margin:22px 0 8px;font-size:13px;text-transform:uppercase;letter-spacing:0.06em;color:{_MUTED}"
        maps_button = ""
        if self.maps_url:
            maps_button = (
                f'<a href="{escape(self.maps_url)}" style="display:inline-block;padding:10px 16px;margin-left:8px;'
                "background:#fff;color:#1c1917;border:1px solid #d6d3d1;text-decoration:none;border-radius:6px;"
                'font-weight:600">📍 Ouvrir dans Google Maps</a>'
            )
        unsubscribe = ""
        if self.unsubscribe_token:
            code = 'style="background:#f5f5f4;padding:2px 6px;border-radius:4px"'
            unsubscribe = (
                '<p style="color:#888;font-size:12px;margin-top:8px">'
                "Pour arrêter ces notifications, envoyez un <strong>nouvel email</strong> (pas une réponse) "
                f"à cette adresse avec pour sujet : <code {code}>STOP {escape(self.unsubscribe_token)}</code><br>"
                f"Pour les réactiver : <code {code}>START {escape(self.unsubscribe_token)}</code></p>"
            )
        return f"""<!doctype html>
<html><body style="font-family:system-ui,sans-serif;max-width:600px;color:#1c1917;font-size:14px;line-height:1.5">
  <p style="font-size:15px">Bonjour {escape(self.name)},</p>
  <p>Vous êtes inscrit(e) aux alertes du Restaurant d'Application. Voici tout ce qui est
  <b>réservable dès maintenant</b>. Ensuite, vous recevrez un message à chaque nouvelle ouverture.</p>
  <div style="margin-top:12px">{stats}</div>
  {gif}
  <div style="{h4}">Calendrier</div>
  {self._months_html()}
  <div style="{h4}">Créneaux</div>
  {self._list_html()}
  <p style="margin-top:18px"><a href="{escape(self.booking_url)}"
        style="display:inline-block;padding:10px 16px;background:#f1c40f;color:#000;
               text-decoration:none;border-radius:6px;font-weight:600">Réserver maintenant</a>{maps_button}</p>
  <hr style="margin-top:24px;border:none;border-top:1px solid {_LINE}">
  <p style="color:#888;font-size:12px">
    Notification automatique du watcher Restaurant d'Application — Lycée Hôtelier d'Occitanie.
  </p>
  {unsubscribe}
</body></html>"""

    def text(self) -> str:
        open_slots, full = self.open_slots, self.full_slots
        lines = [
            f"Bonjour {self.name},",
            "",
            "Vous êtes inscrit(e) aux alertes du Restaurant d'Application.",
            f"Réservable dès maintenant : {len(open_slots)} services"
            + (f" ({len(full)} déjà complets)" if full else "")
            + ".",
            "",
        ]
        shown, hidden = self._weeks()
        for monday, days in shown:
            lines.append(f"Semaine du {monday.day} {_MOIS[monday.month - 1]}")
            for d in sorted(days):
                for s in sorted(days[d], key=lambda x: _SVC_ORDER[x.service]):
                    times = ", ".join(s.times) or ("horaires sur le site" if s.is_open else "")
                    state = "" if s.is_open else " [COMPLET]"
                    lines.append(f"  {fmt_short(d)} {_SVC_SHORT[s.service]} {times}{state}".rstrip())
            lines.append("")
        if hidden:
            lines += [f"+ {hidden} autres créneaux plus loin.", ""]
        lines.append(f"Réserver : {self.booking_url}")
        if self.maps_url:
            if self.address:
                lines.append(f"Adresse : {self.address}")
            lines.append(f"Google Maps : {self.maps_url}")
        if self.unsubscribe_token:
            lines += [
                "",
                "Pour arrêter : envoyez un NOUVEL email (pas une réponse) à cette adresse",
                f"avec pour sujet : STOP {self.unsubscribe_token}",
                f"Pour réactiver : START {self.unsubscribe_token}",
            ]
        return "\n".join(lines) + "\n"

    def gchat_message(self, email_also_sent: bool) -> dict:
        open_slots, full = self.open_slots, self.full_slots
        n_days = len({s.slot_date for s in open_slots})
        widgets: list[dict] = [
            {
                "decoratedText": {
                    "topLabel": fmt_long(s.slot_date).capitalize(),
                    "text": f"{_SVC_SHORT[s.service]} " + (", ".join(s.times) or "horaires sur le site"),
                }
            }
            for s in open_slots[:GCHAT_MAX_ITEMS]
        ]
        extra = len(open_slots) - GCHAT_MAX_ITEMS
        if extra > 0:
            where = "le détail complet est dans l'email" if email_also_sent else "voir le site de réservation"
            widgets.append({"textParagraph": {"text": f"+ {extra} autres, {where}."}})
        buttons = [{"text": "Réserver", "onClick": {"openLink": {"url": self.booking_url}}}]
        if self.maps_url:
            buttons.append({"text": "📍 Google Maps", "onClick": {"openLink": {"url": self.maps_url}}})
        widgets.append({"buttonList": {"buttons": buttons}})
        subtitle = f"{len(open_slots)} services réservables sur {n_days} jours" + (
            f" · {len(full)} déjà complets" if full else ""
        )
        return {
            "cardsV2": [
                {
                    "cardId": f"welcome-{self.today.isoformat()}-{len(open_slots)}",
                    "card": {
                        "header": {"title": f"Bienvenue {self.name} 🍽️", "subtitle": subtitle},
                        "sections": [{"widgets": widgets}],
                    },
                }
            ],
            "text": self.subject,
        }
