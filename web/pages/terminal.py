"""
Terminal (PLAN_V2.md Bölüm 5): webterminal gibi gerçek terminaller — xterm.js + WebSocket.

* Yerel kabuk: web/pty_local.py (POSIX PTY / Windows ConPTY). Kabuk listesi scripts/TerminalAPI.
* Pod kabuğu: scripts/K8sExecAPI.py (kubectl exec -it karşılığı). Prod'da sunucu onayı (web/confirm.py).
* Oturumlar: web/term_sessions.py — sayfa değişse de açık kalır, yeniden bağlanınca son çıktı geri gelir.
* "Terminalde dene" bağlantıları /terminal/?cmd=…&kube=… ile gelir: komut etkin terminalin giriş satırına
  YAZILIR, Enter gönderilmez (adresle komut çalıştırma yok).
* Yerel PTY yoksa (Windows'ta pywinpty kurulamadıysa) sayfa eski "her komut ayrı süreç" kipinde açılır
  (legacy.html, web/terminal_runner.py); aşağıdaki /run, /stop… uçları o kip içindir.

Güvenlik: WebSocket el sıkışması çerez ve Origin ister (web/security.py). Pod kabuğu açılış / kapanışı
değişiklik geçmişine yazılır; tuş vuruşları ve çıktı hiçbir yere kaydedilmez.
"""
from __future__ import annotations

import json
import logging
import queue
import re
import threading
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request

from scripts import K8sExecAPI, TerminalAPI, database, relations
from scripts.apps import ENV_TITLES, ENVS
from scripts.config import APP_ROOT
from web import confirm, k8s_access, pty_local, ui_state
from web.common import error_text, ext, toast, uid
from web.term_sessions import SessionLimitError, SessionManager
from web.terminal_runner import cwd_ok
from web.ws import sock

bp = Blueprint("terminal", __name__)
log = logging.getLogger("web.terminal")
MAX_HISTORY = 200
NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")     # RFC 1123 (pod / container)
MAX_INPUT = 64 * 1024


def runner():
    return ext().runner


def _cwd() -> str:
    cwd = ui_state.get("terminal_cwd") or str(APP_ROOT)
    return cwd if cwd_ok(cwd) else str(APP_ROOT)


def terms() -> SessionManager:
    e = ext()
    if getattr(e, "terms", None) is None:
        e.terms = SessionManager()
    return e.terms


def _shell(shell_id: str | None = None):
    return runner().shell(shell_id or ui_state.get("terminal_shell"))


def _prompt(shell) -> str:
    if not shell:
        return "$"
    return "PS ❯" if shell.is_powershell else ">" if shell.id == "cmd" else "$"


@bp.get("/")
def page():
    ui_state.update(last_page="terminal")
    ok, reason = pty_local.available()
    if ok and request.args.get("legacy") != "1":
        r = runner()
        r.shells_ready.wait(10)
        default = TerminalAPI.default_shell(r.shells)
        kube = request.args.get("kube", "")
        open_pod = None
        if request.args.get("open") == "pod":
            open_pod = {k: request.args.get(k, "") for k in ("env", "pod", "container", "app")}
        return render_template("terminal/page.html", shells=r.shells, default_shell=default.id if default else "",
                               saved_shell=ui_state.get("terminal_shell") or (default.id if default else ""),
                               kube_envs=relations.kubectl_envs(), kube=kube if kube in relations.kubectl_envs() else "",
                               prefill=request.args.get("cmd", ""), sessions=[s.info() for s in terms().list()],
                               focus=request.args.get("session", ""), open_pod=open_pod, envs=relations.k8s_envs(),
                               apps=[a.name for a in relations.catalog()], all_envs=ENVS, cwd=_cwd(),
                               demo=k8s_access.is_demo())
    return _legacy_page(reason)


def _legacy_page(reason: str = ""):
    r = runner()
    shell = _shell()
    kube = request.args.get("kube", "")
    if kube not in relations.kubectl_envs():
        kube = ""
    return render_template("terminal/legacy.html", shells=r.shells, shell=shell, prompt=_prompt(shell), cwd=_cwd(),
                           kube=kube, kube_envs=relations.kubectl_envs(), output=r.output(), running=r.running,
                           prefill=request.args.get("cmd", ""), history=ui_state.get("terminal_history", []),
                           apps=[a.name for a in relations.catalog()], envs=ENVS, reason=reason)


@bp.get("/quick")
def quick():
    app_name, env = request.args.get("app", ""), request.args.get("env", "dev")
    cmds = relations.quick_commands(app_name, env, _shell(request.args.get("shell"))) if app_name else []
    return render_template("terminal/_quick.html", cmds=cmds, env=env, xterm=request.args.get("mode") == "xterm")


