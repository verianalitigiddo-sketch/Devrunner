"""
Freeze -> scripts/FreezeAPI.py. Ay takvimi sunucuda üretilir; dönem ekle / sil; başlıktaki rozet
relations.freeze_status()'tan gelir (ekleme / silmeden sonra qt.call(relations.refresh_shared)).
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from flask import Blueprint, render_template, request

from scripts import FreezeAPI, relations
from scripts.fmt import today
from web import ui_state
from web.common import db_problem, error_fragment, error_text, form_int, qt, toast, trigger

bp = Blueprint("freeze", __name__)
MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
DOW = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]


def _month() -> date:
    raw = request.args.get("m", "")
    try:
        y, mo = (int(x) for x in raw.split("-"))
        return date(y, mo, 1)
    except ValueError:
        t = today()
        return date(t.year, t.month, 1)


def _calendar(first: date, items: list[FreezeAPI.Freeze]) -> list[list[dict]]:
    weeks = []
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(first.year, first.month):
        row = []
        for d in week:
            hits = [f for f in items if f.contains(d)]
            row.append({"d": d, "out": d.month != first.month, "today": d == today(), "hits": hits})
        weeks.append(row)
    return weeks


@bp.get("/")
def page():
    ui_state.update(last_page="freeze")
    return render_template("freeze/page.html", problem=db_problem(), month=_month().strftime("%Y-%m"), today=today())


@bp.get("/body")
def body():
    problem = db_problem()
    if problem:
        return render_template("_error.html", message=problem)
    first = _month()
    try:
        items = FreezeAPI.list_freezes()
    except Exception as e:   # noqa: BLE001
        return error_fragment(e, "Freeze")
    prev = (first - timedelta(days=1)).replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    t = today()
    rows = []
    for f in items:
        state = "Devam ediyor" if f.contains(t) else "Geçti" if f.end < t else "Gelecek"
        rows.append({"f": f, "state": state, "days": (f.end - f.start).days + 1,
                     "role": {"Devam ediyor": "danger", "Geçti": "missing", "Gelecek": "warning"}[state]})
    return render_template("freeze/_body.html", weeks=_calendar(first, items), title=f"{MONTHS[first.month - 1]} {first.year}",
                           prev=prev.strftime("%Y-%m"), next=nxt.strftime("%Y-%m"), now=t.strftime("%Y-%m"), dow=DOW,
                           rows=rows, st=relations.freeze_status(), today=t)


@bp.post("/add")
def add():
    try:
        start = date.fromisoformat(request.form.get("start", ""))
        end = date.fromisoformat(request.form.get("end", "") or request.form.get("start", ""))
        FreezeAPI.add_freeze(start, end, request.form.get("title", ""))
    except ValueError as e:
        return "", 204, toast(str(e) if "date" not in str(e) else "Tarih geçersiz.", "error")
    except Exception as e:   # noqa: BLE001
        return "", 204, toast(error_text(e), "error")
    qt.call(relations.refresh_shared)    # başlık rozeti hemen güncellensin
    return "", 204, trigger("freeze_saved", toast_message="Freeze dönemi eklendi.")


@bp.post("/delete")
def delete():
    fid = form_int("id")
    try:
        FreezeAPI.delete_freeze(fid)
    except Exception as e:   # noqa: BLE001
        return "", 204, toast(error_text(e), "error")
    qt.call(relations.refresh_shared)
    return "", 204, trigger("freeze_saved", toast_message="Freeze dönemi silindi.", level="info")
