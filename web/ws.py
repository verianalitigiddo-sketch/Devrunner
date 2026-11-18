"""
WebSocket altyapısı (flask-sock). Sayfa modülleri rotalarını kendi blueprint'lerine ekler:

    from web.ws import sock
    @sock.route("/ws/<sid>", bp=bp)
    def terminal_ws(ws, sid): ...

El sıkışma normal bir Flask isteğidir: web/security.py çerezi ve Origin'i (zorunlu) denetler.
"""
from flask_sock import Sock

sock = Sock()

SERVER_OPTIONS = {"ping_interval": 25, "max_message_size": 1024 * 1024}
