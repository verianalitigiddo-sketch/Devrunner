"""
Arka plan yoklayıcıları (PLAN_V2.md Bölüm 6.2). Backend sürecinde çalışır; arayüz iş parçacığına dokunmaz.

    p = Poller("aura", lambda: WENV.AURA_SYNC_MIN * 60, aura_service.sync_quiet, first_delay=20)
    p.start() ... p.stop()

Aralık her turda yeniden okunur (ayar değişince uygulamayı yeniden başlatmak gerekmez); 0 veya
kapalı (enabled() False) ise tur atlanır.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

log = logging.getLogger("web.services")


class Poller:
    def __init__(self, name: str, interval: Callable[[], float], fn: Callable[[], None],
                 enabled: Callable[[], bool] = lambda: True, first_delay: float = 30):
        self.name, self.interval, self.fn, self.enabled = name, interval, fn, enabled
        self.first_delay = first_delay
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"poller-{self.name}", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        wait = self.first_delay
        while not self._stop.wait(wait):
            try:
                if self.enabled() and self.interval() > 0:
                    self.fn()
            except Exception:   # noqa: BLE001 - bir tur düşerse sonraki tur denenir
                log.warning("%s yoklaması hata verdi", self.name, exc_info=True)
            wait = max(60.0, float(self.interval() or 0) or 600.0)

    def stop(self) -> None:
        self._stop.set()


_pollers: list[Poller] = []


def start_all(hub) -> None:
    """server.py çağırır. Testlerde çağrılmaz."""
    from web import aura_service
    from web.web_config import WENV
    aura_service.init(hub)
    p = Poller("aura", lambda: WENV.AURA_SYNC_MIN * 60, aura_service.sync_quiet,
               enabled=lambda: WENV.AURA_ENABLED, first_delay=20)
    p.start()
    _pollers.append(p)


def stop_all() -> None:
    for p in _pollers:
        p.stop()
    _pollers.clear()
