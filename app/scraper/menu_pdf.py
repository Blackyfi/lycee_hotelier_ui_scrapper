"""Extract per-day dishes from the restaurant's menu PDFs.

Each period's PDF gets a new design, so this relies only on what they share: every day
has a "DÉJEUNER" and a "DÎNER" heading side by side, preceded by a weekday + day number
(month either next to it or in the margin). Dishes are read column by column under those
headings. Dates are cross-checked against the weekday, so a misread day is dropped rather
than attached to the wrong service.
"""

from __future__ import annotations

import io
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta

import pdfplumber

from ..models import Service

log = logging.getLogger(__name__)

_WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_MONTHS = {
    "janvier": 1,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
}
_WD = "|".join(_WEEKDAYS)
_MO = "|".join(_MONTHS)
_DAY_LABEL_RE = re.compile(rf"^({_WD})?(\d{{1,2}})(?:er)?({_MO})?$")
_WEEK_HEADING_RE = re.compile(rf"^(semaine|du\d{{1,2}}(?:er)?({_MO})?au\d)")
_COURSE_LABEL_MAX = 25  # "MISE EN BOUCHE", "POISSON"… — uppercase-only lines this short are dropped
_SEGMENT_GAP = 14.0  # pt; wider horizontal gaps split a line into separate columns


def _norm(s: str) -> str:
    """Lowercase, accents stripped, all whitespace removed ("D ÉJ EUN ER" → "dejeuner")."""
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c) and not c.isspace()).lower()


@dataclass
class _Seg:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float

    @property
    def norm(self) -> str:
        return _norm(self.text)


@dataclass
class _Line:
    page: int
    top: float
    segs: list[_Seg]
    in_margin: bool = False  # top/bottom 8% of the page, where running headers/footers live

    @property
    def norm(self) -> str:
        return "".join(s.norm for s in self.segs)


@dataclass
class _Block:
    lunch_x: float
    dinner_x: float
    weekday: int | None
    day: int | None
    month_hint: int | None
    cols: dict[Service, list[_Seg]] = field(default_factory=lambda: {Service.LUNCH: [], Service.DINNER: []})


def _lines(pdf: pdfplumber.PDF) -> list[_Line]:
    out: list[_Line] = []
    for pno, page in enumerate(pdf.pages):
        height = float(page.height)
        words = page.extract_words(x_tolerance=1.5, y_tolerance=2, keep_blank_chars=False)
        words.sort(key=lambda w: (round(w["top"]), w["x0"]))
        rows: list[list[dict]] = []
        for w in words:
            if rows and abs(rows[-1][0]["top"] - w["top"]) <= 2.5:
                rows[-1].append(w)
            else:
                rows.append([w])
        for row in rows:
            row.sort(key=lambda w: w["x0"])
            segs: list[_Seg] = []
            for w in row:
                if segs and w["x0"] - segs[-1].x1 <= _SEGMENT_GAP:
                    gap = " " if w["x0"] - segs[-1].x1 > 1.0 else ""
                    segs[-1].text += gap + w["text"]
                    segs[-1].x1, segs[-1].bottom = w["x1"], max(segs[-1].bottom, w["bottom"])
                else:
                    segs.append(_Seg(w["text"], w["x0"], w["x1"], w["top"], w["bottom"]))
            top = row[0]["top"]
            out.append(_Line(pno, top, segs, top < height * 0.08 or top > height * 0.92))
    return out


def _repeated_norms(lines: list[_Line]) -> set[str]:
    """Running headers/footers: the same margin line (digits ignored) on 3+ pages."""
    pages: dict[str, set[int]] = {}
    for ln in lines:
        if not ln.in_margin:
            continue
        key = re.sub(r"\d", "", ln.norm)
        if len(key) > 6:
            pages.setdefault(key, set()).add(ln.page)
    return {k for k, v in pages.items() if len(v) >= 3}


def _header(line: _Line, page_width: float) -> tuple[float, float] | None:
    """(lunch_x, dinner_x) if this line is a DÉJEUNER | DÎNER column heading."""
    lunch = next((s for s in line.segs if s.norm.startswith("dejeuner") and len(s.norm) <= 12), None)
    dinner = next((s for s in line.segs if s.norm.startswith("diner") and len(s.norm) <= 9), None)
    if lunch and dinner and dinner.x0 - lunch.x0 > page_width * 0.25:
        return lunch.x0, dinner.x0
    return None


def _is_label(norm: str) -> bool:
    return norm in _WEEKDAYS or norm in _MONTHS or bool(_DAY_LABEL_RE.match(norm))


def _is_course_label(seg: _Seg) -> bool:
    letters = [c for c in seg.text if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters) and len(seg.norm) <= _COURSE_LABEL_MAX


