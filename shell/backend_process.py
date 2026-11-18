"""
Backend alt süreci (python -m web.server): başlat, READY bekle, kapat.

* Oturum belirteci ortam değişkeniyle değil stdin'in ilk satırıyla verilir (süreç listesinde görünmesin).
* stdin açık tutulur: kabuk ölürse stdin kapanır ve backend kendini kapatır (web/server.py).
* Kapanış: stdin kapatılır (backend relations.stop_services ile düzgün kapanır), 6 sn beklenir, gerekirse sonlandırılır.
Bu modül backend kodunu import ETMEZ.
"""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IS_WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000


def log_file() -> Path:
    profile = os.environ.get("DEVRUNNER_PROFILE", "")
    base = ROOT / ".local" / profile if profile else ROOT / ".local"
    return base / "logs" / "devrunner.log"


def log_tail(lines: int = 25) -> str:
    try:
        return "\n".join(log_file().read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return "(log dosyası yok)"


class Backend:
    def __init__(self):
        self.token = secrets.token_urlsafe(32)
        self.proc: subprocess.Popen | None = None
        self.port: int | None = None
        self.ready = threading.Event()
        self.failed = ""

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self.ready.clear()
        self.port, self.failed = None, ""
        python = sys.executable
        if IS_WINDOWS and python.lower().endswith("pythonw.exe"):
            candidate = Path(python).with_name("python.exe")   # konsol penceresi CREATE_NO_WINDOW ile açılmaz
            python = str(candidate) if candidate.exists() else python
        kwargs = {"cwd": str(ROOT), "stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL,
                  "env": {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}}
        if IS_WINDOWS:
            kwargs["creationflags"] = CREATE_NO_WINDOW
        self.proc = subprocess.Popen([python, "-m", "web.server"], **kwargs)
        self.proc.stdin.write((self.token + "\n").encode())
        self.proc.stdin.flush()
        threading.Thread(target=self._read, name="backend-stdout", daemon=True).start()

    def _read(self) -> None:
        proc = self.proc
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("READY ") and not self.ready.is_set():
                try:
                    self.port = int(line.split()[1])
                except (IndexError, ValueError):
                    self.failed = f"Beklenmeyen yanıt: {line}"
                self.ready.set()
            elif line.startswith("ERROR port_in_use"):
                port = line.split()[-1]
                self.failed = (f"{port} portu kullanılıyor. Başka bir Devrunner açık olabilir; değilse .env içinde "
                               f"DEVRUNNER_PORT ile başka bir port verin (Ayarlar → Web arayüzü).")
            elif line.startswith("ERROR"):
                self.failed = line
        if not self.ready.is_set():
            self.failed = self.failed or f"Backend süreci kapandı (çıkış kodu {proc.wait()})."
            self.ready.set()

    def wait_ready(self, timeout: float = 30) -> bool:
        if not self.ready.wait(timeout):
            self.failed = f"Backend {timeout:.0f} sn içinde hazır olmadı."
            return False
        return self.port is not None and not self.failed

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self, timeout: float = 6) -> None:
        proc = self.proc
        if proc is None:
            return
        try:
            proc.stdin.close()                      # backend kendini düzgün kapatır
        except OSError:
            pass
        try:
            proc.wait(timeout)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(3)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.proc = None
