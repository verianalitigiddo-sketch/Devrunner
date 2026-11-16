"""
Modüller arası TEK bağlantı noktası (iç API).

KURAL: Bir modül başka bir modülün sayfasını veya API'sini import etmez. Başka modülden bilgi veya
eylem gerekiyorsa buraya bir fonksiyon eklenir; çağıran taraf hangi modülün yanıtladığını bilmez.
Olaylar için scripts/bus.py kullanılır.

İlişkiler (PLAN.md 4.2):
    DevOps      -> Katalog          repo listesi uygulama kataloğunu oluşturur          refresh_catalog, catalog
    Katalog     -> Health           yoklanacak Dev/Test/Prod adresleri                  app_urls, HealthService
    Katalog     -> K8s              uygulamanın iş yükü ve pod'ları                     app_workload
    Katalog     -> Notlar, Bilge    kayıtlar bir uygulamaya bağlanır                    app_notes, app_requests, app_counts
    Her sayfa   -> Hepsi            app_selected olayı                                  select_app, selected_app
    Freeze      -> Başlık, DevOps,  freeze rozeti, CD ve Prod işlemlerinde uyarı        freeze_status, prod_warning
                   K8s
    Health      -> Tepsi, Uygulamalar  durum değişince bildirim, özet tablo             app_health, health_summary
    Health/K8s/ -> Terminal         "Terminalde dene"                                    open_in_terminal, quick_commands
    DevOps
    Ayarlar     -> Tüm API'ler      ilgili istemci yeniden kurulur                       save_settings (+ _on_settings)
    Sohbet      -> Başlık           çevrimiçi kişi sayısı                                online_count
    Tüm yazma   -> Geçmiş           database.audit                                       app_history
"""
from __future__ import annotations

import logging
from datetime import date

from PySide6.QtCore import QObject, QTimer

from scripts import BilgeAPI, DevopsAPI, FreezeAPI, HealthAPI, K8sAPI, NotesAPI, TerminalAPI
from scripts import apps as catalog_mod
from scripts import config, database
from scripts.apps import ENV_TITLES, AppRef
from scripts.bus import bus
from scripts.config import ENV
from scripts.fmt import today
from scripts.user import current_user
from scripts.worker import Poller, run_async

log = logging.getLogger(__name__)

_services: "Services | None" = None
_selected = ""


# =============================================================================
# Katalog ve DevOps
# =============================================================================
def catalog() -> list[AppRef]:
    return catalog_mod.apps()


def app(name: str) -> AppRef | None:
    return catalog_mod.get(name)


def catalog_source() -> str:
    """devops | demo | cache | '' (henüz yok)"""
    return catalog_mod.source()


def overview() -> DevopsAPI.Overview | None:
    return _services.overview if _services else None


def overview_error() -> str:
    return _services.overview_error if _services else ""


def app_pipelines(name: str) -> DevopsAPI.AppPipelines | None:
    ov = overview()
    return ov.pipelines.get(name) if ov else None


def refresh_catalog() -> None:
    if _services:
        _services.catalog_poller.trigger()


def app_urls(name: str) -> dict[str, str]:
    a = app(name)
    return a.urls() if a else {}


def set_override(name: str, values: dict[str, str]) -> None:
    """Uygulama bazında adres / iş yükü ezmesi (blocking: arka planda çağırın)."""
    catalog_mod.save_override(name, values)
    bus.data_changed.emit("overrides")


# =============================================================================
# Seçili uygulama ve gezinme
# =============================================================================
def select_app(name: str) -> None:
    global _selected
    if name and name != _selected:
        _selected = name
        bus.app_selected.emit(name)


def selected_app() -> str:
    return _selected


def open_page(page_id: str, app_name: str | None = None) -> None:
    if app_name:
        select_app(app_name)
    bus.open_page.emit(page_id)


def open_in_terminal(command: str, kube_env: str = "") -> None:
    bus.open_page.emit("terminal")
    bus.run_command.emit(command, kube_env)


def quick_commands(name: str, env: str, shell: TerminalAPI.Shell | None) -> list[tuple[str, str]]:
    a = app(name)
    if not a:
        return []
    kube_env = env if env in K8sAPI.configured_envs() else ""
    selector = f"app={a.k8s_workload() or a.host_name}" if kube_env else ""
    return TerminalAPI.quick_commands(shell, a.host(env), a.url(env), kube_env, selector)


