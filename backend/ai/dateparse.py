"""Small, dependency-free natural date parser for the rule-based fallback."""
import re
from datetime import datetime, timedelta

from timeutil import TZ

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
RE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
RE_DMY = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
RE_D_MON = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON}\.?(?:,?\s+(\d{{4}}))?\b", re.I)
RE_MON_D = re.compile(rf"\b{_MON}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b", re.I)
RE_REL = re.compile(r"\b(today|tonight|tomorrow|day after tomorrow)\b", re.I)
RE_WEEKDAY = re.compile(r"\b(?:(next|this|coming)\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I)
RE_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b|\b([01]?\d|2[0-3]):([0-5]\d)\b", re.I)


def _mon(s: str) -> int:
    return MONTHS[s[:3].lower()]


def _time_near(text: str, start: int, end: int):
    """Look for a clock time within 40 chars after the date match."""
    m = RE_TIME.search(text[end : end + 40])
    if not m:
        return None
    if m.group(3):
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower()
        if not 1 <= h <= 12 or not 0 <= mi <= 59:
            return None
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        return h, mi
    h, mi = int(m.group(4)), int(m.group(5))
    return (h, mi) if 0 <= h <= 23 and 0 <= mi <= 59 else None


def find_dates(text: str, ref: datetime) -> list[tuple[int, datetime]]:
    """Return [(position, local aware datetime)] for each date mention. ref = local aware."""
    out: list[tuple[int, datetime]] = []

    def add(pos, end, y, mo, d):
        try:
            base = datetime(y, mo, d, tzinfo=TZ)
        except ValueError:
            return
        t = _time_near(text, pos, end) or (23, 59)
        try:
            out.append((pos, base.replace(hour=t[0], minute=t[1])))
        except ValueError:
            return

    for m in RE_ISO.finditer(text):
        add(m.start(), m.end(), int(m.group(1)), int(m.group(2)), int(m.group(3)))
    for m in RE_DMY.finditer(text):
        add(m.start(), m.end(), int(m.group(3)), int(m.group(2)), int(m.group(1)))
    for rx, order in ((RE_D_MON, "dm"), (RE_MON_D, "md")):
        for m in rx.finditer(text):
            if order == "dm":
                d, mo, y = int(m.group(1)), _mon(m.group(2)), m.group(3)
            else:
                mo, d, y = _mon(m.group(1)), int(m.group(2)), m.group(3)
            year = int(y) if y else ref.year
            cand = None
            try:
                cand = datetime(year, mo, d, tzinfo=TZ)
            except ValueError:
                continue
            if not y and cand.date() < ref.date() - timedelta(days=1):
                year += 1  # "20 Jan" mentioned in November -> next year
            add(m.start(), m.end(), year, mo, d)
    for m in RE_REL.finditer(text):
        w = m.group(1).lower()
        off = {"today": 0, "tonight": 0, "tomorrow": 1, "day after tomorrow": 2}[w]
        day = ref + timedelta(days=off)
        add(m.start(), m.end(), day.year, day.month, day.day)
    for m in RE_WEEKDAY.finditer(text):
        target = WEEKDAYS.index(m.group(2).lower())
        delta = (target - ref.weekday()) % 7
        if delta == 0 or (m.group(1) and m.group(1).lower() == "next"):
            delta = delta or 7
        day = ref + timedelta(days=delta)
        add(m.start(), m.end(), day.year, day.month, day.day)
    return sorted(out, key=lambda x: x[0])
