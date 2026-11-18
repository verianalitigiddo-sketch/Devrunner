"""Sayfaların ortak yardımcıları (masaüstündeki _pages/_common.py'nin web karşılığı)."""
from __future__ import annotations

import logging

from flask import current_app, render_template, request

from scripts import database
from scripts.config import redact
from scripts.user import current_user

log = logging.getLogger("web")


def ext():
    return current_app.extensions["dr"]


class _Qt:
    """Qt ana iş parçacığına çağrı: qt.call(fn, *args) (bkz. web/qt_bridge.py)."""

    def call(self, fn, *args, **kwargs):
        return ext().bridge.call(fn, *args, **kwargs)

    def post(self, fn, *args, **kwargs):
        return ext().bridge.post(fn, *args, **kwargs)


qt = _Qt()


def hub():
    return ext().hub


def is_htmx() -> bool:
    return request.headers.get("HX-Request") == "true"


def uid() -> str:
    return current_user().id


def error_text(e: BaseException | str) -> str:
    text = e if isinstance(e, str) else (str(e) or type(e).__name__)
    return redact(text)


def error_fragment(e: BaseException | str, title: str = "", status: int = 200):
    """Bir bölümün içinde gösterilen hata kartı. Hata o bölümle sınırlı kalır."""
    if isinstance(e, BaseException) and not isinstance(e, (ValueError, database.DbUnavailable)):
        log.warning("%s: %s", title or request.path, error_text(e))
    return render_template("_error.html", title=title, message=error_text(e)), status


def db_problem() -> str:
    """Veritabanı hazır değilse kullanıcıya gösterilecek metin (hazırsa "")."""
    if database.is_ready():
        return ""
    return database.status_text() + " Bu bölüm ortak PostgreSQL gerektirir; diğer modüller çalışmaya devam eder."


def trigger(*events: str, toast_message: str = "", level: str = "success", **headers) -> dict:
    """
    htmx yanıt başlıkları: istemcide olay(lar) tetikler (ör. "bm_changed", "close_modal") ve isteğe bağlı
    bildirim baloncuğu gösterir. Ek başlıklar (HX-Push-Url…) anahtar sözcükle verilir: push_url="/x".
    """
    import json
    data: dict = {e: {} for e in events}
    if toast_message:
        data["toast"] = {"message": toast_message, "level": level}
    out = {"HX-Trigger": json.dumps(data, ensure_ascii=True)} if data else {}   # başlıklar Latin-1: \u kaçışı
    for k, v in headers.items():
        out["HX-" + "-".join(w.capitalize() for w in k.split("_"))] = v
    return out


def toast(message: str, level: str = "success") -> dict:
    """htmx yanıtına eklenecek başlık: istemcide bildirim baloncuğu gösterir."""
    return trigger(toast_message=message, level=level)


def form_int(name: str, default=None):
    v = request.values.get(name, "")
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def current_query(name: str, default: str = "") -> str:
    """
    Seçim gibi durumları sayfanın GÜNCEL adresinden okur (htmx HX-Current-URL başlığı). Parça ilk yüklemedeki
    adresi taşıyorsa, kullanıcı başka satıra tıkladıktan sonraki yenilemede vurgu eski satıra zıplıyordu.
    """
    from urllib.parse import parse_qs, urlparse
    url = request.headers.get("HX-Current-URL")
    if url:
        values = parse_qs(urlparse(url).query).get(name)
        if values:
            return values[0]
    return request.args.get(name, default)
