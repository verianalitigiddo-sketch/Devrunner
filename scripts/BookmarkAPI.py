"""
Bilge yer imleri: talep linklerinin tarayıcı yer imleri gibi düzenlenmesi (PLAN_WEB.md Bölüm 8).

Linkin kendisi dr_bilge_requests'te kalır ve BilgeAPI ile yazılır (doğrulama + audit orada); böylece
masaüstü sürüm aynı talepleri görür. Düzen yan tablolardadır:

    dr_bilge_folders     ekip geneli klasörler (iç içe, yumuşak silme)
    dr_bilge_bookmarks   talep -> klasör, sıra, etiketler (ekip geneli). Satırı olmayan talep "Klasörsüz"
    dr_bilge_stars       kişisel yıldızlar (user_id, request_id)

Şema ensure_schema() ile kurulur ve dr_meta'da kendi anahtarını ('bookmarks_schema') tutar;
'schema_version'a DOKUNMAZ (masaüstü sürüm "veritabanı daha yeni" uyarısı vermesin). Değişiklikler
yalnızca ekleyicidir. Her yazma aynı transaction'da database.audit çağırır.

Bu dosya arayüz import etmez.
"""
from __future__ import annotations

import html
import threading
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlparse

from sqlalchemy import text

from scripts import BilgeAPI
from scripts.database import audit, read_sql, rows, transaction
from scripts.user import current_user

SCHEMA = 1
MAX_TAGS = 10
MAX_TAG_LEN = 30
RECENT_DAYS = 14

SMART_VIEWS = {
    "all": "Tümü",
    "starred": "★ Yıldızlılar",
    "open": "Açık",
    "progress": "Devam eden",
    "closed": "Kapananlar",
    "recent": "Son eklenenler",
    "unfiled": "Klasörsüz",
}

_DDL = [
    """CREATE TABLE IF NOT EXISTS dr_bilge_folders (
        id SERIAL PRIMARY KEY,
        name VARCHAR(200) NOT NULL,
        parent_id INTEGER REFERENCES dr_bilge_folders(id),
        position INTEGER NOT NULL DEFAULT 0,
        created_by VARCHAR(100) NOT NULL,
        created_at TIMESTAMPTZ DEFAULT now(),
        deleted_at TIMESTAMPTZ)""",
    """CREATE TABLE IF NOT EXISTS dr_bilge_bookmarks (
        request_id INTEGER PRIMARY KEY REFERENCES dr_bilge_requests(id),
        folder_id INTEGER REFERENCES dr_bilge_folders(id),
        position INTEGER NOT NULL DEFAULT 0,
        tags TEXT[] NOT NULL DEFAULT '{}',
        updated_by VARCHAR(100),
        updated_at TIMESTAMPTZ DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS dr_bilge_stars (
        user_id VARCHAR(36) NOT NULL,
        request_id INTEGER NOT NULL REFERENCES dr_bilge_requests(id),
        created_at TIMESTAMPTZ DEFAULT now(),
        PRIMARY KEY (user_id, request_id))""",
    "CREATE INDEX IF NOT EXISTS dr_bilge_bookmarks_folder ON dr_bilge_bookmarks (folder_id)",
]

_lock = threading.Lock()
_ready_for: object = None   # şemanın kurulduğu engine (ayar değişince yeniden denenir)


def ensure_schema() -> None:
    """Tabloları (yoksa) oluşturur. Süreç başına ve veritabanı bağlantısı başına bir kez çalışır."""
    global _ready_for
    from scripts.database import get_engine
    engine = get_engine()
    with _lock:
        if _ready_for is engine:
            return
        with transaction() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(7735102)"))
            for ddl in _DDL:
                conn.execute(text(ddl))
            conn.execute(text("""INSERT INTO dr_meta (key, value) VALUES ('bookmarks_schema', :v)
                                 ON CONFLICT (key) DO UPDATE SET value = :v"""), {"v": str(SCHEMA)})
        _ready_for = engine


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------
def clean_tags(tags) -> list[str]:
    if isinstance(tags, str):
        tags = tags.replace(";", ",").split(",")
    out: list[str] = []
    for t in tags or []:
        t = " ".join(str(t).split()).strip().lstrip("#")[:MAX_TAG_LEN]
        if t and t.lower() not in (x.lower() for x in out):
            out.append(t)
    return out[:MAX_TAGS]


