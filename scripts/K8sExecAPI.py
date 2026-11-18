"""
Pod içinde etkileşimli kabuk (kubectl exec -it karşılığı) — PLAN_V2.md Bölüm 5.

Kubernetes API'nin pods/exec ucuna WebSocket ile bağlanılır (webterminal/kube/exec.py ile aynı protokol);
"channel.k8s.io" alt protokolünde her ikili mesajın ilk baytı kanaldır:
    0 stdin · 1 stdout · 2 stderr · 3 hata/durum (JSON) · 4 boyut (JSON) · 255 kapat (v5)

Adres ve token K8sAPI.env_settings'ten; proxy kuralı K8sAPI ile aynı (sistem proxy'si yok, K8S_PROXY).
İstemci bloklayan websockets.sync'tir: okuma ayrı iş parçacığında, yazma / boyut herhangi bir iş
parçacığından (websockets.sync gönderimde iş parçacığı güvenlidir).

Hiçbir ortam tanımlı değilse (demo) yalnızca yankı yapan sahte bir kabuk döner.
Tuş vuruşları ve çıktı hiçbir yere kaydedilmez. Bu dosya arayüz import etmez.
"""
from __future__ import annotations

import codecs
import json
import logging
import ssl
import threading
from typing import Callable
from urllib.parse import quote, urlencode

from scripts import K8sAPI
from scripts.config import ENV, redact

log = logging.getLogger(__name__)

SUBPROTOCOLS = ["v5.channel.k8s.io", "v4.channel.k8s.io", "channel.k8s.io"]
_PRELUDE = "export TERM=xterm-256color COLORTERM=truecolor 2>/dev/null; "
SHELLS = {
    "auto": ["/bin/sh", "-c", _PRELUDE +
             "if command -v bash >/dev/null 2>&1; then exec bash -l; "
             "elif command -v ash >/dev/null 2>&1; then exec ash; else exec sh; fi"],
    "bash": ["/bin/sh", "-c", _PRELUDE + "exec bash -l"],
    "sh": ["/bin/sh", "-c", _PRELUDE + "exec sh"],
}


class ExecError(Exception):
    pass


def exit_code_from_status(status: dict) -> int | None:
    """Kanal 3'teki metav1.Status -> çıkış kodu."""
    if not isinstance(status, dict):
        return None
    if status.get("status") == "Success":
        return 0
    for cause in (status.get("details") or {}).get("causes") or []:
        if cause.get("reason") == "ExitCode":
            try:
                return int(cause.get("message"))
            except (TypeError, ValueError):
                return None
    return None


def exec_url(server: str, namespace: str, pod: str, container: str, command: list[str]) -> str:
    query = [("stdin", "true"), ("stdout", "true"), ("stderr", "true"), ("tty", "true")]
    if container:
        query.append(("container", container))
    query += [("command", part) for part in command]
    base = server.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    return f"{base}/api/v1/namespaces/{quote(namespace)}/pods/{quote(pod)}/exec?{urlencode(query)}"