@bp.get("/hint")
def hint():
    shell = _shell(request.args.get("shell"))
    text = TerminalAPI.hint(shell, request.args.get("command", "")) if shell else ""
    return render_template("terminal/_hint.html", text=text)


@bp.post("/shell")
def set_shell():
    shell = _shell(request.form.get("shell"))
    if shell:
        ui_state.update(terminal_shell=shell.id)
    return render_template("terminal/_badge.html", shell=shell, prompt=_prompt(shell))


@bp.post("/cwd")
def set_cwd():
    path = request.form.get("cwd", "").strip()
    try:
        new = TerminalAPI.parse_cd(f'cd "{path}"', _cwd()) if path else _cwd()
    except ValueError as e:
        return render_template("terminal/_cwd.html", cwd=_cwd()), 200, toast(str(e), "error")
    ui_state.update(terminal_cwd=new)
    return render_template("terminal/_cwd.html", cwd=new)


@bp.post("/run")
def run():
    r = runner()
    command = request.form.get("command", "").strip()
    shell = _shell(request.form.get("shell"))
    kube = request.form.get("kube", "")
    if kube not in relations.kubectl_envs():
        kube = ""
    if not command or shell is None:
        return "", 204
    if r.running:
        return "", 204, toast("Bir komut zaten çalışıyor; önce durdurun.", "warning")
    hist = [h for h in ui_state.get("terminal_history", []) if h != command] + [command]
    ui_state.update(terminal_history=hist[-MAX_HISTORY:], terminal_shell=shell.id)
    cwd = _cwd()
    header = (f"\n── {shell.title} · {cwd}" + (f" · KUBECONFIG={kube}" if kube else "") +
              f"\n{_prompt(shell)} {command}\n")
    if command.lower() in ("cls", "clear"):
        r.clear()
        return "", 204, {"HX-Trigger": "terminal_clear"}
    try:
        new_dir = TerminalAPI.parse_cd(command, cwd)
    except ValueError as e:
        r._append(header + f"{e}\n")
        return "", 204
    if new_dir is not None:
        ui_state.update(terminal_cwd=new_dir)
        r._append(header + f"{new_dir}\n")
        return render_template("terminal/_cwd.html", cwd=new_dir, oob=True)
    try:
        r.start(command, shell, cwd, kube, header)
    except RuntimeError as e:
        return "", 204, toast(str(e), "warning")
    return "", 204, {"HX-Trigger": "terminal_started"}


@bp.post("/stop")
def stop():
    stopped = runner().stop()
    return "", 204, toast("Durduruldu." if stopped else "Çalışan komut yok.", "info")


@bp.post("/clear")
def clear():
    runner().clear()
    return "", 204, {"HX-Trigger": "terminal_clear"}


# =============================================================================
# Native terminal oturumları
# =============================================================================
def _size() -> tuple[int, int]:
    def num(name, default, low, high):
        try:
            return max(low, min(high, int(request.values.get(name, default))))
        except ValueError:
            return default
    return num("cols", 100, 20, 500), num("rows", 30, 5, 300)


@bp.get("/sessions")
def sessions():
    return jsonify([s.info() for s in terms().list()])


@bp.post("/sessions/local")
def open_local():
    ok, reason = pty_local.available()
    if not ok:
        return jsonify(error=reason), 400
    shell = _shell(request.form.get("shell"))
    if shell is None:
        return jsonify(error="Kabuk bulunamadı."), 400
    kube = request.form.get("kube", "")
    if kube not in relations.kubectl_envs():
        kube = ""
    cols, rows = _size()
    cwd = _cwd()
    ui_state.update(terminal_shell=shell.id)

    def factory(on_output, on_exit):
        p = pty_local.LocalPty(shell, cwd, kube, cols, rows, on_output, on_exit)
        p.start()
        return p

    detail = f"{shell.path} · {cwd}" + (f" · KUBECONFIG={ENV_TITLES.get(kube, kube)}" if kube else "")
    try:
        s = terms().create("local", shell.title, detail, factory, {"shell": shell.id, "kube": kube})
    except SessionLimitError as e:
        return jsonify(error=str(e)), 409
    except Exception as e:   # noqa: BLE001
        log.exception("Yerel terminal açılamadı")
        return jsonify(error=f"Yerel terminal açılamadı: {error_text(e)}"), 500
    return jsonify(s.info())


@bp.get("/pods")
def pods():
    """Pod kabuğu penceresi için: ortamdaki (veya uygulamanın) pod'ları ve container'ları."""
    env, app_name = request.args.get("env", ""), request.args.get("app", "")
    if env not in relations.k8s_envs():
        return jsonify(error="Ortam yok."), 400
    try:
        items = k8s_access.pods(env, app_name)
    except Exception as e:   # noqa: BLE001
        return jsonify(error=error_text(e)), 502
    return jsonify([{"name": p.name, "status": p.status, "ready": p.ready, "containers": p.containers} for p in items])


