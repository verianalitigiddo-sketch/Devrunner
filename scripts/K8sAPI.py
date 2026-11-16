"""
Kubernetes: deployment, pod, log, olay ve tek seferlik komut (exec).

Kubeconfig şablonu uygulamaya gömülüdür (KUBECONFIG_TEMPLATE); kullanıcı ortam başına yalnızca
cluster adresini ve servis hesabı token'ını girer (Ayarlar → Kubernetes). Python istemcisi
yapılandırmayı bellekten yükler; Terminal'de kubectl kullanılabilsin diye .local/kube/<ortam>.yaml
de üretilir.

Hiçbir ortam tanımlı değilse sample_data/k8s.json'dan yanıt verilir (demo). Gerçek nesneler
sanitize_for_serialization ile API JSON'una (camelCase) çevrilir; demo verisi de aynı biçimdedir,
böylece aynı ayrıştırma kodu çalışır.

Servis hesabının yetkisi dar olabilir: 403 alınan kaynak K8sForbidden olarak döner, çağıran taraf
o bölümü "yetki yok" gösterir. Bu dosya arayüz import etmez.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
from dataclasses import dataclass, field

from scripts.apps import ENV_TITLES, AppRef
from scripts.config import ENV, LOCAL_DIR, SAMPLE_DIR, redact
from scripts.fmt import ago, parse_dt

log = logging.getLogger(__name__)

KUBE_DIR = LOCAL_DIR / "kube"
TIMEOUT = 15
ENVS = ("dev", "test", "prod")

KUBECONFIG_TEMPLATE = """apiVersion: v1
kind: Config
clusters:
- cluster:
    insecure-skip-tls-verify: true
    server: {server}
  name: cce-cluster
contexts:
- context:
    cluster: cce-cluster
    namespace: {namespace}
    user: verianalitigi-sa
  name: verianalitigi-context
current-context: verianalitigi-context
users:
- name: verianalitigi-sa
  user:
    token: {token}