def normalize_url(url: str) -> str:
    """Yinelenen kayıt denetimi için: şema + ana makine küçük harf, sondaki / yok."""
    p = urlparse(url.strip())
    return f"{p.scheme.lower()}://{p.netloc.lower()}{p.path.rstrip('/')}{'?' + p.query if p.query else ''}"


def find_by_url(url: str) -> dict | None:
    ensure_schema()
    key = normalize_url(url)
    for r in read_sql("SELECT id, title, url FROM dr_bilge_requests WHERE deleted_at IS NULL"):
        if normalize_url(r["url"]) == key:
            return r
    return None


def _folder(conn, folder_id: int | None) -> dict | None:
    if folder_id is None:
        return None
    r = rows(conn, "SELECT id, name, parent_id FROM dr_bilge_folders WHERE id = :id AND deleted_at IS NULL",
             {"id": folder_id})
    if not r:
        raise ValueError("Klasör bulunamadı (silinmiş olabilir).")
    return r[0]


def _request(conn, request_id: int) -> dict:
    r = rows(conn, "SELECT id, title, app_name FROM dr_bilge_requests WHERE id = :id AND deleted_at IS NULL",
             {"id": request_id})
    if not r:
        raise ValueError("Talep bulunamadı (silinmiş olabilir).")
    return r[0]


# ---------------------------------------------------------------------------
# Okuma
# ---------------------------------------------------------------------------
@dataclass
class FolderNode:
    id: int
    name: str
    parent_id: int | None
    position: int
    count: int = 0
    total: int = 0
    children: list["FolderNode"] = field(default_factory=list)


def tree(user_id: str) -> dict:
    """Klasör ağacı + akıllı klasör sayıları + uygulamaya göre sayılar."""
    ensure_schema()
    folders = read_sql("""SELECT f.id, f.name, f.parent_id, f.position,
                                 (SELECT count(*) FROM dr_bilge_bookmarks b JOIN dr_bilge_requests r ON r.id = b.request_id
                                  WHERE b.folder_id = f.id AND r.deleted_at IS NULL) AS count
                          FROM dr_bilge_folders f WHERE f.deleted_at IS NULL ORDER BY f.position, lower(f.name)""")
    nodes = {f["id"]: FolderNode(f["id"], f["name"], f["parent_id"], f["position"], int(f["count"])) for f in folders}
    roots: list[FolderNode] = []
    for n in nodes.values():
        parent = nodes.get(n.parent_id) if n.parent_id else None
        (parent.children if parent else roots).append(n)

    def total(n: FolderNode) -> int:
        n.total = n.count + sum(total(c) for c in n.children)
        return n.total

    for r in roots:
        total(r)
    counts = read_sql(f"""
        SELECT count(*) AS all_,
               count(*) FILTER (WHERE s.user_id IS NOT NULL) AS starred,
               count(*) FILTER (WHERE r.status = 'Açık') AS open,
               count(*) FILTER (WHERE r.status = 'Devam ediyor') AS progress,
               count(*) FILTER (WHERE r.status = 'Kapandı') AS closed,
               count(*) FILTER (WHERE r.created_at > now() - interval '{RECENT_DAYS} days') AS recent,
               count(*) FILTER (WHERE b.folder_id IS NULL) AS unfiled
        FROM dr_bilge_requests r
        LEFT JOIN dr_bilge_bookmarks b ON b.request_id = r.id
        LEFT JOIN dr_bilge_stars s ON s.request_id = r.id AND s.user_id = :uid
        WHERE r.deleted_at IS NULL""", {"uid": user_id})[0]
    smart = {"all": counts["all_"], **{k: counts[k] for k in SMART_VIEWS if k != "all"}}
    apps = {r["app_name"]: r["n"] for r in read_sql(
        """SELECT app_name, count(*) AS n FROM dr_bilge_requests WHERE deleted_at IS NULL AND app_name IS NOT NULL
           GROUP BY app_name ORDER BY app_name""")}
    return {"folders": roots, "by_id": nodes, "smart": smart, "apps": apps}


