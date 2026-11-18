"""
Kabuk girişi (PyQt6). Tek kopya kontrolü, backend alt süreci, açılış / hata ekranı, pencere.

    pythonw Devrunner.pyw [--profile ad] [--debug]

Kural: shell/ yalnızca PyQt6 ve standart kütüphane import eder; backend (scripts/, web/) ile yalnızca
HTTP ve alt süreç üzerinden konuşur (iki Qt bağlamı aynı süreçte çakışır).
"""
from __future__ import annotations

import argparse
import getpass
import logging
import logging.handlers
import os
import sys
import threading

from shell.backend_process import ROOT, Backend, log_file, log_tail

log = logging.getLogger("shell")


def _setup_logging(debug: bool) -> None:
    path = log_file().with_name("shell.log")
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if debug and sys.stderr is not None:
        root.addHandler(logging.StreamHandler())


def _chromium_flags() -> None:
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    if os.environ.get("DEVRUNNER_DISABLE_GPU") == "1":     # VDI / sürücüsüz makineler
        flags += " --disable-gpu"
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = flags.strip()
    if sys.platform != "win32" and hasattr(os, "geteuid") and os.geteuid() == 0:
        os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")   # root altında Chromium sandbox açılmaz


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="Devrunner")
    parser.add_argument("--debug", action="store_true", help="F12 geliştirici araçları, log konsola")
    args, _ = parser.parse_known_args(argv)
    _setup_logging(args.debug)
    _chromium_flags()

    from PyQt6.QtCore import QTimer, pyqtSignal, QObject
    from PyQt6.QtGui import QIcon
    from PyQt6.QtNetwork import QLocalServer, QLocalSocket
    from PyQt6.QtWidgets import QApplication

    from shell.window import APP_NAME, MainWindow

    profile = os.environ.get("DEVRUNNER_PROFILE", "")
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    icon = QIcon(str(ROOT / "assets" / "devrunner.png"))
    app.setWindowIcon(icon)

    # Tek kopya: açıksa mevcut pencereyi öne getir
    key = f"devrunner-web-{getpass.getuser()}-{profile or 'default'}"
    sock = QLocalSocket()
    sock.connectToServer(key)
    if sock.waitForConnected(400):
        sock.write(b"show")
        sock.waitForBytesWritten(400)
        return 0
    QLocalServer.removeServer(key)
    server = QLocalServer(app)
    server.listen(key)

    storage = str((log_file().parent.parent / "webprofile").resolve())
    window = MainWindow(profile, storage, icon, args.debug)
    server.newConnection.connect(lambda: (server.nextPendingConnection(), window.bring_to_front()))
    backend = Backend()

    class Relay(QObject):
        ready = pyqtSignal(bool)

    relay = Relay()

    def start_backend():
        window.show_starting()
        backend.start()
        threading.Thread(target=lambda: relay.ready.emit(backend.wait_ready(30)), daemon=True).start()

    def on_ready(ok: bool):
        if ok:
            log.info("Backend hazır: %s", backend.url)
            window.load(backend.url, backend.token)
        else:
            log.error("Backend başlatılamadı: %s", backend.failed)
            window.show_error("Devrunner çekirdeği başlatılamadı", f"{backend.failed}\n\nSon log satırları:\n{log_tail()}")

    def watchdog():
        if backend.port and not backend.alive() and not window.quitting:
            backend.port = None
            window.show_error("Devrunner çekirdeği beklenmedik biçimde kapandı", log_tail())

    relay.ready.connect(on_ready)
    window.status.retry.connect(lambda: (backend.stop(), start_backend()))
    timer = QTimer()
    timer.timeout.connect(watchdog)
    timer.start(3000)

    window.show()
    start_backend()
    code = app.exec()
    log.info("Kabuk kapanıyor")
    backend.stop()
    from PyQt6 import sip
    sip.delete(window)            # sayfa (pencerenin çocuğu), profilden (uygulamanın çocuğu) önce silinsin
    return code


if __name__ == "__main__":
    sys.exit(main())