@bp.post("/sessions/pod")
def open_pod():
    f = request.form
    env, pod, container = f.get("env", ""), f.get("pod", ""), f.get("container", "")
    shell_kind = f.get("shell", "auto")
    app_name = f.get("app") or None
    if env not in relations.k8s_envs():
        return jsonify(error="Geçersiz ortam."), 400
    if not NAME_RE.match(pod) or (container and not NAME_RE.match(container)):
        return jsonify(error="Geçersiz pod / container adı."), 400
    if shell_kind not in K8sExecAPI.SHELLS:
        return jsonify(error="Geçersiz kabuk."), 400
    key = ("shell", env, pod, container)
    if env == "prod":
        token = f.get("confirm", "")
        if not token or not confirm.use(token, key):
            if token:
                return jsonify(error="Onay geçersiz veya süresi dolmuş; yeniden deneyin."), 403
            return jsonify(confirm=confirm.issue(key), warn=relations.prod_warning("prod"),
                           message=f"PROD ortamında {pod}/{container} içinde etkileşimli kabuk açılacak.")
    cols, rows = _size()
    who = {"env": env, "container": container, "title": f"{ENV_TITLES.get(env, env)} kabuk: {pod}/{container}"}

    def on_close(sess):
        database.audit_now("k8s", "shell_close", "pod", pod, {**who, "exit": sess.exit_code}, app_name)

    def factory(on_output, on_exit):
        return K8sExecAPI.open_shell(env, pod, container, shell_kind, cols, rows, on_output, on_exit)

    try:
        s = terms().create("pod", f"{pod}", f"{ENV_TITLES.get(env, env)} · {pod} / {container}", factory,
                           {"env": env, "pod": pod, "container": container, "app": app_name, "on_close": on_close})
    except SessionLimitError as e:
        return jsonify(error=str(e)), 409
    except K8sExecAPI.ExecError as e:
        return jsonify(error=str(e)), 502
    except Exception as e:   # noqa: BLE001
        log.exception("Pod kabuğu açılamadı")
        return jsonify(error=f"Pod kabuğu açılamadı: {error_text(e)}"), 500
    database.audit_now("k8s", "shell_open", "pod", pod, who, app_name)
    return jsonify(s.info())


@bp.post("/sessions/<sid>/close")
def close_session(sid):
    return jsonify(closed=terms().close(sid))


@sock.route("/ws/<sid>", bp=bp)
def term_ws(ws, sid):
    """Önce geri oynatma (son çıktı), sonra canlı akış. İstemci: {"type": "in"|"resize", ...}."""
    s = terms().get(sid)
    if s is None:
        ws.send(json.dumps({"type": "error", "message": "Oturum bulunamadı (kapanmış olabilir)."}))
        return
    cid, q, replay = s.attach()
    send_lock = threading.Lock()

    def send(obj):
        with send_lock:
            if not ws.connected:                 # kapanma el sıkışması sürerken yazma
                raise ConnectionError("istemci gitti")
            ws.send(json.dumps(obj, ensure_ascii=False))

    send({"type": "hello", "replay": replay, **s.info()})
    if s.closed:
        send({"type": "exit", "code": s.exit_code})
    stop = threading.Event()

    def pump():
        while not stop.is_set():
            try:
                kind, value = q.get(timeout=1)
            except queue.Empty:
                continue
            try:
                if kind == "out":
                    parts = [value]
                    while len(parts) < 256:          # art arda gelen çıktıyı tek iletide birleştir
                        try:
                            k2, v2 = q.get_nowait()
                        except queue.Empty:
                            break
                        if k2 != "out":
                            q.put((k2, v2))
                            break
                        parts.append(v2)
                    send({"type": "out", "data": "".join(parts)})
                else:
                    send({"type": "exit", "code": value})
            except Exception:   # noqa: BLE001 - istemci gitti
                stop.set()

    t = threading.Thread(target=pump, name=f"term-ws-{sid[:6]}", daemon=True)
    t.start()
    try:
        while True:
            raw = ws.receive()
            if raw is None:
                break
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            if len(raw) > MAX_INPUT:
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("type") == "in" and isinstance(msg.get("data"), str):
                s.write(msg["data"])
            elif msg.get("type") == "resize":
                try:
                    s.resize(max(2, min(500, int(msg["cols"]))), max(2, min(300, int(msg["rows"]))))
                except (KeyError, TypeError, ValueError):
                    pass
    finally:
        stop.set()
        s.detach(cid)
        t.join(2)                                # pompa bitmeden işleyici dönmesin
