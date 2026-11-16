"""
Erişilebilirlik: uygulamaların Dev/Test/Prod adreslerine düzenli HTTP GET.
(ICMP ping değil: asıl soru uygulamanın yanıt verip vermediği.)

Durumlar
    up       Çalışıyor   2xx/3xx/4xx (sunucu yanıt veriyor), eşiğin altında
    slow     Yavaş       yanıt var ama HEALTH_SLOW_MS üstünde
    down     Kapalı      5xx, zaman aşımı, bağlantı reddi; ya da daha önce yanıt vermiş adres artık çözülmüyor
    missing  Yok         DNS çözülmüyor ve hiç yanıt alınmamış: o ortama aktarılmamış sayılır (bildirim yok)
    unknown  Henüz yoklanmadı

Durum iki ardışık yoklamada aynı çıkınca değişmiş sayılır (tek seferlik takılma bildirim üretmez).
Süre geçmişi bellekte tutulur (son 1 saat); veritabanına yalnızca durum değişimleri yazılır.

Bu dosya arayüz import etmez. Periyodik çalıştırma relations.HealthService'tedir.
"""
from __future__ import annotations

import hashlib
import logging
import random
import socket
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests
import urllib3

from scripts.apps import ENVS, AppRef
from scripts.config import ENV
from scripts.fmt import now

log = logging.getLogger(__name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

STATE_TITLES = {"up": "Çalışıyor", "slow": "Yavaş", "down": "Kapalı", "missing": "Yok", "unknown": "Bekleniyor"}
HISTORY_SEC = 3600


@dataclass
class ProbeResult:
    url: str
    dns_ok: bool
    status_code: int | None = None
    ms: float | None = None
    error: str = ""


@dataclass
class HealthState:
    app: str
    env: str
    url: str
    state: str = "unknown"
    status_code: int | None = None
    ms: float | None = None
    error: str = ""
    checked_at: str = ""
    changed_at: str = ""
    ever_up: bool = False
    history: deque = field(default_factory=lambda: deque(maxlen=400))   # (epoch, ms | None)
    _pending: str = ""

    @property
    def title(self) -> str:
        return STATE_TITLES.get(self.state, self.state)


@dataclass
class Change:
    app: str
    env: str
    old: str
    new: str
    state: HealthState


def classify(result: ProbeResult, ever_up: bool, slow_ms: int) -> str:
    if not result.dns_ok:
        return "down" if ever_up else "missing"
    if result.status_code is None:
        return "down"
    if result.status_code >= 500:
        return "down"
    if result.ms is not None and result.ms > slow_ms:
        return "slow"
    return "up"


# ---------------------------------------------------------------------------
# Yoklama
# ---------------------------------------------------------------------------
def _session(use_proxy: bool) -> requests.Session:
    s = requests.Session()
    s.trust_env = use_proxy
    s.verify = False   # yalnızca erişilebilirlik ölçülüyor, veri gönderilmiyor
    s.headers["User-Agent"] = "Devrunner-health"
    return s


def probe(url: str, timeout: float, session: requests.Session | None = None) -> ProbeResult:
    host = urlparse(url).hostname or ""
    try:
        socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return ProbeResult(url, dns_ok=False, error="DNS çözülemedi")
    s = session or _session(False)
    start = time.perf_counter()
    try:
        # stream=True: başlık gelince döner, gövde indirilmez
        with s.get(url, timeout=timeout, stream=True, allow_redirects=False) as r:
            ms = (time.perf_counter() - start) * 1000
            return ProbeResult(url, True, r.status_code, ms)
    except requests.exceptions.Timeout:
        return ProbeResult(url, True, None, None, f"Zaman aşımı ({timeout:.0f} sn)")
    except requests.exceptions.ConnectionError as e:
        reason = "Bağlantı reddedildi" if "refused" in str(e).lower() else "Bağlantı kurulamadı"
        return ProbeResult(url, True, None, None, reason)
    except requests.RequestException as e:
        return ProbeResult(url, True, None, None, type(e).__name__)


def demo_probe(url: str) -> ProbeResult:
    """Örnek katalog için gerçekçi ama uydurma sonuçlar (gerçek adreslere gidilmez)."""
    h = int(hashlib.md5(url.encode()).hexdigest()[:8], 16)
    host = urlparse(url).hostname or ""
    if host.startswith("subeanaliz.") or (host.startswith("veriKalite".lower()) and ".pk8s." in host):
        return ProbeResult(url, False, error="DNS çözülemedi")
    if host.startswith("krediskor.") and ".dk8s." in host:
        return ProbeResult(url, True, 503, 40 + random.random() * 20)
    if host.startswith("raporportal.") and ".tk8s." in host:
        return ProbeResult(url, True, 200, 3300 + random.random() * 900)
    base = 60 + h % 400
    return ProbeResult(url, True, 200 if h % 5 else 302, base + random.random() * base * 0.4)


# ---------------------------------------------------------------------------
# İzleyici (durum makinesi; Qt'siz, test edilebilir)
# ---------------------------------------------------------------------------
class HealthMonitor:
    def __init__(self):
        self._lock = threading.Lock()
        self._states: dict[tuple[str, str], HealthState] = {}

    def states(self) -> list[HealthState]:
        with self._lock:
            return list(self._states.values())

    def get(self, app: str, env: str) -> HealthState | None:
        with self._lock:
            return self._states.get((app, env))

    def for_app(self, app: str) -> dict[str, HealthState]:
        with self._lock:
            return {env: s for (a, env), s in self._states.items() if a == app}

    def prune(self, keep: set[tuple[str, str]]) -> None:
        with self._lock:
            for k in list(self._states):
                if k not in keep:
                    del self._states[k]

    def update(self, app: str, env: str, result: ProbeResult, slow_ms: int) -> Change | None:
        with self._lock:
            st = self._states.get((app, env))
            if st is None or st.url != result.url:
                st = HealthState(app, env, result.url)
                self._states[(app, env)] = st
            new = classify(result, st.ever_up, slow_ms)
            st.status_code, st.ms, st.error = result.status_code, result.ms, result.error
            st.checked_at = now().isoformat(timespec="seconds")
            st.history.append((time.time(), result.ms if new in ("up", "slow") else None))
            cutoff = time.time() - HISTORY_SEC
            while st.history and st.history[0][0] < cutoff:
                st.history.popleft()
            if new in ("up", "slow"):
                st.ever_up = True

            if st.state == "unknown":          # ilk gözlem: hemen kabul, bildirim yok
                st.state, st.changed_at, st._pending = new, st.checked_at, ""
                return None
            if new == st.state:
                st._pending = ""
                return None
            if st._pending != new:              # ilk farklı sonuç: bir tur daha bekle
                st._pending = new
                return None
            old = st.state
            st.state, st.changed_at, st._pending = new, st.checked_at, ""
            return Change(app, env, old, new, st)


def run_round(monitor: HealthMonitor, items: list[AppRef], demo: bool) -> list[Change]:
    """Tüm uygulama × ortam adreslerini eşzamanlı yoklar, değişimleri döner."""
    timeout, slow = ENV.HEALTH_TIMEOUT_SEC, ENV.HEALTH_SLOW_MS
    targets = [(a.name, env, a.url(env)) for a in items for env in ENVS]
    monitor.prune({(a, e) for a, e, _ in targets})
    if not targets:
        return []
    session = _session(ENV.HEALTH_USE_PROXY)
    adapter = requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=32)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    def one(t):
        app, env, url = t
        return t, (demo_probe(url) if demo else probe(url, timeout, session))

    changes = []
    with ThreadPoolExecutor(max_workers=min(24, len(targets))) as ex:
        for (app, env, url), result in ex.map(one, targets):
            change = monitor.update(app, env, result, slow)
            if change:
                changes.append(change)
    session.close()
    return changes


