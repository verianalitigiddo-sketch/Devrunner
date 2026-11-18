"""
Bilge -> scripts/BilgeAPI.py (linkler) + scripts/BookmarkAPI.py (yer imi düzeni) — PLAN_WEB.md Bölüm 8.

Sol: akıllı klasörler, uygulamaya göre, ekip klasörleri (sürükle-bırak). Üst: kişisel ★ yer imi çubuğu.
Orta: hızlı ekleme, süzgeçler, yer imleri. İçe / dışa aktarma: Netscape HTML (Edge / Chrome).
Her değişiklikten sonra istemcide "bm_changed" tetiklenir; ağaç, çubuk ve liste kendini yeniler.
"""
from __future__ import annotations

from datetime import datetime

from flask import Blueprint, Response, render_template, request

from scripts import BilgeAPI, BookmarkAPI, relations
from web import ui_state
from web.common import db_problem, error_fragment, error_text, form_int, toast, trigger, uid

bp = Blueprint("bilge", __name__)

STATUS_ROLE = {"Açık": "info", "Devam ediyor": "warning", "Kapandı": "missing"}
NEXT_STATUS = {"Açık": "Devam ediyor", "Devam ediyor": "Kapandı", "Kapandı": "Açık"}
VIEWS = set(BookmarkAPI.SMART_VIEWS) | {"folder", "app"}


def _state() -> dict:
    v = request.values
    view = v.get("view", "all")
    if view not in VIEWS:
        view = "all"
    return {"view": view, "folder": form_int("folder"), "app": v.get("app", ""), "q": v.get("q", ""),
            "tag": v.get("tag", ""), "status": v.get("status", "")}


def _changed(message: str, level: str = "success", *extra: str):
    return trigger("bm_changed", *extra, toast_message=message, level=level)


def _flat_folders(nodes, depth=0, out=None) -> list[tuple[int, str]]:
    out = [] if out is None else out
    for n in nodes:
        out.append((n.id, "  " * depth + ("└ " if depth else "") + n.name))
        _flat_folders(n.children, depth + 1, out)
    return out


@bp.get("/")
def page():
    ui_state.update(last_page="bilge")
    st = _state()
    problem = db_problem()
    title = BookmarkAPI.SMART_VIEWS.get(st["view"], "")
    folders: list = []
    tags: list = []
    if not problem:
        try:
            t = BookmarkAPI.tree(uid())
            folders = _flat_folders(t["folders"])
            tags = BookmarkAPI.all_tags()
            if st["view"] == "folder" and st["folder"] in t["by_id"]:
                title = BookmarkAPI.folder_path(st["folder"], t["by_id"])
            elif st["view"] == "app":
                title = st["app"]
        except Exception as e:   # noqa: BLE001
            problem = error_text(e)
    return render_template("bilge/page.html", st=st, problem=problem, title=title, folders=folders, tags=tags,
                           statuses=BilgeAPI.STATUSES, apps=[a.name for a in relations.catalog()])


@bp.get("/tree")
def tree():
    st = _state()
    try:
        t = BookmarkAPI.tree(uid())
    except Exception as e:   # noqa: BLE001
        return error_fragment(e, "Klasörler")
    return render_template("bilge/_tree.html", t=t, st=st, smart=BookmarkAPI.SMART_VIEWS)


@bp.get("/bar")
def bar():
    try:
        items = BookmarkAPI.starred(uid())
    except Exception as e:   # noqa: BLE001
        return error_fragment(e)
    return render_template("bilge/_bar.html", items=items)


@bp.get("/list")
def listing():
    st = _state()
    try:
        items = BookmarkAPI.list_bookmarks(uid(), st["view"], st["folder"], st["app"] or None, st["q"], st["tag"],
                                           st["status"])
        by_id = {}
        if st["view"] != "folder":
            t = BookmarkAPI.tree(uid())
            by_id = t["by_id"]
        for it in items:
            it["folder_path"] = BookmarkAPI.folder_path(it["folder_id"], by_id) if by_id and it["folder_id"] else ""
    except Exception as e:   # noqa: BLE001
        return error_fragment(e, "Yer imleri")
    return render_template("bilge/_list.html", items=items, st=st, role=STATUS_ROLE,
                           sortable=st["view"] in ("folder", "unfiled") and not (st["q"] or st["tag"] or st["status"]))