def folder_path(folder_id: int | None, by_id: dict | None = None) -> str:
    if folder_id is None:
        return ""
    if by_id is None:
        by_id = {f["id"]: f for f in read_sql("SELECT id, name, parent_id FROM dr_bilge_folders WHERE deleted_at IS NULL")}
    parts, seen = [], set()
    while folder_id and folder_id in by_id and folder_id not in seen:
        seen.add(folder_id)
        f = by_id[folder_id]
        name = f.name if isinstance(f, FolderNode) else f["name"]
        parts.append(name)
        folder_id = f.parent_id if isinstance(f, FolderNode) else f["parent_id"]
    return " / ".join(reversed(parts))


_BASE = """
    SELECT r.id, r.title, r.url, r.app_name, r.status, r.description, r.created_by, r.created_at, r.updated_at,
           b.folder_id, COALESCE(b.position, 0) AS position, COALESCE(b.tags, '{}') AS tags,
           (s.user_id IS NOT NULL) AS starred
    FROM dr_bilge_requests r
    LEFT JOIN dr_bilge_bookmarks b ON b.request_id = r.id
    LEFT JOIN dr_bilge_stars s ON s.request_id = r.id AND s.user_id = :uid
    WHERE r.deleted_at IS NULL"""


def list_bookmarks(user_id: str, view: str = "all", folder_id: int | None = None, app_name: str | None = None,
                   search: str = "", tag: str = "", status: str = "") -> list[dict]:
    ensure_schema()
    sql, params = _BASE, {"uid": user_id}
    if view == "folder" and folder_id is not None:
        sql += " AND b.folder_id = :fid"
        params["fid"] = folder_id
    elif view == "app" and app_name:
        sql += " AND r.app_name = :app"
        params["app"] = app_name
    elif view == "starred":
        sql += " AND s.user_id IS NOT NULL"
    elif view == "open":
        sql += " AND r.status = 'Açık'"
    elif view == "progress":
        sql += " AND r.status = 'Devam ediyor'"
    elif view == "closed":
        sql += " AND r.status = 'Kapandı'"
    elif view == "recent":
        sql += f" AND r.created_at > now() - interval '{RECENT_DAYS} days'"
    elif view == "unfiled":
        sql += " AND b.folder_id IS NULL"
    if status:
        sql += " AND r.status = :status"
        params["status"] = status
    if tag:
        sql += " AND :tag = ANY(b.tags)"
        params["tag"] = tag
    if search.strip():
        sql += """ AND (r.title ILIKE :q OR r.url ILIKE :q OR r.description ILIKE :q
                        OR EXISTS (SELECT 1 FROM unnest(b.tags) t WHERE t ILIKE :q))"""
        params["q"] = f"%{search.strip()}%"
    order = " ORDER BY COALESCE(b.position, 0), r.created_at DESC" if view == "folder" else \
        " ORDER BY CASE r.status WHEN 'Açık' THEN 0 WHEN 'Devam ediyor' THEN 1 ELSE 2 END, r.created_at DESC"
    return read_sql(sql + order, params)


def starred(user_id: str, limit: int = 20) -> list[dict]:
    ensure_schema()
    return read_sql("""SELECT r.id, r.title, r.url, r.status FROM dr_bilge_stars s
                       JOIN dr_bilge_requests r ON r.id = s.request_id AND r.deleted_at IS NULL
                       WHERE s.user_id = :uid ORDER BY s.created_at LIMIT :limit""", {"uid": user_id, "limit": limit})


def all_tags() -> list[str]:
    ensure_schema()
    return [r["t"] for r in read_sql(
        """SELECT DISTINCT unnest(b.tags) AS t FROM dr_bilge_bookmarks b
           JOIN dr_bilge_requests r ON r.id = b.request_id AND r.deleted_at IS NULL ORDER BY 1""")]


