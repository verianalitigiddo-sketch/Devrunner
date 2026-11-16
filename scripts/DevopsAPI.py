"""
Azure DevOps Server REST API (kişinin kendi PAT'i ile, salt okunur).

    overview = fetch_overview()          # repolar + CI/CD son durumları
    builds(definition_id)                # build geçmişi
    deployments(release_definition_id)   # CD klasik release ise dağıtım geçmişi
    timeline(build_id), build_log(build_id, log_id)
    commits(repo_id), pull_requests(repo_id)

Adres: DEVOPS_URL (collection) + DEVOPS_PROJECT, ör. https://devops.halkbank.com.tr/DefaultCollection + Halkbank.
DEVOPS_PAT boşsa sample_data/devops/*.json'dan yanıt verilir (demo modu). Demo verisi gerçek API
yanıt biçimindedir; aynı ayrıştırma kodu çalışır.

Bu dosya arayüz (widget) import etmez.
"""
from __future__ import annotations

import base64
import json
import logging
import ssl
from dataclasses import asdict, dataclass, field
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter

from scripts import apps as catalog
from scripts.apps import AppRef
from scripts.config import ENV, SAMPLE_DIR, redact
from scripts.fmt import now, parse_dt

log = logging.getLogger(__name__)

TIMEOUT = 20
DEMO_DIR = SAMPLE_DIR / "devops"

STATE_TITLES = {
    "queued": "Kuyrukta", "running": "Çalışıyor", "succeeded": "Başarılı", "partial": "Kısmen başarılı",
    "failed": "Başarısız", "canceled": "İptal", "none": "Pipeline yok",
}


class DevopsError(Exception):
    pass


class DevopsAuthError(DevopsError):
    pass


def is_demo() -> bool:
    return not ENV.DEVOPS_PAT


# ---------------------------------------------------------------------------
# Veri yapıları
# ---------------------------------------------------------------------------
@dataclass
class BuildInfo:
    id: int
    number: str
    state: str                 # STATE_TITLES anahtarlarından biri
    kind: str = "build"        # build | release
    definition_id: int = 0
    definition_name: str = ""
    branch: str = ""
    commit: str = ""
    requested_by: str = ""
    queued: str = ""
    started: str = ""
    finished: str = ""
    web_url: str = ""
    stage: str = ""            # release: ortam adı

    @property
    def duration_sec(self) -> float | None:
        s, f = parse_dt(self.started), parse_dt(self.finished)
        if s and f:
            return (f - s).total_seconds()
        if s and self.state == "running":
            return (now() - s).total_seconds()
        return None

    @property
    def when(self) -> str:
        return self.finished or self.started or self.queued


@dataclass
class PipelineInfo:
    kind: str                  # build | release
    definition_id: int
    name: str
    web_url: str
    last: BuildInfo | None = None

    @property
    def state(self) -> str:
        return self.last.state if self.last else "none"


@dataclass
class AppPipelines:
    ci: PipelineInfo | None = None
    cd: PipelineInfo | None = None


@dataclass
class Overview:
    apps: list[AppRef]
    pipelines: dict[str, AppPipelines]
    source: str                         # devops | demo
    fetched_at: str
    warnings: list[str] = field(default_factory=list)

    def to_cache(self) -> dict:
        return {"pipelines": {k: asdict(v) for k, v in self.pipelines.items()}, "fetched_at": self.fetched_at}

    @staticmethod
    def pipelines_from_cache(data: dict) -> dict[str, AppPipelines]:
        out = {}
        for name, p in (data.get("pipelines") or {}).items():
            def pi(d):
                if not d:
                    return None
                last = BuildInfo(**d["last"]) if d.get("last") else None
                return PipelineInfo(d["kind"], d["definition_id"], d["name"], d["web_url"], last)
            out[name] = AppPipelines(pi(p.get("ci")), pi(p.get("cd")))
        return out


def build_state(status: str | None, result: str | None) -> str:
    status, result = (status or "").lower(), (result or "").lower()
    if status in ("notstarted", "postponed"):
        return "queued"
    if status in ("inprogress", "cancelling"):
        return "running"
    return {"succeeded": "succeeded", "partiallysucceeded": "partial", "failed": "failed",
            "canceled": "canceled"}.get(result, "none" if not status else "queued")