def _join_dishes(segs: list[_Seg]) -> list[str]:
    segs = [s for s in segs if s.text.strip() and s.text.strip() not in ("✦", "·", "•")]
    if not segs:
        return []
    dishes: list[str] = []
    in_allergens = False
    prev: _Seg | None = None
    for s in segs:
        text = " ".join(s.text.split())
        if _norm(text).startswith("allergenes"):
            in_allergens, prev = True, s
            continue
        # wrapped lines sit tight under each other; a new dish leaves at least half a line of air
        new_dish = not dishes or prev is None or s.top - prev.bottom > 0.55 * (s.bottom - s.top)
        if in_allergens and not new_dish:
            prev = s
            continue
        in_allergens = False
        if new_dish:
            dishes.append(text)
        else:
            dishes[-1] += ("" if dishes[-1].endswith("-") else " ") + text
        prev = s
    return dishes


def _resolve_dates(blocks: list[_Block], year: int, first_month: int) -> list[tuple[date, _Block]]:
    """Walk blocks in order, picking the first date after the previous one that matches the
    day number, the weekday when known, and the margin month when given."""
    out: list[tuple[date, _Block]] = []
    prev = date(year, first_month, 1) - timedelta(days=1)
    for b in blocks:
        if b.day is None:
            continue
        found = None
        for i in range(1, 46):
            d = prev + timedelta(days=i)
            if d.day != b.day or (b.weekday is not None and d.weekday() != b.weekday):
                continue
            if b.month_hint is not None and d.month != b.month_hint:
                continue
            found = d
            break
        if found is None:
            log.info("menu pdf: could not date block (weekday=%s day=%s)", b.weekday, b.day)
            continue
        out.append((found, b))
        prev = found
    return out


def parse_menu_pdf(data: bytes) -> dict[tuple[date, Service], list[str]]:
    """Return {(date, service): [dish, …]} for every served service the PDF describes.

    Services marked "Pas de service" are left out. Returns {} when the layout isn't recognised.
    """
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        if not pdf.pages:
            return {}
        width = float(pdf.pages[0].width)
        lines = _lines(pdf)
        meta_title = str(pdf.metadata.get("Title") or "")

    repeated = _repeated_norms(lines)
    head_text = _norm(meta_title + " " + " ".join(" ".join(s.text for s in ln.segs) for ln in lines[:15]))
    year_m = re.search(r"20\d\d", head_text)
    month_m = re.search(rf"({_MO})", head_text)

    blocks: list[_Block] = []
    pending: dict[str, int | None] = {"weekday": None, "day": None, "month": None}
    cur: _Block | None = None

    for ln in lines:
        hdr = _header(ln, width)
        if (
            ln.in_margin
            and not hdr
            and re.sub(r"\d", "", ln.norm) in repeated
            and not any(_is_label(seg.norm) for seg in ln.segs)
        ):
            continue
        if hdr:
            for seg in ln.segs:  # the day number may share the heading's line, left of it
                m = _DAY_LABEL_RE.match(seg.norm)
                if seg.x0 < hdr[0] - 5 and m:
                    pending["day"] = int(m.group(2))
            cur = _Block(hdr[0], hdr[1], pending["weekday"], pending["day"], pending["month"])
            blocks.append(cur)
            pending.update(weekday=None, day=None, month=None)
            continue
        n = ln.norm
        if _WEEK_HEADING_RE.match(n) or n.startswith(("dejeuner", "diner")):
            cur = None  # week banner or a side section (e.g. "DÉJEUNER · RESTAURANT D'INITIATION")
            continue
        for seg in ln.segs:
            sn = seg.norm
            if sn in _WEEKDAYS:
                pending["weekday"] = _WEEKDAYS.index(sn)
                cur = None
                continue
            if sn in _MONTHS:
                if cur is not None and cur.month_hint is None and seg.x0 < cur.lunch_x - 5:
                    cur.month_hint = _MONTHS[sn]  # month printed in the margin under the heading
                else:
                    pending["month"] = _MONTHS[sn]
                continue
            m = _DAY_LABEL_RE.match(sn)
            if m and (cur is None or seg.x0 < cur.lunch_x - 5 or m.group(1)):
                if m.group(1):
                    pending["weekday"] = _WEEKDAYS.index(m.group(1))
                pending["day"] = int(m.group(2))
                if m.group(3):
                    pending["month"] = _MONTHS[m.group(3)]
                cur = None
                continue
            if cur is None or _is_course_label(seg):
                continue
            if seg.x0 >= cur.dinner_x - 8:
                cur.cols[Service.DINNER].append(seg)
            elif seg.x0 >= cur.lunch_x - 8:
                cur.cols[Service.LUNCH].append(seg)

    if len(blocks) < 3 or not year_m or not month_m:
        log.info("menu pdf: layout not recognised (%d day headings)", len(blocks))
        return {}

    result: dict[tuple[date, Service], list[str]] = {}
    for d, b in _resolve_dates(blocks, int(year_m.group()), _MONTHS[month_m.group(1)]):
        for svc, segs in b.cols.items():
            dishes = _join_dishes(segs)
            if dishes and not _norm(dishes[0]).startswith("pasdeservice"):
                result[(d, svc)] = dishes
    return result