def for_app(app_name: str, user_id: str) -> list[dict]:
    """Uygulama ayrıntısında: o uygulamanın yer imleri, klasör yoluyla."""
    items = list_bookmarks(user_id, "app", app_name=app_name)
    by_id = None
    for it in items:
        if it["folder_id"]:
            if by_id is None:
                by_id = {f["id"]: f for f in read_sql(
                    "SELECT id, name, parent_id FROM dr_bilge_folders WHERE deleted_at IS NULL")}
            it["folder_path"] = folder_path(it["folder_id"], by_id)
        else:
            it["folder_path"] = ""
    return items


# ---------------------------------------------------------------------------
# Klasörler
# ---------------------------------------------------------------------------
def _clean_name(name: str) -> str:
    name = " ".join(name.split())
    if not name:
        raise ValueError("Klasör adı boş olamaz.")
    return name[:200]


def add_folder(name: str, parent_id: int | None = None) -> int:
    ensure_schema()
    name = _clean_name(name)
    with transaction() as conn:
        _folder(conn, parent_id)
        pos = rows(conn, """SELECT COALESCE(max(position), -1) + 1 AS p FROM dr_bilge_folders
                            WHERE deleted_at IS NULL AND parent_id IS NOT DISTINCT FROM :p""", {"p": parent_id})[0]["p"]
        fid = conn.execute(text("""INSERT INTO dr_bilge_folders (name, parent_id, position, created_by)
                                   VALUES (:n, :p, :pos, :by) RETURNING id"""),
                           {"n": name, "p": parent_id, "pos": pos, "by": current_user().name}).scalar()
        audit(conn, "bilge", "create", "folder", fid, {"title": name})
    return int(fid)


def rename_folder(folder_id: int, name: str) -> None:
    ensure_schema()
    name = _clean_name(name)
    with transaction() as conn:
        old = _folder(conn, folder_id)
        conn.execute(text("UPDATE dr_bilge_folders SET name = :n WHERE id = :id"), {"n": name, "id": folder_id})
        audit(conn, "bilge", "update", "folder", folder_id, {"title": name, "old": old["name"]})


def move_folder(folder_id: int, parent_id: int | None) -> None:
    """Klasörü başka klasörün altına (None: köke) taşır. Kendi altına taşınamaz."""
    ensure_schema()
    with transaction() as conn:
        f = _folder(conn, folder_id)
        cur = parent_id
        while cur is not None:
            if cur == folder_id:
                raise ValueError("Klasör kendi alt klasörüne taşınamaz.")
            cur = _folder(conn, cur)["parent_id"]
        conn.execute(text("UPDATE dr_bilge_folders SET parent_id = :p WHERE id = :id"),
                     {"p": parent_id, "id": folder_id})
        audit(conn, "bilge", "move", "folder", folder_id, {"title": f["name"], "parent": parent_id})


def delete_folder(folder_id: int) -> None:
    """Yumuşak silme. Alt klasörler ve yer imleri üst klasöre taşınır; talepler silinmez."""
    ensure_schema()
    with transaction() as conn:
        f = _folder(conn, folder_id)
        parent = f["parent_id"]
        conn.execute(text("UPDATE dr_bilge_folders SET parent_id = :p WHERE parent_id = :id AND deleted_at IS NULL"),
                     {"p": parent, "id": folder_id})
        conn.execute(text("UPDATE dr_bilge_bookmarks SET folder_id = :p WHERE folder_id = :id"),
                     {"p": parent, "id": folder_id})
        conn.execute(text("UPDATE dr_bilge_folders SET deleted_at = now() WHERE id = :id"), {"id": folder_id})
        audit(conn, "bilge", "delete", "folder", folder_id, {"title": f["name"]})


# ---------------------------------------------------------------------------
# Yer imleri
# ---------------------------------------------------------------------------
def _upsert(conn, request_id: int, **fields) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    cols = ", ".join(fields)
    vals = ", ".join(f":{k}" for k in fields)
    conn.execute(text(f"""
        INSERT INTO dr_bilge_bookmarks (request_id, {cols}, updated_by, updated_at)
        VALUES (:rid, {vals}, :by, now())
        ON CONFLICT (request_id) DO UPDATE SET {sets}, updated_by = :by, updated_at = now()"""),
        {"rid": request_id, "by": current_user().name, **fields})


