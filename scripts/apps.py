"""
Uygulama kataloğu ve adlandırma kuralları. Hiçbir modül önek, sonek veya adres kalıbını kendi
içine gömmez; hepsi buradan türetilir:

    repo            DDDB.Iz.Anm.<ad>
    CI pipeline     DDDB.Iz.Anm.<ad>_Container-CI
    CD pipeline     DDDB.Iz.Anm.<ad>_Container-CD
    Dev             https://<ad>.dk8s.gentest.hqtest.local/
    Test            https://<ad>.tk8s.gentest.hqtest.local/
    Prod            https://<ad>.pk8s.gen.halkbank.local/

Önek, sonekler ve adres kalıpları ayardır (config: DEVOPS_REPO_PREFIX, PIPELINE_*_SUFFIX, URL_*).
Adreste ad küçük harfe çevrilir. Kurala uymayan uygulamalar için dr_app_overrides tablosunda
uygulama bazında adres ve iş yükü adı ezilebilir (set_overrides).

Katalog DevOps repo listesinden oluşur (relations.refresh_catalog) ve .local/cache/apps.json'a
yazılır: uygulama açılır açılmaz son bilinen listeyle dolar.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from urllib.parse import urlparse

from scripts.config import ENV, LOCAL_DIR

log = logging.getLogger(__name__)

ENVS = ("dev", "test", "prod")
ENV_TITLES = {"dev": "Dev", "test": "Test", "prod": "Prod"}
CACHE_FILE = LOCAL_DIR / "cache" / "apps.json"


def app_name_from_repo(repo: str) -> str | None:
    prefix = ENV.DEVOPS_REPO_PREFIX
    if not repo.lower().startswith(prefix.lower()) or len(repo) <= len(prefix):
        return None
    return repo[len(prefix):]


def url_pattern(env: str) -> str:
    return {"dev": ENV.URL_DEV, "test": ENV.URL_TEST, "prod": ENV.URL_PROD}[env]


@dataclass
class AppRef:
    name: str                  # repo adından önek atılmış hâli (DB'de app_name olarak kullanılır)
    repo: str
    repo_id: str = ""
    web_url: str = ""
    default_branch: str = ""

    @property
    def host_name(self) -> str:
        return self.name.lower()

    @property
    def ci_name(self) -> str:
        return self.repo + ENV.PIPELINE_CI_SUFFIX

    @property
    def cd_name(self) -> str:
        return self.repo + ENV.PIPELINE_CD_SUFFIX

    def url(self, env: str) -> str:
        override = (_overrides.get(self.name) or {}).get(f"url_{env}")
        if override:
            return override
        return url_pattern(env).replace("{app}", self.host_name)

    def urls(self) -> dict[str, str]:
        return {env: self.url(env) for env in ENVS}

    def host(self, env: str) -> str:
        return urlparse(self.url(env)).hostname or ""

    def k8s_workload(self) -> str:
        return (_overrides.get(self.name) or {}).get("k8s_workload") or ""

    def muted_envs(self) -> set[str]:
        raw = (_overrides.get(self.name) or {}).get("health_muted") or ""
        return {e for e in raw.split(",") if e}


def app_from_repo(repo: dict) -> AppRef | None:
    """Azure DevOps git/repositories öğesinden AppRef."""
    name = app_name_from_repo(repo.get("name", ""))
    if not name:
        return None
    branch = (repo.get("defaultBranch") or "").replace("refs/heads/", "")
    return AppRef(name=name, repo=repo["name"], repo_id=repo.get("id", ""), web_url=repo.get("webUrl", ""),
                  default_branch=branch)


# ---------------------------------------------------------------------------
# Katalog (süreç geneli; arka plan iş parçacıklarından okunabilir)
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_apps: dict[str, AppRef] = {}
_source = ""            # "devops" | "demo" | "cache" | ""
_updated_at = ""
_overrides: dict[str, dict] = {}


def apps() -> list[AppRef]:
    with _lock:
        return sorted(_apps.values(), key=lambda a: a.name.lower())


def names() -> list[str]:
    return [a.name for a in apps()]


def get(name: str) -> AppRef | None:
    with _lock:
        return _apps.get(name)


def source() -> str:
    return _source


def updated_at() -> str:
    return _updated_at


def set_apps(items: list[AppRef], src: str, updated: str) -> None:
    global _source, _updated_at
    with _lock:
        _apps.clear()
        _apps.update({a.name: a for a in items})
        _source, _updated_at = src, updated


def set_overrides(rows: list[dict]) -> None:
    with _lock:
        _overrides.clear()
        _overrides.update({r["app_name"]: r for r in rows})


def overrides(name: str) -> dict:
    return dict(_overrides.get(name) or {})


def save_cache(extra: dict) -> None:
    """Katalog + ek veri (DevOps özeti) diske. Secret içermez."""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {"source": _source, "updated_at": _updated_at, "apps": [asdict(a) for a in apps()], **extra}
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(CACHE_FILE)
    except OSError:
        log.warning("Katalog önbelleği yazılamadı", exc_info=True)


def load_cache() -> dict:
    """Önbellekten kataloğu yükler, ek veriyi döner. Önbellek yoksa {}."""
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        items = [AppRef(**a) for a in data.get("apps", [])]
        set_apps(items, "cache" if data.get("source") != "demo" else "demo", data.get("updated_at", ""))
        return data
    except (OSError, ValueError, TypeError):
        return {}


# ---------------------------------------------------------------------------
# Uygulama bazında ezmeler (dr_app_overrides)
# ---------------------------------------------------------------------------
OVERRIDE_FIELDS = ("url_dev", "url_test", "url_prod", "k8s_workload", "health_muted")


def load_overrides() -> None:
    from scripts import database
    set_overrides(database.read_sql(f"SELECT app_name, {', '.join(OVERRIDE_FIELDS)} FROM dr_app_overrides"))


def save_override(name: str, values: dict[str, str]) -> None:
    """Boş değer = kurala dön. Değişiklik geçmişe yazılır."""
    from scripts import database
    from scripts.user import current_user
    values = {k: (values.get(k) or "").strip() or None for k in OVERRIDE_FIELDS if k in values}
    old = overrides(name)
    merged = {k: values.get(k, old.get(k)) for k in OVERRIDE_FIELDS}
    changed = {k: v for k, v in merged.items() if (old.get(k) or None) != v}
    if not changed:
        return
    with database.transaction() as conn:
        conn.execute(database.text(f"""
            INSERT INTO dr_app_overrides (app_name, {', '.join(OVERRIDE_FIELDS)}, updated_by, updated_at)
            VALUES (:app, {', '.join(':' + k for k in OVERRIDE_FIELDS)}, :by, now())
            ON CONFLICT (app_name) DO UPDATE SET {', '.join(f'{k} = :{k}' for k in OVERRIDE_FIELDS)},
                updated_by = :by, updated_at = now()"""),
            {"app": name, **merged, "by": current_user().name})
        database.audit(conn, "apps", "override", "app", name, changed, name)
    with _lock:
        _overrides[name] = {"app_name": name, **merged}
