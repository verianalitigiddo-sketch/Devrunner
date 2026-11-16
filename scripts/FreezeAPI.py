"""
Freeze dönemleri (dr_freezes). Başlangıç ve bitiş günleri dahildir; karşılaştırma yerel tarihle
(Europe/Istanbul) yapılır.

    status(today, freezes, warn_days)  -> FreezeStatus(active=[...], upcoming=[...])   (saf fonksiyon)
    current_status()                   -> önbellekteki dönemlerle bugünün durumu

Önbellek (refresh) relations.DbService tarafından periyodik yenilenir.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import text

from scripts.config import ENV
from scripts.database import audit, read_sql, rows, transaction
from scripts.fmt import day, today
from scripts.user import current_user


@dataclass(frozen=True)
class Freeze:
    id: int
    start: date
    end: date
    title: str
    created_by: str = ""

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end

    @property
    def label(self) -> str:
        span = day(self.start) if self.start == self.end else f"{day(self.start)} – {day(self.end)}"
        return f"{span}: {self.title}"


@dataclass
class FreezeStatus:
    active: list[Freeze] = field(default_factory=list)
    upcoming: list[Freeze] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        return bool(self.active)

    @property
    def active_until(self) -> date | None:
        return max((f.end for f in self.active), default=None)

    def tooltip(self) -> str:
        if self.active:
            return "Freeze dönemindeyiz:\n" + "\n".join(f.label for f in self.active)
        if self.upcoming:
            return "Yaklaşan freeze:\n" + "\n".join(f.label for f in self.upcoming)
        return ""


def status(on: date, freezes: list[Freeze], warn_days: int) -> FreezeStatus:
    st = FreezeStatus()
    horizon = on + timedelta(days=max(0, warn_days))
    for f in sorted(freezes, key=lambda f: f.start):
        if f.contains(on):
            st.active.append(f)
        elif on < f.start <= horizon:
            st.upcoming.append(f)
    return st


# ---------------------------------------------------------------------------
# Önbellek
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_cache: list[Freeze] = []


def cached() -> list[Freeze]:
    with _lock:
        return list(_cache)


def current_status() -> FreezeStatus:
    return status(today(), cached(), ENV.FREEZE_WARN_DAYS)


def _row(r: dict) -> Freeze:
    return Freeze(r["id"], r["start_date"], r["end_date"], r["title"], r.get("created_by", ""))


def list_freezes(include_past: bool = True) -> list[Freeze]:
    sql = "SELECT id, start_date, end_date, title, created_by FROM dr_freezes WHERE deleted_at IS NULL"
    if not include_past:
        sql += " AND end_date >= CURRENT_DATE - 1"
    return [_row(r) for r in read_sql(sql + " ORDER BY start_date DESC")]


def refresh() -> list[Freeze]:
    items = list_freezes(include_past=False)
    with _lock:
        _cache[:] = items
    return items


def add_freeze(start: date, end: date, title: str) -> int:
    title = title.strip()
    if not title:
        raise ValueError("Açıklama boş olamaz.")
    if end < start:
        raise ValueError("Bitiş tarihi başlangıçtan önce olamaz.")
    with transaction() as conn:
        fid = conn.execute(text("""INSERT INTO dr_freezes (start_date, end_date, title, created_by)
                                   VALUES (:s, :e, :t, :by) RETURNING id"""),
                           {"s": start, "e": end, "t": title[:300], "by": current_user().name}).scalar()
        audit(conn, "freeze", "create", "freeze", fid, {"start": str(start), "end": str(end), "title": title})
    return int(fid)


def delete_freeze(fid: int) -> None:
    with transaction() as conn:
        old = rows(conn, "SELECT start_date, end_date, title FROM dr_freezes WHERE id = :id AND deleted_at IS NULL",
                   {"id": fid})
        if not old:
            return
        conn.execute(text("UPDATE dr_freezes SET deleted_at = now() WHERE id = :id"), {"id": fid})
        audit(conn, "freeze", "delete", "freeze", fid,
              {"start": str(old[0]["start_date"]), "end": str(old[0]["end_date"]), "title": old[0]["title"]})
