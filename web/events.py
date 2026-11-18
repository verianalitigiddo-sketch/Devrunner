"""
Canlı güncelleme: scripts.bus olayları -> Server-Sent Events (PLAN_WEB.md 3.3).

    hub = EventHub()
    BusRelay(hub)                        # ana iş parçacığında: bus sinyallerini hub'a aktarır
    hub.publish("terminal_output", {...})  # herhangi bir iş parçacığından

Tarayıcıda tek bir EventSource vardır (static/js/app.js); her olay body üzerinde aynı adlı DOM olayı
olarak yayınlanır, htmx parçaları hx-trigger="health_updated from:body" ile kendini yeniler.

Olay verisine secret YAZILMAZ; metin alanları config.redact'ten geçer.
"""
from __future__ import annotations

import json
import queue
import threading

from PySide6.QtCore import QObject

from scripts.config import redact

HEARTBEAT_SEC = 15
QUEUE_SIZE = 500

# bus sinyali -> olay adı (sinyal adıyla aynı)
BUS_EVENTS = ("catalog_changed", "settings_changed", "freeze_changed", "health_changed", "health_updated",
              "presence_changed", "db_state_changed", "data_changed", "notify")


class EventHub:
    def __init__(self):
        self._lock = threading.Lock()
        self._subs: set[queue.Queue] = set()
        self.closed = threading.Event()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=QUEUE_SIZE)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def publish(self, name: str, data=None) -> None:
        payload = (name, data)
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(payload)
            except queue.Full:      # yavaş istemci: en eskisini at
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except (queue.Empty, queue.Full):
                    pass

    def stream(self, q: queue.Queue):
        """SSE üreteci. İstemci kopunca (yazma hatası) GeneratorExit ile biter ve abonelik silinir."""
        try:
            yield "retry: 3000\n\n"
            while not self.closed.is_set():
                try:
                    name, data = q.get(timeout=HEARTBEAT_SEC)
                except queue.Empty:
                    yield ": ping\n\n"
                    continue
                body = json.dumps(data if data is not None else {}, ensure_ascii=False, default=str)
                yield f"event: {name}\ndata: {body}\n\n"
        finally:
            self.unsubscribe(q)

    def close(self) -> None:
        self.closed.set()


class BusRelay(QObject):
    """Ana iş parçacığında yaşar; bus sinyallerini ve sohbet mesajlarını hub'a yayınlar."""

    def __init__(self, hub: EventHub, parent=None):
        super().__init__(parent)
        from scripts.bus import bus
        self.hub = hub
        self._chat = None
        for name in BUS_EVENTS:
            getattr(bus, name).connect(lambda *args, n=name: self._emit(n, args))
        bus.presence_changed.connect(self._hook_chat)

    def _emit(self, name: str, args: tuple) -> None:
        if name == "health_changed":
            data = {"app": args[0], "env": args[1], "state": args[2]}
        elif name == "notify":
            data = {"title": redact(args[0]), "message": redact(args[1]), "level": args[2]}
        elif name in ("settings_changed", "data_changed"):
            data = {"what": args[0]}
        elif name == "db_state_changed":
            data = {"ok": bool(args[0])}
        else:
            data = {}
        self.hub.publish(name, data)
        if name == "presence_changed":
            self._hook_chat()

    def _hook_chat(self) -> None:
        from scripts import relations
        chat = relations.chat_service()
        if chat is not None and chat is not self._chat:
            chat.message.connect(self._chat_message)
            chat.key_warning.connect(lambda uid, name: self.hub.publish(
                "notify", {"title": "Sohbet: anahtar değişti",
                           "message": f"{name} kişisinin şifreleme anahtarı değişti. Parmak izini teyit edin.",
                           "level": "warning"}))
            self._chat = chat

    def _chat_message(self, msg: dict) -> None:
        # Metnin kendisi yayınlanmaz: sayfa mesaj listesini sunucudan (kaçışlanmış) yeniden ister.
        self.hub.publish("chat_message", {"room": msg.get("room"), "mine": bool(msg.get("mine")),
                                          "name": msg.get("name", ""), "preview": msg.get("text", "")[:120]})