def place(request_id: int, folder_id: int | None, before_id: int | None = None) -> None:
    """Yer imini klasöre (None: Klasörsüz) taşır; before_id verilirse o yer iminin önüne yerleştirir."""
    ensure_schema()
    with transaction() as conn:
        req = _request(conn, request_id)
        f = _folder(conn, folder_id)
        siblings = [r["request_id"] for r in rows(conn, """
            SELECT b.request_id FROM dr_bilge_bookmarks b JOIN dr_bilge_requests r ON r.id = b.request_id
            WHERE r.deleted_at IS NULL AND b.folder_id IS NOT DISTINCT FROM :f AND b.request_id <> :rid
            ORDER BY b.position, r.created_at DESC""", {"f": folder_id, "rid": request_id})]
        idx = siblings.index(before_id) if before_id in siblings else len(siblings)
        siblings.insert(idx, request_id)
        for pos, rid in enumerate(siblings):
            if rid == request_id:
                _upsert(conn, rid, folder_id=folder_id, position=pos)
            else:
                conn.execute(text("UPDATE dr_bilge_bookmarks SET position = :p WHERE request_id = :r"),
                             {"p": pos, "r": rid})
        audit(conn, "bilge", "move", "request", request_id,
              {"title": req["title"], "folder": f["name"] if f else "Klasörsüz"}, req["app_name"])


def set_tags(request_id: int, tags) -> list[str]:
    ensure_schema()
    tags = clean_tags(tags)
    with transaction() as conn:
        req = _request(conn, request_id)
        _upsert(conn, request_id, tags=tags)
        audit(conn, "bilge", "update", "request", request_id, {"title": req["title"], "tags": tags}, req["app_name"])
    return tags


def star(user_id: str, request_id: int, on: bool = True) -> None:
    """Kişisel yıldız (yalnızca yıldızlayan kullanıcıda görünür)."""
    ensure_schema()
    with transaction() as conn:
        req = _request(conn, request_id)
        if on:
            conn.execute(text("""INSERT INTO dr_bilge_stars (user_id, request_id) VALUES (:u, :r)
                                 ON CONFLICT DO NOTHING"""), {"u": user_id, "r": request_id})
        else:
            conn.execute(text("DELETE FROM dr_bilge_stars WHERE user_id = :u AND request_id = :r"),
                         {"u": user_id, "r": request_id})
        audit(conn, "bilge", "star" if on else "unstar", "request", request_id, {"title": req["title"]},
              req["app_name"])


def add_bookmark(title: str, url: str, app_name: str | None = None, status: str = "Açık", description: str = "",
                 folder_id: int | None = None, tags=None) -> int:
    """Yeni talep linki (BilgeAPI ile) + klasör / etiket. Aynı adres varsa ValueError."""
    ensure_schema()
    if not BilgeAPI.valid_url(url):
        raise ValueError("Adres http:// veya https:// ile başlamalı.")
    existing = find_by_url(url)
    if existing:
        raise DuplicateError(existing)
    rid = BilgeAPI.add_request(title, url, app_name, status, description)
    tags = clean_tags(tags or [])
    if folder_id is not None or tags:
        with transaction() as conn:
            _folder(conn, folder_id)
            _upsert(conn, rid, folder_id=folder_id, tags=tags,
                    position=rows(conn, """SELECT COALESCE(max(position), -1) + 1 AS p FROM dr_bilge_bookmarks
                                            WHERE folder_id IS NOT DISTINCT FROM :f""", {"f": folder_id})[0]["p"])
    return rid


class DuplicateError(ValueError):
    def __init__(self, existing: dict):
        super().__init__(f"Bu adres zaten kayıtlı: “{existing['title']}”")
        self.existing = existing


def parse_bulk(text_value: str) -> list[dict]:
    """Toplu yapıştırma: her satır 'başlık | adres' veya yalnızca adres. Geçersiz satırlar ok=False."""
    out = []
    for line in text_value.splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" in line:
            title, _, url = (p.strip() for p in line.rpartition("|"))
        else:
            title, url = "", line
        ok = BilgeAPI.valid_url(url)
        if ok and not title:
            p = urlparse(url)
            title = (p.path.rstrip("/").rsplit("/", 1)[-1] or p.netloc)[:300]
        out.append({"title": title, "url": url, "ok": ok})
    return out


