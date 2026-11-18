"""
Aura servis katmanı (PLAN_V2.md Bölüm 6): depo seçimi, önbellek, eşitleme, rozetler, bildirimler.

Depo:  DevOps demo modunda  -> MemoryStore + örnek olgular (asla veritabanına yazılmaz)
       veritabanı hazırsa   -> DbStore (ortak; herkes aynı sıralamayı görür)
       değilse              -> MemoryStore (yalnızca bu oturum; eşitleme DevOps'tan doldurur)

Hesap pahalı değildir (birkaç bin olgu < 0,1 sn) ama başlık ve sohbet her istekte rozet ister: sonuçlar
kısa süre (TTL) önbelleklenir, eşitleme / profil değişikliği önbelleği boşaltır.
"""
from __future__ import annotations

import difflib
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from scripts import AuraAPI, DevopsAPI, database, relations
from scripts.user import current_user

log = logging.getLogger("web.aura")
TTL = 60
PERIODS = {"all": "Tüm zamanlar", "30d": "Son 30 gün"}

_lock = threading.RLock()
_hub = None
_store = None
_store_mode = ""
_cache: dict = {}


@dataclass
class SyncState:
    running: bool = False
    done: int = 0
    total: int = 0
    scope: str = ""
    started: float = 0.0
    result: AuraAPI.SyncResult | None = None
    error: str = ""
    finished_at: datetime | None = None
    history: list = field(default_factory=list)


sync_state = SyncState()


def init(hub) -> None:
    global _hub
    _hub = hub


def _publish(name: str, data=None) -> None:
    if _hub is not None:
        try:
            _hub.publish(name, data)
        except Exception:   # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Depo
# ---------------------------------------------------------------------------
def mode() -> str:
    if DevopsAPI.is_demo():
        return "demo"
    return "db" if database.is_ready() else "memory"


MODE_TEXT = {"demo": "Örnek veri: DevOps PAT girilmedi; sıralama bu oturuma özgü örnek olgulardan.",
             "memory": "Veritabanı bağlı değil: olgular yalnızca bu oturumda tutulur (uygulama kapanınca silinir).",
             "db": ""}


def store():
    global _store, _store_mode
    m = mode()
    with _lock:
        if _store is None or m != _store_mode:
            _store_mode = m
            _cache.clear()
            if m == "db":
                _store = AuraAPI.DbStore()
            else:
                _store = AuraAPI.MemoryStore()
                if m == "demo":
                    apps = [a.name for a in relations.catalog()]
                    _store.add(AuraAPI.demo_facts(apps, me=current_user().name))
        return _store


def reset() -> None:
    """Testler ve ayar değişimi için."""
    global _store, _store_mode
    with _lock:
        _store, _store_mode = None, ""
        _cache.clear()


def invalidate() -> None:
    with _lock:
        _cache.clear()


def _cached(key, fn):
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < TTL:
            return hit[1]
    value = fn()
    with _lock:
        _cache[key] = (now, value)
    return value


def facts() -> list[AuraAPI.Fact]:
    s = store()
    return _cached(("facts", id(s)), s.facts)


def profiles() -> dict:
    s = store()
    return _cached(("profiles", id(s)), s.profiles)


def _freezes() -> list:
    try:
        from scripts import FreezeAPI
        return FreezeAPI.cached()
    except Exception:   # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# Sıralama ve kartlar
# ---------------------------------------------------------------------------
def board(period: str = "all", app: str = "") -> list[AuraAPI.PersonCard]:
    period = period if period in PERIODS else "all"

    def compute():
        since = datetime.now(timezone.utc) - timedelta(days=30) if period == "30d" else None
        return AuraAPI.score(facts(), since=since, app=app or None, freezes=_freezes(), profiles=profiles())
    return _cached(("board", period, app), compute)


def _by_key() -> dict[str, AuraAPI.PersonCard]:
    return _cached(("by_key",), lambda: {c.key: c for c in board()})


def card(key: str) -> AuraAPI.PersonCard | None:
    return _by_key().get(key)


def my_key() -> str:
    return AuraAPI.person_key(current_user().name)


def me() -> AuraAPI.PersonCard | None:
    return card(my_key())


def suggestions(limit: int = 3) -> list[str]:
    """Kullanıcının adı DevOps'taki hiçbir adla eşleşmezse en yakın adlar."""
    cards = _by_key()
    keys = difflib.get_close_matches(my_key(), list(cards), n=limit, cutoff=0.5)
    if not keys:   # soyadı ya da adı tutan
        words = set(my_key().split())
        keys = [k for k in cards if words & set(k.split())][:limit]
    return [cards[k].name for k in keys]


