"""
WebSocket altyapısı (flask-sock). Sayfa modülleri rotalarını kendi blueprint'lerine ekler:

    from web.ws import sock
    @sock.route("/ws/<sid>", bp=bp)
    def terminal_ws(ws, sid): ...

El sıkışma normal bir Flask isteğidir: web/security.py çerezi ve Origin'i (zorunlu) denetler.

Kapanış düzeni: flask-sock werkzeug sunucusunda işleyici dönünce aynı bağlantıya boş bir HTTP yanıtı
yazar. İstemci kapattığında simple-websocket iş parçacığı kapanış çerçevesini GÖNDERMEDEN işleyiciyi
uyandırdığı için o HTTP baytları kapanış çerçevesinin önüne geçebiliyor; sonra gelse bile Chrome onları
çerçeve sanıyor ("Invalid frame header"). Bu yüzden her işleyiciden sonra kapanış el sıkışması
tamamlanana dek beklenir ve TCP bağlantısı kapatılır (werkzeug'un yazması bağlantı düşmesi sayılır).
"""
from __future__ import annotations

import functools
import socket
import threading

from flask_sock import Sock

SERVER_OPTIONS = {"ping_interval": 25, "max_message_size": 1024 * 1024}


def finish(ws, timeout: float = 2.0) -> None:
    """Kapanış çerçevesi gönderilsin / karşılık gelsin ve soket iş parçacığı bitsin."""
    try:
        if ws.connected:
            ws.close()
    except Exception:   # noqa: BLE001 - zaten kapanmış
        pass
    t = getattr(ws, "thread", None)
    if t is not None and t is not threading.current_thread():
        t.join(timeout)
    # TCP'yi biz kapatırız (RFC 6455: önce sunucu). Ardından werkzeug'un yazmak istediği HTTP yanıtı
    # "bağlantı düştü" olarak yutulur ve tarayıcıya bozuk çerçeve gitmez.
    try:
        ws.sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


class _Sock(Sock):
    def route(self, path, bp=None, **kwargs):
        register = super().route(path, bp=bp, **kwargs)

        def decorator(f):
            @functools.wraps(f)
            def handler(ws, *args, **kw):
                try:
                    return f(ws, *args, **kw)
                finally:
                    finish(ws)
            return register(handler)
        return decorator


sock = _Sock()
