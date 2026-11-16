"""Biçimlendirme yardımcıları (Qt'siz; hem sayfalar hem relations kullanır)."""
from __future__ import annotations

import html
from datetime import date, datetime, timezone

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Istanbul")
except Exception:   # noqa: BLE001 - Windows'ta tzdata kurulu değilse sistem saat dilimi
    TZ = None


def now() -> datetime:
    return datetime.now(TZ) if TZ else datetime.now().astimezone()


def today() -> date:
    return now().date()


def parse_dt(value) -> datetime | None:
    """ISO metni (Azure DevOps / K8s, 'Z' sonekli ve 7 haneli kesirli olabilir) veya datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).replace("Z", "+00:00")
        if "." in s:   # .1234567+00:00 -> .123456+00:00 (Python 6 haneyi kabul eder)
            head, _, rest = s.partition(".")
            digits = "".join(ch for ch in rest if ch.isdigit())
            tail = rest[len(digits):]
            s = f"{head}.{digits[:6].ljust(6, '0')}{tail}"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc) if not isinstance(value, datetime) else dt.astimezone()
    return dt.astimezone(TZ) if TZ else dt.astimezone()


def dt(value, with_seconds: bool = False) -> str:
    d = parse_dt(value)
    if not d:
        return ""
    return d.strftime("%d.%m.%Y %H:%M:%S" if with_seconds else "%d.%m.%Y %H:%M")


def day(value) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    return str(value or "")


def ago(value) -> str:
    d = parse_dt(value)
    if not d:
        return ""
    sec = int((now() - d).total_seconds())
    if sec < 0:
        sec = 0
    if sec < 60:
        return f"{sec} sn önce"
    if sec < 3600:
        return f"{sec // 60} dk önce"
    if sec < 86400:
        return f"{sec // 3600} sa önce"
    return f"{sec // 86400} gün önce"


def duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    s = int(seconds)
    if s < 60:
        return f"{s} sn"
    if s < 3600:
        return f"{s // 60} dk {s % 60} sn"
    return f"{s // 3600} sa {(s % 3600) // 60} dk"


def ms(value: float | None) -> str:
    return "" if value is None else f"{value:.0f} ms"


def esc(value) -> str:
    return html.escape("" if value is None else str(value))
