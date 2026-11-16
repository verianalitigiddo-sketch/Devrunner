"""
Bilge talepleri (dr_bilge_requests): Bilge'de açılan taleplerin başlık + link listesi.
Yalnızca http/https adresleri kabul edilir.
"""
from __future__ import annotations

from urllib.parse import urlparse

from sqlalchemy import text

from scripts.database import audit, read_sql, rows, transaction
from scripts.user import current_user

STATUSES = ("Açık", "Devam ediyor", "Kapandı")
_COLUMNS = "id, title, url, app_name, status, description, created_by, created_at, updated_at"


def valid_url(url: str) -> bool:
    p = urlparse(url.strip())
    return p.scheme in ("http", "https") and bool(p.netloc)


def _clean(title: str, url: str, status: str) -> tuple[str, str]:
    title, url = title.strip(), url.strip()
    if not title:
        raise ValueError("Başlık boş olamaz.")
    if not valid_url(url):
        raise ValueError("Adres http:// veya https:// ile başlamalı.")
    if status not in STATUSES:
        raise ValueError("Geçersiz durum.")
    return title[:300], url


def list_requests(app_name: str | None = None, status: str | None = None, search: str = "") -> list[dict]:
    sql = f"SELECT {_COLUMNS} FROM dr_bilge_requests WHERE deleted_at IS NULL"
    params: dict = {}
    if app_name:
        sql += " AND app_name = :app"
        params["app"] = app_name
    if status:
        sql += " AND status = :status"
        params["status"] = status
    if search.strip():
        sql += " AND (title ILIKE :q OR description ILIKE :q OR url ILIKE :q)"
        params["q"] = f"%{search.strip()}%"
    order = "CASE status WHEN 'Açık' THEN 0 WHEN 'Devam ediyor' THEN 1 ELSE 2 END, created_at DESC"
    return read_sql(f"{sql} ORDER BY {order}", params)


def add_request(title: str, url: str, app_name: str | None = None, status: str = "Açık",
                description: str = "") -> int:
    title, url = _clean(title, url, status)
    with transaction() as conn:
        rid = conn.execute(text("""
            INSERT INTO dr_bilge_requests (title, url, app_name, status, description, created_by)
            VALUES (:title, :url, :app, :status, :desc, :by) RETURNING id"""),
            {"title": title, "url": url, "app": app_name or None, "status": status,
             "desc": description.strip() or None, "by": current_user().name}).scalar()
        audit(conn, "bilge", "create", "request", rid, {"title": title, "url": url}, app_name)
    return int(rid)


def update_request(rid: int, title: str, url: str, app_name: str | None, status: str, description: str = "") -> None:
    title, url = _clean(title, url, status)
    with transaction() as conn:
        old = rows(conn, "SELECT status, app_name FROM dr_bilge_requests WHERE id = :id AND deleted_at IS NULL",
                   {"id": rid})
        if not old:
            raise ValueError("Talep bulunamadı (silinmiş olabilir).")
        conn.execute(text("""UPDATE dr_bilge_requests SET title = :title, url = :url, app_name = :app, status = :status,
                             description = :desc, updated_at = now() WHERE id = :id"""),
                     {"title": title, "url": url, "app": app_name or None, "status": status,
                      "desc": description.strip() or None, "id": rid})
        detail = {"title": title}
        if old[0]["status"] != status:
            detail["status"] = f"{old[0]['status']} → {status}"
        audit(conn, "bilge", "update", "request", rid, detail, app_name or old[0]["app_name"])


def delete_request(rid: int) -> None:
    with transaction() as conn:
        old = rows(conn, "SELECT title, app_name FROM dr_bilge_requests WHERE id = :id AND deleted_at IS NULL",
                   {"id": rid})
        if not old:
            return
        conn.execute(text("UPDATE dr_bilge_requests SET deleted_at = now() WHERE id = :id"), {"id": rid})
        audit(conn, "bilge", "delete", "request", rid, {"title": old[0]["title"]}, old[0]["app_name"])


def counts_by_app() -> dict[str, int]:
    return {r["app_name"]: r["n"] for r in read_sql(
        """SELECT app_name, count(*) AS n FROM dr_bilge_requests
           WHERE deleted_at IS NULL AND app_name IS NOT NULL AND status <> 'Kapandı' GROUP BY app_name""")}