# =============================================================================
# Erişilebilirlik
# =============================================================================
def health_monitor() -> HealthAPI.HealthMonitor:
    return _services.monitor if _services else HealthAPI.HealthMonitor()


def app_health(name: str) -> dict[str, HealthAPI.HealthState]:
    return health_monitor().for_app(name)


def health_summary() -> dict[str, int]:
    out: dict[str, int] = {}
    for s in health_monitor().states():
        out[s.state] = out.get(s.state, 0) + 1
    return out


def refresh_health() -> None:
    if _services:
        _services.health_poller.trigger()


def set_muted(name: str, env: str, muted: bool) -> None:
    """Blocking. Sessize alınan ortam bildirim üretmez."""
    a = app(name)
    current = a.muted_envs() if a else set()
    current = (current | {env}) if muted else (current - {env})
    set_override(name, {"health_muted": ",".join(sorted(current))})


# =============================================================================
# Kubernetes
# =============================================================================
def k8s_envs() -> list[str]:
    return K8sAPI.available_envs()


def kubectl_envs() -> list[str]:
    """Terminalde KUBECONFIG olarak seçilebilecek (gerçekten tanımlı) ortamlar."""
    return K8sAPI.configured_envs()


def app_workload(name: str, env: str) -> K8sAPI.Workload:
    """Blocking. Uygulamanın o ortamdaki deployment ve pod'ları."""
    a = app(name)
    if not a:
        return K8sAPI.Workload(env, False, warnings=["Uygulama katalogda yok."])
    return K8sAPI.resolve_workload(a, env)


def pod_summary() -> dict[str, dict[str, str]]:
    """Uygulama -> {ortam: "hazır/toplam"} (blocking). Ortam başına tek deployment + pod listesi çekilir;
    eşleme: elle tanımlı iş yükü -> aynı adlı deployment'ın seçicisi -> app=<ad> etiketi."""
    out: dict[str, dict[str, str]] = {}
    for env in K8sAPI.available_envs():
        try:
            c = K8sAPI.cluster(env)
            deps = {d["metadata"]["name"]: K8sAPI.deployment_info(d) for d in c.deployments()}
            pods = [(p, K8sAPI.pod_info(p)) for p in c.pods()]
        except K8sAPI.K8sError:
            continue
        for a in catalog():
            name = a.k8s_workload() or a.host_name
            d = deps.get(name)
            sel = d.selector if d and d.selector else {"app": name}
            matched = [info for raw, info in pods
                       if all((raw["metadata"].get("labels") or {}).get(k) == v for k, v in sel.items())]
            if matched:
                ready = sum(1 for p in matched if p.phase == "Running" and p.ready.split("/")[0] == p.ready.split("/")[1])
                out.setdefault(a.name, {})[env] = f"{ready}/{len(matched)}"
    return out


# =============================================================================
# Ortak veri (blocking: arka planda çağırın)
# =============================================================================
def app_notes(name: str) -> list[dict]:
    return NotesAPI.list_notes(name)


def app_requests(name: str) -> list[dict]:
    return BilgeAPI.list_requests(name)


def app_history(name: str, limit: int = 50) -> list[dict]:
    return database.history(app_name=name, limit=limit)


def app_counts() -> tuple[dict[str, int], dict[str, int]]:
    """(uygulama -> not sayısı, uygulama -> açık talep sayısı). Veritabanı yoksa boş."""
    if not database.is_ready():
        return {}, {}
    return NotesAPI.counts_by_app(), BilgeAPI.counts_by_app()


# =============================================================================
# Freeze
# =============================================================================
def freeze_status() -> FreezeAPI.FreezeStatus:
    return FreezeAPI.current_status()


def active_freeze() -> list[FreezeAPI.Freeze]:
    return freeze_status().active


def prod_warning(env: str) -> str:
    """Freeze sırasında Prod / CD işlemleri için uyarı metni (yoksa "")."""
    st = freeze_status()
    if st.is_active and env in ("prod", "cd"):
        return "FREEZE dönemindeyiz: " + "; ".join(f.label for f in st.active)
    return ""


# =============================================================================
# Sohbet
# =============================================================================
def chat_service():
    return _services.chat if _services else None


def online_count() -> int:
    chat = chat_service()
    return chat.node.online_count() if chat and chat.running else 0


