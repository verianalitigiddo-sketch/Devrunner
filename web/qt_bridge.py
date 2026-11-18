"""
Flask iş parçacıklarından Qt ana iş parçacığına güvenli çağrı (PLAN_WEB.md 3.2).

    from web.common import qt
    qt.call(relations.save_settings, "devops", values)      # sonucu bekler (en çok 15 sn)
    qt.post(app.quit)                                        # beklemeden sıraya koyar

Neden: backend'deki QObject'ler (bus, Services, ChatService, worker.run_async'in oluşturduğu nesneler)
ana iş parçacığında yaşar. Olay döngüsü olmayan bir Flask iş parçacığında oluşturulan QObject'lerin
sinyalleri hiç teslim edilmez; bu yüzden Qt'ye dokunan her çağrı buradan geçer.

Engelleyen düz backend çağrıları (DevopsAPI, K8sAPI, NotesAPI, …) doğrudan istek iş parçacığında çağrılır.
"""
from __future__ import annotations

import concurrent.futures
import threading

from PySide6.QtCore import QObject, Qt, Signal

TIMEOUT = 15


class BridgeTimeout(TimeoutError):
    pass


class DirectBridge:
    """Testler ve Qt'siz çalışma: çağrıyı olduğu yerde yapar."""

    def call(self, fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def post(self, fn, *args, **kwargs) -> None:
        fn(*args, **kwargs)


class _Relay(QObject):
    run = Signal(object)


class QtBridge:
    """Ana iş parçacığında oluşturulmalıdır."""

    def __init__(self):
        self._main = threading.current_thread()
        self._relay = _Relay()
        self._relay.run.connect(self._exec, Qt.QueuedConnection)

    @staticmethod
    def _exec(job) -> None:
        fn, args, kwargs, fut = job
        if fut is not None and not fut.set_running_or_notify_cancel():
            return
        try:
            result = fn(*args, **kwargs)
        except BaseException as e:   # noqa: BLE001 - çağırana taşınır
            if fut is not None:
                fut.set_exception(e)
            return
        if fut is not None:
            fut.set_result(result)

    def call(self, fn, *args, timeout: float = TIMEOUT, **kwargs):
        if threading.current_thread() is self._main:
            return fn(*args, **kwargs)
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._relay.run.emit((fn, args, kwargs, fut))
        try:
            return fut.result(timeout)
        except concurrent.futures.TimeoutError:
            fut.cancel()
            raise BridgeTimeout("Uygulama çekirdeği yanıt vermedi (zaman aşımı).") from None

    def post(self, fn, *args, **kwargs) -> None:
        self._relay.run.emit((fn, args, kwargs, None))
