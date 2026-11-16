"""
Ortak geliştirici notları (dr_notes). Not genel olabilir veya bir uygulamaya (ve ortama) bağlanır.
Herkes ekler ve düzenler; her değişiklik dr_audit_log'a düşer; silme deleted_at ile yapılır.
"""
from __future__ import annotations

from sqlalchemy import text

from scripts.database import audit, read_sql, rows, transaction
from scripts.user import current_user

_COLUMNS = "id, app_name, env, title, body, pinned, created_by, created_at, updated_by, updated_at"


def list_notes(app_name: str | None = None, search: str = "") -> list[dict]:
    sql = f"SELECT {_COLUMNS} FROM dr_notes WHERE deleted_at IS NULL"
    params: dict = {}
    if app_name == "":           # yalnızca genel notlar
        sql += " AND app_name IS NULL"
    elif app_name:
        sql += " AND app_name = :app"
        params["app"] = app_name
    if search.strip():
        sql += " AND (title ILIKE :q OR body ILIKE :q)"
        params["q"] = f"%{search.strip()}%"
    return read_sql(sql + " ORDER BY pinned DESC, COALESCE(updated_at, created_at) DESC", params)


def get_note(note_id: int) -> dict | None:
    r = read_sql(f"SELECT {_COLUMNS} FROM dr_notes WHERE id = :id AND deleted_at IS NULL", {"id": note_id})
    return r[0] if r else None


def _clean(title: str, body: str) -> tuple[str, str]:
    title = title.strip()
    if not title:
        raise ValueError("Başlık boş olamaz.")
    return title[:200], body.rstrip()


def add_note(title: str, body: str, app_name: str | None = None, env: str | None = None,
             pinned: bool = False) -> int:
    title, body = _clean(title, body)
    user = current_user().name
    with transaction() as conn:
        note_id = conn.execute(text("""
            INSERT INTO dr_notes (app_name, env, title, body, pinned, created_by)
            VALUES (:app, :env, :title, :body, :pinned, :by) RETURNING id"""),
            {"app": app_name or None, "env": env or None, "title": title, "body": body, "pinned": pinned,
             "by": user}).scalar()
        audit(conn, "notes", "create", "note", note_id, {"title": title}, app_name)
    return int(note_id)


def update_note(note_id: int, title: str, body: str, app_name: str | None = None, env: str | None = None,
                pinned: bool = False) -> None:
    title, body = _clean(title, body)
    with transaction() as conn:
        old = rows(conn, "SELECT title, body, app_name, env, pinned FROM dr_notes WHERE id = :id AND deleted_at IS NULL",
                   {"id": note_id})
        if not old:
            raise ValueError("Not bulunamadı (silinmiş olabilir).")
        new = {"title": title, "body": body, "app_name": app_name or None, "env": env or None, "pinned": pinned}
        changed = [k for k, v in new.items() if old[0][k] != v]
        if not changed:
            return
        conn.execute(text("""UPDATE dr_notes SET title = :title, body = :body, app_name = :app_name, env = :env,
                             pinned = :pinned, updated_by = :by, updated_at = now() WHERE id = :id"""),
                     {**new, "by": current_user().name, "id": note_id})
        audit(conn, "notes", "update", "note", note_id, {"title": title, "fields": changed}, app_name or old[0]["app_name"])


def delete_note(note_id: int) -> None:
    with transaction() as conn:
        old = rows(conn, "SELECT title, app_name FROM dr_notes WHERE id = :id AND deleted_at IS NULL", {"id": note_id})
        if not old:
            return
        conn.execute(text("UPDATE dr_notes SET deleted_at = now(), updated_by = :by WHERE id = :id"),
                     {"id": note_id, "by": current_user().name})
        audit(conn, "notes", "delete", "note", note_id, {"title": old[0]["title"]}, old[0]["app_name"])


def counts_by_app() -> dict[str, int]:
    return {r["app_name"]: r["n"] for r in read_sql(
        "SELECT app_name, count(*) AS n FROM dr_notes WHERE deleted_at IS NULL AND app_name IS NOT NULL GROUP BY app_name")}