# =============================================================================
# Ayarlar
# =============================================================================
def save_settings(group_id: str, values: dict[str, str]) -> list[str]:
    """Ayarları .env'e yazar, ilgili modülleri yeniden kurar, geçmişe 'X değişti' yazar (değer yazılmaz)."""
    changed = config.save(values)
    if changed:
        labels = [config.setting(k).label for k in changed]
        if _services:
            _services.on_settings(group_id, changed)
        bus.settings_changed.emit(group_id)
        run_async(database.audit_now, "settings", "update", "settings", group_id,
                  {"changed": changed, "labels": labels})
    return changed


def test_connection(group_id: str) -> tuple[bool, str]:
    """Blocking. Ayarlar ekranındaki 'Bağlantıyı sına' düğmesi."""
    if group_id == "db":
        return database.test_connection()
    if group_id == "devops":
        return DevopsAPI.test_connection()
    if group_id == "k8s":
        return K8sAPI.test_connection()
    return False, "Bu grup için bağlantı testi yok."


# =============================================================================
# Servisler (uygulama boyunca arka planda çalışır)
# =============================================================================
class Services(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.overview: DevopsAPI.Overview | None = None
        self.overview_error = ""
        self.monitor = HealthAPI.HealthMonitor()
        self.db_warning = ""
        self.chat = None
        self._db_ready = False
        self._db_failed_once = False
        self._day: date = today()
        self._freeze_sig = None

        self.catalog_poller = Poller(DevopsAPI.fetch_overview, ENV.DEVOPS_REFRESH_SEC * 1000,
                                     on_done=self._overview_done, on_error=self._overview_failed, parent=self)
        self.health_poller = Poller(self._health_round, ENV.HEALTH_INTERVAL_SEC * 1000,
                                    on_done=self._health_done, parent=self)
        self.db_poller = Poller(self._db_tick, min(ENV.DB_REFRESH_SEC, 60) * 1000,
                                on_done=self._db_done, on_error=self._db_failed, parent=self)
        self._clock = QTimer(self)
        self._clock.setInterval(60_000)
        self._clock.timeout.connect(self._tick_clock)

    # ---- başlat / durdur
    def start(self) -> None:
        data = catalog_mod.load_cache()
        if data.get("pipelines"):
            self.overview = DevopsAPI.Overview(catalog_mod.apps(), DevopsAPI.Overview.pipelines_from_cache(data),
                                               catalog_mod.source(), data.get("fetched_at", ""))
            bus.catalog_changed.emit()
        try:
            K8sAPI.write_kubeconfigs()
        except OSError:
            log.warning("kubeconfig dosyaları yazılamadı", exc_info=True)
        self.catalog_poller.start()
        if ENV.HEALTH_ENABLED:
            self.health_poller.start(immediate=bool(catalog_mod.apps()))
        if database.db_configured():
            self.db_poller.start()
        self._clock.start()

    def stop(self) -> None:
        for p in (self.catalog_poller, self.health_poller, self.db_poller):
            p.stop()
        self._clock.stop()
        if self.chat:
            self.chat.stop()

    # ---- DevOps
    def _overview_done(self, ov: DevopsAPI.Overview) -> None:
        first = not catalog_mod.apps()
        self.overview, self.overview_error = ov, ""
        catalog_mod.set_apps(ov.apps, ov.source, ov.fetched_at)
        catalog_mod.save_cache(ov.to_cache())
        bus.catalog_changed.emit()
        if first and ENV.HEALTH_ENABLED:
            self.health_poller.trigger()

    def _overview_failed(self, e: BaseException) -> None:
        self.overview_error = config.redact(str(e)) or type(e).__name__
        log.warning("DevOps özeti alınamadı: %s", self.overview_error)
        bus.catalog_changed.emit()

    # ---- Erişilebilirlik
    def _health_round(self):
        changes = HealthAPI.run_round(self.monitor, catalog_mod.apps(), demo=catalog_mod.source() == "demo")
        try:
            HealthAPI.record_changes(changes, current_user().name)
        except Exception:   # noqa: BLE001
            log.warning("Sağlık olayları veritabanına yazılamadı", exc_info=True)
        return changes

    def _health_done(self, changes: list[HealthAPI.Change]) -> None:
        for c in changes:
            bus.health_changed.emit(c.app, c.env, c.new)
            a = catalog_mod.get(c.app)
            muted = a is not None and c.env in a.muted_envs()
            if ENV.HEALTH_NOTIFY and not muted and c.new != "missing":
                level = "error" if c.new == "down" else "warning" if c.new == "slow" else "info"
                bus.notify.emit(f"{c.app} · {ENV_TITLES[c.env]}",
                                f"{HealthAPI.STATE_TITLES.get(c.old, c.old)} → {HealthAPI.STATE_TITLES[c.new]}"
                                + (f" ({c.state.error})" if c.state.error else ""), level)
        bus.health_updated.emit()

    # ---- Veritabanı, freeze, ezmeler
    @staticmethod
    def _db_tick() -> dict:
        warning = ""
        if not database.is_ready():
            warning = database.init_db()
        elif not database.ping():
            raise database.DbUnavailable(database.last_error())
        FreezeAPI.refresh()
        catalog_mod.load_overrides()
        return {"warning": warning}

    def _db_done(self, result: dict) -> None:
        self.db_warning = result.get("warning", "")
        if not self._db_ready:
            self._db_ready = True
            self._db_failed_once = False
            bus.db_state_changed.emit(True)
            self._start_chat()
        sig = tuple(FreezeAPI.cached())
        if sig != self._freeze_sig:
            self._freeze_sig = sig
            bus.freeze_changed.emit()

    def _db_failed(self, e: BaseException) -> None:
        was_ready, first = self._db_ready, not self._db_failed_once
        self._db_failed_once = True
        if was_ready or first:   # her turda değil, yalnızca durum değişince yayınla
            log.warning("Veritabanı: %s", database.last_error() or e)
            self._db_ready = False
            if self.chat:
                self.chat.stop()
            bus.db_state_changed.emit(False)

    def refresh_shared(self) -> None:
        """Freeze ve ezmeleri hemen yeniden oku (yazma işleminden sonra)."""
        if database.db_configured():
            self.db_poller.trigger()

    # ---- Sohbet
    def _start_chat(self) -> None:
        if not ENV.CHAT_ENABLED:
            return
        from scripts.ChatAPI import ChatService
        if self.chat is None:
            user = current_user()
            self.chat = ChatService(user.id, user.name, self)
            self.chat.changed.connect(bus.presence_changed)
        self.chat.start()
        bus.presence_changed.emit()

    def _restart_chat(self) -> None:
        if self.chat:
            self.chat.stop()
            self.chat.deleteLater()
            self.chat = None
        if self._db_ready:
            self._start_chat()
        bus.presence_changed.emit()

    # ---- Gün dönümü
    def _tick_clock(self) -> None:
        d = today()
        if d != self._day:
            self._day = d
            bus.freeze_changed.emit()
            self.refresh_shared()

    # ---- Ayar değişikliği
    def on_settings(self, group_id: str, changed: list[str]) -> None:
        if group_id == "db":
            database.reset_engine()
            self._db_ready = False
            if self.chat:
                self.chat.stop()
            bus.db_state_changed.emit(False)
            self.db_poller.set_interval(min(ENV.DB_REFRESH_SEC, 60) * 1000)
            if database.db_configured():
                self.db_poller.start()
            else:
                self.db_poller.stop()
        elif group_id in ("devops", "urls"):
            self.catalog_poller.set_interval(ENV.DEVOPS_REFRESH_SEC * 1000)
            if group_id == "devops":
                self.catalog_poller.trigger()
            else:
                bus.catalog_changed.emit()
                self.health_poller.trigger()
        elif group_id == "health":
            self.health_poller.set_interval(ENV.HEALTH_INTERVAL_SEC * 1000)
            if ENV.HEALTH_ENABLED:
                self.health_poller.start()
            else:
                self.health_poller.stop()
        elif group_id == "k8s":
            K8sAPI.reset_clients()
            K8sAPI.write_kubeconfigs()
        elif group_id in ("chat", "general"):
            if group_id == "general":
                self.refresh_shared()   # adı dr_users'a yaz
            self._restart_chat()
        elif group_id == "freeze":
            bus.freeze_changed.emit()


def start_services(parent=None) -> Services:
    global _services
    _services = Services(parent)
    _services.start()
    return _services


def stop_services() -> None:
    if _services:
        _services.stop()


def services() -> Services | None:
    return _services


def refresh_shared() -> None:
    if _services:
        _services.refresh_shared()
