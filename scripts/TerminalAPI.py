"""
Terminal: nslookup, curl, ping, kubectl gibi teşhis komutları.

Her komut ayrı süreçte çalışır (kalıcı oturum yok); hangi kabuğun çalıştığı her zaman görünür.
Bu dosya kabukları bulur ve komut satırını kurar; süreci _pages/terminal.py (QProcess) çalıştırır.

Windows: Windows PowerShell 5.1, varsa PowerShell 7 (pwsh), CMD.   Linux/macOS: bash, zsh, sh.
Çıktı kodlaması UTF-8'e sabitlenir; yoksa Türkçe Windows'ta karakterler bozulur.
Komutlar veritabanına veya geçmişe YAZILMAZ: içlerinde secret olabilir.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from scripts.config import ENV

IS_WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000


@dataclass(frozen=True)
class Shell:
    id: str           # powershell | pwsh | cmd | bash | zsh | sh
    name: str         # "Windows PowerShell"
    path: str
    version: str = ""

    @property
    def title(self) -> str:
        return f"{self.name} {self.version}".strip()

    @property
    def is_powershell(self) -> bool:
        return self.id in ("powershell", "pwsh")


_CANDIDATES_WIN = [
    ("powershell", "Windows PowerShell", ["powershell.exe"]),
    ("pwsh", "PowerShell 7", ["pwsh.exe"]),
    ("cmd", "Komut İstemi (CMD)", ["cmd.exe"]),
]
_CANDIDATES_UNIX = [
    ("bash", "Bash", ["bash"]),
    ("zsh", "Zsh", ["zsh"]),
    ("sh", "POSIX sh", ["sh"]),
    ("pwsh", "PowerShell 7", ["pwsh"]),
]


def _run_quiet(args: list[str], timeout: float = 5) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace",
                           creationflags=CREATE_NO_WINDOW if IS_WINDOWS else 0, stdin=subprocess.DEVNULL)
        return (r.stdout or r.stderr).strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _version(shell_id: str, path: str) -> str:
    if shell_id in ("powershell", "pwsh"):
        out = _run_quiet([path, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
                          "$PSVersionTable.PSVersion.ToString()"]).splitlines()
        return out[0].strip() if out else ""
    if shell_id == "cmd":
        out = _run_quiet([path, "/d", "/c", "ver"])
        return out.split("Version")[-1].strip(" ]") if "Version" in out else out
    if shell_id in ("bash", "zsh"):
        out = _run_quiet([path, "--version"]).splitlines()
        if out:
            words = [w for w in out[0].replace(",", " ").split() if w[:1].isdigit()]
            return words[0].split("(")[0] if words else ""
    return ""


def detect_shells() -> list[Shell]:
    """Kurulu kabukları sürümleriyle bulur (birkaç alt süreç açar: arka planda çağırın)."""
    out = []
    for shell_id, name, exes in (_CANDIDATES_WIN if IS_WINDOWS else _CANDIDATES_UNIX):
        for exe in exes:
            path = shutil.which(exe)
            if path:
                out.append(Shell(shell_id, name, path, _version(shell_id, path) or ""))
                break
    return out


def default_shell(shells: list[Shell]) -> Shell | None:
    want = ENV.TERMINAL_SHELL
    for s in shells:
        if s.id == want:
            return s
    return shells[0] if shells else None


def build_command(shell: Shell, command: str) -> tuple[str, list[str], str | None]:
    """
    (program, argümanlar, native_args) döner. native_args doluysa Windows'ta QProcess.setNativeArguments
    ile verilir (cmd.exe'nin tırnak kuralları için).
    """
    if shell.is_powershell:
        prefix = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
                  "$OutputEncoding=[System.Text.Encoding]::UTF8;$ProgressPreference='SilentlyContinue';")
        return shell.path, ["-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-Command", prefix + command], None
    if shell.id == "cmd":
        return shell.path, [], f'/d /s /c "chcp 65001>nul & {command}"'
    setsid = shutil.which("setsid")
    if setsid:   # Linux: yeni süreç grubu -> Durdur tüm alt süreçleri sonlandırabilir
        return setsid, ["-w", shell.path, "-c", command], None
    return shell.path, ["-c", command], None


def process_env(kube_env: str = "") -> dict[str, str]:
    """Alt süreç ortamı. KUBECONFIG seçili cluster'a ayarlanır. Uygulama secret'ları EKLENMEZ."""
    from scripts.K8sAPI import kubeconfig_path
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if kube_env:
        env["KUBECONFIG"] = str(kubeconfig_path(kube_env))
    return env


def parse_cd(command: str, cwd: str) -> str | None:
    """'cd X' ise yeni dizini döner (dizin yoksa ValueError), cd değilse None."""
    parts = command.strip().split(None, 1)
    if not parts or parts[0].lower() not in ("cd", "chdir", "set-location", "sl"):
        return None
    target = parts[1].strip() if len(parts) > 1 else ""
    if IS_WINDOWS and target.lower().startswith("/d "):
        target = target[3:].strip()
    target = target.strip('"').strip("'")
    if not target:
        if IS_WINDOWS:
            return cwd
        target = "~"
    path = Path(os.path.expanduser(target))
    if not path.is_absolute():
        path = Path(cwd) / path
    path = path.resolve()
    if not path.is_dir():
        raise ValueError(f"Dizin yok: {path}")
    return str(path)


INTERACTIVE = {"vim", "vi", "nano", "less", "more", "top", "htop", "ssh", "python", "python3", "pwsh", "powershell",
               "cmd", "bash", "sh", "zsh", "mysql", "psql"}


def hint(shell: Shell, command: str) -> str:
    """Komut çalıştırılmadan önce gösterilecek uyarı (yoksa "")."""
    try:
        words = shlex.split(command, posix=not IS_WINDOWS)
    except ValueError:
        words = command.split()
    if not words:
        return ""
    first = words[0].lower()
    if shell.id == "powershell" and first == "curl":
        return ("Windows PowerShell 5.1'de 'curl', Invoke-WebRequest'in takma adıdır. "
                "Gerçek curl için 'curl.exe' yazın.")
    if first == "nslookup" and len(words) == 1:
        return "Parametresiz nslookup etkileşimlidir; desteklenmez. Örnek: nslookup <adres>"
    if first in INTERACTIVE and len(words) == 1:
        return f"'{first}' etkileşimli bir programdır; burada desteklenmez (her komut ayrı süreçte çalışır)."
    if first == "ping" and not IS_WINDOWS and "-c" not in words:
        return "Linux/macOS'ta ping durmaz; '-c 4' ekleyin veya Durdur'a basın."
    return ""


def quick_commands(shell: Shell | None, host: str = "", url: str = "", kube_env: str = "",
                   workload_selector: str = "") -> list[tuple[str, str]]:
    """Seçili uygulamaya göre hazır komutlar: (etiket, komut)."""
    ps = bool(shell and shell.is_powershell)
    cmds: list[tuple[str, str]] = []
    if host:
        cmds.append(("nslookup", f"nslookup {host}"))
        if ps:
            cmds.append(("Port 443", f"Test-NetConnection {host} -Port 443"))
        cmds.append(("ping", f"ping {'-n' if IS_WINDOWS else '-c'} 4 {host}"))
    if url:
        curl = "curl.exe" if IS_WINDOWS else "curl"
        cmds.append(("curl -I", f"{curl} -k -I -s -m 15 {url}"))
        cmds.append(("curl süre", f"{curl} -k -o {'NUL' if IS_WINDOWS else '/dev/null'} -s -m 15 -w "
                                  f"\"dns=%{{time_namelookup}} tcp=%{{time_connect}} tls=%{{time_appconnect}} "
                                  f"ilk_bayt=%{{time_starttransfer}} toplam=%{{time_total}} kod=%{{http_code}}\" {url}"))
    if kube_env:
        sel = f" -l {workload_selector}" if workload_selector else ""
        cmds.append(("kubectl pods", f"kubectl get pods{sel} -o wide"))
        cmds.append(("kubectl events", "kubectl get events --sort-by=.lastTimestamp"))
    return cmds


def kill_tree(pid: int) -> None:
    """Sürecin tüm alt süreçleriyle birlikte sonlandırılması."""
    if not pid:
        return
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                       creationflags=CREATE_NO_WINDOW)
    else:
        import signal
        try:
            pgid = os.getpgid(pid)
            if pgid != os.getpgrp():          # alt süreç kendi grubundaysa grubu sonlandır
                os.killpg(pgid, signal.SIGKILL)
            else:                              # ASLA kendi grubumuzu öldürme: yalnızca süreç ve çocukları
                for child in _children(pid):
                    os.kill(child, signal.SIGKILL)
                os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _children(pid: int) -> list[int]:
    """Linux'ta /proc'tan alt süreçler (torunlar dahil). Başka sistemde boş liste."""
    out: list[int] = []
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/stat", encoding="utf-8") as f:
                    ppid = int(f.read().rsplit(")", 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                continue
            if ppid == pid:
                out += _children(int(entry)) + [int(entry)]
    except OSError:
        pass
    return out
