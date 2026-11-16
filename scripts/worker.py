"""
Arka plan işleri. Ağ ve veritabanı çağrıları arayüz iş parçacığında YAPILMAZ (pencere donar).

    run_async(DevopsAPI.fetch_overview, on_done=self._show, on_error=self._show_error, owner=self)

on_done / on_error arayüz iş parçacığında çağrılır. owner verilirse ve iş bitmeden silinmişse
geri çağırma atlanır. on_error verilmezse hata log'a yazılır.

    poller = Poller(fn, interval_ms=60_000, on_done=..., parent=self)
    poller.start()

Poller bir önceki tur bitmeden yenisini başlatmaz.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

import shiboken6
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal

log = logging.getLogger(__name__)

_pool = QThreadPool.globalInstance()
_pool.setMaxThreadCount(max(_pool.maxThreadCount(), 12))
_alive: dict[int, QObject] = {}


class _Relay(QObject):
    done = Signal(object)
    failed = Signal(object)


class _Task(QRunnable):
    def __init__(self, fn, args, kwargs, relay: _Relay):
        super().__init__()
        self.setAutoDelete(True)
        self.fn, self.args, self.kwargs, self.relay = fn, args, kwargs, relay

    def run(self) -> None:
        # relay'e bu iş parçacığında referans bırakma: QObject yalnızca arayüz iş parçacığında silinmeli
        relay, self.relay = self.relay, None
        try:
            result = self.fn(*self.args, **self.kwargs)
        except BaseException as e:   # noqa: BLE001 - her hata arayüze taşınır
            relay.failed.emit(e)
        else:
            relay.done.emit(result)


def _default_error(e: BaseException) -> None:
    log.error("Arka plan işi hata verdi: %s", e, exc_info=e)


def run_async(fn: Callable, *args, on_done: Callable[[Any], None] | None = None,
              on_error: Callable[[BaseException], None] | None = None,
              owner: QObject | None = None, **kwargs) -> None:
    relay = _Relay()
    key = id(relay)
    _alive[key] = relay
    # Python referansı, C++ nesnesi arayüz iş parçacığında deleteLater ile silinene kadar tutulur
    relay.destroyed.connect(lambda *_: _alive.pop(key, None))

    def alive() -> bool:
        return owner is None or shiboken6.isValid(owner)

    def finish() -> None:
        relay.deleteLater()

    def done(result):
        try:
            if on_done and alive():
                on_done(result)
        finally:
            finish()

    def failed(e):
        try:
            if alive():
                (on_error or _default_error)(e)
        finally:
            finish()

    relay.done.connect(done, Qt.QueuedConnection)
    relay.failed.connect(failed, Qt.QueuedConnection)
    _pool.start(_Task(fn, args, kwargs, relay))


class Poller(QObject):
    """fn'i interval_ms'de bir arka planda çalıştırır; önceki tur bitmeden yenisini başlatmaz."""

    def __init__(self, fn: Callable, interval_ms: int, on_done=None, on_error=None, parent=None):
        super().__init__(parent)
        self.fn, self.on_done, self.on_error = fn, on_done, on_error
        self.busy = False
        self._timer = QTimer(self)
        self._timer.setInterval(max(1000, interval_ms))
        self._timer.timeout.connect(self.trigger)

    def start(self, immediate: bool = True) -> None:
        self._timer.start()
        if immediate:
            self.trigger()

    def stop(self) -> None:
        self._timer.stop()

    def is_active(self) -> bool:
        return self._timer.isActive()

    def set_interval(self, ms: int) -> None:
        self._timer.setInterval(max(1000, ms))

    def trigger(self) -> None:
        if self.busy:
            return
        self.busy = True

        def done(result):
            self.busy = False
            if self.on_done:
                self.on_done(result)

        def failed(e):
            self.busy = False
            (self.on_error or _default_error)(e)

        run_async(self.fn, on_done=done, on_error=failed, owner=self)
