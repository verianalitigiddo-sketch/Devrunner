"""
Uygulamalar: açılış sayfası ve modüllerin kesişimi.

Bu sayfa hiçbir modül API'sini doğrudan çağırmaz; her şeyi scripts/relations.py üzerinden alır.
Satır seçilince app_selected yayınlanır; sağdaki ayrıntı panelinde pipeline, pod, adres, not,
Bilge talebi, değişiklik geçmişi ve "Terminalde dene" kısayolları birlikte görünür.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QSplitter, QTabWidget,
                               QTextBrowser, QVBoxLayout, QWidget)

from _pages import _common as ui
from scripts import database, relations, theme
from scripts.apps import ENV_TITLES, ENVS
from scripts.bus import bus
from scripts.DevopsAPI import STATE_TITLES as PIPE_TITLES
from scripts.fmt import ago, dt, esc, ms
from scripts.HealthAPI import STATE_TITLES as HEALTH_TITLES
from scripts.worker import run_async

MODULE_TITLES = {"notes": "Not", "bilge": "Bilge", "freeze": "Freeze", "settings": "Ayar", "apps": "Uygulama",
                 "k8s": "Kubernetes"}
ACTION_TITLES = {"create": "ekledi", "update": "güncelledi", "delete": "sildi", "override": "eşlemeyi değiştirdi",
                 "exec": "komut çalıştırdı"}


class OverrideDialog(QDialog):
    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        from scripts import apps
        self.setWindowTitle(f"{name}: adres ve eşleme")
        self.name = name
        cur = apps.overrides(name)
        a = relations.app(name)
        form = QFormLayout(self)
        form.addRow(ui.label("Boş bırakılan alan kurala göre türetilir.", "muted", wrap=True))
        self.edits = {}
        for env in ENVS:
            e = QLineEdit(cur.get(f"url_{env}") or "")
            e.setPlaceholderText(a.url(env) if a and not cur.get(f"url_{env}") else "")
            self.edits[f"url_{env}"] = e
            form.addRow(f"{ENV_TITLES[env]} adresi", e)
        e = QLineEdit(cur.get("k8s_workload") or "")
        e.setPlaceholderText(a.host_name if a else "")
        self.edits["k8s_workload"] = e
        form.addRow("Kubernetes iş yükü", e)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.resize(560, 0)

    def values(self) -> dict[str, str]:
        return {k: e.text() for k, e in self.edits.items()}


class AppsTab(QWidget):
    COLS = ["Uygulama", "CI", "CD", "Dev", "Test", "Prod", "Pod", "Not", "Talep"]

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.counts: tuple[dict, dict] = ({}, {})
        self.pods: dict[str, dict[str, str]] = {}
        self.detail_data: dict = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Uygulama ara…")
        self.search.textChanged.connect(self.render_table)
        lay.addLayout(ui.hbox(self.search, ui.button("Yenile", slot=self.refresh_all), None))

        split = QSplitter(Qt.Horizontal)
        self.table = ui.table(self.COLS)
        self.table.itemSelectionChanged.connect(self._selected)
        self.detail = QTextBrowser()
        self.detail.setOpenLinks(False)
        self.detail.anchorClicked.connect(self._link)
        split.addWidget(self.table)
        split.addWidget(self.detail)
        split.setSizes([640, 520])
        lay.addWidget(split, 1)

    # ---- veri
    def refresh_all(self) -> None:
        relations.refresh_catalog()
        self.load_side_data()

    def load_side_data(self) -> None:
        def load():
            counts = ({}, {})
            try:
                counts = relations.app_counts()
            except Exception:   # noqa: BLE001 - sayaçlar olmadan da tablo çalışır
                pass
            return counts, relations.pod_summary()

        def done(result):
            self.counts, self.pods = result
            self.render_table()

        run_async(load, on_done=done, owner=self)

    def render_table(self) -> None:
        q = self.search.text().strip().lower()
        notes, reqs = self.counts
        rows = []
        center = Qt.AlignCenter
        for a in relations.catalog():
            if q and q not in a.name.lower():
                continue
            p = relations.app_pipelines(a.name)
            ci = p.ci if p else None
            cd = p.cd if p else None
            cells = [ui.item(a.name, data=a.name, bold=True)]
            for pipe in (ci, cd):
                state = pipe.state if pipe else "none"
                cells.append(ui.item(f"● {PIPE_TITLES[state]}" if pipe else "–", state if pipe else "missing",
                                     tooltip=f"{pipe.name}\n{dt(pipe.last.when) if pipe.last else ''}" if pipe else
                                     "Pipeline yok"))
            health = relations.app_health(a.name)
            for env in ENVS:
                h = health.get(env)
                if not h:
                    cells.append(ui.item("…", "unknown"))
                    continue
                text = "–" if h.state == "missing" else f"● {ms(h.ms)}" if h.ms is not None else f"● {h.title}"
                tip = f"{h.url}\n{h.title}" + (f" · HTTP {h.status_code}" if h.status_code else "") + \
                      (f"\n{h.error}" if h.error else "")
                cells.append(ui.item(text, h.state, tooltip=tip))
            pods = self.pods.get(a.name, {})
            cells.append(ui.item(" · ".join(f"{ENV_TITLES[e][0]} {v}" for e, v in pods.items()) or "–",
                                 tooltip="Hazır / toplam pod (D=Dev, T=Test, P=Prod)"))
            cells.append(ui.item(notes.get(a.name, "") or "", align=center))
            cells.append(ui.item(reqs.get(a.name, "") or "", align=center))
            rows.append(cells)
        ui.fill_table(self.table, rows)
        if self.table.currentRow() < 0 and rows:
            target = relations.selected_app()
            row = next((r for r in range(self.table.rowCount())
                        if self.table.item(r, 0).data(Qt.UserRole) == target), 0)
            self.table.selectRow(row)
        if not rows:
            self.detail.setHtml(self._empty_html())

    def _empty_html(self) -> str:
        err = relations.overview_error()
        if err:
            return f"<h3>DevOps'tan uygulama listesi alınamadı</h3><p>{esc(err)}</p><p><a href='page:settings'>Ayarlar</a></p>"
        return "<p>Uygulama listesi yükleniyor…</p>"

    def _selected(self) -> None:
        name = ui.row_data(self.table)
        if name:
            relations.select_app(name)

    def show_app(self, name: str) -> None:
        self.detail_data = {"name": name}
        self.detail.setHtml(self._html(name))

        def load():
            out = {"name": name, "workloads": {}, "errors": []}
            for env in relations.k8s_envs():
                try:
                    out["workloads"][env] = relations.app_workload(name, env)
                except Exception as e:   # noqa: BLE001
                    out["errors"].append(f"Kubernetes {ENV_TITLES[env]}: {e}")
            if database.is_ready():
                try:
                    out["notes"] = relations.app_notes(name)
                    out["requests"] = relations.app_requests(name)
                    out["history"] = relations.app_history(name, 15)
                except Exception as e:   # noqa: BLE001
                    out["errors"].append(f"Veritabanı: {e}")
            return out

        def done(data):
            if data["name"] == self.detail_data.get("name"):
                self.detail_data = data
                self.detail.setHtml(self._html(name))

        run_async(load, on_done=done, owner=self)

    # ---- ayrıntı HTML
    def _html(self, name: str) -> str:
        a = relations.app(name)
        if not a:
            return self._empty_html()
        c = theme.palette()
        d = self.detail_data
        muted = f"color:{c['muted']}"

        def dot(state, text):
            return f"<span style='color:{theme.state_color(state)}'>● {esc(text)}</span>"

        h = [f"<h2 style='margin-bottom:0'>{esc(a.name)}</h2>",
             f"<div style='{muted}'>{esc(a.repo)} · <a href='url:{esc(a.web_url)}'>repo</a> · "
             f"<a href='page:devops'>DevOps</a> · <a href='edit:{esc(a.name)}'>adres / eşleme düzenle</a></div>"]
        warn = relations.prod_warning("cd")
        if warn:
            h.append(f"<p style='color:{c['danger']}'><b>{esc(warn)}</b></p>")

        # Pipeline'lar
        h.append("<h3>Pipeline'lar</h3><table cellpadding='3'>")
        p = relations.app_pipelines(name)
        for label, pipe in (("CI", p.ci if p else None), ("CD", p.cd if p else None)):
            if not pipe:
                h.append(f"<tr><td><b>{label}</b></td><td style='{muted}'>pipeline yok</td></tr>")
                continue
            last = pipe.last
            info = f"#{esc(last.number)} · {esc(last.branch)} · {esc(last.requested_by)} · {ago(last.when)}" if last else ""
            h.append(f"<tr><td><b>{label}</b></td><td>{dot(pipe.state, PIPE_TITLES[pipe.state])}</td>"
                     f"<td style='{muted}'>{info}</td><td><a href='url:{esc(last.web_url if last else pipe.web_url)}'>aç</a></td></tr>")
        h.append("</table>")

        # Adresler + sağlık + terminal
        h.append("<h3>Adresler</h3><table cellpadding='3'>")
        health = relations.app_health(name)
        muted_envs = a.muted_envs()
        for env in ENVS:
            st = health.get(env)
            state = st.state if st else "unknown"
            extra = []
            if st and st.ms is not None:
                extra.append(ms(st.ms))
            if st and st.status_code:
                extra.append(f"HTTP {st.status_code}")
            if st and st.error:
                extra.append(st.error)
            if env in muted_envs:
                extra.append("sessizde")
            url = a.url(env)
            h.append(f"<tr><td><b>{ENV_TITLES[env]}</b></td><td>{dot(state, HEALTH_TITLES.get(state, state))}</td>"
                     f"<td><a href='url:{esc(url)}'>{esc(url)}</a></td><td style='{muted}'>{esc(' · '.join(extra))}</td>"
                     f"<td><a href='term::nslookup {esc(a.host(env))}'>nslookup</a> "
                     f"<a href='term::curl -k -I -s -m 15 {esc(url)}'>curl</a></td></tr>")
        h.append("</table>")

        # Kubernetes
        h.append("<h3>Kubernetes</h3>")
        if "workloads" not in d:
            h.append(f"<p style='{muted}'>yükleniyor…</p>")
        else:
            for env, w in d["workloads"].items():
                if not w.found:
                    h.append(f"<p><b>{ENV_TITLES[env]}</b> <span style='{muted}'>iş yükü bulunamadı (aktarılmamış olabilir)</span></p>")
                    continue
                dep = w.deployment
                img = f" · {esc(dep.images[0].split('/')[-1])}" if dep and dep.images else ""
                h.append(f"<p><b>{ENV_TITLES[env]}</b> pod {w.ready_text}{img} <span style='{muted}'>({esc(w.how)})</span> "
                         f"<a href='k8s:{env}'>ayrıntı</a></p><ul>")
                for pod in w.pods:
                    state = "Running" if pod.status == "Running" and pod.ready.split("/")[0] == pod.ready.split("/")[1] \
                        else pod.status
                    h.append(f"<li>{dot(state, pod.status)} {esc(pod.name)} <span style='{muted}'>"
                             f"{pod.ready} · {pod.restarts} yeniden başlama · {esc(pod.age)}</span></li>")
                h.append("</ul>")
                for wtxt in w.warnings:
                    h.append(f"<p style='color:{c['warning']}'>{esc(wtxt)}</p>")

        # Ortak veri
        if not database.is_ready():
            h.append(f"<h3>Notlar ve talepler</h3><p style='{muted}'>{esc(database.status_text())}</p>")
        elif "notes" in d:
            h.append(f"<h3>Notlar ({len(d['notes'])})</h3>")
            for n in d["notes"][:8]:
                pin = "📌 " if n["pinned"] else ""
                h.append(f"<p><b>{pin}{esc(n['title'])}</b> <span style='{muted}'>{esc(n['created_by'])} · "
                         f"{ago(n['updated_at'] or n['created_at'])}</span><br>{esc(n['body'][:240])}</p>")
            h.append("<p><a href='page:notes'>Notlara git</a></p>")
            h.append(f"<h3>Bilge talepleri ({len(d['requests'])})</h3><ul>")
            for r in d["requests"][:10]:
                h.append(f"<li><a href='url:{esc(r['url'])}'>{esc(r['title'])}</a> <span style='{muted}'>{esc(r['status'])}</span></li>")
            h.append("</ul><p><a href='page:bilge'>Taleplere git</a></p>")
            h.append("<h3>Değişiklik geçmişi</h3><ul>")
            for e in d["history"]:
                title = (e.get("detail") or {}).get("title") or ""
                h.append(f"<li><span style='{muted}'>{dt(e['at'])}</span> {esc(e['user_name'])} "
                         f"{esc(MODULE_TITLES.get(e['module'], e['module']))} {esc(ACTION_TITLES.get(e['action'], e['action']))}"
                         f" {esc(title)}</li>")
            h.append("</ul>")
        for err in d.get("errors", []):
            h.append(f"<p style='color:{c['danger']}'>{esc(err)}</p>")
        return "".join(h)

    def _link(self, url) -> None:
        s = url.toString()
        scheme, _, rest = s.partition(":")
        if scheme == "url":
            ui.open_url(rest)
        elif scheme == "page":
            relations.open_page(rest)
        elif scheme == "k8s":
            relations.open_page("k8s")
        elif scheme == "term":
            env, _, cmd = rest.partition(":")
            relations.open_in_terminal(cmd, env)
        elif scheme == "edit":
            self._edit_override(rest)

    def _edit_override(self, name: str) -> None:
        if not database.is_ready():
            ui.show_error(self, "Eşleme ortak veritabanında tutulur: " + database.status_text())
            return
        dlg = OverrideDialog(name, self)
        if dlg.exec() != QDialog.Accepted:
            return
        run_async(relations.set_override, name, dlg.values(),
                  on_done=lambda _r: (self.render_table(), self.show_app(name), relations.refresh_health()),
                  on_error=lambda e: ui.show_error(self, e), owner=self)


class HistoryTab(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.module = QComboBox()
        self.module.addItem("Tüm modüller", None)
        for k, v in MODULE_TITLES.items():
            self.module.addItem(v, k)
        self.module.currentIndexChanged.connect(self.load)
        self.app = ui.app_combo(include_all="Tüm uygulamalar")
        self.app.currentIndexChanged.connect(self.load)
        lay.addLayout(ui.hbox(self.module, self.app, ui.button("Yenile", slot=self.load), None))
        self.info = ui.label("", "muted")
        lay.addWidget(self.info)
        self.table = ui.table(["Zaman", "Kişi", "Modül", "İşlem", "Uygulama", "Ayrıntı"], stretch=5)
        lay.addWidget(self.table, 1)

    def load(self) -> None:
        if not database.is_ready():
            self.info.setText(ui.db_unavailable_text())
            self.table.setRowCount(0)
            return
        self.info.setText("Ortak kayıtlar (notlar, talepler, freeze, eşlemeler, ayar değişiklikleri). Secret değerler yazılmaz.")

        def done(rows):
            out = []
            for r in rows:
                detail = r.get("detail") or {}
                text = detail.get("title") or ", ".join(detail.get("labels") or []) or \
                    ", ".join(f"{k}: {v}" for k, v in detail.items() if k not in ("fields",))
                out.append([ui.item(dt(r["at"], True)), ui.item(r["user_name"]),
                            ui.item(MODULE_TITLES.get(r["module"], r["module"])),
                            ui.item(ACTION_TITLES.get(r["action"], r["action"])), ui.item(r["app_name"] or ""),
                            ui.item(text, tooltip=str(detail))])
            ui.fill_table(self.table, out)

        run_async(database.history, app_name=self.app.currentData(), module=self.module.currentData(), limit=300,
                  on_done=done, on_error=lambda e: self.info.setText(str(e)), owner=self)


class Page(ui.Page):
    def __init__(self):
        super().__init__("Uygulamalar", "DDDB.Iz.Anm altındaki tüm uygulamalar: pipeline, erişilebilirlik, pod, not ve talepler")
        self.tabs = QTabWidget()
        self.apps = AppsTab(self)
        self.history = HistoryTab()
        self.tabs.addTab(self.apps, "Uygulamalar")
        self.tabs.addTab(self.history, "Değişiklik geçmişi")
        self.tabs.currentChanged.connect(lambda i: self.history.load() if i == 1 else None)
        self.body.addWidget(self.tabs)

        ui.on(bus.catalog_changed, self, self._catalog_changed)
        ui.on(bus.health_updated, self, self.apps.render_table)
        ui.on(bus.app_selected, self, self.apps.show_app)
        ui.on(bus.data_changed, self, lambda _k: self.apps.load_side_data())
        ui.on(bus.db_state_changed, self, lambda _ok: (self.apps.load_side_data(), self._update_banner()))
        ui.on(bus.theme_changed, self, lambda _t: self.apps.show_app(relations.selected_app()) if relations.selected_app() else None)
        ui.on(bus.freeze_changed, self, lambda: self.apps.show_app(relations.selected_app()) if relations.selected_app() else None)
        self._timer = QTimer(self)
        self._timer.timeout.connect(lambda: self.isVisible() and self.apps.load_side_data())
        self._timer.start(60_000)
        self._catalog_changed()

    def _catalog_changed(self) -> None:
        self.apps.render_table()
        self.history.app.refill()
        self._update_banner()
        if not self.apps.counts[0] and not self.apps.pods:
            self.apps.load_side_data()
        if relations.selected_app() and self.apps.detail_data.get("name") != relations.selected_app():
            self.apps.show_app(relations.selected_app())

    def _update_banner(self) -> None:
        src = relations.catalog_source()
        err = relations.overview_error()
        if err:
            self.banner.show_message("error", f"DevOps: {err}", "Ayarlar", lambda: relations.open_page("settings"))
        elif src == "demo":
            self.banner.show_message("demo", "Örnek veri gösteriliyor: Azure DevOps PAT'i girilmemiş "
                                             "(Ayarlar → Azure DevOps).", "Ayarlar", lambda: relations.open_page("settings"))
        else:
            self.banner.clear()

    def on_shown(self) -> None:
        self.apps.load_side_data()
