"""
Kubernetes -> scripts/K8sAPI.py

Ortam (Dev/Test/Prod) ve uygulama seçilir; deployment'lar, pod'lar, container log'u (canlı izleme),
olaylar ve tek seferlik komut. Prod'da her komut öncesi onay istenir ve değişiklik geçmişine yazılır.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QLineEdit, QPlainTextEdit, QSpinBox, QSplitter, QTabWidget,
                               QVBoxLayout, QWidget)

from _pages import _common as ui
from scripts import K8sAPI, database, relations
from scripts.apps import ENV_TITLES
from scripts.bus import bus
from scripts.config import ENV, redact
from scripts.fmt import ago, dt
from scripts.worker import run_async


class Page(ui.Page):
    def __init__(self):
        super().__init__("Kubernetes", f"Namespace: {ENV.K8S_NAMESPACE}. Container durumu, log, olaylar ve komut.")
        self.env = QComboBox()
        self.env.currentIndexChanged.connect(self.load)
        self.app = ui.app_combo(include_all="Tüm namespace")
        self.app.currentIndexChanged.connect(self._app_changed)
        self.add_tool(ui.label("Ortam"))
        self.add_tool(self.env)
        self.add_tool(ui.label("Uygulama"))
        self.add_tool(self.app)
        self.add_tool(ui.button("Yenile", slot=self.load))

        self.info = ui.label("", "muted", wrap=True)
        self.body.addWidget(self.info)
        split = QSplitter(Qt.Horizontal)
        left = QTabWidget()
        self.deps = ui.table(["Deployment", "Hazır", "İmaj", "Yaş"], stretch=2)
        self.pods = ui.table(["Pod", "Durum", "Hazır", "Yeniden başlama", "Yaş", "Node"])
        self.pods.itemSelectionChanged.connect(self._pod_selected)
        left.addTab(self.pods, "Pod'lar")
        left.addTab(self.deps, "Deployment'lar")
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.pod_title = ui.label("Bir pod seçin.", "h2")
        self.container = QComboBox()
        self.container.currentIndexChanged.connect(self._load_logs)
        rl.addLayout(ui.hbox(self.pod_title, None, ui.label("Container"), self.container))
        self.tabs = QTabWidget()

        logs = QWidget()
        ll = QVBoxLayout(logs)
        ll.setContentsMargins(6, 6, 6, 6)
        self.lines = QSpinBox()
        self.lines.setRange(10, 5000)
        self.lines.setValue(ENV.K8S_LOG_LINES)
        self.previous = QCheckBox("Önceki container (çöktüyse)")
        self.follow = QCheckBox("Canlı izle (3 sn)")
        self.follow.toggled.connect(lambda on: self._follow.start() if on else self._follow.stop())
        ll.addLayout(ui.hbox(ui.label("Satır"), self.lines, self.previous, self.follow, None,
                             ui.button("Log'u yenile", slot=self._load_logs),
                             ui.button("Terminalde kubectl", slot=self._kubectl_logs)))
        self.log_box = QPlainTextEdit()
        self.log_box.setObjectName("Console")
        self.log_box.setReadOnly(True)
        self.log_box.setLineWrapMode(QPlainTextEdit.NoWrap)
        ll.addWidget(self.log_box, 1)
        self.tabs.addTab(logs, "Log")

        self.events = ui.table(["Tür", "Neden", "Sayı", "Zaman", "Mesaj"], stretch=4)
        self.tabs.addTab(self.events, "Olaylar")

        exe = QWidget()
        el = QVBoxLayout(exe)
        el.setContentsMargins(6, 6, 6, 6)
        el.addWidget(ui.label("Seçili container'da /bin/sh -c ile tek seferlik komut çalışır; etkileşimli kabuk yoktur. "
                              "Prod'da her komut onay ister ve değişiklik geçmişine yazılır.", "muted", wrap=True))
        self.cmd = QLineEdit()
        self.cmd.setPlaceholderText("ör. env | sort   ·   ls -la /app   ·   cat /etc/resolv.conf")
        self.cmd.returnPressed.connect(self._exec)
        el.addLayout(ui.hbox(self.cmd, ui.button("Çalıştır", "primary", self._exec)))
        self.exec_out = QPlainTextEdit()
        self.exec_out.setObjectName("Console")
        self.exec_out.setReadOnly(True)
        el.addWidget(self.exec_out, 1)
        self.tabs.addTab(exe, "Komut")
        rl.addWidget(self.tabs, 1)
        split.addWidget(right)
        split.setSizes([560, 700])
        self.body.addWidget(split, 1)

        self._pods: list[K8sAPI.PodInfo] = []
        self._follow = QTimer(self)
        self._follow.setInterval(3000)
        self._follow.timeout.connect(lambda: self.isVisible() and self._load_logs(quiet=True))
        self._loaded = False

        ui.on(bus.settings_changed, self, lambda g: g == "k8s" and self._fill_envs())
        ui.on(bus.catalog_changed, self, self.app.refill)
        ui.on(bus.app_selected, self, self._external_select)
        ui.on(bus.freeze_changed, self, self._banner)
        self._fill_envs()

    # ---- ortam / uygulama
    def _fill_envs(self) -> None:
        cur = self.env.currentData()
        self.env.blockSignals(True)
        self.env.clear()
        for e in relations.k8s_envs():
            self.env.addItem(ENV_TITLES[e], e)
        ui.select_combo_data(self.env, cur)
        self.env.blockSignals(False)
        self._banner()
        if self._loaded:
            self.load()

    def _banner(self) -> None:
        if K8sAPI.is_demo():
            self.banner.show_message("demo", "Örnek veri: hiçbir ortam için cluster adresi ve token girilmemiş "
                                             "(Ayarlar → Kubernetes).", "Ayarlar", lambda: relations.open_page("settings"))
        elif self.env.currentData() == "prod" and relations.prod_warning("prod"):
            self.banner.show_message("warning", "❄ " + relations.prod_warning("prod"))
        else:
            self.banner.clear()

    def _external_select(self, name: str) -> None:
        if ui.select_combo_data(self.app, name) and self.isVisible():
            self.load()

    def _app_changed(self) -> None:
        if self.app.currentData():
            relations.select_app(self.app.currentData())
        self.load()

    def on_shown(self) -> None:
        if relations.selected_app():
            ui.select_combo_data(self.app, relations.selected_app())
        if not self._loaded:
            self._loaded = True
            self.load()

    # ---- yükleme
    def load(self) -> None:
        env = self.env.currentData()
        if not env:
            self.info.setText("Ortam yok.")
            return
        self._banner()
        app_name = self.app.currentData()
        self.info.setText("yükleniyor…")

        def work():
            if app_name:
                w = relations.app_workload(app_name, env)
                return {"deps": [w.deployment] if w.deployment else [], "pods": w.pods, "warnings": w.warnings,
                        "how": w.how, "found": w.found}
            c = K8sAPI.cluster(env)
            out = {"deps": [], "pods": [], "warnings": [], "how": "", "found": True}
            for kind, fn, conv in (("deps", c.deployments, K8sAPI.deployment_info), ("pods", c.pods, K8sAPI.pod_info)):
                try:
                    out[kind] = [conv(x) for x in fn()]
                except K8sAPI.K8sForbidden as e:
                    out["warnings"].append(str(e))
            return out

        def done(r):
            if env != self.env.currentData() or app_name != self.app.currentData():
                return
            self._pods = r["pods"]
            parts = [f"{ENV_TITLES[env]} · {len(r['pods'])} pod"]
            if app_name:
                parts.append(f"eşleme: {r['how']}" if r["found"] else "bu ortamda iş yükü bulunamadı (aktarılmamış olabilir)")
            self.info.setText(" · ".join(parts) + ("\n" + "\n".join(r["warnings"]) if r["warnings"] else ""))
            ui.fill_table(self.deps, [[ui.item(d.name, data=d.name, bold=True),
                                       ui.item(f"{d.ready}/{d.desired}", "Running" if d.ready >= d.desired > 0 else "failed"),
                                       ui.item(", ".join(i.split("/")[-1] for i in d.images), tooltip="\n".join(d.images)),
                                       ui.item(d.age)] for d in r["deps"]])
            ui.fill_table(self.pods, [[ui.item(p.name, data=p.name), ui.item(f"● {p.status}", self._pod_state(p)),
                                       ui.item(p.ready), ui.item(p.restarts, "failed" if p.restarts > 5 else None),
                                       ui.item(p.age), ui.item(p.node, tooltip=p.ip)] for p in r["pods"]])
            if self.pods.currentRow() < 0 and r["pods"]:
                self.pods.selectRow(0)

        def failed(e):
            self.info.setText(f"{ENV_TITLES.get(env, env)}: {e}")
            self.pods.setRowCount(0)
            self.deps.setRowCount(0)

        run_async(work, on_done=done, on_error=failed, owner=self)

    @staticmethod
    def _pod_state(p: K8sAPI.PodInfo) -> str:
        if p.status == "Running" and p.ready.split("/")[0] == p.ready.split("/")[1]:
            return "Running"
        return "failed" if p.status in ("CrashLoopBackOff", "Error", "ImagePullBackOff", "ErrImagePull", "OOMKilled") \
            else "Pending"

    def _current_pod(self) -> K8sAPI.PodInfo | None:
        name = ui.row_data(self.pods)
        return next((p for p in self._pods if p.name == name), None)

    def _pod_selected(self) -> None:
        pod = self._current_pod()
        if not pod:
            return
        self.pod_title.setText(pod.name)
        self.container.blockSignals(True)
        self.container.clear()
        self.container.addItems(pod.containers)
        self.container.blockSignals(False)
        self._load_logs()
        env = self.env.currentData()
        run_async(lambda: K8sAPI.event_rows(K8sAPI.cluster(env).events(pod.name)),
                  on_done=lambda rows: ui.fill_table(self.events, [[
                      ui.item(e["type"], "failed" if e["type"] == "Warning" else None), ui.item(e["reason"]),
                      ui.item(e["count"]), ui.item(ago(e["when"]), tooltip=dt(e["when"], True)),
                      ui.item(e["message"], tooltip=e["message"])] for e in rows]),
                  on_error=lambda e: ui.fill_table(self.events, [[ui.item(""), ui.item(""), ui.item(""), ui.item(""),
                                                                   ui.item(str(e), "failed")]]), owner=self)

    def _load_logs(self, quiet: bool = False) -> None:
        pod, container, env = self._current_pod(), self.container.currentText(), self.env.currentData()
        if not pod or not container:
            return
        if not quiet:
            self.log_box.setPlainText("yükleniyor…")
        bar = self.log_box.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4

        def done(text):
            if self._current_pod() is not pod and (self._current_pod() is None or self._current_pod().name != pod.name):
                return
            pos = bar.value()
            self.log_box.setPlainText(text)
            bar.setValue(bar.maximum() if at_bottom or not quiet else pos)

        run_async(lambda: K8sAPI.cluster(env).logs(pod.name, container, self.lines.value(), self.previous.isChecked()),
                  on_done=done, on_error=lambda e: self.log_box.setPlainText(str(e)), owner=self)

    def _kubectl_logs(self) -> None:
        pod, env = self._current_pod(), self.env.currentData()
        if pod:
            relations.open_in_terminal(f"kubectl logs {pod.name} -c {self.container.currentText()} --tail {self.lines.value()}",
                                       env if env in K8sAPI.configured_envs() else "")

    def _exec(self) -> None:
        pod, container, env = self._current_pod(), self.container.currentText(), self.env.currentData()
        command = self.cmd.text().strip()
        if not pod or not command:
            return
        if env == "prod":
            warn = relations.prod_warning("prod")
            text = f"PROD ortamında çalıştırılacak:\n\n{pod.name}/{container}\n$ {command}\n\n" + \
                   (f"{warn}\n\n" if warn else "") + "Devam edilsin mi?"
            if not ui.confirm(self, "Prod komutu", text, danger=True):
                return
        self.exec_out.appendPlainText(f"── {ENV_TITLES[env]} · {pod.name}/{container} $ {command}")
        app_name = self.app.currentData()

        def work():
            out = K8sAPI.cluster(env).exec(pod.name, container, command)
            if env == "prod":
                database.audit_now("k8s", "exec", "pod", pod.name, {"title": redact(command)[:200], "env": env},
                                   app_name)
            return out

        run_async(work, on_done=lambda out: self.exec_out.appendPlainText(out.rstrip() + "\n"),
                  on_error=lambda e: self.exec_out.appendPlainText(f"[hata] {e}\n"), owner=self)
