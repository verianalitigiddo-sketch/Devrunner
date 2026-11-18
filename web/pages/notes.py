"""
Notlar -> scripts/NotesAPI.py. Ortak geliştirici notları; Markdown sunucuda markdown-it-py ile ham HTML
KAPALI olarak çevrilir, bağlantılar yalnızca http/https (PLAN_WEB.md Bölüm 6).
"""
from __future__ import annotations

from flask import Blueprint, render_template, request
from markdown_it import MarkdownIt
from markupsafe import Markup

from scripts import NotesAPI, database, relations
from scripts.apps import ENVS
from web import ui_state
from web.common import db_problem, error_fragment, form_int, toast, trigger

bp = Blueprint("notes", __name__)


def _md() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False}).enable("table")
    md.validateLink = lambda url: url.strip().lower().startswith(("http://", "https://", "#"))
    default = md.renderer.rules.get("link_open")

    def link_open(tokens, idx, options, env):
        tokens[idx].attrSet("target", "_blank")
        tokens[idx].attrSet("rel", "noopener noreferrer")
        return default(tokens, idx, options, env) if default else md.renderer.renderToken(tokens, idx, options, env)

    md.renderer.rules["link_open"] = link_open
    return md


_MD = _md()


def render_markdown(text: str) -> Markup:
    return Markup(_MD.render(text or ""))


@bp.get("/")
def page():
    ui_state.update(last_page="notes")
    return render_template("notes/page.html", problem=db_problem(), note_id=request.args.get("id", ""),
                           app=request.args.get("app", ""), apps=[a.name for a in relations.catalog()])


@bp.get("/list")
def listing():
    problem = db_problem()
    if problem:
        return render_template("_error.html", message=problem)
    app_name = request.args.get("app")
    scope = None if app_name in (None, "*") else app_name   # "" = yalnızca genel
    try:
        rows = NotesAPI.list_notes(scope, request.args.get("q", ""))
    except Exception as e:   # noqa: BLE001
        return error_fragment(e, "Notlar")
    return render_template("notes/_list.html", rows=rows, active=form_int("id"))


@bp.get("/edit")
def edit():
    problem = db_problem()
    if problem:
        return render_template("_error.html", message=problem)
    note_id = form_int("id")
    note = NotesAPI.get_note(note_id) if note_id else None
    if note_id and not note:
        return render_template("_error.html", message="Not bulunamadı (silinmiş olabilir).")
    history = database.history(entity="note", entity_id=note_id, limit=30) if note else []
    return render_template("notes/_editor.html", note=note, history=history, envs=ENVS,
                           apps=[a.name for a in relations.catalog()], preset_app=request.args.get("app", ""),
                           html=render_markdown(note["body"]) if note else Markup(""),
                           mode="view" if note else "edit")


@bp.post("/preview")
def preview():
    return render_template("notes/_preview.html", html=render_markdown(request.form.get("body", "")))


@bp.post("/save")
def save():
    note_id = form_int("id")
    args = (request.form.get("title", ""), request.form.get("body", ""), request.form.get("app") or None,
            request.form.get("env") or None, bool(request.form.get("pinned")))
    try:
        if note_id:
            NotesAPI.update_note(note_id, *args)
        else:
            note_id = NotesAPI.add_note(*args)
    except ValueError as e:
        return "", 204, toast(str(e), "error")
    except Exception as e:   # noqa: BLE001
        return error_fragment(e, "Not kaydedilemedi")
    note = NotesAPI.get_note(note_id)
    headers = trigger("notes_changed", toast_message="Not kaydedildi.", push_url=f"/notes/?id={note_id}")
    return render_template("notes/_editor.html", note=note, envs=ENVS, apps=[a.name for a in relations.catalog()],
                           history=database.history(entity="note", entity_id=note_id, limit=30),
                           html=render_markdown(note["body"]), mode="view", preset_app=""), 200, headers


@bp.post("/delete")
def delete():
    note_id = form_int("id")
    if note_id:
        try:
            NotesAPI.delete_note(note_id)
        except Exception as e:   # noqa: BLE001
            return error_fragment(e, "Not silinemedi")
    headers = trigger("notes_changed", toast_message="Not silindi.", push_url="/notes/")
    return render_template("notes/_editor.html", note=None, envs=ENVS, apps=[a.name for a in relations.catalog()],
                           history=[], html=Markup(""), mode="edit", preset_app=""), 200, headers
