from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from config import settings

TZ = ZoneInfo(settings.TIMEZONE)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_local(dt_naive_utc: datetime) -> datetime:
    return dt_naive_utc.replace(tzinfo=timezone.utc).astimezone(TZ)


def local_to_utc_naive(dt_local: datetime) -> datetime:
    if dt_local.tzinfo is None:
        dt_local = dt_local.replace(tzinfo=TZ)
    return dt_local.astimezone(timezone.utc).replace(tzinfo=None)


def local_now() -> datetime:
    return datetime.now(TZ)


def iso_z(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    # PIDS timestamps use an aware UTC type while the legacy Nexus schema stores
    # naive UTC. Normalize both shapes before appending Z; otherwise aware values
    # become invalid strings such as `2026-11-17T09:42:35+00:00Z` in the UI.
    if dt.tzinfo is not None and dt.utcoffset() is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat(timespec="seconds") + "Z"


def human_due(dt_utc: datetime | None, now_utc: datetime | None = None) -> str:
    """'overdue', 'today 6:00 PM', 'tomorrow 11:59 PM', 'in 3 days (Fri)'."""
    if not dt_utc:
        return "no deadline"
    now_l = to_local(now_utc or utcnow())
    d = to_local(dt_utc)
    days = (d.date() - now_l.date()).days
    t = d.strftime("%I:%M %p").lstrip("0")
    if d < now_l:
        return "overdue"
    if days == 0:
        return f"today {t}"
    if days == 1:
        return f"tomorrow {t}"
    return f"in {days} days ({d.strftime('%a %d %b')})"
