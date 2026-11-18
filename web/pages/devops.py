"""
DevOps -> scripts/DevopsAPI.py (salt okunur). CI/CD geçmişi, düşen adım, log, commit'ler, PR'lar.
Liste relations.overview() önbelleğinden gelir; ayrıntılar istek iş parçacığında DevopsAPI'den çekilir.
"""
from __future__ import annotations

from flask import Blueprint, Response, render_template, request

from scripts import DevopsAPI, relations
from scripts.DevopsAPI import STATE_TITLES
from scripts.config import ENV
from scripts.fmt import parse_dt
from web import ui_state
from web.common import error_fragment, qt, toast

bp = Blueprint("devops", __name__)


@bp.get("/")
def page():
    ui_state.update(last_page="devops")
    names = [a.name for a in relations.catalog()]
    selected = request.args.get("app") or (names[0] if names else "")
    ov = relations.overview()
    return render_template("devops/page.html", selected=selected, ov=ov, error=relations.overview_error(),
                           base=f"{ENV.DEVOPS_URL}/{ENV.DEVOPS_PROJECT}", T=STATE_TITLES)


@bp.post("/refresh")
def refresh():
    qt.call(relations.refresh_catalog)
    return "", 204, toast("DevOps yenileniyor…", "info")


@bp.get("/table")
def table():
    ov = relations.overview()
    rows = [{"app": a, "p": ov.pipelines.get(a.name) if ov else None} for a in relations.catalog()]
    return render_template("devops/_table.html", rows=rows, selected=request.args.get("app", ""), T=STATE_TITLES,
                           fetched=ov.fetched_at if ov else "")


@bp.get("/app/<name>")
def detail(name):
    a = relations.app(name)
    if not a:
        return render_template("_error.html", title=name, message="Uygulama katalogda yok.")
    return render_template("devops/_detail.html", a=a, p=relations.app_pipelines(name),
                           warn=relations.prod_warning("cd"), tab=request.args.get("tab", "ci"), T=STATE_TITLES)


@bp.get("/app/<name>/history/<kind>")
def history(name, kind):
    p = relations.app_pipelines(name)
    pipe = (p.ci if kind == "ci" else p.cd) if p else None
    if pipe is None:
        return render_template("devops/_nopipe.html", label=kind.upper())
    try:
        builds = DevopsAPI.history(pipe)
    except Exception as e:   # noqa: BLE001
        return error_fragment(e, pipe.name)
    return render_template("devops/_history.html", pipe=pipe, builds=builds, T=STATE_TITLES)


@bp.get("/build/<int:build_id>/steps")
def steps(build_id):
    try:
        items = DevopsAPI.timeline(build_id)
    except Exception as e:   # noqa: BLE001
        return error_fragment(e, "Adımlar alınamadı")
    rows = []
    for s in items:
        st, fi = parse_dt(s.started), parse_dt(s.finished)
        rows.append({"s": s, "dur": (fi - st).total_seconds() if st and fi else None})
    return render_template("devops/_steps.html", rows=rows, build_id=build_id,
                           number=request.args.get("n", ""), T=STATE_TITLES)


@bp.get("/build/<int:build_id>/log/<int:log_id>")
def log(build_id, log_id):
    try:
        text = DevopsAPI.build_log(build_id, log_id)
    except Exception as e:   # noqa: BLE001
        return error_fragment(e, "Log alınamadı")
    if request.args.get("download"):
        return Response(text, mimetype="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="build-{build_id}-log-{log_id}.log"'})
    return render_template("devops/_log.html", text=text, build_id=build_id, log_id=log_id,
                           title=request.args.get("t", ""))


@bp.get("/app/<name>/commits")
def commits(name):
    a = relations.app(name)
    try:
        rows = DevopsAPI.commits(a.repo_id) if a and a.repo_id else []
    except Exception as e:   # noqa: BLE001
        return error_fragment(e, "Commit'ler")
    return render_template("devops/_commits.html", rows=rows)


@bp.get("/app/<name>/prs")
def prs(name):
    a = relations.app(name)
    try:
        rows = DevopsAPI.pull_requests(a.repo_id) if a and a.repo_id else []
    except Exception as e:   # noqa: BLE001
        return error_fragment(e, "PR'lar")
    return render_template("devops/_prs.html", rows=rows)