"""


class K8sError(Exception):
    pass


class K8sForbidden(K8sError):
    pass


def normalize_server(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value
    return value


def env_settings(env: str) -> tuple[str, str]:
    key = env.upper()
    from scripts import config
    return normalize_server(config.get(f"K8S_{key}_SERVER")), config.get(f"K8S_{key}_TOKEN")


def configured_envs() -> list[str]:
    return [e for e in ENVS if all(env_settings(e))]


def is_demo() -> bool:
    return not configured_envs()


def available_envs() -> list[str]:
    """Sayfada seçilebilecek ortamlar (demo modunda üçü de örnek veriyle)."""
    return list(ENVS) if is_demo() else configured_envs()


def kubeconfig_text(env: str) -> str:
    server, token = env_settings(env)
    return KUBECONFIG_TEMPLATE.format(server=server, namespace=ENV.K8S_NAMESPACE, token=token)


def kubeconfig_dict(env: str) -> dict:
    server, token = env_settings(env)
    return {
        "apiVersion": "v1", "kind": "Config",
        "clusters": [{"cluster": {"insecure-skip-tls-verify": True, "server": server}, "name": "cce-cluster"}],
        "contexts": [{"context": {"cluster": "cce-cluster", "namespace": ENV.K8S_NAMESPACE, "user": "verianalitigi-sa"},
                      "name": "verianalitigi-context"}],
        "current-context": "verianalitigi-context",
        "users": [{"name": "verianalitigi-sa", "user": {"token": token}}],
    }


def kubeconfig_path(env: str):
    return KUBE_DIR / f"{env}.yaml"


def write_kubeconfigs() -> list[str]:
    """Tanımlı ortamlar için .local/kube/<env>.yaml yazar, tanımsızların dosyasını siler."""
    KUBE_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for env in ENVS:
        path = kubeconfig_path(env)
        if env in configured_envs():
            path.write_text(kubeconfig_text(env), encoding="utf-8")
            if sys.platform != "win32":
                path.chmod(0o600)
            written.append(env)
        elif path.exists():
            path.unlink()
    return written


# ---------------------------------------------------------------------------
# İstemciler
# ---------------------------------------------------------------------------
_clients: dict[str, object] = {}
_lock = threading.Lock()


def reset_clients() -> None:
    with _lock:
        _clients.clear()


def _api_client(env: str):
    from kubernetes import config as kconfig
    with _lock:
        if env not in _clients:
            if env not in configured_envs():
                raise K8sError(f"{ENV_TITLES.get(env, env)} cluster'ı tanımlı değil (Ayarlar → Kubernetes).")
            client = kconfig.new_client_from_config_dict(kubeconfig_dict(env))
            client.configuration.verify_ssl = False
            _clients[env] = client
        return _clients[env]


class Cluster:
    """Bir ortamın cluster'ı. Tüm yanıtlar API JSON sözlüğüdür."""

    def __init__(self, env: str):
        from kubernetes import client as k
        self.env = env
        self.ns = ENV.K8S_NAMESPACE
        self.api = _api_client(env)
        self.core = k.CoreV1Api(self.api)
        self.apps = k.AppsV1Api(self.api)
        self.net = k.NetworkingV1Api(self.api)

    def _call(self, kind: str, fn, *args, **kwargs):
        from kubernetes.client.exceptions import ApiException
        try:
            result = fn(*args, _request_timeout=TIMEOUT, **kwargs)
        except ApiException as e:
            if e.status in (401, 403):
                raise K8sForbidden(f"{kind}: servis hesabının yetkisi yok ({e.status}).") from None
            raise K8sError(f"{kind}: {e.status} {e.reason}") from None
        except Exception as e:   # noqa: BLE001 - ağ hataları
            raise K8sError(f"{kind}: cluster'a ulaşılamadı ({type(e).__name__}: {redact(str(e))[:200]})") from None
        return result if isinstance(result, str) else self.api.sanitize_for_serialization(result)

    def deployments(self) -> list[dict]:
        return self._call("Deployment", self.apps.list_namespaced_deployment, self.ns).get("items", [])

    def pods(self, selector: str = "") -> list[dict]:
        return self._call("Pod", self.core.list_namespaced_pod, self.ns, label_selector=selector or None).get("items", [])

    def ingresses(self) -> list[dict]:
        return self._call("Ingress", self.net.list_namespaced_ingress, self.ns).get("items", [])

    def services(self) -> list[dict]:
        return self._call("Service", self.core.list_namespaced_service, self.ns).get("items", [])

    def events(self, pod: str) -> list[dict]:
        return self._call("Event", self.core.list_namespaced_event, self.ns,
                          field_selector=f"involvedObject.name={pod}").get("items", [])

    def logs(self, pod: str, container: str, lines: int, previous: bool = False) -> str:
        return self._call("Log", self.core.read_namespaced_pod_log, pod, self.ns, container=container,
                          tail_lines=lines, previous=previous, timestamps=False)

    def exec(self, pod: str, container: str, command: str) -> str:
        from kubernetes.stream import stream
        try:
            return stream(self.core.connect_get_namespaced_pod_exec, pod, self.ns, container=container,
                          command=["/bin/sh", "-c", command], stderr=True, stdout=True, stdin=False, tty=False,
                          _request_timeout=60)
        except Exception as e:   # noqa: BLE001
            status = getattr(e, "status", None)
            if status in (401, 403):
                raise K8sForbidden("Komut çalıştırma (pods/exec) yetkisi yok.") from None
            raise K8sError(f"Komut çalıştırılamadı: {redact(str(e))[:300]}") from None


