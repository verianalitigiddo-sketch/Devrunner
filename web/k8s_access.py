"""
Web katmanının TÜM Kubernetes erişimi (PLAN_V2.md Bölüm 3).

Servis hesabının deployments yetkisi yoktur ve gerekmez. Dondurulmuş backend'de iki fonksiyon yine de
listelemeyi dener: K8sAPI.resolve_workload (403'ü uyarı olarak ekler) ve relations.pod_summary (403'te
ortamı tümüyle atlar, Pod sütunu boş kalır). Bu modül:

* NoDeployCluster: K8sAPI.cluster(env)'i sarar; deployments() API'ye gitmeden [] döner. resolve_workload
  cluster parametresi aldığı için dondurulmuş eşleme kodu bununla uyarısız çalışır.
* Yetkisiz kaynak bir kez sorulur: 403 dönen kaynak türü (ingress, service, event) ortam başına hatırlanır,
  tekrar istenmez, her yenilemede uyarı basılmaz; Kubernetes sayfasında soluk bir not olarak görünür.
* Eşleme sırası (deployment'sız): elle tanımlı iş yükü -> Ingress'in servis seçicisi -> app=<ad> etiketi ->
  adı "<ad>-" ile başlayan pod'lar (son çare).

Sayfaların relations.app_workload / relations.pod_summary yerine bunu kullanması "sayfalar relations
kullanır" kuralının bilinçli istisnasıdır: düzeltme dondurulmuş backend'e yapılamaz.
"""
from __future__ import annotations

import logging
import threading
from urllib.parse import quote

from scripts import K8sAPI, relations
from scripts.apps import AppRef
from scripts.config import ENV, redact

log = logging.getLogger("web.k8s")

KIND_TITLES = {"ingresses": "Ingress", "services": "Service", "events": "Event"}
_forbidden: dict[str, set[str]] = {}
_lock = threading.Lock()


def forbidden(env: str) -> list[str]:
    with _lock:
        return sorted(KIND_TITLES[k] for k in _forbidden.get(env, set()))


def reset() -> None:
    with _lock:
        _forbidden.clear()


def _mark(env: str, kind: str) -> None:
    with _lock:
        _forbidden.setdefault(env, set()).add(kind)


def _is_forbidden(env: str, kind: str) -> bool:
    with _lock:
        return kind in _forbidden.get(env, set())


class NoDeployCluster:
    """K8sAPI.Cluster sarmalayıcısı: deployments hiç istenmez; yetkisiz kaynaklar sessizce boş döner."""

    def __init__(self, env: str):
        self.env = env
        self.inner = K8sAPI.cluster(env)
        self.ns = getattr(self.inner, "ns", ENV.K8S_NAMESPACE)

    def deployments(self) -> list[dict]:
        return []

    def _quiet(self, kind: str, fn, *args):
        if _is_forbidden(self.env, kind):
            return []
        try:
            return fn(*args)
        except K8sAPI.K8sForbidden:
            _mark(self.env, kind)
            log.info("%s: %s yetkisi yok; bu oturumda tekrar istenmeyecek", self.env, kind)
            return []

    def ingresses(self) -> list[dict]:
        return self._quiet("ingresses", self.inner.ingresses)

    def services(self) -> list[dict]:
        return self._quiet("services", self.inner.services)

    def events(self, pod: str) -> list[dict]:
        return self._quiet("events", self.inner.events, pod)

    def pods(self, selector: str = "") -> list[dict]:
        return self.inner.pods(selector)

    def logs(self, pod: str, container: str, lines: int, previous: bool = False) -> str:
        return self.inner.logs(pod, container, lines, previous)


def cluster(env: str) -> NoDeployCluster:
    return NoDeployCluster(env)


def workload(app_name: str, env: str) -> K8sAPI.Workload:
    a = relations.app(app_name)
    if not a:
        return K8sAPI.Workload(env, False, warnings=["Uygulama katalogda yok."])
    c = cluster(env)
    w = K8sAPI.resolve_workload(a, env, c)
    w.warnings = [x for x in w.warnings if "Deployment" not in x]
    if not w.pods:
        prefix = (a.k8s_workload() or a.host_name) + "-"
        pods = [K8sAPI.pod_info(p) for p in c.pods() if p["metadata"]["name"].startswith(prefix)]
        if pods:
            w.pods, w.found, w.how = pods, True, f"pod adı öneki ({prefix}…)"
    return w


def _match(a: AppRef, raw: dict) -> bool:
    name = a.k8s_workload() or a.host_name
    labels = (raw.get("metadata") or {}).get("labels") or {}
    return labels.get("app") == name or raw["metadata"]["name"].startswith(name + "-")


def pod_summary() -> dict[str, dict[str, str]]:
    """Uygulama -> {ortam: "hazır/toplam"}. Ortam başına TEK pod listesi; deployment istenmez."""
    out: dict[str, dict[str, str]] = {}
    for env in relations.k8s_envs():
        try:
            raw = cluster(env).pods()
        except K8sAPI.K8sError as e:
            log.info("Pod özeti %s: %s", env, e)
            continue
        infos = [(p, K8sAPI.pod_info(p)) for p in raw]
        for a in relations.catalog():
            matched = [info for p, info in infos if _match(a, p)]
            if matched:
                ready = sum(1 for p in matched if p.phase == "Running" and p.ready.split("/")[0] == p.ready.split("/")[1])
                out.setdefault(a.name, {})[env] = f"{ready}/{len(matched)}"
    return out


def stream_logs(env: str, pod: str, container: str, lines: int, previous: bool, stop: threading.Event):
    """Canlı log (follow=true): satır satır üretir. Demo kipinde örnek satırlar."""
    if K8sAPI.is_demo():
        yield K8sAPI.DemoCluster(env).logs(pod, container, lines, previous) + "\n"
        n = 0
        while not stop.wait(1.5):
            n += 1
            yield f"2026-11-18 10:{n // 60:02d}:{n % 60:02d} INFO GET /health 200 {8 + n % 7} ms  [örnek canlı satır]\n"
        return
    c = K8sAPI.Cluster(env)
    url = f"{c.server}/api/v1/namespaces/{quote(c.ns)}/pods/{quote(pod)}/log"
    params = {"container": container, "tailLines": lines, "follow": "true"}
    if previous:
        params = {"container": container, "tailLines": lines, "previous": "true"}
    try:
        with c.session.get(url, params=params, stream=True, timeout=(K8sAPI.TIMEOUT, None)) as r:
            if r.status_code in (401, 403):
                yield f"[log okunamadı: yetki yok ({r.status_code})]\n"
                return
            if r.status_code >= 400:
                yield f"[log okunamadı: {r.status_code}]\n"
                return
            for chunk in r.iter_content(chunk_size=None, decode_unicode=False):
                if stop.is_set():
                    return
                if chunk:
                    yield chunk.decode("utf-8", "replace")
    except Exception as e:   # noqa: BLE001
        yield f"\n[log akışı kesildi: {redact(str(e))[:200]}]\n"

