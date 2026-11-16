"""
Ortak PostgreSQL.

    from scripts.database import read_sql, execute, transaction, audit

    rows = read_sql("SELECT * FROM dr_notes WHERE app_name = :app", {"app": "musteri360"})   # list[dict]
    with transaction() as conn:
        conn.execute(text("UPDATE ..."), {...})
        audit(conn, "notes", "update", "note", 5, app_name="musteri360")

KURALLAR
* Değerleri SQL'e f-string ile GÖMMEYİN, :isim parametresiyle verin.
* Veritabanına yazan her API fonksiyonu aynı transaction içinde audit() çağırır.
* Ortak kayıtlar silinmez, deleted_at ile işaretlenir.
* Şema değişiklikleri yalnızca EKLEYİCİDİR (yeni tablo, yeni nullable kolon). Farklı geliştiriciler
  uygulamanın farklı sürümlerini aynı veritabanına bağlar; kolon silmek / yeniden adlandırmak
  eski sürümleri bozar. Yeni şema adımı: SCHEMA_VERSION'ı artırın, MIGRATIONS'a fonksiyon ekleyin.

DB_HOST boşsa veya bağlantı kurulamıyorsa is_ready() False döner; veritabanı gerektiren sayfalar
"bağlantı yok" durumu gösterir, uygulamanın geri kalanı çalışır.
"""
from __future__ import annotations

import json
import logging
import threading
from contextlib import contextmanager

from sqlalchemy import (Boolean, Column, Date, DateTime, Integer, MetaData, String, Table, Text, create_engine,
                        func, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import URL

from scripts import __version__
from scripts.config import APP_NAME, ENV, redact
from scripts.user import current_user, hostname, os_user

log = logging.getLogger(__name__)

_engine = None
_lock = threading.Lock()
_ready = False
_last_error = ""


class DbUnavailable(RuntimeError):
    pass


def db_configured() -> bool:
    return bool(ENV.DB_HOST)


def is_ready() -> bool:
    return _ready


def last_error() -> str:
    return _last_error


def status_text() -> str:
    if not db_configured():
        return "PostgreSQL ayarlanmamış (Ayarlar → PostgreSQL)."
    if _ready:
        return f"PostgreSQL bağlı: {ENV.DB_HOST}/{ENV.DB}"
    return f"PostgreSQL'e bağlanılamadı: {_last_error or 'deneniyor…'}"


def get_engine():
    global _engine
    with _lock:
        if _engine is None:
            if not db_configured():
                raise DbUnavailable("PostgreSQL ayarları girilmemiş (Ayarlar → PostgreSQL).")
            url = URL.create("postgresql+psycopg2", host=ENV.DB_HOST, port=ENV.DB_PORT, database=ENV.DB,
                             username=ENV.DB_USER, password=ENV.DB_PW)
            _engine = create_engine(url, pool_pre_ping=True, pool_size=4, max_overflow=4, pool_recycle=1800,
                                    connect_args={"connect_timeout": 5, "application_name": APP_NAME})
        return _engine


def reset_engine() -> None:
    """Ayarlar değişince çağrılır; bir sonraki istek yeni ayarlarla bağlanır."""
    global _engine, _ready
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _ready = False


def read_sql(sql: str, params: dict | None = None) -> list[dict]:
    with get_engine().connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(sql), params or {})]


def execute(sql: str, params: dict | None = None) -> int:
    with get_engine().begin() as conn:
        return conn.execute(text(sql), params or {}).rowcount


@contextmanager
def transaction():
    with get_engine().begin() as conn:
        yield conn


def rows(conn, sql: str, params: dict | None = None) -> list[dict]:
    """transaction() içinde SELECT."""
    return [dict(r._mapping) for r in conn.execute(text(sql), params or {})]


# =============================================================================
# Tablolar (dr_ öneki: ortak veritabanında çakışmasın)
# =============================================================================
metadata = MetaData()