def deployment_state(status: str | None) -> str:
    return {"succeeded": "succeeded", "partiallysucceeded": "partial", "failed": "failed",
            "inprogress": "running", "notdeployed": "queued", "undefined": "none"}.get((status or "").lower(), "none")


# ---------------------------------------------------------------------------
# HTTP istemcisi
# ---------------------------------------------------------------------------
class _TruststoreAdapter(HTTPAdapter):
    """Windows sertifika deposunu kullanır: kurum içi CA için verify=False gerekmez."""

    def init_poolmanager(self, *args, **kwargs):
        try:
            import truststore
            kwargs["ssl_context"] = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except Exception:   # noqa: BLE001 - truststore yoksa requests'in varsayılanı
            log.warning("truststore kullanılamadı; varsayılan sertifika deposu kullanılıyor")
        return super().init_poolmanager(*args, **kwargs)


class Client:
    def __init__(self):
        self.base = ENV.DEVOPS_URL.rstrip("/")
        self.project = ENV.DEVOPS_PROJECT
        self.api_version = ENV.DEVOPS_API_VERSION
        s = requests.Session()
        s.trust_env = ENV.DEVOPS_USE_PROXY
        token = base64.b64encode(f":{ENV.DEVOPS_PAT}".encode()).decode()
        s.headers.update({"Authorization": f"Basic {token}", "Accept": "application/json"})
        if ENV.DEVOPS_VERIFY_TLS:
            s.mount("https://", _TruststoreAdapter())
        else:
            s.verify = False
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self.session = s

    def url(self, path: str, project: bool = True) -> str:
        if project:
            return f"{self.base}/{quote(self.project)}/_apis/{path}"
        return f"{self.base}/_apis/{path}"

    def _request(self, path: str, params: dict | None, project: bool, text: bool = False):
        params = {"api-version": self.api_version, **(params or {})}
        headers = {"Accept": "text/plain"} if text else None
        try:
            r = self.session.get(self.url(path, project), params=params, headers=headers, timeout=TIMEOUT,
                                 allow_redirects=False)
        except requests.exceptions.SSLError as e:
            raise DevopsError(f"Sertifika doğrulanamadı ({self.base}). Ayarlar → Azure DevOps → "
                              f"'Sertifikayı doğrula' seçeneğini kontrol edin.\n{redact(str(e))}") from None
        except requests.RequestException as e:
            raise DevopsError(f"DevOps sunucusuna ulaşılamadı ({self.base}): {type(e).__name__}") from None
        ctype = r.headers.get("Content-Type", "")
        # Geçersiz PAT'te sunucu 401 yerine 203 + giriş sayfası veya 302 dönebilir
        if r.status_code in (401, 403) or r.status_code in (203, 302) or (
                r.ok and not text and "json" not in ctype):
            raise DevopsAuthError("PAT geçersiz, süresi dolmuş veya yetkisi eksik. "
                                  "Gereken yetkiler: Code (Read), Build (Read), Release (Read).")
        if r.status_code == 404:
            raise DevopsError(f"Bulunamadı: {path} (proje adı '{self.project}' doğru mu?)")
        if not r.ok:
            raise DevopsError(f"DevOps hatası {r.status_code}: {redact(r.text[:300])}")
        return r

    def get(self, path: str, params: dict | None = None, project: bool = True):
        return self._request(path, params, project).json()

    def get_text(self, path: str, params: dict | None = None) -> str:
        return self._request(path, params, True, text=True).text

    def get_all(self, path: str, params: dict | None = None, limit: int = 5000) -> list[dict]:
        """x-ms-continuationtoken ile tüm sayfaları toplar."""
        params = dict(params or {})
        out: list[dict] = []
        while True:
            r = self._request(path, params, True)
            out += r.json().get("value", [])
            token = r.headers.get("x-ms-continuationtoken")
            if not token or len(out) >= limit:
                return out
            params["continuationToken"] = token


