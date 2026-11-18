"""
Backend süreci: QCoreApplication (servisler: yoklama, sohbet…) + Flask (ayrı iş parçacığı).

Kabuk (shell/) bu modülü alt süreç olarak başlatır:
    python -m web.server                 belirteç stdin'in ilk satırından okunur; stdout'a "READY <port>" yazılır
    python -m web.server --dev           belirteç üretilir, adres konsola yazılır (normal tarayıcıda açılabilir)
    python -m web.server --dev --port 8765

stdin kapanırsa (kabuk öldü) süreç kendini kapatır. Bu süreç PyQt6 import ETMEZ (yalnızca PySide6 QtCore).
"""
from __future__ import annotations

import argparse
import logging
import secrets
import sys
import threading

from PySide6.QtCore import QCoreApplication, QObject

from scripts import logsetup, relations
from scripts import user as user_mod
from scripts.config import PROFILE

log = logging.getLogger("web.server")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="web.server")
    parser.add_argument("--dev", action="store_true", help="geliştirme: belirteci üret ve adresi yazdır")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)

    logsetup.setup_logging()
    logsetup.install_excepthook()
    logging.getLogger("werkzeug").setLevel(logging.ERROR)   # erişim log'u /auth?t=... içerir: kapalı

    if args.dev:
        token = secrets.token_urlsafe(32)
    else:
        token = sys.stdin.readline().strip()
        if len(token) < 20:
            print("ERROR geçersiz oturum belirteci", flush=True)
            return 2

    qapp = QCoreApplication(sys.argv[:1])
    holder = QObject()
    user_mod.ensure_identity()

    from werkzeug.serving import make_server

    from web.app import create_app
    from web.events import BusRelay, EventHub
    from web.qt_bridge import QtBridge
    from web.terminal_runner import TerminalRunner

    hub = EventHub()
    bridge = QtBridge()
    relay = BusRelay(hub, holder)   # noqa: F841 - ana iş parçacığında yaşamalı
    runner = TerminalRunner(hub)

    def request_quit():
        bridge.post(qapp.quit)

    app = create_app(bridge, hub, token, runner=runner, on_shutdown=request_quit)
    srv = make_server("127.0.0.1", args.port, app, threaded=True)
    port = srv.server_port
    app.config["DR_PORT"] = port
    threading.Thread(target=srv.serve_forever, name="flask", daemon=True).start()

    relations.start_services(holder)
    if args.dev:
        print(f"Devrunner web (geliştirme, profil: {PROFILE or 'varsayılan'})\n"
              f"  http://127.0.0.1:{port}/auth?t={token}", flush=True)
    else:
        print(f"READY {port}", flush=True)

        def watch_stdin():
            try:
                while sys.stdin.readline():
                    pass
            except (OSError, ValueError):
                pass
            log.info("Kabuk bağlantısı koptu; kapanıyor.")
            request_quit()

        threading.Thread(target=watch_stdin, name="stdin-izleyici", daemon=True).start()

    log.info("Backend %s portunda hazır (profil: %s)", port, PROFILE or "varsayılan")
    try:
        code = qapp.exec()
    except KeyboardInterrupt:
        code = 0
    log.info("Backend kapanıyor")
    hub.close()
    runner.shutdown()
    relations.stop_services()
    srv.shutdown()
    return code


if __name__ == "__main__":
    sys.exit(main())