class PodShell:
    """Bir pod / container içinde TTY'li kabuk."""

    def __init__(self, env: str, pod: str, container: str, shell: str = "auto", cols: int = 80, rows: int = 24,
                 on_output: Callable[[str], None] = lambda s: None, on_exit: Callable[[int | None], None] = lambda c: None):
        if shell not in SHELLS:
            raise ExecError("Geçersiz kabuk seçimi.")
        self.env, self.pod, self.container, self.shell = env, pod, container, shell
        self.cols, self.rows = cols, rows
        self._on_output, self._on_exit = on_output, on_exit
        self._ws = None
        self.closed = False
        self.exit_code: int | None = None
        self._decoders = {1: codecs.getincrementaldecoder("utf-8")("replace"),
                          2: codecs.getincrementaldecoder("utf-8")("replace")}

    def start(self) -> None:
        from websockets.exceptions import InvalidStatus, WebSocketException
        from websockets.sync.client import connect

        server, token = K8sAPI.env_settings(self.env)
        if not (server and token):
            raise ExecError("Bu ortam için cluster adresi / token girilmemiş (Ayarlar → Kubernetes).")
        url = exec_url(server, ENV.K8S_NAMESPACE, self.pod, self.container, SHELLS[self.shell])
        ctx = None
        if url.startswith("wss://"):
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE           # kubeconfig: insecure-skip-tls-verify: true
        try:
            self._ws = connect(url, ssl=ctx, subprotocols=SUBPROTOCOLS, proxy=ENV.K8S_PROXY or None,
                               additional_headers={"Authorization": f"Bearer {token}"},
                               user_agent_header="Devrunner", open_timeout=K8sAPI.TIMEOUT, close_timeout=3,
                               max_size=None, compression=None)
        except InvalidStatus as e:
            code = e.response.status_code
            body = (e.response.body or b"").decode("utf-8", "replace")
            try:
                body = json.loads(body).get("message", body)
            except ValueError:
                pass
            if code == 401:
                raise ExecError("Kubernetes token'ı geçersiz (401).") from None
            if code == 403:
                raise ExecError(f"Servis hesabının pods/exec yetkisi yok (403). {redact(body)[:200]}") from None
            if code == 404:
                raise ExecError("Pod ya da container bulunamadı (404).") from None
            raise ExecError(f"exec reddedildi ({code}).") from None
        except (OSError, WebSocketException, TimeoutError) as e:
            raise ExecError(f"Kubernetes API'ye bağlanılamadı: {type(e).__name__}: {redact(str(e))[:200]}") from None
        self.resize(self.cols, self.rows)
        threading.Thread(target=self._reader, name="pod-shell", daemon=True).start()

    def _reader(self) -> None:
        from websockets.exceptions import ConnectionClosed
        status = None
        try:
            for message in self._ws:
                if isinstance(message, str) or not message:
                    continue
                channel, payload = message[0], message[1:]
                if channel in (1, 2):
                    text = self._decoders[channel].decode(payload)
                    if text:
                        self._on_output(text)
                elif channel == 3:
                    try:
                        status = json.loads(payload.decode("utf-8", "replace"))
                    except ValueError:
                        status = {"status": "Failure", "message": payload.decode("utf-8", "replace")}
                    if status.get("status") != "Success" and exit_code_from_status(status) is None:
                        self._on_output(f"\r\n\x1b[31m{status.get('message') or 'exec hatası'}\x1b[0m\r\n")
        except ConnectionClosed:
            pass
        except Exception as e:   # noqa: BLE001
            self._on_output(f"\r\n\x1b[31mBağlantı koptu: {type(e).__name__}\x1b[0m\r\n")
        finally:
            self._finish(exit_code_from_status(status) if status else None)

    def _finish(self, code) -> None:
        if self.closed:
            return
        self.closed = True
        self.exit_code = code
        try:
            self._ws.close()
        except Exception:   # noqa: BLE001
            pass
        self._on_exit(code)

    def write(self, data: str) -> None:
        if not self.closed and self._ws is not None:
            try:
                self._ws.send(b"\x00" + data.encode("utf-8", "replace"))
            except Exception:   # noqa: BLE001 - bağlantı kapanmışsa okuyucu bitirir
                pass

    def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = cols, rows
        if not self.closed and self._ws is not None:
            try:
                self._ws.send(b"\x04" + json.dumps({"Width": cols, "Height": rows}).encode())
            except Exception:   # noqa: BLE001
                pass

    def terminate(self) -> None:
        """Akışı kapatır; kubelet container'daki kabuğa SIGHUP gönderir."""
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:   # noqa: BLE001
                pass


class DemoPodShell(PodShell):
    """Demo: satır düzenlemeli, yankı yapan sahte kabuk (gerçek cluster'a gidilmez)."""

    def start(self) -> None:
        self._line = ""
        self._prompt = f"\x1b[32m{self.container}@{self.pod[:24]}\x1b[0m:\x1b[34m/app\x1b[0m$ "
        self._on_output(f"\x1b[2m[örnek pod kabuğu · {self.env} · {self.pod}/{self.container}]\x1b[0m\r\n"
                        f"Gerçek cluster tanımlı değil; komutlar çalıştırılmaz, yankılanır.\r\n{self._prompt}")

    def write(self, data: str) -> None:
        if self.closed:
            return
        for ch in data:
            if ch in ("\r", "\n"):
                cmd = self._line.strip()
                self._line = ""
                if cmd == "exit":
                    self._on_output("\r\nlogout\r\n")
                    self._finish(0)
                    return
                out = f"\r\n[örnek] {cmd}\r\n" if cmd else "\r\n"
                self._on_output(out + self._prompt)
            elif ch in ("\x7f", "\b"):
                if self._line:
                    self._line = self._line[:-1]
                    self._on_output("\b \b")
            elif ch == "\x03":
                self._line = ""
                self._on_output("^C\r\n" + self._prompt)
            elif ch >= " ":
                self._line += ch
                self._on_output(ch)

    def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = cols, rows

    def _finish(self, code) -> None:
        if self.closed:
            return
        self.closed = True
        self.exit_code = code
        self._on_exit(code)

    def terminate(self) -> None:
        self._finish(None)


def open_shell(env: str, pod: str, container: str, shell: str = "auto", cols: int = 80, rows: int = 24,
               on_output=lambda s: None, on_exit=lambda c: None) -> PodShell:
    cls = DemoPodShell if K8sAPI.is_demo() else PodShell
    s = cls(env, pod, container, shell, cols, rows, on_output, on_exit)
    s.start()
    return s
