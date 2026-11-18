// Devrunner Web — ortak istemci betiği. Satır içi betik ve eval YOKTUR (CSP).
// * Tek EventSource: /api/events'ten gelen her olay body üzerinde aynı adlı DOM olayı olarak yayınlanır;
//   htmx parçaları hx-trigger="health_updated from:body" ile kendini yeniler.
// * Bildirim balonları, sistem bildirimi (Notification API; kabuk kendi kökeni için izin verir).
// * Komut paleti (Ctrl+K), anında tema değişimi.
// Dış kaynaklı metin DOM'a yalnızca textContent ile yazılır.
(function () {
  "use strict";

  if (window.htmx) {
    htmx.config.allowEval = false;
    htmx.config.allowScriptTags = false;
    htmx.config.selfRequestsOnly = true;
    htmx.config.historyCacheSize = 0;
    htmx.config.refreshOnHistoryMiss = true;   // geri tuşu: sayfayı sunucudan tam yükle
    htmx.config.defaultSwapStyle = "innerHTML";
    // Hata yanıtları (4xx/5xx) da gösterilsin: sunucu hata kartı döndürür
    document.addEventListener("htmx:beforeSwap", function (e) {
      if (e.detail.xhr.status >= 400 && e.detail.xhr.status !== 401 && e.detail.xhr.status !== 403) {
        e.detail.shouldSwap = true; e.detail.isError = false;
      }
    });
    document.addEventListener("htmx:responseError", function (e) {
      if (e.detail.xhr.status === 401) toast("Oturum yok. Devrunner'ı yeniden açın.", "error");
      if (e.detail.xhr.status === 403) toast("İstek reddedildi.", "error");
    });
    document.addEventListener("htmx:sendError", function () { toast("Uygulama çekirdeğine ulaşılamadı.", "error"); });
  }

  // ---------------------------------------------------------------- simge
  function icon(name) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg"); svg.setAttribute("class", "i");
    var use = document.createElementNS(ns, "use"); use.setAttribute("href", "#i-" + name);
    svg.appendChild(use); return svg;
  }
  window.drIcon = icon;

  // ---------------------------------------------------------------- bildirim balonu
  function toast(message, level, title) {
    var box = document.getElementById("toasts"); if (!box) return;
    var t = document.createElement("div"); t.className = "toast " + (level || "info");
    t.appendChild(icon(level === "error" ? "alert" : level === "warning" ? "alert" : level === "success" ? "check" : "clock"));
    var body = document.createElement("div");
    if (title) { var b = document.createElement("b"); b.textContent = title; body.appendChild(b); }
    var p = document.createElement("span"); p.textContent = message; body.appendChild(p);
    t.appendChild(body); box.appendChild(t);
    setTimeout(function () { t.classList.add("out"); setTimeout(function () { t.remove(); }, 250); }, level === "error" ? 9000 : 5000);
  }
  window.drToast = toast;
  document.body.addEventListener("toast", function (e) { var d = e.detail || {}; toast(d.message || "", d.level || "success"); });
  document.body.addEventListener("close_modal", function () {
    document.querySelectorAll("dialog[open]").forEach(function (d) { d.close(); });
  });

  function systemNotify(title, body) {
    if (!("Notification" in window)) return;
    var show = function () { try { new Notification(title, { body: body, icon: "/favicon.ico" }); } catch (err) { /* yok say */ } };
    if (Notification.permission === "granted") show();
    else if (Notification.permission !== "denied") Notification.requestPermission().then(function (p) { if (p === "granted") show(); });
  }
  function away() { return document.hidden || !document.hasFocus(); }

  // ---------------------------------------------------------------- canlı olaylar
  var NAMES = ["catalog_changed", "settings_changed", "freeze_changed", "health_changed", "health_updated", "presence_changed",
               "db_state_changed", "data_changed", "notify", "chat_message", "terminal_output", "terminal_done",
               "pods_updated"];
  var es = null;
  function connect() {
    if (es) es.close();
    es = new EventSource("/api/events");
    NAMES.forEach(function (n) {
      es.addEventListener(n, function (ev) {
        var data = {}; try { data = JSON.parse(ev.data); } catch (err) { /* boş */ }
        document.body.dispatchEvent(new CustomEvent(n, { detail: data }));
      });
    });
  }
  connect();
  window.addEventListener("beforeunload", function () { if (es) es.close(); });

  document.body.addEventListener("notify", function (e) {
    var d = e.detail || {};
    toast(d.message || "", d.level === "error" ? "error" : d.level === "warning" ? "warning" : "info", d.title);
    if (away()) systemNotify(d.title || "Devrunner", d.message || "");
  });
  document.body.addEventListener("chat_message", function (e) {
    var d = e.detail || {};
    if (d.mine) return;
    var onChat = document.body.dataset.page === "chat";
    if (!onChat || away()) {
      var where = d.room === "general" ? "Genel" : "özel mesaj";
      if (!onChat) toast(d.preview || "", "info", (d.name || "?") + " · " + where);
      if (away()) systemNotify((d.name || "?") + " (" + where + ")", d.preview || "");
      var badge = document.getElementById("chat-unread");
      if (badge && !onChat) { badge.hidden = false; badge.textContent = String((parseInt(badge.textContent || "0", 10) || 0) + 1); }
    }
  });

  // ---------------------------------------------------------------- tema
  var sel = document.getElementById("theme-select");
  function setTheme(key) {
    document.documentElement.setAttribute("data-theme", key);
    if (sel) sel.value = key;
    var fd = new FormData(); fd.append("theme", key);
    fetch("/api/ui", { method: "POST", body: fd, headers: { "X-Devrunner": "1" } });
  }
  if (sel) sel.addEventListener("change", function () { setTheme(sel.value); });

  // ---------------------------------------------------------------- komut paleti (Ctrl+K)
  var dlg = document.getElementById("palette"), input = document.getElementById("palette-input"),
      list = document.getElementById("palette-list"), items = [], shown = [], idx = 0;
  function score(q, text) {
    q = q.toLocaleLowerCase("tr"); text = text.toLocaleLowerCase("tr");
    if (!q) return 1; var p = text.indexOf(q); if (p >= 0) return 100 - p;
    var i = 0; for (var c = 0; c < text.length && i < q.length; c++) if (text[c] === q[i]) i++;
    return i === q.length ? 10 : 0;
  }
  function render() {
    var q = input.value.trim();
    shown = items.map(function (it) { return [score(q, it.title + " " + it.kind), it]; })
                 .filter(function (x) { return x[0] > 0; }).sort(function (a, b) { return b[0] - a[0]; })
                 .slice(0, 40).map(function (x) { return x[1]; });
    idx = Math.min(idx, Math.max(0, shown.length - 1));
    list.replaceChildren();
    shown.forEach(function (it, i) {
      var li = document.createElement("li"); if (i === idx) li.className = "on";
      li.appendChild(icon(it.icon || "chevron"));
      var t = document.createElement("span"); t.textContent = it.title; li.appendChild(t);
      var k = document.createElement("span"); k.className = "kind"; k.textContent = it.kind; li.appendChild(k);
      li.addEventListener("click", function () { go(it); });
      li.addEventListener("mousemove", function () { if (idx !== i) { idx = i; render(); } });
      list.appendChild(li);
    });
  }
  function go(it) {
    dlg.close();
    if (it.theme) setTheme(it.theme); else if (it.url) location.href = it.url;
  }
  function openPalette() {
    if (!dlg) return;
    fetch("/api/palette").then(function (r) { return r.json(); }).then(function (data) { items = data; render(); });
    input.value = ""; idx = 0; list.replaceChildren(); dlg.showModal(); input.focus();
  }
  if (input) {
    input.addEventListener("input", function () { idx = 0; render(); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { idx = Math.min(idx + 1, shown.length - 1); render(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { idx = Math.max(idx - 1, 0); render(); e.preventDefault(); }
      else if (e.key === "Enter" && shown[idx]) { go(shown[idx]); e.preventDefault(); }
    });
  }
  document.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); }
  });
  document.addEventListener("click", function (e) {
    var a = e.target.closest("[data-action]"); if (!a) return;
    var act = a.dataset.action;
    if (act === "palette") openPalette();
    else if (act === "copy") {
      navigator.clipboard.writeText(a.dataset.value || "").then(function () { toast("Bağlantı kopyalandı.", "success"); });
    } else if (act === "close-dialog") { var d = a.closest("dialog"); if (d) d.close(); }
    else if (act === "open-dialog") { var t = document.getElementById(a.dataset.target); if (t) t.showModal(); }
    else if (act === "note-edit" || act === "note-view") {
      var body = document.getElementById("note-body"), prev = document.getElementById("note-preview");
      if (body && prev) { body.hidden = act === "note-view"; prev.hidden = act === "note-edit"; if (act === "note-edit") body.focus(); }
    }
  });

  // Modal içeriği htmx ile dolunca aç; log kutularını sona kaydır
  document.body.addEventListener("htmx:afterSwap", function (e) {
    var tgt = e.detail.target;
    var boxes = tgt.matches && tgt.matches("[data-autoscroll]") ? [tgt] : (tgt.querySelectorAll ? tgt.querySelectorAll("[data-autoscroll]") : []);
    Array.prototype.forEach.call(boxes, function (b) { b.scrollTop = b.scrollHeight; });
    if (tgt.id === "modal-body") { var d = tgt.closest("dialog"); if (d && !d.open) d.showModal(); }
  });

  // Seçim vurgusu: tablo satırı ve sekme düğmeleri
  document.addEventListener("click", function (e) {
    var row = e.target.closest("tr[data-row]");
    if (row && !e.target.closest("a, button, input, select")) {
      row.parentElement.querySelectorAll("tr.selected").forEach(function (r) { r.classList.remove("selected"); });
      row.classList.add("selected");
    }
    var card = e.target.closest(".note-card[hx-get]");
    if (card) {
      document.querySelectorAll(".note-card.active").forEach(function (c) { c.classList.remove("active"); });
      card.classList.add("active");
    }
    var tab = e.target.closest("[data-tab]");
    if (tab) {
      tab.parentElement.querySelectorAll(".tab.active").forEach(function (t) { t.classList.remove("active"); });
      tab.classList.add("active");
    }
    var fill = e.target.closest("[data-fill]");
    if (fill) {
      var spec = {}; try { spec = JSON.parse(fill.dataset.fill); } catch (err) { /* yok */ }
      Object.keys(spec).forEach(function (sel) { var el = document.querySelector(sel); if (el) el.value = spec[sel]; });
    }
  });

  // Satır tıklaması: data-href olan satırlar bağlantı gibi davranır
  document.addEventListener("click", function (e) {
    var tr = e.target.closest("tr[data-href]"); if (!tr || e.target.closest("a, button, input, select")) return;
    location.href = tr.dataset.href;
  });
})();