class DemoCluster(Cluster):
    def __init__(self, env: str):
        self.env = env
        self.ns = ENV.K8S_NAMESPACE
        self.data = json.loads((SAMPLE_DIR / "k8s.json").read_text(encoding="utf-8"))

    def deployments(self):
        return self.data["deployments"]["items"]

    def pods(self, selector: str = ""):
        items = self.data["pods"]["items"]
        if selector:
            want = dict(p.split("=", 1) for p in selector.split(","))
            items = [p for p in items if all(p["metadata"].get("labels", {}).get(k) == v for k, v in want.items())]
        return items

    def ingresses(self):
        if self.env == "prod":
            raise K8sForbidden("Ingress: servis hesabının yetkisi yok (403). (örnek)")
        return [{**i, "spec": {"rules": [{**r, "host": r["host"].replace(".dk8s.", f".{self.env[0]}k8s.")}
                                         for r in i["spec"]["rules"]]}} for i in self.data["ingresses"]["items"]]

    def services(self):
        return self.data["services"]["items"]

    def events(self, pod: str):
        return [e for e in self.data["events"]["items"] if e["involvedObject"]["name"] == pod]

    def logs(self, pod: str, container: str, lines: int, previous: bool = False) -> str:
        if "krediskor" in pod:
            body = ["Traceback (most recent call last):", '  File "/app/app.py", line 4, in <module>',
                    "    import pyarrow", "ModuleNotFoundError: No module named 'pyarrow'"]
        else:
            body = [f"2026-11-16 09:{m:02d}:00 INFO GET / 200 {12 + m} ms" for m in range(20)]
        return f"[örnek log: {pod}/{container}]\n" + "\n".join(body[-lines:])

    def exec(self, pod: str, container: str, command: str) -> str:
        return f"[örnek] {container}$ {command}\nLinux {pod} 5.10.0 x86_64\n"


def cluster(env: str) -> Cluster:
    return DemoCluster(env) if is_demo() else Cluster(env)


# ---------------------------------------------------------------------------
# Özetler
# ---------------------------------------------------------------------------
@dataclass
class PodInfo:
    name: str
    phase: str
    status: str               # Running / CrashLoopBackOff / Pending…
    ready: str                # "1/1"
    restarts: int
    age: str
    node: str
    ip: str
    containers: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)


@dataclass
class DeploymentInfo:
    name: str
    ready: int
    desired: int
    images: list[str]
    selector: dict
    age: str


def pod_info(p: dict) -> PodInfo:
    meta, spec, status = p.get("metadata", {}), p.get("spec", {}), p.get("status", {})
    cs = status.get("containerStatuses") or []
    reason = status.get("phase", "?")
    for c in cs:
        waiting = (c.get("state") or {}).get("waiting")
        terminated = (c.get("state") or {}).get("terminated")
        if waiting and waiting.get("reason"):
            reason = waiting["reason"]
        elif terminated and terminated.get("reason") and reason == "Running":
            reason = terminated["reason"]
    if meta.get("deletionTimestamp"):
        reason = "Terminating"
    containers = [c.get("name", "") for c in spec.get("containers") or []]
    return PodInfo(
        name=meta.get("name", ""), phase=status.get("phase", ""), status=reason,
        ready=f"{sum(1 for c in cs if c.get('ready'))}/{len(containers)}",
        restarts=sum(int(c.get("restartCount") or 0) for c in cs),
        age=ago(meta.get("creationTimestamp")).replace(" önce", ""),
        node=spec.get("nodeName", ""), ip=status.get("podIP", ""), containers=containers,
        images=[c.get("image", "") for c in spec.get("containers") or []])


def deployment_info(d: dict) -> DeploymentInfo:
    meta, spec, status = d.get("metadata", {}), d.get("spec", {}), d.get("status", {})
    containers = ((spec.get("template") or {}).get("spec") or {}).get("containers") or []
    return DeploymentInfo(
        name=meta.get("name", ""), ready=int(status.get("readyReplicas") or 0), desired=int(spec.get("replicas") or 0),
        images=[c.get("image", "") for c in containers],
        selector=(spec.get("selector") or {}).get("matchLabels") or {},
        age=ago(meta.get("creationTimestamp")).replace(" önce", ""))


def event_rows(items: list[dict]) -> list[dict]:
    out = [{"type": e.get("type", ""), "reason": e.get("reason", ""), "message": e.get("message", ""),
            "count": e.get("count") or 1, "when": e.get("lastTimestamp") or e.get("eventTime") or ""}
           for e in items]
    return sorted(out, key=lambda e: parse_dt(e["when"]) or parse_dt("1970-01-01T00:00:00Z"), reverse=True)


