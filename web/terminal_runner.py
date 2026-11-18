"""
Terminal komutlarının çalıştırılması (masaüstünde _pages/terminal.py'deki QProcess'in karşılığı).

Komut satırı, ortam, durdurma, cd izleme ve uyarılar dondurulmuş scripts/TerminalAPI'den gelir.
Aynı anda tek komut çalışır; çıktı bir okuyucu iş parçacığından SSE ile akar:
    terminal_output {"run": n, "text": "..."}     terminal_done {"run": n, "code": 0, "seconds": 1.2, "stopped": false}
Komutlar veritabanına veya log'a yazılmaz (içlerinde secret olabilir).
"""
from __future__ import annotations

import codecs
import os
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from scripts import TerminalAPI

MAX_BUFFER = 400_000   # sayfa yenilenince gösterilecek son çıktı (karakter)


class TerminalRunner:
    def __init__(self, hub):
        self.hub = hub
        self.shells: list[TerminalAPI.Shell] = []
        self.shells_ready = threading.Event()
        self.proc: subprocess.Popen | None = None
        self.run_id = 0
        self.started = 0.0
        self.stopped = False
        self.current: dict = {}
        self._buf: deque[str] = deque()
        self._size = 0
        self._lock = threading.Lock()
        threading.Thread(target=self._detect, name="kabuk-arama", daemon=True).start()

    # ---- kabuklar
    def _detect(self) -> None:
        try:
            self.shells = TerminalAPI.detect_shells()
        finally:
            self.shells_ready.set()

    def shell(self, shell_id: str | None) -> TerminalAPI.Shell | None:
        self.shells_ready.wait(10)
        for s in self.shells:
            if s.id == shell_id:
                return s
        return TerminalAPI.default_shell(self.shells)

    # ---- çıktı arabelleği
    def _append(self, text: str) -> None:
        with self._lock:
            self._buf.append(text)
            self._size += len(text)
            while self._size > MAX_BUFFER and len(self._buf) > 1:
                self._size -= len(self._buf.popleft())
        self.hub.publish("terminal_output", {"run": self.run_id, "text": text})

    def output(self) -> str:
        with self._lock:
            return "".join(self._buf)

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()
            self._size = 0

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # ---- çalıştırma
    def start(self, command: str, shell: TerminalAPI.Shell, cwd: str, kube_env: str = "", header: str = "") -> int:
        if self.running:
            raise RuntimeError("Bir komut zaten çalışıyor; önce durdurun.")
        program, args, native = TerminalAPI.build_command(shell, command)
        kwargs: dict = {"cwd": cwd, "env": TerminalAPI.process_env(kube_env), "stdout": subprocess.PIPE,
                        "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
        if TerminalAPI.IS_WINDOWS:
            kwargs["creationflags"] = TerminalAPI.CREATE_NO_WINDOW
            cmdline = f'"{program}" {native}' if native is not None else subprocess.list2cmdline([program, *args])
            argv: str | list[str] = cmdline
        else:
            # Durdur tüm süreç grubunu sonlandırabilsin: komut yeni bir oturumda çalışmalı. build_command
            # bunu "setsid" ile yapar; burada ayrıca start_new_session verilmez, yoksa setsid çatallanır ve
            # komut kendi ayrı oturumuna kaçar (Durdur onu bulamaz).
            if Path(program).name != "setsid":
                kwargs["start_new_session"] = True
            argv = [program, *args]
        self.run_id += 1
        run = self.run_id
        self.stopped = False
        self._append(header)
        try:
            self.proc = subprocess.Popen(argv, **kwargs)
        except OSError as e:
            self._append(f"[başlatılamadı: {e}]\n")
            self.hub.publish("terminal_done", {"run": run, "code": None, "seconds": 0, "stopped": False})
            self.proc = None
            return run
        self.started = time.monotonic()
        self.current = {"command": command, "shell": shell.title, "kube": kube_env}
        threading.Thread(target=self._read, args=(self.proc, run), name="terminal-okuyucu", daemon=True).start()
        return run

    def _read(self, proc: subprocess.Popen, run: int) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        stream = proc.stdout
        while True:
            chunk = stream.read1(4096) if hasattr(stream, "read1") else stream.read(4096)
            if not chunk:
                break
            text = decoder.decode(chunk).replace("\r\n", "\n")
            if text:
                self._append(text)
        tail = decoder.decode(b"", final=True)
        if tail:
            self._append(tail)
        code = proc.wait()
        took = time.monotonic() - self.started
        how = "durduruldu" if self.stopped else f"çıkış kodu {code}"
        self._append(f"\n[{how} · {took:.1f} sn]\n")
        if self.proc is proc:
            self.proc = None
        self.hub.publish("terminal_done", {"run": run, "code": code, "seconds": round(took, 2), "stopped": self.stopped})

    def stop(self) -> bool:
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return False
        self.stopped = True
        TerminalAPI.kill_tree(proc.pid)
        try:
            proc.kill()
        except OSError:
            pass
        return True

    def shutdown(self) -> None:
        self.stop()


def cwd_ok(path: str) -> bool:
    return bool(path) and os.path.isdir(path)
