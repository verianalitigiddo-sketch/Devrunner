"""
DevOps -> scripts/DevopsAPI.py

Önekle başlayan tüm repolar, CI (…_Container-CI) ve CD (…_Container-CD) pipeline'larının son durumu,
build geçmişi, başarısız adım ve log'u, son commit'ler ve açık PR'lar. Salt okunur.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QPlainTextEdit, QSplitter, QTabWidget, QVBoxLayout,
                               QWidget)

from _pages import _common as ui
from scripts import DevopsAPI, relations
from scripts.bus import bus
from scripts.config import ENV
from scripts.DevopsAPI import STATE_TITLES
from scripts.fmt import ago, dt, duration
from scripts.worker import run_async


class LogDialog(QDialog):
    def __init__(self, title: str, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(980, 620)
        lay = QVBoxLayout(self)
        box = QPlainTextEdit(text)
        box.setReadOnly(True)
        box.setObjectName("Console")
        box.setLineWrapMode(QPlainTextEdit.NoWrap)
        lay.addWidget(box)
        box.moveCursor(box.textCursor().End)
        b = QDialogButtonBox(QDialogButtonBox.Close)
        b.rejected.connect(self.reject)
        lay.addWidget(b)


class HistoryPanel(QWidget):
    """Bir pipeline'ın (CI veya CD) geçmişi + seçili build'in adımları."""

    def __init__(self, kind: str):
        super().__init__()
        self.kind = kind
        self.pipeline: DevopsAPI.PipelineInfo | None = None
        self.builds: list[DevopsAPI.BuildInfo] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.info = ui.label("", "muted", wrap=True)
        self.open_btn = ui.button("DevOps'ta aç", "link", self._open_pipeline)
        lay.addLayout(ui.hbox(self.info, None, self.open_btn))
        split = QSplitter(Qt.Vertical)
        self.table = ui.table(["#", "Durum", "Dal / Ortam", "Commit", "Tetikleyen", "Süre", "Zaman"], stretch=2)
        self.table.itemSelectionChanged.connect(self._build_selected)
        self.table.cellDoubleClicked.connect(lambda r, _c: ui.open_url(self.builds[r].web_url))
        steps_box = QWidget()
        sl = QVBoxLayout(steps_box)
        sl.setContentsMargins(0, 6, 0, 0)
        self.steps_title = ui.label("Adımları görmek için bir build seçin.", "muted")
        self.log_btn = ui.button("Log'u göster", slot=self._show_log)
        self.log_btn.setEnabled(False)
        sl.addLayout(ui.hbox(self.steps_title, None, self.log_btn))
        self.steps = ui.table(["Adım", "Durum", "Süre", "Hata"], stretch=3)
        self.steps.itemSelectionChanged.connect(lambda: self.log_btn.setEnabled(ui.row_data(self.steps) is not None))
        self.steps.cellDoubleClicked.connect(lambda *_: self._show_log())
        sl.addWidget(self.steps)
        split.addWidget(self.table)
        split.addWidget(steps_box)
        split.setSizes([300, 220])
        lay.addWidget(split, 1)
        self._step_items: list[DevopsAPI.TimelineStep] = []

    def set_pipeline(self, pipeline: DevopsAPI.PipelineInfo | None, label: str) -> None:
        self.pipeline = pipeline
        self.table.setRowCount(0)
        self.steps.setRowCount(0)
        self.builds = []
        self.open_btn.setVisible(pipeline is not None)
        if pipeline is None:
            self.info.setText(f"Bu uygulamanın {label} pipeline'ı yok.")
            return
        kind = "klasik Release" if pipeline.kind == "release" else "YAML / build pipeline"
        self.info.setText(f"{pipeline.name} · {kind} · yükleniyor…")
        run_async(DevopsAPI.history, pipeline, on_done=lambda b, p=pipeline: self._show(p, b),
                  on_error=lambda e: self.info.setText(f"{pipeline.name}: {e}"), owner=self)

    def _show(self, pipeline, builds) -> None:
        if pipeline is not self.pipeline:
            return
        self.builds = builds
        kind = "klasik Release" if pipeline.kind == "release" else "YAML / build pipeline"
        self.info.setText(f"{pipeline.name} · {kind} · son {len(builds)} çalışma (çift tık: DevOps'ta aç)")
        rows = []
        for b in builds:
            rows.append([ui.item(b.number, data=b.id), ui.item(f"● {STATE_TITLES[b.state]}", b.state),
                         ui.item(b.stage or b.branch), ui.item(b.commit), ui.item(b.requested_by),
                         ui.item(duration(b.duration_sec)), ui.item(ago(b.when), tooltip=dt(b.when, True))])
        ui.fill_table(self.table, rows)
        failed = next((i for i, b in enumerate(builds) if b.state in ("failed", "partial")), None)
        if failed == 0:
            self.table.selectRow(0)

    def _build_selected(self) -> None:
        r = self.table.currentRow()
        if r < 0 or r >= len(self.builds):
            return
        b = self.builds[r]
        self.steps.setRowCount(0)
        if b.kind == "release":
            self.steps_title.setText(f"Release {b.number}: adımlar için DevOps'ta açın.")
            self.log_btn.setEnabled(False)
            return
        self.steps_title.setText(f"Build {b.number} adımları yükleniyor…")

        def done(steps):
            if not self.builds or self.builds[self.table.currentRow()].id != b.id:
                return
            self._step_items = steps
            failed = [s for s in steps if s.state == "failed"]
            self.steps_title.setText(f"Build {b.number}: {len(steps)} adım" +
                                     (f", düşen adım: {failed[0].name}" if failed else ""))
            rows = []
            from scripts.fmt import parse_dt
            for s in steps:
                st, fi = parse_dt(s.started), parse_dt(s.finished)
                rows.append([ui.item(s.name, data=s.log_id), ui.item(f"● {STATE_TITLES.get(s.state, s.state)}", s.state),
                             ui.item(duration((fi - st).total_seconds()) if st and fi else ""),
                             ui.item(" | ".join(s.errors), "failed" if s.errors else None, tooltip="\n".join(s.errors))])
            ui.fill_table(self.steps, rows)
            if failed:
                self.steps.selectRow(steps.index(failed[0]))

        run_async(DevopsAPI.timeline, b.id, on_done=done,
                  on_error=lambda e: self.steps_title.setText(f"Adımlar alınamadı: {e}"), owner=self)

    def _show_log(self) -> None:
        r = self.table.currentRow()
        log_id = ui.row_data(self.steps)
        if r < 0 or log_id is None:
            return
        b = self.builds[r]
        name = self.steps.item(self.steps.currentRow(), 0).text()
        run_async(DevopsAPI.build_log, b.id, log_id,
                  on_done=lambda text: LogDialog(f"{b.definition_name} #{b.number} · {name}", text, self).exec(),
                  on_error=lambda e: ui.show_error(self, e), owner=self)

    def _open_pipeline(self) -> None:
        if self.pipeline:
            ui.open_url(self.pipeline.web_url)


class Page(ui.Page):
    def __init__(self):
        super().__init__("DevOps", "Repolar ve CI/CD pipeline'ları (Azure DevOps, salt okunur)")
        self.sync_label = self.add_tool(ui.label("", "muted"))
        self.add_tool(ui.button("Yenile", slot=relations.refresh_catalog))

        split = QSplitter(Qt.Horizontal)
        self.table = ui.table(["Uygulama", "CI", "Son CI", "CD", "Son CD"])
        self.table.itemSelectionChanged.connect(self._selected)
        split.addWidget(self.table)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.title = ui.label("Bir uygulama seçin.", "h2")
        self.links = ui.label("", "muted")
        self.links.setOpenExternalLinks(True)
        self.freeze_warn = ui.label("", wrap=True)
        rl.addWidget(self.title)
        rl.addWidget(self.links)
        rl.addWidget(self.freeze_warn)
        self.tabs = QTabWidget()
        self.ci = HistoryPanel("ci")
        self.cd = HistoryPanel("cd")
        self.commits = ui.table(["Commit", "Mesaj", "Yazan", "Zaman"], stretch=1)
        self.prs = ui.table(["#", "Başlık", "Açan", "Dal", "Zaman"], stretch=1)
        self.prs.cellDoubleClicked.connect(lambda r, _c: ui.open_url(self.prs.item(r, 0).data(Qt.UserRole)))
        self.tabs.addTab(self.ci, "CI geçmişi")
        self.tabs.addTab(self.cd, "CD geçmişi")
        self.tabs.addTab(self.commits, "Son commit'ler")
        self.tabs.addTab(self.prs, "Açık PR'lar")
        rl.addWidget(self.tabs, 1)
        split.addWidget(right)
        split.setSizes([480, 760])
        self.body.addWidget(split, 1)

        self._app = ""
        ui.on(bus.catalog_changed, self, self.render)
        ui.on(bus.app_selected, self, self.show_app)
        ui.on(bus.freeze_changed, self, self._freeze)
        self.render()

    def render(self) -> None:
        ov = relations.overview()
        err = relations.overview_error()
        if err:
            self.banner.show_message("error", f"DevOps'a ulaşılamadı: {err}" +
                                     ("\nSon bilinen durum gösteriliyor." if ov else ""),
                                     "Ayarlar", lambda: relations.open_page("settings"))
        elif ov and ov.source == "demo":
            self.banner.show_message("demo", "Örnek veri: Azure DevOps PAT'i girilmemiş. Ayarlar → Azure DevOps "
                                             f"({ENV.DEVOPS_URL}/{ENV.DEVOPS_PROJECT}).",
                                     "Ayarlar", lambda: relations.open_page("settings"))
        elif ov and ov.warnings:
            self.banner.show_message("warning", "\n".join(ov.warnings))
        else:
            self.banner.clear()
        if ov and ov.fetched_at:
            self.sync_label.setText(f"Son eşitleme: {ago(ov.fetched_at)}")
        rows = []
        for a in relations.catalog():
            p = ov.pipelines.get(a.name) if ov else None
            cells = [ui.item(a.name, data=a.name, bold=True)]
            for pipe in ((p.ci if p else None), (p.cd if p else None)):
                if not pipe:
                    cells += [ui.item("pipeline yok", "missing"), ui.item("")]
                    continue
                kind = " (release)" if pipe.kind == "release" else ""
                cells += [ui.item(f"● {STATE_TITLES[pipe.state]}{kind}", pipe.state,
                                  tooltip=pipe.name),
                          ui.item(ago(pipe.last.when) if pipe.last else "", tooltip=dt(pipe.last.when) if pipe.last else "")]
            rows.append(cells)
        ui.fill_table(self.table, rows)
        if self._app:
            self.show_app(self._app, force=False)
        elif relations.selected_app() or rows:
            self.show_app(relations.selected_app() or rows[0][0].data(Qt.UserRole))
        self._freeze()

    def _selected(self) -> None:
        name = ui.row_data(self.table)
        if name:
            relations.select_app(name)

    def show_app(self, name: str, force: bool = True) -> None:
        a = relations.app(name)
        if not a or (not force and name == self._app and self.ci.pipeline is not None):
            return
        self._app = name
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).data(Qt.UserRole) == name and self.table.currentRow() != r:
                self.table.blockSignals(True)
                self.table.selectRow(r)
                self.table.blockSignals(False)
        self.title.setText(a.name)
        self.links.setText(f"{a.repo} · <a href='{a.web_url}'>repo</a> · varsayılan dal: {a.default_branch or '?'}")
        p = relations.app_pipelines(name)
        self.ci.set_pipeline(p.ci if p else None, "CI")
        self.cd.set_pipeline(p.cd if p else None, "CD")
        self.commits.setRowCount(0)
        self.prs.setRowCount(0)
        if a.repo_id:
            run_async(DevopsAPI.commits, a.repo_id, on_done=lambda rows, n=name: self._commits(n, rows),
                      on_error=lambda e: None, owner=self)
            run_async(DevopsAPI.pull_requests, a.repo_id, on_done=lambda rows, n=name: self._prs(n, rows),
                      on_error=lambda e: None, owner=self)
        self._freeze()

    def _commits(self, name, rows) -> None:
        if name != self._app:
            return
        ui.fill_table(self.commits, [[ui.item(c["id"]), ui.item(c["message"]), ui.item(c["author"]),
                                      ui.item(ago(c["date"]), tooltip=dt(c["date"]))] for c in rows])

    def _prs(self, name, rows) -> None:
        if name != self._app:
            return
        self.tabs.setTabText(3, f"Açık PR'lar ({len(rows)})")
        ui.fill_table(self.prs, [[ui.item(p["id"], data=p["url"]), ui.item(("[taslak] " if p["draft"] else "") + p["title"]),
                                  ui.item(p["author"]), ui.item(f"{p['source']} → {p['target']}"),
                                  ui.item(ago(p["created"]))] for p in rows])

    def _freeze(self) -> None:
        warn = relations.prod_warning("cd")
        self.freeze_warn.setVisible(bool(warn))
        if warn:
            self.freeze_warn.setText(f"❄ {warn}. CD çalıştırmadan önce ekiple teyitleşin.")
            self.freeze_warn.setStyleSheet(f"color: {ui.theme.color('danger')}; font-weight: 700;")
        self.tabs.setTabText(1, "CD geçmişi ❄" if warn else "CD geçmişi")