def record_changes(changes: list[Change], observer: str) -> None:
    """Değişimleri dr_health_events'e yazar. Son kayıt aynı durumdaysa yazmaz (birden çok istemci aynı
    değişimi görür)."""
    from scripts import database
    if not changes or not database.is_ready():
        return
    with database.transaction() as conn:
        for c in changes:
            last = database.rows(conn, """SELECT state FROM dr_health_events WHERE app_name = :a AND env = :e
                                          ORDER BY observed_at DESC, id DESC LIMIT 1""", {"a": c.app, "e": c.env})
            if last and last[0]["state"] == c.new:
                continue
            conn.execute(database.text(
                """INSERT INTO dr_health_events (app_name, env, state, status_code, response_ms, detail, observed_by)
                   VALUES (:a, :e, :s, :code, :ms, :d, :by)"""),
                {"a": c.app, "e": c.env, "s": c.new, "code": c.state.status_code,
                 "ms": None if c.state.ms is None else int(c.state.ms), "d": c.state.error or None, "by": observer})


def events(app: str | None = None, limit: int = 100) -> list[dict]:
    from scripts import database
    sql = "SELECT app_name, env, state, status_code, response_ms, detail, observed_by, observed_at FROM dr_health_events"
    params: dict = {"limit": limit}
    if app:
        sql += " WHERE app_name = :a"
        params["a"] = app
    return database.read_sql(sql + " ORDER BY observed_at DESC, id DESC LIMIT :limit", params)