# ---------------------------------------------------------------------------
# Ekleme
# ---------------------------------------------------------------------------
@bp.post("/add")
def add():
    st = _state()
    url, title = request.form.get("url", "").strip(), request.form.get("title", "").strip()
    folder = st["folder"] if st["view"] == "folder" else None
    app_name = request.form.get("target_app") or (st["app"] if st["view"] == "app" else None)
    if not title and BilgeAPI.valid_url(url):
        title = BookmarkAPI.parse_bulk(url)[0]["title"]
    try:
        BookmarkAPI.add_bookmark(title, url, app_name, folder_id=folder, tags=request.form.get("tags", ""))
    except BookmarkAPI.DuplicateError as e:
        return render_template("bilge/_quick_msg.html", dup=e.existing), 200, toast(str(e), "warning")
    except ValueError as e:
        return render_template("bilge/_quick_msg.html", error=str(e)), 200
    except Exception as e:   # noqa: BLE001
        return error_fragment(e, "Eklenemedi")
    return render_template("bilge/_quick_msg.html", ok=title), 200, _changed("Talep eklendi.", "success", "quick_added")


@bp.post("/bulk/preview")
def bulk_preview():
    rows = BookmarkAPI.parse_bulk(request.form.get("text", ""))
    for r in rows:
        r["dup"] = BookmarkAPI.find_by_url(r["url"]) if r["ok"] else None
    return render_template("bilge/_bulk_preview.html", rows=rows)


@bp.post("/bulk/save")
def bulk_save():
    st = _state()
    folder = st["folder"] if st["view"] == "folder" else None
    app_name = request.form.get("target_app") or None
    added = skipped = 0
    for r in BookmarkAPI.parse_bulk(request.form.get("text", "")):
        if not r["ok"]:
            skipped += 1
            continue
        try:
            BookmarkAPI.add_bookmark(r["title"], r["url"], app_name, folder_id=folder)
            added += 1
        except ValueError:
            skipped += 1
    return "", 204, _changed(f"{added} talep eklendi, {skipped} satır atlandı.", "success", "close_modal")


# ---------------------------------------------------------------------------
# Düzenleme
# ---------------------------------------------------------------------------
def _request_row(rid: int) -> dict | None:
    return next((r for r in BookmarkAPI.list_bookmarks(uid()) if r["id"] == rid), None)


@bp.get("/edit/<int:rid>")
def edit_form(rid):
    r = _request_row(rid)
    if not r:
        return render_template("_error.html", message="Talep bulunamadı.")
    t = BookmarkAPI.tree(uid())
    return render_template("bilge/_edit.html", r=r, folders=_flat_folders(t["folders"]), statuses=BilgeAPI.STATUSES,
                           apps=[a.name for a in relations.catalog()])


@bp.post("/edit/<int:rid>")
def edit(rid):
    r = _request_row(rid)
    if not r:
        return "", 204, toast("Talep bulunamadı.", "error")
    f = request.form
    try:
        BilgeAPI.update_request(rid, f.get("title", ""), f.get("url", ""), f.get("app") or None,
                                f.get("status", r["status"]), f.get("description", ""))
        BookmarkAPI.set_tags(rid, f.get("tags", ""))
        folder = form_int("folder_id")
        if folder != r["folder_id"]:
            BookmarkAPI.place(rid, folder)
    except ValueError as e:
        return render_template("_error.html", message=str(e))
    except Exception as e:   # noqa: BLE001
        return error_fragment(e, "Kaydedilemedi")
    return "", 204, _changed("Talep güncellendi.", "success", "close_modal")


@bp.post("/status/<int:rid>")
def cycle_status(rid):
    r = _request_row(rid)
    if not r:
        return "", 204, toast("Talep bulunamadı.", "error")
    new = NEXT_STATUS.get(r["status"], "Açık")
    try:
        BilgeAPI.update_request(rid, r["title"], r["url"], r["app_name"], new, r["description"] or "")
    except Exception as e:   # noqa: BLE001
        return "", 204, toast(error_text(e), "error")
    return "", 204, _changed(f"Durum: {new}")


