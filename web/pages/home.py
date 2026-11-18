"""
Uygulamalar: açılış sayfası ve modüllerin kesişimi. Yalnızca scripts/relations.py kullanılır
(yer imi yolu için BookmarkAPI: çatala özgü yeni backend dosyası).

Seçili uygulama adres durumudur: /home/?app=<ad>. Ayrıntıdaki K8s ve ortak veri bölümleri ayrı
parçalar olarak yüklenir (yavaş servis sayfayı bekletmez).
"""
from __future__ import annotations

import threading
import time

from flask import Blueprint, abort, render_template, request

from scripts import BookmarkAPI, database, relations
from scripts.apps import ENVS
from scripts.DevopsAPI import STATE_TITLES as PIPE_TITLES
from scripts.HealthAPI import STATE_TITLES as HEALTH_TITLES
from web import k8s_access, ui_state
from web.common import current_query, db_problem, error_fragment, hub, qt, toast, uid

bp = Blueprint("home", __name__)

MODULE_TITLES = {"notes": "Not", "bilge": "Bilge", "freeze": "Freeze", "settings": "Ayar", "apps": "Uygulama",
                 "k8s": "Kubernetes"}
ACTION_TITLES = {"create": "ekledi", "update": "güncelledi", "delete": "sildi", "override": "eşlemeyi değiştirdi",
                 "exec": "komut çalıştırdı", "move": "taşıdı", "star": "yıldızladı", "unstar": "yıldızı kaldırdı"}

# Pod özeti yavaş olabilir (her ortamda iki K8s çağrısı): arka planda hesaplanır, 60 sn önbelleklenir
_pods: dict = {"data": {}, "at": 0.0, "busy": False}
_pods_lock = threading.Lock()


def pods_cached() -> dict:
    with _pods_lock:
        stale = time.time() - _pods["at"] > 60
        if stale and not _pods["busy"]:
            _pods["busy"] = True
            h = hub()
            threading.Thread(target=_compute_pods, args=(h,), daemon=True).start()
        return _pods["data"]


def _compute_pods(h) -> None:
    try:
        data = k8s_access.pod_summary()
    except Exception:   # noqa: BLE001 - sütun boş kalır
        data = {}
    with _pods_lock:
        _pods.update(data=data, at=time.time(), busy=False)
    h.publish("pods_updated", {})


def _ctx():
    return {"PIPE_TITLES": PIPE_TITLES, "HEALTH_TITLES": HEALTH_TITLES, "MODULE_TITLES": MODULE_TITLES,
            "ACTION_TITLES": ACTION_TITLES}


@bp.get("/")
def page():
    ui_state.update(last_page="home")
    names = [a.name for a in relations.catalog()]
    selected = request.args.get("app") or (names[0] if names else "")
    return render_template("home/page.html", selected=selected, tab=request.args.get("tab", "apps"),
                           source=relations.catalog_source(), overview_error=relations.overview_error(), **_ctx())


@bp.get("/table")
def table():
    q = request.args.get("q", "").strip().lower()
    counts = ({}, {})
    if database.is_ready():
        try:
            counts = relations.app_counts()
        except Exception:   # noqa: BLE001 - sayaçlar olmadan da tablo çalışır
            pass
    rows = []
    for a in relations.catalog():
        if q and q not in a.name.lower():
            continue
        rows.append({"app": a, "pipes": relations.app_pipelines(a.name), "health": relations.app_health(a.name)})
    return render_template("home/_table.html", rows=rows, notes=counts[0], reqs=counts[1], pods=pods_cached(),
                           selected=current_query("app"), **_ctx())


@bp.get("/app/<name>")
def detail(name):
    a = relations.app(name)
    if not a:
        return render_template("_error.html", title=name, message="Uygulama katalogda yok.")
    return render_template("home/_detail.html", a=a, p=relations.app_pipelines(name), health=relations.app_health(name),
                           muted=a.muted_envs(), warn=relations.prod_warning("cd"), envs=ENVS, **_ctx())


@bp.get("/app/<name>/k8s")
def detail_k8s(name):
    out, errors = {}, []
    for env in relations.k8s_envs():
        try:
            out[env] = k8s_access.workload(name, env)
        except Exception as e:   # noqa: BLE001
            errors.append(f"{env}: {e}")
    return render_template("home/_k8s.html", workloads=out, errors=errors, name=name)


@bp.get("/app/<name>/shared")
def detail_shared(name):
    problem = db_problem()
    if problem:
        return render_template("_error.html", title="Notlar ve talepler", message=problem)
    try:
        notes = relations.app_notes(name)
        bookmarks = BookmarkAPI.for_app(name, uid())
        history = relations.app_history(name, 15)
    except Exception as e:   # noqa: BLE001
        return error_fragment(e, "Ortak veri")
    return render_template("home/_shared.html", notes=notes, bookmarks=bookmarks, history=history, name=name, **_ctx())


@bp.route("/app/<name>/override", methods=["GET", "POST"])
def override(name):
    a = relations.app(name)
    if not a:
        abort(404)
    if request.method == "POST":
        problem = db_problem()
        if problem:
            return render_template("_error.html", message=problem)
        values = {k: request.form.get(k, "") for k in ("url_dev", "url_test", "url_prod", "k8s_workload")}
        try:
            qt.call(relations.set_override, name, values)
        except Exception as e:   # noqa: BLE001
            return error_fragment(e, "Eşleme kaydedilemedi")
        headers = toast("Adres / eşleme kaydedildi.")
        headers["HX-Refresh"] = "true"
        return "", 204, headers
    from scripts import apps as catalog
    return render_template("home/_override.html", a=a, cur=catalog.overrides(name), envs=ENVS)


@bp.get("/history")
def history():
    problem = db_problem()
    if problem:
        return render_template("_error.html", title="Değişiklik geçmişi", message=problem)
    module = request.args.get("module") or None
    app_name = request.args.get("app") or None
    try:
        rows = database.history(app_name=app_name, module=module, limit=300)
    except Exception as e:   # noqa: BLE001
        return error_fragment(e, "Değişiklik geçmişi")
    return render_template("home/_history.html", rows=rows, **_ctx())