class DemoClient(Client):
    """sample_data/devops/ altındaki gerçek biçimli yanıtları döner."""

    def __init__(self):
        self.base = ENV.DEVOPS_URL.rstrip("/") or "https://devops.example/DefaultCollection"
        self.project = ENV.DEVOPS_PROJECT or "Proje"
        self.api_version = ENV.DEVOPS_API_VERSION

    @staticmethod
    def _load(name: str):
        return json.loads((DEMO_DIR / f"{name}.json").read_text(encoding="utf-8"))

    def get(self, path: str, params: dict | None = None, project: bool = True):
        params = params or {}
        parts = path.split("/")
        if path == "connectionData":
            return self._load("connection")
        if path == "git/repositories":
            return self._load("repositories")
        if path == "build/definitions":
            return self._load("build_definitions")
        if path == "build/builds":
            return self._load("builds").get(str(params.get("definitions")), {"count": 0, "value": []})
        if parts[:2] == ["build", "builds"] and parts[-1] == "timeline":
            data = self._load("timelines")
            return data.get(parts[2], data["default"])
        if path == "release/definitions":
            return self._load("release_definitions")
        if path == "release/deployments":
            return self._load("deployments").get(str(params.get("definitionId")), {"count": 0, "value": []})
        if parts[:2] == ["git", "repositories"] and parts[-1] == "commits":
            return self._load("commits").get(parts[2], {"count": 0, "value": []})
        if parts[:2] == ["git", "repositories"] and parts[-1] == "pullrequests":
            return self._load("pullrequests").get(parts[2], {"count": 0, "value": []})
        raise DevopsError(f"Demo verisinde yok: {path}")

    def get_text(self, path: str, params: dict | None = None) -> str:
        build_id, log_id = path.split("/")[2], path.split("/")[-1]
        lines = [f"2026-11-10T09:00:{i:02d}.0000000Z Adım {log_id} çıktısı (örnek)" for i in range(8)]
        if log_id == "7":
            lines += ["##[error]ModuleNotFoundError: No module named 'pyarrow'",
                      "##[error]Process completed with exit code 1."]
        return f"Build {build_id} / log {log_id}\n" + "\n".join(lines)

    def get_all(self, path: str, params: dict | None = None, limit: int = 5000) -> list[dict]:
        return self.get(path, params).get("value", [])


def make_client() -> Client:
    return DemoClient() if is_demo() else Client()


# ---------------------------------------------------------------------------
# Web bağlantıları (demo ve gerçek için aynı)
# ---------------------------------------------------------------------------
def _web(c: Client, tail: str) -> str:
    return f"{c.base}/{quote(c.project)}/{tail}"


def repo_web_url(c: Client, repo: str) -> str:
    return _web(c, f"_git/{quote(repo)}")


def build_web_url(c: Client, build_id: int) -> str:
    return _web(c, f"_build/results?buildId={build_id}")


def definition_web_url(c: Client, definition_id: int) -> str:
    return _web(c, f"_build?definitionId={definition_id}")


def release_web_url(c: Client, definition_id: int) -> str:
    return _web(c, f"_release?definitionId={definition_id}&view=mine&_a=releases")


# ---------------------------------------------------------------------------
# Ayrıştırma
# ---------------------------------------------------------------------------
def parse_build(c: Client, b: dict) -> BuildInfo:
    d = b.get("definition") or {}
    return BuildInfo(
        id=int(b.get("id", 0)), number=str(b.get("buildNumber", "")),
        state=build_state(b.get("status"), b.get("result")), kind="build",
        definition_id=int(d.get("id", 0)), definition_name=d.get("name", ""),
        branch=(b.get("sourceBranch") or "").replace("refs/heads/", ""),
        commit=(b.get("sourceVersion") or "")[:8],
        requested_by=(b.get("requestedFor") or b.get("requestedBy") or {}).get("displayName", ""),
        queued=b.get("queueTime") or "", started=b.get("startTime") or "", finished=b.get("finishTime") or "",
        web_url=build_web_url(c, int(b.get("id", 0))),
    )


