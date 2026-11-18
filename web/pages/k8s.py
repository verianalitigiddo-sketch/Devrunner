"""
Kubernetes -> scripts/K8sAPI.py: ortam / uygulama seçimi, pod ve deployment, log (canlı izleme), olaylar, komut.

Prod'da komut: sunucu önce onay parçası döner ve TEK KULLANIMLIK bir onay belirteci üretir (ortam, pod,
container ve komuta bağlı, 2 dk geçerli). Belirteçsiz veya uyuşmayan istek reddedilir; yalnızca
arayüzdeki onay penceresine güvenilmez. Prod komutları değişiklik geçmişine yazılır.
"""
from __future__ import annotations

import secrets
import threading
import time

from flask import Blueprint, render_template, request

from scripts import K8sAPI, database, relations
from scripts.config import ENV, redact
from web import ui_state
from web.common import error_fragment

bp = Blueprint("k8s", __name__)

_confirm: dict[str, tuple] = {}
_confirm_lock = threading.Lock()
CONFIRM_TTL = 120
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
            w = relations.app_workload(app_name, env)
            data = {"deps": [w.deployment] if w.deployment else [], "pods": w.pods, "warnings": w.warnings,
                    "how": w.how, "found": w.found}
        else:
            c = K8sAPI.cluster(env)
            data = {"deps": [], "pods": [], "warnings": [], "how": "", "found": True}
            for kind, fn, conv in (("deps", c.deployments, K8sAPI.deployment_info), ("pods", c.pods, K8sAPI.pod_info)):
                try:
                    data[kind] = [conv(x) for x in fn()]
                except K8sAPI.K8sForbidden as e:
                    data["warnings"].append(str(e))
    except Exception as e:   # noqa: BLE001
        return error_fragment(e, env)
    return render_template("k8s/_list.html", env=env, app=app_name, pod_state=pod_state, **data)


@bp.get("/pod")
def pod():
    env, name = _env(), request.args.get("pod", "")
    try:
        p = next((K8sAPI.pod_info(x) for x in K8sAPI.cluster(env).pods() if x["metadata"]["name"] == name), None)
    except Exception as e:   # noqa: BLE001
        return error_fragment(e, name)
    if not p:
        return render_template("_error.html", message=f"Pod bulunamadı: {name}")
    container = request.args.get("container") or (p.containers[0] if p.containers else "")
    return render_template("k8s/_pod.html", env=env, p=p, container=container, lines=ENV.K8S_LOG_LINES,
                           kubectl=env in relations.kubectl_envs(), state=pod_state(p))


@bp.get("/logs")
def logs():
    env, name, container = _env(), request.args.get("pod", ""), request.args.get("container", "")
    lines = max(10, min(5000, int(request.args.get("lines", ENV.K8S_LOG_LINES) or ENV.K8S_LOG_LINES)))
    previous = bool(request.args.get("previous"))
    follow = bool(request.args.get("follow"))
    try:
        text = K8sAPI.cluster(env).logs(name, container, lines, previous)
        error = ""
    except Exception as e:   # noqa: BLE001
        text, error = "", redact(str(e))
    return render_template("k8s/_logs.html", env=env, pod=name, container=container, lines=lines, previous=previous,
                           follow=follow, text=text, error=error)


@bp.get("/events")
def events():
    env, name = _env(), request.args.get("pod", "")
    try:
        rows = K8sAPI.event_rows(K8sAPI.cluster(env).events(name))
    except Exception as e:   # noqa: BLE001
        return error_fragment(e, "Olaylar")
    return render_template("k8s/_events.html", rows=rows)


def _issue_confirm(key: tuple) -> str:
    token = secrets.token_urlsafe(16)
    now = time.time()
    with _confirm_lock:
        for k in [k for k, v in _confirm.items() if v[1] < now]:
            del _confirm[k]
        _confirm[token] = (key, now + CONFIRM_TTL)
    return token


def _use_confirm(token: str, key: tuple) -> bool:
    with _confirm_lock:
        entry = _confirm.pop(token, None)
    return bool(entry) and entry[0] == key and entry[1] >= time.time()


@bp.post("/exec")
def execute():
    env = _env()
    pod_name, container = request.form.get("pod", ""), request.form.get("container", "")
    command = request.form.get("command", "").strip()
    app_name = request.form.get("app") or None
    if not (pod_name and container and command):
        return render_template("_error.html", message="Pod, container ve komut gerekli.")
    key = (env, pod_name, container, command)
    if env == "prod":
        token = request.form.get("confirm", "")
        if not token or not _use_confirm(token, key):
            if token:   # süresi dolmuş / uyuşmayan belirteç: yeniden onay iste
                return render_template("_error.html", title="Onay geçersiz", message="Onay süresi dolmuş veya komut değişmiş.")
            return render_template("k8s/_confirm.html", env=env, pod=pod_name, container=container, command=command,
                                   app=app_name or "", token=_issue_confirm(key),
                                   warn=relations.prod_warning("prod"))
    try:
        out = K8sAPI.cluster(env).exec(pod_name, container, command)
        if env == "prod":
            database.audit_now("k8s", "exec", "pod", pod_name, {"title": redact(command)[:200], "env": env}, app_name)
        error = ""
    except Exception as e:   # noqa: BLE001
        out, error = "", redact(str(e))
    return render_template("k8s/_exec_out.html", env=env, pod=pod_name, container=container, command=command,
                           out=out, error=error)