def selector_str(labels: dict) -> str:
    return ",".join(f"{k}={v}" for k, v in labels.items())


@dataclass
class Workload:
    """Bir uygulamanın bir ortamdaki karşılığı. how: eşlemenin nasıl bulunduğu."""
    env: str
    found: bool
    how: str = ""
    deployment: DeploymentInfo | None = None
    selector: str = ""
    pods: list[PodInfo] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ready_text(self) -> str:
        ready = sum(1 for p in self.pods if p.ready.split("/")[0] == p.ready.split("/")[1] and p.phase == "Running")
        return f"{ready}/{len(self.pods)}"


def resolve_workload(app: AppRef, env: str, c: Cluster | None = None) -> Workload:
    """
    Uygulama -> iş yükü eşlemesi, sırasıyla:
      1) dr_app_overrides.k8s_workload  2) host'u <ad>. ile başlayan Ingress'in servisinin seçicisi
      3) adı <ad> olan deployment       4) app=<ad> etiketi
    """
    c = c or cluster(env)
    w = Workload(env, False)
    name = app.host_name
    deployments: list[dict] = []
    try:
        deployments = c.deployments()
    except K8sForbidden as e:
        w.warnings.append(str(e))
    by_name = {d["metadata"]["name"]: d for d in deployments}

    def from_deployment(dname: str, how: str) -> bool:
        d = by_name.get(dname)
        if not d:
            return False
        w.deployment = deployment_info(d)
        w.selector = selector_str(w.deployment.selector)
        w.how, w.found = how, True
        return True

    override = app.k8s_workload()
    if override and from_deployment(override, "elle tanımlı eşleme"):
        pass
    elif override:
        w.selector, w.how, w.found = f"app={override}", "elle tanımlı eşleme (etiket)", True
    else:
        try:
            services: list[dict] | None = None
            for ing in c.ingresses():
                for rule in (ing.get("spec") or {}).get("rules") or []:
                    if not (rule.get("host") or "").lower().startswith(name + "."):
                        continue
                    for path in ((rule.get("http") or {}).get("paths") or []):
                        svc_name = ((path.get("backend") or {}).get("service") or {}).get("name")
                        if services is None:
                            services = c.services()
                        svc = next((s for s in services if s["metadata"]["name"] == svc_name), None)
                        if svc and (svc.get("spec") or {}).get("selector"):
                            w.selector = selector_str(svc["spec"]["selector"])
                            w.how, w.found = f"Ingress {ing['metadata']['name']} → Service {svc_name}", True
                            break
                    if w.found:
                        break
                if w.found:
                    break
        except K8sForbidden as e:
            w.warnings.append(str(e))
        if w.found:
            for d in deployments:
                sel = (d.get("spec") or {}).get("selector", {}).get("matchLabels") or {}
                if sel and selector_str(sel) == w.selector:
                    w.deployment = deployment_info(d)
        elif not from_deployment(name, "deployment adı"):
            w.selector, w.how = f"app={name}", "app etiketi"
    if w.selector:
        try:
            w.pods = [pod_info(p) for p in c.pods(w.selector)]
            if w.pods:
                w.found = True
        except K8sForbidden as e:
            w.warnings.append(str(e))
    return w


def test_connection() -> tuple[bool, str]:
    envs = configured_envs()
    if not envs:
        return False, "Hiçbir ortam için cluster adresi ve token girilmemiş: örnek veri gösteriliyor."
    reset_clients()
    lines, ok = [], True
    for env in envs:
        try:
            n = len(Cluster(env).pods())
            lines.append(f"{ENV_TITLES[env]}: bağlandı, '{ENV.K8S_NAMESPACE}' içinde {n} pod.")
        except K8sError as e:
            ok = False
            lines.append(f"{ENV_TITLES[env]}: {e}")
    return ok, "\n".join(lines)