def parse_deployment(c: Client, d: dict) -> BuildInfo:
    rel = d.get("release") or {}
    rdef = d.get("releaseDefinition") or {}
    artifacts = rel.get("artifacts") or []
    branch = ""
    if artifacts:
        branch = ((artifacts[0].get("definitionReference") or {}).get("branch") or {}).get("name", "")
    return BuildInfo(
        id=int(d.get("id", 0)), number=rel.get("name", ""), state=deployment_state(d.get("deploymentStatus")),
        kind="release", definition_id=int(rdef.get("id", 0)), definition_name=rdef.get("name", ""),
        branch=branch.replace("refs/heads/", ""),
        requested_by=(d.get("requestedFor") or d.get("requestedBy") or {}).get("displayName", ""),
        queued=d.get("queuedOn") or "", started=d.get("startedOn") or "", finished=d.get("completedOn") or "",
        web_url=((rel.get("_links") or {}).get("web") or {}).get("href") or release_web_url(c, int(rdef.get("id", 0))),
        stage=(d.get("releaseEnvironment") or {}).get("name", ""),
    )


# ---------------------------------------------------------------------------
# Sorgular
# ---------------------------------------------------------------------------
def fetch_overview(client: Client | None = None) -> Overview:
    """Önekle başlayan tüm repolar ve her birinin CI/CD son durumu."""
    c = client or make_client()
    prefix = ENV.DEVOPS_REPO_PREFIX
    warnings: list[str] = []

    items: list[AppRef] = []
    for repo in c.get_all("git/repositories"):
        if repo.get("isDisabled"):
            continue
        app = catalog.app_from_repo(repo)
        if app:
            app.web_url = app.web_url or repo_web_url(c, app.repo)
            items.append(app)
    items.sort(key=lambda a: a.name.lower())

    defs = c.get_all("build/definitions", {"name": f"{prefix}*", "includeLatestBuilds": "true"})
    by_name = {d.get("name", "").lower(): d for d in defs}

    def pipeline_from_def(d: dict) -> PipelineInfo:
        latest = d.get("latestBuild") or d.get("latestCompletedBuild")
        last = None
        if latest:
            latest = {**latest, "definition": {"id": d["id"], "name": d["name"]}}
            last = parse_build(c, latest)
        return PipelineInfo("build", int(d["id"]), d["name"], definition_web_url(c, int(d["id"])), last)

    pipelines: dict[str, AppPipelines] = {}
    missing_cd: list[AppRef] = []
    for app in items:
        ci = by_name.get(app.ci_name.lower())
        cd = by_name.get(app.cd_name.lower())
        pipelines[app.name] = AppPipelines(pipeline_from_def(ci) if ci else None, pipeline_from_def(cd) if cd else None)
        if not cd:
            missing_cd.append(app)

    # CD YAML pipeline olarak bulunamadıysa klasik Release tanımlarında ara
    if missing_cd:
        try:
            rdefs = c.get_all("release/definitions", {"searchText": prefix})
            rby_name = {d.get("name", "").lower(): d for d in rdefs}
            for app in missing_cd:
                rd = rby_name.get(app.cd_name.lower())
                if not rd:
                    continue
                deps = c.get("release/deployments", {"definitionId": rd["id"], "$top": 1,
                                                     "queryOrder": "descending"}).get("value", [])
                last = parse_deployment(c, deps[0]) if deps else None
                pipelines[app.name].cd = PipelineInfo("release", int(rd["id"]), rd["name"],
                                                      release_web_url(c, int(rd["id"])), last)
        except DevopsAuthError:
            warnings.append("Release (CD) tanımları okunamadı: PAT'te Release (Read) yetkisi yok.")
        except DevopsError as e:
            warnings.append(f"Release (CD) tanımları okunamadı: {e}")

    return Overview(items, pipelines, "demo" if isinstance(c, DemoClient) else "devops",
                    now().isoformat(timespec="seconds"), warnings)


def builds(definition_id: int, top: int = 20, client: Client | None = None) -> list[BuildInfo]:
    c = client or make_client()
    data = c.get("build/builds", {"definitions": definition_id, "$top": top, "queryOrder": "queueTimeDescending"})
    return [parse_build(c, b) for b in data.get("value", [])]


