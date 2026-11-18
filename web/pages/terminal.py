"""
Terminal -> scripts/TerminalAPI.py (süreç yönetimi web/terminal_runner.py).

nslookup, curl, ping, kubectl. Etkin kabuğun adı, sürümü ve yolu her zaman görünür; her çıktı bloğunun
başlığında da yazar. "Terminalde dene" bağlantıları /terminal/?cmd=…&kube=… ile gelir: komut kutuya
YAZILIR ama kendiliğinden çalıştırılmaz (adresle komut çalıştırma olmasın).
Komut geçmişi .local/ui.json'da yereldir; veritabanına yazılmaz.
"""
from __future__ import annotations

from flask import Blueprint, render_template, request

from scripts import TerminalAPI, relations
from scripts.apps import ENVS
from scripts.config import APP_ROOT
from web import ui_state
from web.common import ext, toast
from web.terminal_runner import cwd_ok

bp = Blueprint("terminal", __name__)
MAX_HISTORY = 200


def runner():
    return ext().runner


def _cwd() -> str:
    cwd = ui_state.get("terminal_cwd") or str(APP_ROOT)
    return cwd if cwd_ok(cwd) else str(APP_ROOT)


def _shell(shell_id: str | None = None):
    return runner().shell(shell_id or ui_state.get("terminal_shell"))


def _prompt(shell) -> str:
    if not shell:
        return "$"
    return "PS ❯" if shell.is_powershell else ">" if shell.id == "cmd" else "$"


@bp.get("/")
def page():
    ui_state.update(last_page="terminal")
    r = runner()
    shell = _shell()
    kube = request.args.get("kube", "")
    if kube not in relations.kubectl_envs():
        kube = ""
    return render_template("terminal/page.html", shells=r.shells, shell=shell, prompt=_prompt(shell), cwd=_cwd(),
                           kube=kube, kube_envs=relations.kubectl_envs(), output=r.output(), running=r.running,
                           prefill=request.args.get("cmd", ""), history=ui_state.get("terminal_history", []),
                           apps=[a.name for a in relations.catalog()], envs=ENVS)


@bp.get("/quick")
def quick():
    app_name, env = request.args.get("app", ""), request.args.get("env", "dev")
    cmds = relations.quick_commands(app_name, env, _shell()) if app_name else []
    return render_template("terminal/_quick.html", cmds=cmds, env=env)


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
