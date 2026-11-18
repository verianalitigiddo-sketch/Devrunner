"""
Yerel makinede gerçek terminal (PTY) — PLAN_V2.md Bölüm 5.

POSIX: pty.openpty + yeni oturum (Durdur / kapat tüm süreç grubunu sonlandırır).
Windows: ConPTY, pywinpty ile (kurulu değilse available() False döner ve Terminal sayfası eski
"her komut ayrı süreç" kipinde açılır).

Kabuk listesi ve sürümleri TerminalAPI.detect_shells, ortam TerminalAPI.process_env (uygulama secret'ları
aktarılmaz; KUBECONFIG ve NO_PROXY ayarlanır), sonlandırma TerminalAPI.kill_tree. Tuş vuruşları ve çıktı
hiçbir yere kaydedilmez.
"""
from __future__ import annotations

import codecs
import os
import struct
import sys
import threading
import time
from typing import Callable

from scripts import TerminalAPI

IS_WINDOWS = sys.platform == "win32"


def available() -> tuple[bool, str]:
    if IS_WINDOWS:
        try:
            import winpty  # noqa: F401
            return True, ""
        except Exception as e:   # noqa: BLE001
            return False, f"pywinpty kullanılamıyor ({type(e).__name__}); terminal basit kipte çalışıyor."
    try:
        import pty  # noqa: F401
        return True, ""
    except ImportError:
        return False, "Bu sistemde PTY yok."


def _kill_session(sid: int) -> None:
    """POSIX: start_new_session ile açılan kabuğun oturumundaki TÜM süreçler (bash -i arka plan işlerini
    ayrı süreç gruplarına koyar; yalnızca grubu öldürmek yetmez). Linux'ta /proc'tan; başka yerde grup + çocuklar."""
    if IS_WINDOWS or not sid:
        return
    import signal
    members: list[int] = []
    try:
        for entry in os.listdir("/proc"):
            if entry.isdigit():
                try:
                    with open(f"/proc/{entry}/stat", encoding="utf-8") as f:
                        fields = f.read().rsplit(")", 1)[1].split()
                    if int(fields[3]) == sid:          # alan 6: session id
                        members.append(int(entry))
                except (OSError, ValueError, IndexError):
                    continue
    except OSError:
        members = TerminalAPI._children(sid) + [sid]
    me = os.getpid()
    for pid in members:
        if pid != me:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


def shell_args(shell: TerminalAPI.Shell) -> list[str]:
    if shell.is_powershell:
        return [shell.path, "-NoLogo"]
    if shell.id == "cmd":
        return [shell.path]
    return [shell.path, "-i"]


def _env(kube_env: str) -> dict[str, str]:
    env = TerminalAPI.process_env(kube_env)
    env.update({"TERM": "xterm-256color", "COLORTERM": "truecolor"})
    env.pop("PYTHONIOENCODING", None)
    return env


class LocalPty:
    def __init__(self, shell: TerminalAPI.Shell, cwd: str, kube_env: str = "", cols: int = 80, rows: int = 24,
                 on_output: Callable[[str], None] = lambda s: None, on_exit: Callable[[int | None], None] = lambda c: None):
        self.shell, self.cwd, self.kube_env = shell, cwd, kube_env
        self.cols, self.rows = max(2, cols), max(2, rows)
        self._on_output, self._on_exit = on_output, on_exit
        self.closed = False
        self.exit_code: int | None = None
        self.pid = 0
        self._proc = None
        self._fd = None
        self._killed = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ başlat
    def start(self) -> None:
        if IS_WINDOWS:
            self._start_windows()
        else:
            self._start_posix()

    def _start_posix(self) -> None:
        import fcntl
        import pty
        import subprocess
        import termios
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", self.rows, self.cols, 0, 0))
        try:
            self._proc = subprocess.Popen(shell_args(self.shell), stdin=slave, stdout=slave, stderr=slave,
                                          cwd=self.cwd, env=_env(self.kube_env), start_new_session=True,
                                          close_fds=True)
        finally:
            os.close(slave)
        self._fd = master
        self.pid = self._proc.pid
        threading.Thread(target=self._read_posix, name="pty-okuyucu", daemon=True).start()

    def _read_posix(self) -> None:
        import select
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        exited_at = None
        while True:
            try:
                ready, _, _ = select.select([self._fd], [], [], 0.25)
            except (OSError, ValueError):
                break
            if not ready:
                # Kabuk çıktıysa ama arka plandaki bir iş PTY'yi açık tutuyorsa kısa bir süre sonra bırak
                if self._proc.poll() is not None:
                    exited_at = exited_at or time.monotonic()
                    if time.monotonic() - exited_at > 1.0:
                        break
                continue
            try:
                data = os.read(self._fd, 65536)
            except OSError:
                break
            if not data:
                break
            text = decoder.decode(data)
            if text:
                self._on_output(text)
        code = self._proc.wait()
        if code < 0 and self._killed:          # bizim sonlandırmamız: sinyal kodu yerine "yok"
            code = None
        _kill_session(self.pid)                # geride kalan arka plan işleri
        self._finish(code)

    def _start_windows(self) -> None:
        from winpty import PtyProcess
        import subprocess
        cmdline = subprocess.list2cmdline(shell_args(self.shell))
        self._proc = PtyProcess.spawn(cmdline, cwd=self.cwd, env=_env(self.kube_env), dimensions=(self.rows, self.cols))
        self.pid = self._proc.pid
        threading.Thread(target=self._read_windows, name="conpty-okuyucu", daemon=True).start()

    def _read_windows(self) -> None:
        while True:
            try:
                text = self._proc.read(65536)
            except EOFError:
                break
            except Exception:   # noqa: BLE001
                break
            if text:
                self._on_output(text)
            elif not self._proc.isalive():
                break
        code = None
        try:
            code = self._proc.exitstatus
        except Exception:   # noqa: BLE001
            pass
        self._finish(code)

    # ------------------------------------------------------------------ G/Ç
    def write(self, data: str) -> None:
        if self.closed:
            return
        try:
            if IS_WINDOWS:
                self._proc.write(data)
            else:
                os.write(self._fd, data.encode("utf-8", "replace"))
        except OSError:
            pass

    def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = max(2, cols), max(2, rows)
        if self.closed:
            return
        try:
            if IS_WINDOWS:
                self._proc.setwinsize(self.rows, self.cols)
            else:
                import fcntl
                import termios
                fcntl.ioctl(self._fd, termios.TIOCSWINSZ, struct.pack("HHHH", self.rows, self.cols, 0, 0))
        except OSError:
            pass

    def terminate(self) -> None:
        if self.closed:
            return
        try:
            if IS_WINDOWS:
                TerminalAPI.kill_tree(self.pid)
                self._proc.terminate(force=True)
            else:
                self._killed = True
                _kill_session(self.pid)              # iş denetimiyle ayrı gruplara geçenler dahil
                TerminalAPI.kill_tree(self.pid)
        except Exception:   # noqa: BLE001
            pass

    def _finish(self, code) -> None:
        with self._lock:
            if self.closed:
                return
            self.closed = True
        self.exit_code = code
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
        self._on_exit(code)