def deployments(release_definition_id: int, top: int = 20, client: Client | None = None) -> list[BuildInfo]:
    c = client or make_client()
    data = c.get("release/deployments", {"definitionId": release_definition_id, "$top": top,
                                         "queryOrder": "descending"})
    return [parse_deployment(c, d) for d in data.get("value", [])]


def history(pipeline: PipelineInfo, top: int = 20) -> list[BuildInfo]:
    if pipeline.kind == "release":
        return deployments(pipeline.definition_id, top)
    return builds(pipeline.definition_id, top)


@dataclass
class TimelineStep:
    name: str
    type: str
    state: str
    started: str
    finished: str
    log_id: int | None
    errors: list[str]
    order: int


def timeline(build_id: int, client: Client | None = None) -> list[TimelineStep]:
    """Build adımları; Task'lar sırasıyla. Başarısız adımların hata mesajları errors'ta."""
    c = client or make_client()
    data = c.get(f"build/builds/{build_id}/timeline")
    steps = []
    for r in data.get("records") or []:
        if r.get("type") not in ("Task", "Job", "Stage"):
            continue
        errors = [i.get("message", "") for i in (r.get("issues") or []) if i.get("type") == "error"]
        steps.append(TimelineStep(
            name=r.get("name", ""), type=r.get("type", ""),
            state=build_state(r.get("state"), r.get("result")),
            started=r.get("startTime") or "", finished=r.get("finishTime") or "",
            log_id=(r.get("log") or {}).get("id"), errors=errors, order=int(r.get("order") or 0)))
    order = {"Stage": 0, "Job": 1, "Task": 2}
    return sorted([s for s in steps if s.type == "Task"], key=lambda s: (s.started or "9", s.order)) or \
        sorted(steps, key=lambda s: (order[s.type], s.order))


def build_log(build_id: int, log_id: int, client: Client | None = None) -> str:
    c = client or make_client()
    return c.get_text(f"build/builds/{build_id}/logs/{log_id}")


def commits(repo_id: str, top: int = 15, client: Client | None = None) -> list[dict]:
    c = client or make_client()
    data = c.get(f"git/repositories/{repo_id}/commits", {"searchCriteria.$top": top})
    return [{"id": x.get("commitId", "")[:8], "message": (x.get("comment") or "").splitlines()[0] if x.get("comment") else "",
             "author": (x.get("author") or {}).get("name", ""), "date": (x.get("author") or {}).get("date", ""),
             "url": x.get("remoteUrl", "")} for x in data.get("value", [])]


def pull_requests(repo_id: str, client: Client | None = None) -> list[dict]:
    c = client or make_client()
    data = c.get(f"git/repositories/{repo_id}/pullrequests", {"searchCriteria.status": "active"})
    out = []
    for p in data.get("value", []):
        repo = (p.get("repository") or {}).get("name", "")
        out.append({"id": p.get("pullRequestId"), "title": p.get("title", ""),
                    "author": (p.get("createdBy") or {}).get("displayName", ""),
                    "source": (p.get("sourceRefName") or "").replace("refs/heads/", ""),
                    "target": (p.get("targetRefName") or "").replace("refs/heads/", ""),
                    "created": p.get("creationDate", ""), "draft": bool(p.get("isDraft")),
                    "url": _web(c, f"_git/{quote(repo)}/pullrequest/{p.get('pullRequestId')}")})
    return out


def test_connection() -> tuple[bool, str]:
    if is_demo():
        return False, "PAT girilmemiş: örnek veri gösteriliyor."
    try:
        c = Client()
        who = c.get("connectionData", project=False).get("authenticatedUser", {})
        name = who.get("providerDisplayName") or who.get("customDisplayName") or "?"
        repos = [r for r in c.get_all("git/repositories") if catalog.app_name_from_repo(r.get("name", ""))]
        return True, f"Bağlandı: {name}\n'{ENV.DEVOPS_REPO_PREFIX}' ile başlayan {len(repos)} repo bulundu."
    except DevopsError as e:
        return False, str(e)