def badge_for(name: str) -> dict | None:
    """Başlık ve sohbet için: {'level', 'title', 'label', 'aura', 'tip', 'key'} veya None."""
    if not name or not AuraAPI.is_person(name):
        return None
    c = _by_key().get(AuraAPI.person_key(name))
    if not c or c.hidden:
        return None
    stats = " · ".join(f"{k} {v}" for k, v in c.stats.items())
    return {"key": c.key, "level": c.level, "title": c.title, "label": c.label, "aura": c.aura,
            "negative": c.aura < 0, "tip": f"{c.name}\n{c.aura} aura · Sv {c.level} · {c.title}\n{stats}"}


def ignored_count() -> int:
    return _cached(("ignored",), lambda: AuraAPI.count_ignored(facts()))


def apps() -> list[str]:
    return sorted({f.app for f in facts() if f.app})


# ---------------------------------------------------------------------------
# Profil
# ---------------------------------------------------------------------------
def equip(badge_id: str) -> None:
    c = me()
    if badge_id and (not c or badge_id not in c.earned):
        raise ValueError("Bu etiket henüz kazanılmadı.")
    store().set_profile(my_key(), current_user().name, equipped=badge_id)
    invalidate()
    _publish("aura_changed")


def set_hidden(hidden: bool) -> None:
    store().set_profile(my_key(), current_user().name, hidden=hidden)
    invalidate()
    _publish("aura_changed")


# ---------------------------------------------------------------------------
# Eşitleme
# ---------------------------------------------------------------------------
def last_sync() -> dict | None:
    try:
        return store().last_sync()
    except Exception:   # noqa: BLE001
        return None


def _snapshot() -> tuple[int, set[str]] | None:
    c = me()
    return (c.level, set(c.earned)) if c else None


def _progress(done: int, total: int, scope: str) -> None:
    sync_state.done, sync_state.total, sync_state.scope = done, total, scope
    _publish("aura_sync", {"done": done, "total": total})


def sync(wait: bool = False) -> bool:
    """Eşitlemeyi başlatır (zaten sürüyorsa False). Demo modunda DevOps'a gidilmez."""
    with _lock:
        if sync_state.running:
            return False
        sync_state.running, sync_state.error, sync_state.started = True, "", time.time()
        sync_state.done = sync_state.total = 0
    t = threading.Thread(target=_run_sync, name="aura-sync", daemon=True)
    t.start()
    if wait:
        t.join()
    return True


def sync_quiet() -> None:
    """Poller'dan: eşitle ve bitmesini bekle."""
    if mode() != "demo":
        sync(wait=True)


def _run_sync() -> None:
    before = _snapshot()
    try:
        s = store()
        if mode() == "demo":
            sync_state.result = AuraAPI.SyncResult(seen=s.count())
        else:
            ov = relations.overview()
            pipelines = ov.pipelines if ov else {}
            from web.web_config import WENV
            sync_state.result = AuraAPI.collect(s, DevopsAPI.make_client(), relations.catalog(), pipelines,
                                                WENV.AURA_SINCE_DAYS, by=current_user().name,
                                                progress=_progress, pause=0.1)
            if sync_state.result.errors:
                log.warning("Aura eşitlemesinde %d kapsam okunamadı: %s", len(sync_state.result.errors),
                            "; ".join(sync_state.result.errors[:3]))
    except Exception as e:   # noqa: BLE001
        log.exception("Aura eşitlemesi başarısız")
        from scripts.config import redact
        sync_state.error = redact(f"{type(e).__name__}: {e}")[:300]
    finally:
        sync_state.running = False
        sync_state.finished_at = datetime.now(timezone.utc)
        invalidate()
    _announce(before, _snapshot())
    _publish("aura_changed")


def _announce(before, after) -> None:
    """Seviye atlama / yeni etiket: bildirim balonu."""
    if not after:
        return
    level, earned = after
    if before:
        old_level, old_earned = before
        if level > old_level:
            c = me()
            _publish("notify", {"title": "Aura", "level": "success",
                                "message": f"Seviye atladınız: Sv {level} · {c.title if c else ''}"})
        for bid in sorted(earned - old_earned):
            b = AuraAPI.BADGE_BY_ID.get(bid)
            if b:
                _publish("notify", {"title": "Yeni etiket", "level": "success", "message": f"{b.icon} {b.name} — {b.rule}"})