@bp.post("/star/<int:rid>")
def star(rid):
    on = request.form.get("on") == "1"
    try:
        BookmarkAPI.star(uid(), rid, on)
    except Exception as e:   # noqa: BLE001
        return "", 204, toast(error_text(e), "error")
    return "", 204, _changed("Yıldızlandı." if on else "Yıldız kaldırıldı.", "info")


@bp.post("/delete/<int:rid>")
def delete(rid):
    try:
        BilgeAPI.delete_request(rid)
    except Exception as e:   # noqa: BLE001
        return "", 204, toast(error_text(e), "error")
    return "", 204, _changed("Talep listeden kaldırıldı (Bilge'deki talep etkilenmez).", "info")


@bp.post("/place")
def place():
    rid = form_int("request_id")
    folder = form_int("folder_id")
    before = form_int("before_id")
    try:
        BookmarkAPI.place(rid, folder, before)
    except Exception as e:   # noqa: BLE001
        return "", 204, toast(error_text(e), "error")
    return "", 204, _changed("Taşındı.", "info")


# ---------------------------------------------------------------------------
# Klasörler
# ---------------------------------------------------------------------------
def _prompt_value(field: str = "name") -> str:
    """Ad form gövdesinde gelir (static/js/bilge.js: data-prompt-post)."""
    return request.form.get(field, "").strip()


@bp.post("/folder")
def add_folder():
    name = _prompt_value()
    if not name:
        return "", 204
    try:
        BookmarkAPI.add_folder(name, form_int("parent_id"))
    except Exception as e:   # noqa: BLE001
        return "", 204, toast(error_text(e), "error")
    return "", 204, _changed(f"Klasör oluşturuldu: {name}")


@bp.post("/folder/<int:fid>/rename")
def rename_folder(fid):
    name = _prompt_value()
    if not name:
        return "", 204
    try:
        BookmarkAPI.rename_folder(fid, name)
    except Exception as e:   # noqa: BLE001
        return "", 204, toast(error_text(e), "error")
    return "", 204, _changed("Klasör yeniden adlandırıldı.")


@bp.post("/folder/<int:fid>/move")
def move_folder(fid):
    try:
        BookmarkAPI.move_folder(fid, form_int("parent_id"))
    except Exception as e:   # noqa: BLE001
        return "", 204, toast(error_text(e), "error")
    return "", 204, _changed("Klasör taşındı.", "info")


@bp.post("/folder/<int:fid>/delete")
def delete_folder(fid):
    try:
        BookmarkAPI.delete_folder(fid)
    except Exception as e:   # noqa: BLE001
        return "", 204, toast(error_text(e), "error")
    headers = _changed("Klasör silindi; içindekiler üst klasöre taşındı.", "info")
    headers["HX-Redirect"] = "/bilge/?view=all"
    return "", 204, headers


# ---------------------------------------------------------------------------
# İçe / dışa aktarma (Netscape HTML)
# ---------------------------------------------------------------------------
@bp.post("/import")
def import_file():
    f = request.files.get("file")
    if not f or not f.filename:
        return "", 204, toast("Dosya seçin.", "warning")
    raw = f.read(5 * 1024 * 1024)
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = raw.decode("cp1254", errors="replace")
    st = _state()
    try:
        stats = BookmarkAPI.import_netscape(content, st["folder"] if st["view"] == "folder" else None)
    except Exception as e:   # noqa: BLE001
        return "", 204, toast(error_text(e), "error")
    msg = (f"{stats['added']} yeni talep, {stats['existing']} mevcut (yerleştirildi), {stats['folders']} klasör; "
           f"{stats['skipped']} bağlantı atlandı (yalnız http/https).")
    return "", 204, _changed(msg, "success", "close_modal")


@bp.get("/export")
def export():
    fid = form_int("folder")
    try:
        html = BookmarkAPI.export_netscape(uid(), fid)
    except Exception as e:   # noqa: BLE001
        return error_fragment(e, "Dışa aktarılamadı")
    name = f"devrunner-bilge-{datetime.now():%Y%m%d}.html"
    return Response(html, mimetype="text/html; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
