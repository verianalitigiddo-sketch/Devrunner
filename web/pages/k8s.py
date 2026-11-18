"""
Kubernetes -> scripts/K8sAPI.py (erişim web/k8s_access.py üzerinden; deployment hiç istenmez).

Ortam / uygulama seçimi, pod'lar, log (canlı akış: WebSocket, yalnızca ekleme), olaylar. Pod içinde
etkileşimli kabuk Terminal sayfasında açılır ("Terminal" düğmesi); Prod'da sunucu onayı orada istenir.
"""
from __future__ import annotations

import threading

from flask import Blueprint, render_template, request

from scripts import K8sAPI, relations
from scripts.config import ENV, redact
from web import k8s_access, ui_state
from web.common import error_fragment
from web.ws import sock

bp = Blueprint("k8s", __name__)
FAILED = ("CrashLoopBackOff", "Error", "ImagePullBackOff", "ErrImagePull", "OOMKilled")


def pod_state(p: K8sAPI.PodInfo) -> str:
    if p.status == "Running" and p.ready.split("/")[0] == p.ready.split("/")[1]:
        return "Running"
    return "failed" if p.status in FAILED else "Pending"


def _envs() -> list[str]:
    return relations.k8s_envs()


def _env() -> str:
    env = request.values.get("env", "")
    envs = _envs()
    return env if env in envs else (envs[0] if envs else "")


def _lines() -> int:
    try:
        return max(10, min(5000, int(request.values.get("lines", ENV.K8S_LOG_LINES))))
    except ValueError:
        return ENV.K8S_LOG_LINES


@bp.get("/")
def page():
    ui_state.update(last_page="k8s")
    env = _env()
    return render_template("k8s/page.html", envs=_envs(), env=env, app=request.args.get("app", ""),
                           apps=[a.name for a in relations.catalog()], demo=K8sAPI.is_demo(),
                           warn=relations.prod_warning("prod") if env == "prod" else "", ns=ENV.K8S_NAMESPACE)


@bp.get("/list")
def listing():
    env, app_name = _env(), request.args.get("app", "")
    if not env:
        return render_template("_error.html", message="Kubernetes ortamı yok.")
    try:
        if app_name:
            w = k8s_access.workload(app_name, env)
            pods, how, found = w.pods, w.how, w.found
        else:
            pods = [K8sAPI.pod_info(p) for p in k8s_access.cluster(env).pods()]
            how, found = "", True
    except Exception as e:   # noqa: BLE001
        return error_fragment(e, env)
    return render_template("k8s/_list.html", env=env, app=app_name, pods=pods, how=how, found=found,
                           pod_state=pod_state, forbidden=k8s_access.forbidden(env))


@bp.get("/pod")
def pod():
    env, name = _env(), request.args.get("pod", "")
    try:
        p = next((K8sAPI.pod_info(x) for x in k8s_access.cluster(env).pods() if x["metadata"]["name"] == name), None)
    except Exception as e:   # noqa: BLE001
        return error_fragment(e, name)
    if not p:
        return render_template("_error.html", message=f"Pod bulunamadı: {name}")
    container = request.args.get("container") or (p.containers[0] if p.containers else "")
    return render_template("k8s/_pod.html", env=env, p=p, container=container, lines=ENV.K8S_LOG_LINES,
                           kubectl=env in relations.kubectl_envs(), state=pod_state(p),
                           app=request.args.get("app", ""))


@bp.get("/logs")
def logs():
    env, name, container = _env(), request.args.get("pod", ""), request.args.get("container", "")
    lines, previous = _lines(), bool(request.args.get("previous"))
    try:
        text, error = k8s_access.cluster(env).logs(name, container, lines, previous), ""
    except Exception as e:   # noqa: BLE001
        text, error = "", redact(str(e))
    return render_template("k8s/_logs.html", env=env, pod=name, container=container, lines=lines,
                           previous=previous, text=text, error=error)


@sock.route("/ws/logs", bp=bp)
def logs_ws(ws):
    """Canlı log: önce son N satır, sonra yeni satırlar. İstemci kapatınca akış durur."""
    env, name, container = _env(), request.args.get("pod", ""), request.args.get("container", "")
    lines, previous = _lines(), bool(request.args.get("previous"))
    stop = threading.Event()

    def watch_client():
        try:
            while ws.receive() is not None:
                pass
        except Exception:   # noqa: BLE001
            pass
        stop.set()

    threading.Thread(target=watch_client, daemon=True).start()
    try:
        for chunk in k8s_access.stream_logs(env, name, container, lines, previous, stop):
            if stop.is_set():
                break
            ws.send(chunk)
    finally:
        stop.set()


@bp.get("/events")
def events():
    env, name = _env(), request.args.get("pod", "")
    try:
        rows = K8sAPI.event_rows(k8s_access.cluster(env).events(name))
    except Exception as e:   # noqa: BLE001
        return error_fragment(e, "Olaylar")
    return render_template("k8s/_events.html", rows=rows, forbidden="Event" in k8s_access.forbidden(env))
