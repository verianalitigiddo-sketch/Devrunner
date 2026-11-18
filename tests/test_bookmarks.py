"""Bilge yer imleri (scripts/BookmarkAPI.py) ve web akışları — yerel PostgreSQL gerekir (TEST_DB_HOST)."""
from __future__ import annotations

import pytest

from scripts import BilgeAPI, BookmarkAPI

UID = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-0000000000ff"


@pytest.fixture
def bm(db):
    BookmarkAPI._ready_for = None
    BookmarkAPI.ensure_schema()
    return db


def test_schema_does_not_touch_schema_version(bm):
    assert bm.read_sql("SELECT value FROM dr_meta WHERE key = 'schema_version'")[0]["value"] == str(bm.SCHEMA_VERSION)
    assert bm.read_sql("SELECT value FROM dr_meta WHERE key = 'bookmarks_schema'")[0]["value"] == "1"
    assert bm.init_db() == ""          # masaüstü sürüm "daha yeni" uyarısı vermez


def test_folders_place_tags_stars(bm):
    r1 = BookmarkAPI.add_bookmark("Port açılması", "https://bilge.local/t/1", "Musteri360", tags="port, #firewall")
    r2 = BookmarkAPI.add_bookmark("Disk artırımı", "https://bilge.local/t/2")
    legacy = BilgeAPI.add_request("Masaüstünden", "https://bilge.local/t/3")        # yer imi satırı yok
    with pytest.raises(BookmarkAPI.DuplicateError):
        BookmarkAPI.add_bookmark("Aynı", "HTTPS://BILGE.local/t/1/")
    with pytest.raises(ValueError):
        BookmarkAPI.add_bookmark("Kötü", "javascript:alert(1)")

    infra = BookmarkAPI.add_folder("Altyapı İşleri")
    sub = BookmarkAPI.add_folder("Ağ", infra)
    BookmarkAPI.place(r1, sub)
    BookmarkAPI.place(r2, sub, before_id=r1)
    assert [b["id"] for b in BookmarkAPI.list_bookmarks(UID, "folder", sub)] == [r2, r1]
    assert [b["id"] for b in BookmarkAPI.list_bookmarks(UID, "unfiled")] == [legacy]       # masaüstü kaydı

    t = BookmarkAPI.tree(UID)
    node = t["folders"][0]
    assert node.name == "Altyapı İşleri" and node.total == 2 and node.children[0].count == 2
    assert BookmarkAPI.folder_path(sub, t["by_id"]) == "Altyapı İşleri / Ağ"
    with pytest.raises(ValueError):
        BookmarkAPI.move_folder(infra, sub)                                   # kendi altına taşınamaz

    assert BookmarkAPI.set_tags(r1, "port, Port, firewall ") == ["port", "firewall"]
    assert [b["id"] for b in BookmarkAPI.list_bookmarks(UID, tag="firewall")] == [r1]
    assert [b["id"] for b in BookmarkAPI.list_bookmarks(UID, search="firewall")] == [r1]

    BookmarkAPI.star(UID, r2)
    assert [b["id"] for b in BookmarkAPI.starred(UID)] == [r2]
    assert BookmarkAPI.starred(OTHER) == []                                    # yıldız kişisel
    assert BookmarkAPI.tree(UID)["smart"]["starred"] == 1

    BookmarkAPI.delete_folder(sub)                                             # içerik üst klasöre taşınır
    assert {b["id"] for b in BookmarkAPI.list_bookmarks(UID, "folder", infra)} == {r1, r2}
    assert BilgeAPI.list_requests()                                            # talepler silinmedi
    for r in BookmarkAPI.for_app("Musteri360", UID):
        assert r["folder_path"] == "Altyapı İşleri"
    actions = {h["action"] for h in bm.history(module="bilge", limit=100)}
    assert {"create", "move", "update", "star", "delete"} <= actions


def test_netscape_import_export_roundtrip(bm):
    html = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<DL><p>
  <DT><H3>İş Talepleri</H3>
  <DL><p>
    <DT><A HREF="https://bilge.local/t/10">Talep &amp; 10</A>
    <DT><H3>Prod</H3>
    <DL><p><DT><A HREF="https://bilge.local/t/11">Prod talebi</A></DL><p>
    <DT><A HREF="javascript:alert(1)">Kötü</A>
  </DL><p>
  <DT><A HREF="https://bilge.local/t/12">Kökte</A>
</DL><p>"""
    stats = BookmarkAPI.import_netscape(html)
    assert stats == {"folders": 2, "added": 3, "existing": 0, "skipped": 1}
    again = BookmarkAPI.import_netscape(html)
    assert again["added"] == 0 and again["existing"] == 3
    out = BookmarkAPI.export_netscape(UID)
    assert "<H3>İş Talepleri</H3>" in out and "Talep &amp; 10" in out and "javascript" not in out
    t = BookmarkAPI.tree(UID)
    assert any(c.name == "Prod" for f in t["folders"] for c in f.children)


def test_bulk_parse():
    rows = BookmarkAPI.parse_bulk("Port | https://bilge.local/t/1\nhttps://bilge.local/talep/42\nçöp satır\n")
    assert [r["ok"] for r in rows] == [True, True, False]
    assert rows[0]["title"] == "Port" and rows[1]["title"] == "42"


def test_web_bilge_flow_and_xss(bm):
    from tests.test_web import HOST, TOKEN, make_app, post
    c = make_app().test_client()
    c.get(f"/auth?t={TOKEN}")
    assert "Eklendi" in post(c, "/bilge/add", data={"url": "https://bilge.local/x/1",
                                                     "title": "<script>alert(1)</script>"}).get_data(as_text=True)
    assert "zaten kayıtlı" in post(c, "/bilge/add", data={"url": "https://bilge.local/x/1"}).get_data(as_text=True)
    body = c.get("/bilge/list?view=all").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in body and "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    r = post(c, "/bilge/folder", data={"name": "Türkçe Klasör ğüş"})
    assert "bm_changed" in r.headers["HX-Trigger"]
    r.headers["HX-Trigger"].encode("latin-1")          # başlık Latin-1 ile kodlanabilir (\\u kaçışı)
    assert "Türkçe Klasör ğüş" in c.get("/bilge/tree").get_data(as_text=True)
    exp = c.get("/bilge/export")
    assert exp.headers["Content-Disposition"].startswith("attachment") and b"NETSCAPE" in exp.data


def test_web_notes_markdown_is_safe(bm):
    from tests.test_web import HOST, TOKEN, make_app, post
    c = make_app().test_client()
    c.get(f"/auth?t={TOKEN}")
    body = post(c, "/notes/save", data={"title": "<img src=x onerror=alert(1)>",
                                        "body": "**k** <script>x()</script> [a](javascript:alert(1)) [b](https://e.com)"}
                ).get_data(as_text=True)
    assert "<strong>k</strong>" in body and "<script>x()" not in body and 'href="javascript' not in body
    assert 'href="https://e.com"' in body and "&lt;img src=x onerror=alert(1)&gt;" in body