# ---------------------------------------------------------------------------
# Netscape yer imi biçimi (Edge / Chrome / Firefox dışa aktarma dosyası)
# ---------------------------------------------------------------------------
@dataclass
class _NNode:
    name: str
    folders: list["_NNode"] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)


class _NetscapeParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _NNode("")
        self.stack = [self.root]
        self._pending: _NNode | None = None
        self._capture: str | None = None
        self._buf = ""
        self._href = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "h3":
            self._capture, self._buf = "h3", ""
        elif tag == "a":
            self._capture, self._buf, self._href = "a", "", a.get("href", "")
        elif tag == "dl":
            if self._pending:                       # <H3>Klasör</H3><DL> -> klasöre gir
                self.stack[-1].folders.append(self._pending)
                self.stack.append(self._pending)
                self._pending = None
            else:                                   # en dıştaki / adsız DL: aynı düzeyde kal
                self.stack.append(self.stack[-1])

    def handle_endtag(self, tag):
        if tag == "h3" and self._capture == "h3":
            self._pending = _NNode(" ".join(self._buf.split())[:200] or "Klasör")
            self._capture = None
        elif tag == "a" and self._capture == "a":
            self.stack[-1].links.append((" ".join(self._buf.split())[:300], self._href.strip()))
            self._capture = None
        elif tag == "dl" and len(self.stack) > 1:
            self.stack.pop()

    def handle_data(self, data):
        if self._capture:
            self._buf += data


def import_netscape(content: str, parent_id: int | None = None) -> dict:
    """Klasör yapısını korur; yalnız http/https; zaten kayıtlı adresler yeniden eklenmez, klasöre yerleştirilir."""
    ensure_schema()
    p = _NetscapeParser()
    p.feed(content)
    stats = {"folders": 0, "added": 0, "existing": 0, "skipped": 0}

    def walk(node: _NNode, folder_id: int | None):
        for title, url in node.links:
            if not BilgeAPI.valid_url(url):
                stats["skipped"] += 1
                continue
            existing = find_by_url(url)
            if existing:
                place(existing["id"], folder_id)
                stats["existing"] += 1
                continue
            try:
                add_bookmark(title or url, url, folder_id=folder_id)
                stats["added"] += 1
            except ValueError:
                stats["skipped"] += 1
        for child in node.folders:
            fid = add_folder(child.name, folder_id)
            stats["folders"] += 1
            walk(child, fid)

    walk(p.root, parent_id)
    return stats


def export_netscape(user_id: str, folder_id: int | None = None) -> str:
    """Seçili klasörü (None: tümü) Netscape biçiminde döner."""
    t = tree(user_id)
    items = list_bookmarks(user_id)
    by_folder: dict[int | None, list[dict]] = {}
    for it in items:
        by_folder.setdefault(it["folder_id"], []).append(it)
    esc = html.escape
    out = ["<!DOCTYPE NETSCAPE-Bookmark-file-1>", '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
           "<TITLE>Bookmarks</TITLE>", "<H1>Devrunner — Bilge talepleri</H1>", "<DL><p>"]

    def links(fid, indent):
        for it in sorted(by_folder.get(fid, []), key=lambda x: x["position"]):
            out.append(f'{indent}<DT><A HREF="{esc(it["url"])}">{esc(it["title"])}</A>')

    def folder(node: FolderNode, indent: str):
        out.append(f"{indent}<DT><H3>{esc(node.name)}</H3>")
        out.append(f"{indent}<DL><p>")
        for c in node.children:
            folder(c, indent + "    ")
        links(node.id, indent + "    ")
        out.append(f"{indent}</DL><p>")

    if folder_id is None:
        for n in t["folders"]:
            folder(n, "    ")
        links(None, "    ")
    else:
        node = t["by_id"].get(folder_id)
        if node is None:
            raise ValueError("Klasör bulunamadı.")
        folder(node, "    ")
    out.append("</DL><p>")
    return "\n".join(out) + "\n"