T_META = Table(
    "dr_meta", metadata,
    Column("key", String(50), primary_key=True),
    Column("value", Text),
)
T_USERS = Table(
    "dr_users", metadata,
    Column("id", String(36), primary_key=True),
    Column("display_name", String(100), nullable=False),
    Column("os_user", String(100)),
    Column("hostname", String(100)),
    Column("app_version", String(20)),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("last_seen_at", DateTime(timezone=True), server_default=func.now()),
)
T_PRESENCE = Table(
    "dr_presence", metadata,
    Column("user_id", String(36), primary_key=True),
    Column("ip", String(64), nullable=False),
    Column("port", Integer, nullable=False),
    Column("pubkey", String(100), nullable=False),
    Column("app_version", String(20)),
    Column("last_seen", DateTime(timezone=True), server_default=func.now()),
)
T_NOTES = Table(
    "dr_notes", metadata,
    Column("id", Integer, primary_key=True),
    Column("app_name", String(150)),
    Column("env", String(10)),
    Column("title", String(200), nullable=False),
    Column("body", Text, nullable=False, server_default=""),
    Column("pinned", Boolean, nullable=False, server_default="false"),
    Column("created_by", String(100), nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("updated_by", String(100)),
    Column("updated_at", DateTime(timezone=True)),
    Column("deleted_at", DateTime(timezone=True)),
)
T_BILGE = Table(
    "dr_bilge_requests", metadata,
    Column("id", Integer, primary_key=True),
    Column("title", String(300), nullable=False),
    Column("url", Text, nullable=False),
    Column("app_name", String(150)),
    Column("status", String(30), nullable=False, server_default="Açık"),
    Column("description", Text),
    Column("created_by", String(100), nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("updated_at", DateTime(timezone=True)),
    Column("deleted_at", DateTime(timezone=True)),
)
T_FREEZES = Table(
    "dr_freezes", metadata,
    Column("id", Integer, primary_key=True),
    Column("start_date", Date, nullable=False),
    Column("end_date", Date, nullable=False),
    Column("title", String(300), nullable=False),
    Column("created_by", String(100), nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("deleted_at", DateTime(timezone=True)),
)
T_HEALTH = Table(
    "dr_health_events", metadata,
    Column("id", Integer, primary_key=True),
    Column("app_name", String(150), nullable=False),
    Column("env", String(10), nullable=False),
    Column("state", String(20), nullable=False),
    Column("status_code", Integer),
    Column("response_ms", Integer),
    Column("detail", Text),
    Column("observed_by", String(100)),
    Column("observed_at", DateTime(timezone=True), server_default=func.now()),
)
T_OVERRIDES = Table(
    "dr_app_overrides", metadata,
    Column("app_name", String(150), primary_key=True),
    Column("url_dev", Text),
    Column("url_test", Text),
    Column("url_prod", Text),
    Column("k8s_workload", String(150)),
    Column("health_muted", String(30)),        # sessize alınan ortamlar: "dev,prod"
    Column("updated_by", String(100)),
    Column("updated_at", DateTime(timezone=True), server_default=func.now()),
)
T_AUDIT = Table(
    "dr_audit_log", metadata,
    Column("id", Integer, primary_key=True),
    Column("at", DateTime(timezone=True), server_default=func.now(), index=True),
    Column("user_id", String(36)),
    Column("user_name", String(100)),
    Column("module", String(30), nullable=False),
    Column("action", String(30), nullable=False),
    Column("entity", String(50)),
    Column("entity_id", String(150)),
    Column("app_name", String(150), index=True),
    Column("detail", JSONB),
)

SCHEMA_VERSION = 1
_V1_TABLES = [T_USERS, T_PRESENCE, T_NOTES, T_BILGE, T_FREEZES, T_HEALTH, T_OVERRIDES, T_AUDIT]


def _migrate_v1(conn) -> None:
    metadata.create_all(conn, tables=_V1_TABLES, checkfirst=True)


# Sürüm -> adım. Adımlar idempotent olmalı (IF NOT EXISTS): iki istemci aynı anda açılabilir.
MIGRATIONS = {1: _migrate_v1}


def init_db() -> str:
    """
    Eksik şema adımlarını uygular, kullanıcıyı kaydeder. Uyarı metni döner (yoksa "").
    Arka planda çağrılır; başarısızsa is_ready() False kalır.
    """
    global _ready, _last_error
    try:
        warning = ""
        with transaction() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(7735101)"))   # eşzamanlı migrasyonu engeller
            T_META.create(conn, checkfirst=True)
            current = int(conn.execute(text("SELECT value FROM dr_meta WHERE key = 'schema_version'")).scalar() or 0)
            for version in range(current + 1, SCHEMA_VERSION + 1):
                MIGRATIONS[version](conn)
                conn.execute(text("""INSERT INTO dr_meta (key, value) VALUES ('schema_version', :v)
                                     ON CONFLICT (key) DO UPDATE SET value = :v"""), {"v": str(version)})
                log.info("Veritabanı şeması %s sürümüne yükseltildi", version)
            if current > SCHEMA_VERSION:
                warning = (f"Veritabanı şeması ({current}) bu sürümden ({SCHEMA_VERSION}) yeni. "
                           "Uygulamayı güncelleyin; bazı alanlar görünmeyebilir.")
            _register_user(conn)
        _ready = True
        _last_error = ""
        return warning
    except Exception as e:
        _ready = False
        _last_error = redact(str(e).splitlines()[0] if str(e) else type(e).__name__)
        raise


def ping() -> bool:
    """Bağlantı hâlâ var mı? Durumu günceller."""
    global _ready, _last_error
    try:
        execute("SELECT 1")
        return True
    except Exception as e:   # noqa: BLE001
        _ready = False
        _last_error = redact(str(e).splitlines()[0] if str(e) else type(e).__name__)
        return False


def _register_user(conn) -> None:
    user = current_user()
    if not user.id:
        return
    conn.execute(text("""
        INSERT INTO dr_users (id, display_name, os_user, hostname, app_version)
        VALUES (:id, :name, :os_user, :host, :ver)
        ON CONFLICT (id) DO UPDATE SET display_name = :name, os_user = :os_user, hostname = :host,
                                       app_version = :ver, last_seen_at = now()
    """), {"id": user.id, "name": user.name, "os_user": os_user(), "host": hostname(), "ver": __version__})


def audit(conn, module: str, action: str, entity: str = "", entity_id=None, detail: dict | None = None,
          app_name: str | None = None) -> None:
    """
    Değişiklik geçmişine kayıt. Secret değer YAZMAYIN; yalnızca "ne değişti" bilgisini verin.
    """
    user = current_user()
    conn.execute(text("""
        INSERT INTO dr_audit_log (user_id, user_name, module, action, entity, entity_id, app_name, detail)
        VALUES (:uid, :uname, :module, :action, :entity, :eid, :app, CAST(:detail AS JSONB))
    """), {"uid": user.id, "uname": user.name, "module": module, "action": action, "entity": entity,
           "eid": None if entity_id is None else str(entity_id), "app": app_name,
           "detail": json.dumps(detail or {}, ensure_ascii=False, default=str)})


def audit_now(module: str, action: str, entity: str = "", entity_id=None, detail: dict | None = None,
              app_name: str | None = None) -> None:
    """Kendi transaction'ı olmayan işlemler için (ör. ayar değişikliği). Veritabanı yoksa sessizce atlanır."""
    if not _ready:
        return
    try:
        with transaction() as conn:
            audit(conn, module, action, entity, entity_id, detail, app_name)
    except Exception:   # noqa: BLE001
        log.warning("Değişiklik geçmişine yazılamadı", exc_info=True)


def history(app_name: str | None = None, module: str | None = None, entity: str | None = None,
            entity_id=None, limit: int = 200) -> list[dict]:
    sql = "SELECT at, user_name, module, action, entity, entity_id, app_name, detail FROM dr_audit_log WHERE 1=1"
    params: dict = {"limit": limit}
    if app_name:
        sql += " AND app_name = :app"
        params["app"] = app_name
    if module:
        sql += " AND module = :module"
        params["module"] = module
    if entity:
        sql += " AND entity = :entity"
        params["entity"] = entity
    if entity_id is not None:
        sql += " AND entity_id = :eid"
        params["eid"] = str(entity_id)
    return read_sql(sql + " ORDER BY at DESC, id DESC LIMIT :limit", params)


def test_connection() -> tuple[bool, str]:
    try:
        version = read_sql("SELECT version() AS v")[0]["v"]
        return True, f"Bağlantı başarılı.\n{version.split(',')[0]}"
    except Exception as e:   # noqa: BLE001
        return False, redact(str(e).strip() or type(e).__name__)
