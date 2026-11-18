"""PLAN_V2 Faz B: gerçek terminaller — yerel PTY, pod kabuğu (pods/exec), oturumlar, WebSocket, Prod onayı."""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time

import pytest
import requests

from scripts import K8sExecAPI, TerminalAPI, config
from tests.test_web import TOKEN, make_app, post
from web import pty_local
from web.term_sessions import Scrollback, SessionLimitError, SessionManager

posix_only = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="POSIX + bash gerekir")


def wait_for(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def bash() -> TerminalAPI.Shell:
    return TerminalAPI.Shell("bash", "Bash", shutil.which("bash"), "5")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:   # zombi sayılmaz
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split()[2] != "Z"
    except OSError:
        return True


# ---------------------------------------------------------------------------
# Yerel PTY
# ---------------------------------------------------------------------------
@posix_only
def test_local_pty_echo_resize_and_terminate(tmp_path):
    out, done = [], []
    p = pty_local.LocalPty(bash(), str(tmp_path), "", 90, 20, out.append, done.append)
    p.start()
    p.write("echo merhaba_$((40+2)); stty size\n")
    assert wait_for(lambda: "merhaba_42" in "".join(out) and "20 90" in "".join(out)), "".join(out)
    p.resize(120, 33)
    p.write("stty size\n")
    assert wait_for(lambda: "33 120" in "".join(out)), "".join(out)
    p.write("sleep 300 &\n")                     # alt süreç de kapanışta ölmeli
    time.sleep(0.3)
    p.terminate()
    assert wait_for(lambda: done), "kabuk kapanmadı"
    assert not alive(p.pid)
    assert os.environ.get("TERM") != "kapatildi"  # kendi sürecimiz ayakta


@posix_only
def test_local_pty_exit_code(tmp_path):
    done = []
    p = pty_local.LocalPty(bash(), str(tmp_path), on_exit=done.append)
    p.start()
    p.write("exit 7\n")
    assert wait_for(lambda: done) and done == [7]


def test_local_pty_env_has_no_app_secrets(monkeypatch):
    config.save({"DEVOPS_PAT": "gizli-pat-xyz"})
    env = pty_local._env("")
    assert env["TERM"] == "xterm-256color" and "gizli-pat-xyz" not in json.dumps(env)


# ---------------------------------------------------------------------------
# Oturum yöneticisi
# ---------------------------------------------------------------------------
class FakeBackend:
    def __init__(self, on_output, on_exit):
        self.on_output, self.on_exit = on_output, on_exit
        self.written, self.size, self.terminated = [], None, False

    def write(self, d):
        self.written.append(d)
        self.on_output(d.upper())

    def resize(self, c, r):
        self.size = (c, r)

    def terminate(self):
        self.terminated = True
        self.on_exit(None)


def test_scrollback_is_bounded():
    sb = Scrollback(limit=10)
    for part in ("abcd", "efgh", "ijkl"):
        sb.append(part)
    assert sb.dump() == "efghijkl"
    sb.append("x" * 25)
    assert sb.dump() == "x" * 10


def test_session_replay_broadcast_limit_and_reap():
    m = SessionManager(max_sessions=2, detached_min=1)
    s = m.create("local", "Bash 5", "/bin/bash", FakeBackend)
    s.write("ls\n")
    cid, q, replay = s.attach()
    assert replay == "LS\n"                          # yeniden bağlanınca son çıktı gelir
    s.write("pwd")
    assert q.get(timeout=1) == ("out", "PWD")
    s.resize(100, 40)
    assert s.backend.size == (100, 40)
    m.create("local", "B", "", FakeBackend)
    with pytest.raises(SessionLimitError):
        m.create("local", "C", "", FakeBackend)
    assert m.reap(time.time() + 3600) == 1           # bağlı istemcisi olan kalır
    s.detach(cid)
    assert m.reap(time.time() + 3600) == 1 and s.backend.terminated and m.list() == []
    m.shutdown()


def test_close_hook_runs_once():
    closed = []
    m = SessionManager(max_sessions=4, detached_min=30)
    s = m.create("pod", "p", "", FakeBackend, {"on_close": lambda sess: closed.append(sess.id)})
    assert m.close(s.id) and closed == [s.id] and m.get(s.id) is None
    m.shutdown()


# ---------------------------------------------------------------------------
# Pod kabuğu: sahte Kubernetes pods/exec sunucusu
# ---------------------------------------------------------------------------
@pytest.fixture
def fake_exec_server():
    from websockets.sync.server import serve
    seen = {"headers": None, "path": None, "resize": [], "proto": None}

    def handler(ws):
        seen["headers"], seen["path"], seen["proto"] = ws.request.headers, ws.request.path, ws.subprotocol
        ws.send(b"\x01" + "hoş geldin\r\n$ ".encode())
        for msg in ws:
            ch, payload = msg[0], msg[1:]
            if ch == 4:
                seen["resize"].append(json.loads(payload))
            elif ch == 0:
                text = payload.decode()
                if "exit" in text:
                    ws.send(b"\x03" + json.dumps({"status": "Failure", "reason": "NonZeroExitCode", "details": {
                        "causes": [{"reason": "ExitCode", "message": "3"}]}}).encode())
                    ws.close()
                    return
                ws.send(b"\x01" + text.encode())
                ws.send(b"\x02" + b"err:" + text.encode())

    def select(conn, protocols):
        return "v4.channel.k8s.io" if "v4.channel.k8s.io" in protocols else None

    srv = serve(handler, "127.0.0.1", 0, select_subprotocol=select)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.socket.getsockname()[1]
    config.save({"K8S_DEV_SERVER": f"http://127.0.0.1:{port}", "K8S_DEV_TOKEN": "tok-123", "K8S_NAMESPACE": "verianalitigi"})
    yield seen
    srv.shutdown()


def test_pod_shell_channels_and_exit_code(fake_exec_server):
    out, done = [], []
    sh = K8sExecAPI.open_shell("dev", "app-1", "main", "auto", 101, 31, out.append, done.append)
    assert isinstance(sh, K8sExecAPI.PodShell) and not isinstance(sh, K8sExecAPI.DemoPodShell)
    assert wait_for(lambda: "hoş geldin" in "".join(out))
    h, path = fake_exec_server["headers"], fake_exec_server["path"]
    assert h["Authorization"] == "Bearer tok-123" and fake_exec_server["proto"] == "v4.channel.k8s.io"
    assert "/api/v1/namespaces/verianalitigi/pods/app-1/exec?" in path and "tty=true" in path and "container=main" in path
    sh.write("ls\n")
    assert wait_for(lambda: "err:ls" in "".join(out))
    sh.resize(80, 24)
    assert wait_for(lambda: {"Width": 80, "Height": 24} in fake_exec_server["resize"])
    assert {"Width": 101, "Height": 31} in fake_exec_server["resize"]
    sh.write("exit\n")
    assert wait_for(lambda: done) and done == [3]


def test_pod_shell_errors_are_friendly(monkeypatch):
    from websockets.sync.server import serve
    from websockets.http11 import Response
    from websockets.datastructures import Headers

    def reject(conn, request):
        return Response(403, "Forbidden", Headers(), b'{"message": "pods \\"x\\" is forbidden: exec"}')

    srv = serve(lambda ws: None, "127.0.0.1", 0, process_request=reject)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        config.save({"K8S_DEV_SERVER": f"http://127.0.0.1:{srv.socket.getsockname()[1]}", "K8S_DEV_TOKEN": "t"})
        with pytest.raises(K8sExecAPI.ExecError, match="pods/exec yetkisi yok"):
            K8sExecAPI.open_shell("dev", "x", "c")
    finally:
        srv.shutdown()
    with pytest.raises(K8sExecAPI.ExecError):
        K8sExecAPI.PodShell("dev", "x", "c", shell="rm -rf /")


def test_exit_code_from_status():
    assert K8sExecAPI.exit_code_from_status({"status": "Success"}) == 0
    assert K8sExecAPI.exit_code_from_status({"details": {"causes": [{"reason": "ExitCode", "message": "130"}]}}) == 130
    assert K8sExecAPI.exit_code_from_status("x") is None


def test_demo_pod_shell_echoes():
    out, done = [], []
    sh = K8sExecAPI.open_shell("dev", "demo-1", "app", on_output=out.append, on_exit=done.append)
    assert isinstance(sh, K8sExecAPI.DemoPodShell)
    sh.write("whoami\r")
    assert "[örnek] whoami" in "".join(out)
    sh.write("exit\r")
    assert done == [0]


# ---------------------------------------------------------------------------
# Web: sayfa, oturum uçları, Prod onayı
# ---------------------------------------------------------------------------
@pytest.fixture
def client():
    from scripts import DevopsAPI
    from scripts import apps as catalog
    ov = DevopsAPI.fetch_overview()
    catalog.set_apps(ov.apps, ov.source, ov.fetched_at)
    app = make_app(_runner())
    c = app.test_client()
    c.get(f"/auth?t={TOKEN}")
    yield c
    t = getattr(app.extensions["dr"], "terms", None)
    if t:
        t.shutdown()
    catalog.set_apps([], "", "")


def _runner():
    class Runner:
        shells = [bash()] if shutil.which("bash") else []
        running = False
        shells_ready = threading.Event()
        shells_ready.set()

        def shell(self, sid):
            return next((s for s in self.shells if s.id == sid), None)

        def output(self):
            return ""
    return Runner()


def test_terminal_page_is_xterm_with_legacy_fallback(client, monkeypatch):
    body = client.get("/terminal/").get_data(as_text=True)
    assert "vendor/xterm/xterm.js" in body and 'id="term-host"' in body and "Pod kabuğu" in body
    monkeypatch.setattr(pty_local, "available", lambda: (False, "pywinpty kullanılamıyor"))
    body = client.get("/terminal/").get_data(as_text=True)
    assert "terminal_legacy.js" in body and "pywinpty kullanılamıyor" in body
    assert client.get("/static/vendor/xterm/xterm.js").status_code == 200


def test_pod_link_opens_dialog(client):
    body = client.get("/terminal/?open=pod&env=dev&pod=musteri360-1&container=app").get_data(as_text=True)
    assert "musteri360-1" in body and "data-open-pod" in body


def test_pods_endpoint_lists_containers(client):
    pods = client.get("/terminal/pods?env=dev&app=Musteri360").get_json()
    assert pods and all(p["containers"] for p in pods)
    assert client.get("/terminal/pods?env=yok").status_code == 400


def test_pod_session_prod_requires_single_use_confirm(client):
    pods = client.get("/terminal/pods?env=prod&app=Musteri360").get_json()
    pod, cont = pods[0]["name"], pods[0]["containers"][0]
    form = {"env": "prod", "pod": pod, "container": cont, "shell": "auto"}
    r = post(client, "/terminal/sessions/pod", data=form).get_json()
    assert r["confirm"] and "PROD" in r["message"] and "id" not in r
    other = post(client, "/terminal/sessions/pod", data={**form, "pod": pod + "x", "confirm": r["confirm"]})
    assert other.status_code == 403                                     # onay başka pod'a geçmez
    r2 = post(client, "/terminal/sessions/pod", data=form).get_json()
    ok = post(client, "/terminal/sessions/pod", data={**form, "confirm": r2["confirm"]}).get_json()
    assert ok["kind"] == "pod" and ok["env"] == "prod"
    again = post(client, "/terminal/sessions/pod", data={**form, "confirm": r2["confirm"]})
    assert again.status_code == 403                                     # tek kullanımlık
    assert any(s["id"] == ok["id"] for s in client.get("/terminal/sessions").get_json())
    assert post(client, f"/terminal/sessions/{ok['id']}/close").get_json() == {"closed": True}


def test_pod_session_validates_input(client):
    for bad in ({"env": "dev", "pod": "../etc", "container": "a"}, {"env": "dev", "pod": "a", "container": "A B"},
                {"env": "dev", "pod": "a", "container": "a", "shell": "zsh -c id"}, {"env": "yok", "pod": "a"}):
        assert post(client, "/terminal/sessions/pod", data=bad).status_code == 400, bad
    dev = post(client, "/terminal/sessions/pod", data={"env": "dev", "pod": "a-1", "container": "app"}).get_json()
    assert dev["kind"] == "pod" and "confirm" not in dev                # dev'de onay yok


def test_session_endpoints_need_header(client):
    assert client.post("/terminal/sessions/local", data={"shell": "bash"}).status_code == 403


# ---------------------------------------------------------------------------
# Uçtan uca: gerçek sunucu + WebSocket (çerez ve Origin ile)
# ---------------------------------------------------------------------------
@pytest.fixture
def live():
    from werkzeug.serving import make_server
    from scripts import DevopsAPI
    from scripts import apps as catalog
    ov = DevopsAPI.fetch_overview()
    catalog.set_apps(ov.apps, ov.source, ov.fetched_at)
    app = make_app(_runner())
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    port = srv.server_port
    app.config.update(DR_PORT=port, SERVER_NAME=None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    s = requests.Session()
    s.trust_env = False
    s.get(f"http://127.0.0.1:{port}/auth?t={TOKEN}", allow_redirects=False)
    yield app, port, s
    srv.shutdown()
    if getattr(app.extensions["dr"], "terms", None):
        app.extensions["dr"].terms.shutdown()
    catalog.set_apps([], "", "")


def _ws(port, sid, cookie_header, origin=True):
    from websockets.sync.client import connect
    headers = {"Cookie": cookie_header}
    if origin:
        headers["Origin"] = f"http://127.0.0.1:{port}"
    return connect(f"ws://127.0.0.1:{port}/terminal/ws/{sid}", additional_headers=headers, proxy=None, open_timeout=5)


def _collect(ws, want, timeout=10):
    buf, end = "", time.time() + timeout
    while time.time() < end and want not in buf:
        try:
            m = json.loads(ws.recv(timeout=0.5))
        except TimeoutError:
            continue
        if m["type"] in ("out", "hello"):
            buf += m.get("data") or m.get("replay") or ""
    return buf


@posix_only
def test_local_terminal_over_websocket(live):
    from websockets.exceptions import InvalidStatus
    app, port, s = live
    cookie = "; ".join(f"{k}={v}" for k, v in s.cookies.items())
    r = s.post(f"http://127.0.0.1:{port}/terminal/sessions/local", data={"shell": "bash", "cols": 100, "rows": 30},
               headers={"X-Devrunner": "1", "Origin": f"http://127.0.0.1:{port}"})
    info = r.json()
    assert r.status_code == 200 and info["kind"] == "local", info
    with pytest.raises(InvalidStatus):                                   # Origin olmadan reddedilir
        _ws(port, info["id"], cookie, origin=False)
    with pytest.raises(InvalidStatus):                                   # çerez olmadan reddedilir
        _ws(port, info["id"], "x=y")
    with _ws(port, info["id"], cookie) as ws:
        hello = json.loads(ws.recv(timeout=5))
        assert hello["type"] == "hello" and hello["id"] == info["id"]
        ws.send(json.dumps({"type": "in", "data": "echo ws_$((6*7))\n"}))
        assert "ws_42" in _collect(ws, "ws_42")
    with _ws(port, info["id"], cookie) as ws:                            # yeniden bağlan: geri oynatma
        hello = json.loads(ws.recv(timeout=5))
        assert "ws_42" in hello["replay"]
        ws.send(json.dumps({"type": "in", "data": "exit 5\n"}))
        end = time.time() + 10
        while time.time() < end:
            m = json.loads(ws.recv(timeout=5))
            if m["type"] == "exit":
                assert m["code"] == 5
                break
        else:
            pytest.fail("exit gelmedi")
    with _ws(port, "yok", cookie) as ws:
        assert json.loads(ws.recv(timeout=5))["type"] == "error"


def test_websocket_close_sends_no_http_garbage(live):
    """flask-sock + werkzeug: istemci kapatınca kapanış çerçevesinden sonra HTTP yanıtı gelmemeli
    (Chrome bunu "Invalid frame header" hatası olarak gösterir). web/ws.finish düzeltmesi."""
    import base64
    import socket
    import struct
    app, port, s = live
    cookie = "; ".join(f"{k}={v}" for k, v in s.cookies.items())
    m = SessionManager(max_sessions=4, detached_min=30)
    app.extensions["dr"].terms = m
    sess = m.create("local", "x", "", FakeBackend)
    c = socket.create_connection(("127.0.0.1", port), timeout=5)
    key = base64.b64encode(os.urandom(16)).decode()
    c.sendall((f"GET /terminal/ws/{sess.id} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
               f"Origin: http://127.0.0.1:{port}\r\nCookie: {cookie}\r\n\r\n").encode())
    buf = b""
    while b"\r\n\r\n" not in buf or b'"hello"' not in buf:
        buf += c.recv(65536)
    assert buf.startswith(b"HTTP/1.1 101")
    mask, payload = os.urandom(4), struct.pack("!H", 1000)
    c.sendall(bytes([0x88, 0x80 | len(payload)]) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
    rest = b""
    while True:
        try:
            d = c.recv(65536)
        except (socket.timeout, ConnectionResetError):
            break
        if not d:
            break
        rest += d
    assert rest.endswith(b"\x88\x02\x03\xe8") and b"HTTP/1.1" not in rest, rest[:120]
