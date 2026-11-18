"""
Testler ekransız çalışır (QT_QPA_PLATFORM=offscreen) ve geliştiricinin .env'ine dokunmaz
(DEVRUNNER_PROFILE=pytest + geçici .env).

Veritabanı testleri için yerel PostgreSQL (AYRI bir test veritabanı; her testte sıfırlanır):
    TEST_DB_HOST=localhost TEST_DB=app_test TEST_DB_USER=app TEST_DB_PW=app
Tanımlı değilse veritabanı testleri atlanır.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["DEVRUNNER_PROFILE"] = "pytest"
for key in ("DEVOPS_PAT", "DB_HOST", "K8S_DEV_SERVER", "K8S_DEV_TOKEN", "USER_NAME", "USER_ID"):
    os.environ.pop(key, None)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from scripts import config  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Her test temiz ayarlarla ve geçici .env / .local ile başlar."""
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(config, "LOCAL_DIR", tmp_path / ".local")
    config.load()
    config.save({"USER_NAME": "Test Kişi", "USER_ID": "00000000-0000-4000-8000-000000000001"})
    yield
    config.load()
    import gc
    gc.collect()   # Qt nesneleri arayüz iş parçacığında toplansın


def _db_settings():
    host = os.getenv("TEST_DB_HOST")
    if not host:
        return None
    return {"DB_HOST": host, "DB_PORT": os.getenv("TEST_DB_PORT", "5432"), "DB": os.getenv("TEST_DB", "app"),
            "DB_USER": os.getenv("TEST_DB_USER", "app"), "DB_PW": os.getenv("TEST_DB_PW", "app")}


@pytest.fixture
def db():
    """Temiz dr_* tablolarıyla hazır veritabanı."""
    settings = _db_settings()
    if not settings:
        pytest.skip("TEST_DB_HOST tanımlı değil")
    from scripts import database
    config.save(settings)
    database.reset_engine()
    # Yalnızca TEST veritabanında: her test temiz şemayla başlar. Yer imi tabloları (web çatalı,
    # scripts/BookmarkAPI.py) dr_bilge_requests'e bağlı olduğu için önce onlar kaldırılır.
    bookmark_tables = ["dr_bilge_stars", "dr_bilge_bookmarks", "dr_bilge_folders"]
    with database.transaction() as conn:
        conn.execute(database.text("DROP TABLE IF EXISTS " + ", ".join(
            bookmark_tables + [t.name for t in database.metadata.sorted_tables])))
    database.init_db()
    yield database
    database.reset_engine()
