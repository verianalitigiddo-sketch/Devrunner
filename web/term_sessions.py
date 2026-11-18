"""
Terminal oturumları (webterminal/kube/sessions.py'nin iş parçacıklı karşılığı) — PLAN_V2.md Bölüm 5.

Oturum = yerel PTY kabuğu veya pod içinde kabuk. Tarayıcı bağlantısından bağımsızdır: sayfa değişse /
yenilense oturum açık kalır, yeniden bağlanınca son çıktı (geri oynatma arabelleği) gönderilir. Bağlı
sekmesi kalmayan oturum TERMINAL_DETACHED_MIN dakika sonra kapatılır; uygulama kapanınca hepsi sonlandırılır.

Oturum kimlikleri tahmin edilemez; oturumlar yalnızca bu yerel uygulamaya aittir. Tuş vuruşları ve çıktı
diske / veritabanına YAZILMAZ; pod kabuğu açılış ve kapanışı değişiklik geçmişine yazılır.
"""
from __future__ import annotations

import logging
import queue
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field

log = logging.getLogger("web.term")

SCROLLBACK_CHARS = 200_000
CLIENT_QUEUE = 4096


class Scrollback:
    def __init__(self, limit: int = SCROLLBACK_CHARS):
        self.limit = limit
        self._chunks: deque[str] = deque()
        self._size = 0
        self._lock = threading.Lock()

    def append(self, text: str) -> None:
        with self._lock:
            self._chunks.append(text)
            self._size += len(text)
            while self._size > self.limit and len(self._chunks) > 1:
                self._size -= len(self._chunks.popleft())
            if self._size > self.limit:
                only = self._chunks.pop()[-self.limit:]
                self._chunks.append(only)
                self._size = len(only)

    def dump(self) -> str:
        with self._lock:
            return "".join(self._chunks)


@dataclass
class Session:
    id: str
    kind: str                     # local | pod
    title: str
    detail: str                   # "Bash 5.2 · /usr/bin/bash" veya "Prod · pod / container"
    meta: dict = field(default_factory=dict)
    backend: object = None
    scrollback: Scrollback = field(default_factory=Scrollback)
    clients: dict = field(default_factory=dict)       # client_id -> queue.Queue
    created: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    closed: bool = False
    exit_code: int | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    # ---- backend geri çağırmaları (okuyucu iş parçacığından)
    def on_output(self, text: str) -> None:
        self.scrollback.append(text)
        self._broadcast(("out", text))

    def on_exit(self, code) -> None:
        self.closed, self.exit_code = True, code
        self._broadcast(("exit", code))
        hook = self.meta.get("on_close")
        if hook:
            try:
                hook(self)
            except Exception:   # noqa: BLE001
                log.warning("Oturum kapanış kancası hata verdi", exc_info=True)

    def _broadcast(self, item) -> None:
        with self.lock:
            targets = list(self.clients.values())
        for q in targets:
            try:
                q.put_nowait(item)
            except queue.Full:        # çok yavaş istemci: en eskisini at
                try:
                    q.get_nowait()
                    q.put_nowait(item)
                except (queue.Empty, queue.Full):
                    pass

    # ---- istemciler
    def attach(self) -> tuple[str, queue.Queue, str]:
        cid = secrets.token_hex(6)
        q: queue.Queue = queue.Queue(maxsize=CLIENT_QUEUE)
        with self.lock:
            replay = self.scrollback.dump()
            self.clients[cid] = q
            self.last_seen = time.time()
        return cid, q, replay

    def detach(self, cid: str) -> None:
        with self.lock:
            self.clients.pop(cid, None)
            self.last_seen = time.time()

    def write(self, data: str) -> None:
        if not self.closed:
            self.backend.write(data)

    def resize(self, cols: int, rows: int) -> None:
        if not self.closed:
            self.backend.resize(cols, rows)

    def terminate(self) -> None:
        if not self.closed:
            self.backend.terminate()

    def info(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title, "detail": self.detail, "closed": self.closed,
                "exit_code": self.exit_code, "clients": len(self.clients), "created": self.created,
                "env": self.meta.get("env", "")}


class SessionLimitError(Exception):
    pass


class SessionManager:
    def __init__(self, max_sessions=None, detached_min=None):
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._max = max_sessions
        self._detached = detached_min
        self._stop = threading.Event()
        threading.Thread(target=self._reaper, name="terminal-reaper", daemon=True).start()

    def _limits(self) -> tuple[int, int]:
        from web.web_config import WENV
        return (self._max or WENV.TERMINAL_MAX_SESSIONS), (self._detached or WENV.TERMINAL_DETACHED_MIN)

    def _reaper(self) -> None:
        while not self._stop.wait(30):
            self.reap()

    def reap(self, now: float | None = None) -> int:
        """Bağlısı kalmayan ve süresi dolan, ya da kapanmış ve kimsenin izlemediği oturumları kaldırır."""
        now = now or time.time()
        _, detached_min = self._limits()
        removed = 0
        with self._lock:
            items = list(self._sessions.items())
        for sid, s in items:
            idle = not s.clients and now - s.last_seen > detached_min * 60
            gone = s.closed and not s.clients and now - s.last_seen > 120
            if idle or gone:
                s.terminate()
                with self._lock:
                    self._sessions.pop(sid, None)
                removed += 1
                log.info("Terminal oturumu kapatıldı (%s): %s", "bağlantısız" if idle else "bitti", s.title)
        return removed

    def list(self) -> list[Session]:
        with self._lock:
            return sorted(self._sessions.values(), key=lambda s: s.created)

    def get(self, sid: str) -> Session | None:
        with self._lock:
            return self._sessions.get(sid)

    def create(self, kind: str, title: str, detail: str, factory, meta: dict | None = None) -> Session:
        """factory(on_output, on_exit) -> başlatılmış backend (LocalPty / PodShell)."""
        max_sessions, _ = self._limits()
        with self._lock:
            open_count = sum(1 for s in self._sessions.values() if not s.closed)
        if open_count >= max_sessions:
            raise SessionLimitError(f"En çok {max_sessions} terminal açık olabilir; birini kapatın.")
        s = Session(id=secrets.token_urlsafe(18), kind=kind, title=title, detail=detail, meta=meta or {})
        s.backend = factory(s.on_output, s.on_exit)
        with self._lock:
            self._sessions[s.id] = s
        return s

    def close(self, sid: str) -> bool:
        with self._lock:
            s = self._sessions.pop(sid, None)
        if not s:
            return False
        s.terminate()
        s._broadcast(("exit", s.exit_code))
        return True

    def shutdown(self) -> None:
        self._stop.set()
        for s in self.list():
            s.terminate()
        with self._lock:
            self._sessions.clear()
