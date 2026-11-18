// Terminal (PLAN_V2 Bölüm 5): xterm.js + WebSocket. Yerel kabuk ve pod kabuğu, sekmeli; oturumlar sunucuda
// yaşar (sayfa değişse de açık kalır), yeniden bağlanınca son çıktı geri oynatılır.
// Dış kaynaklı metin yalnızca xterm'e (term.write) veya textContent ile yazılır.
(function () {
  "use strict";
  var root = document.getElementById("term-root");
  if (!root || !window.Terminal) return;
  var host = document.getElementById("term-host"), tabsEl = document.getElementById("term-tabs"),
      info = document.getElementById("term-info"), shellPick = document.getElementById("shell-pick"),
      kubePick = document.getElementById("kube-pick");
  var tabs = new Map(), active = null, prefill = root.dataset.prefill || "";

  function parse(attr, fallback) { try { return JSON.parse(root.dataset[attr] || ""); } catch (e) { return fallback; } }
  function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
  function theme() {
    return { background: cssVar("--input-bg") || "#0b0d14", foreground: cssVar("--text") || "#eceef5",
             cursor: cssVar("--accent") || "#8b5cf6", cursorAccent: cssVar("--input-bg") || "#0b0d14",
             selectionBackground: cssVar("--selection") || "#2b2350", selectionForeground: cssVar("--selection-text") || "#fff" };
  }
  function post(url, data) {
    var fd = new FormData(); Object.keys(data || {}).forEach(function (k) { if (data[k] !== undefined && data[k] !== null) fd.append(k, data[k]); });
    return fetch(url, { method: "POST", body: fd, headers: { "X-Devrunner": "1" } })
      .then(function (r) { return r.json().catch(function () { return { error: "Beklenmeyen yanıt (" + r.status + ")" }; }); });
  }
  function toast(msg, level) { if (window.drToast) window.drToast(msg, level || "error"); }

  // ------------------------------------------------------------------ sekme
  function addTab(s, activate) {
    if (tabs.has(s.id)) { if (activate) select(s.id); return tabs.get(s.id); }
    var pane = document.createElement("div"); pane.className = "term-pane"; pane.hidden = true; host.appendChild(pane);
    var term = new window.Terminal({ fontFamily: cssVar("--mono") || "monospace", fontSize: 13, lineHeight: 1.15,
      cursorBlink: true, cursorStyle: "bar", scrollback: 10000, allowProposedApi: true, theme: theme(),
      drawBoldTextInBrightColors: true });
    var fit = new window.FitAddon.FitAddon(); term.loadAddon(fit);
    term.loadAddon(new window.WebLinksAddon.WebLinksAddon(function (e, uri) { window.open(uri, "_blank", "noopener,noreferrer"); }));
    try { term.loadAddon(new window.Unicode11Addon.Unicode11Addon()); term.unicode.activeVersion = "11"; } catch (e) { /* yok */ }
    term.open(pane);
    var tab = { info: s, term: term, fit: fit, pane: pane, ws: null, closed: !!s.closed, retry: 0, gone: false };
    term.onData(function (d) { send(tab, { type: "in", data: d }); });
    term.onResize(function (sz) { send(tab, { type: "resize", cols: sz.cols, rows: sz.rows }); });
    term.attachCustomKeyEventHandler(function (e) { return keys(tab, e); });
    pane.addEventListener("contextmenu", function (e) {   // sağ tık: seçim varsa kopyala, yoksa yapıştır
      e.preventDefault();
      if (term.hasSelection()) copy(term.getSelection()); else paste(tab);
    });
    var el = document.createElement("button"); el.type = "button"; el.className = "term-tab"; el.setAttribute("role", "tab");
    var ic = window.drIcon ? window.drIcon(s.kind === "pod" ? "cube" : "terminal") : null; if (ic) el.appendChild(ic);
    var label = document.createElement("span"); label.className = "t"; label.textContent = s.title; el.appendChild(label);
    if (s.env) { var chip = document.createElement("span"); chip.className = "chip env-" + s.env; chip.textContent = s.env; el.appendChild(chip); }
    var x = document.createElement("span"); x.className = "x"; x.title = "Kapat"; x.textContent = "×"; el.appendChild(x);
    el.addEventListener("click", function (e) { if (e.target === x) close(tab); else select(s.id); });
    el.addEventListener("auxclick", function (e) { if (e.button === 1) close(tab); });
    tabsEl.appendChild(el); tab.tabEl = el;
    tabs.set(s.id, tab);
    connect(tab);
    if (activate) select(s.id);
    return tab;
  }

  function select(id) {
    var tab = tabs.get(id); if (!tab) return;
    tabs.forEach(function (t) { t.pane.hidden = t !== tab; t.tabEl.classList.toggle("active", t === tab); });
    active = tab;
    renderInfo();
    requestAnimationFrame(function () { try { tab.fit.fit(); } catch (e) { /* gizli */ } tab.term.focus(); });
    var url = new URL(location.href); url.searchParams.set("session", id);
    ["cmd", "open", "env", "pod", "container", "app"].forEach(function (k) { url.searchParams.delete(k); });
    history.replaceState(null, "", url.pathname + url.search);
  }

  function renderInfo() {
    info.replaceChildren();
    if (!active) { var m = document.createElement("span"); m.className = "muted"; m.textContent = "Açık terminal yok. Yeni bir yerel kabuk veya pod kabuğu açın."; info.appendChild(m); return; }
    var s = active.info;
    var b = document.createElement("span"); b.className = "shell-badge";
    if (window.drIcon) b.appendChild(window.drIcon(s.kind === "pod" ? "cube" : "terminal"));
    var t = document.createElement("span"); t.textContent = (s.kind === "pod" ? "Pod kabuğu: " : "Etkin kabuk: ") + s.title; b.appendChild(t);
    var p = document.createElement("span"); p.className = "path"; p.textContent = s.detail; b.appendChild(p);
    info.appendChild(b);
    var st = document.createElement("span"); st.className = "state " + (active.closed ? "state-missing" : "state-success");
    st.appendChild(document.createElement("i")); st.appendChild(document.createTextNode(active.closed ? "kapandı" : "bağlı"));
    info.appendChild(st);
  }

  // ------------------------------------------------------------------ bağlantı
  function connect(tab) {
    if (tab.gone) return;
    var ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/terminal/ws/" + encodeURIComponent(tab.info.id));
    tab.ws = ws;
    ws.onopen = function () { tab.retry = 0; send(tab, { type: "resize", cols: tab.term.cols, rows: tab.term.rows }); };
    ws.onmessage = function (ev) {
      var m; try { m = JSON.parse(ev.data); } catch (e) { return; }
      if (m.type === "hello") {
        tab.term.reset(); if (m.replay) tab.term.write(m.replay);
        tab.closed = !!m.closed; tab.tabEl.classList.toggle("closed", tab.closed);
        if (tab === active) renderInfo();
        if (prefill && tab === active && !tab.closed) { insert(prefill); prefill = ""; }
      } else if (m.type === "out") { tab.term.write(m.data); }
      else if (m.type === "exit") {
        tab.closed = true; tab.tabEl.classList.add("closed");
        tab.term.write("\r\n\x1b[2m[oturum kapandı" + (m.code !== null && m.code !== undefined ? ": çıkış kodu " + m.code : "") + "]\x1b[0m\r\n");
        if (tab === active) renderInfo();
      } else if (m.type === "error") { tab.term.write("\r\n\x1b[31m" + m.message + "\x1b[0m\r\n"); tab.closed = true; tab.gone = true; }
    };
    ws.onclose = function () {
      if (tab.gone || tab.closed) return;
      tab.retry = Math.min(tab.retry + 1, 6);
      setTimeout(function () { connect(tab); }, 500 * tab.retry);
    };
  }
  function send(tab, obj) { if (tab.ws && tab.ws.readyState === 1) tab.ws.send(JSON.stringify(obj)); }

  function close(tab) {
    tab.gone = true;
    // Önce soket kapanır, sonra oturum: sunucu kapanmakta olan sokete "exit" yazmaya çalışmasın
    var id = tab.info.id, sent = false, ws = tab.ws;
    function kill() { if (!sent) { sent = true; post("/terminal/sessions/" + encodeURIComponent(id) + "/close", {}); } }
    if (ws && ws.readyState < 2) { ws.onmessage = null; ws.onclose = kill; ws.close(); setTimeout(kill, 1000); } else kill();
    tab.term.dispose(); tab.pane.remove(); tab.tabEl.remove(); tabs.delete(tab.info.id);
    if (active === tab) { active = null; var last = Array.from(tabs.keys()).pop(); if (last) select(last); else renderInfo(); }
  }

  // ------------------------------------------------------------------ pano ve kısayollar
  function copy(text) {
    if (!text) return;
    navigator.clipboard.writeText(text).then(function () { if (window.drToast) window.drToast("Kopyalandı.", "success"); }, function () { toast("Panoya yazılamadı."); });
  }
  function paste(tab) {
    navigator.clipboard.readText().then(function (t) { if (t) tab.term.paste(t); }, function () { toast("Panodan okunamadı."); });
  }
  function keys(tab, e) {
    if (e.type !== "keydown") return true;
    var k = e.key.toLowerCase();
    if (e.ctrlKey && e.shiftKey && k === "c") { copy(tab.term.getSelection()); return false; }
    if (e.ctrlKey && e.shiftKey && k === "v") { paste(tab); return false; }
    if (e.ctrlKey && !e.shiftKey && k === "c" && tab.term.hasSelection()) { copy(tab.term.getSelection()); tab.term.clearSelection(); return false; }
    if (e.ctrlKey && e.shiftKey && k === "k") return false;   // palet (app.js yakalar)
    return true;
  }

  // "Terminalde dene" ve hazır komutlar: giriş satırına YAZILIR, Enter gönderilmez
  function insert(cmd) {
    if (!active || active.closed) { toast("Açık bir terminal yok.", "warning"); return; }
    send(active, { type: "in", data: cmd }); active.term.focus();
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-term-insert]"); if (b) { insert(b.dataset.termInsert); return; }
    var n = e.target.closest("[data-term='new-local']"); if (n) openLocal();
  });

  // ------------------------------------------------------------------ yeni oturum
  function size() {
    if (active) return { cols: active.term.cols, rows: active.term.rows };
    var w = host.clientWidth || 900, h = host.clientHeight || 500;
    return { cols: Math.max(40, Math.floor(w / 8)), rows: Math.max(10, Math.floor(h / 17)) };
  }
  function openLocal() {
    var sz = size();
    return post("/terminal/sessions/local", { shell: shellPick ? shellPick.value : "", kube: kubePick ? kubePick.value : "", cols: sz.cols, rows: sz.rows })
      .then(function (r) { if (r.error) toast(r.error); else addTab(r, true); });
  }
  if (shellPick) shellPick.addEventListener("change", function () { var q = document.getElementById("quick-shell"); if (q) q.value = shellPick.value; });

  // Pod penceresi
  var podForm = document.getElementById("pod-form"), podEnv = document.getElementById("pod-env"), podApp = document.getElementById("pod-app"),
      podName = document.getElementById("pod-name"), podCont = document.getElementById("pod-container"), pods = [], confirmToken = "";
  function option(sel, value, text) { var o = document.createElement("option"); o.value = value; o.textContent = text; sel.appendChild(o); }
  function loadPods(want) {
    podName.replaceChildren(); podCont.replaceChildren(); option(podName, "", "yükleniyor…");
    return fetch("/terminal/pods?env=" + encodeURIComponent(podEnv.value) + "&app=" + encodeURIComponent(podApp.value))
      .then(function (r) { return r.json(); }).then(function (list) {
        podName.replaceChildren();
        if (list.error) { option(podName, "", list.error); return; }
        pods = list;
        if (!list.length) option(podName, "", "pod yok");
        list.forEach(function (p) { option(podName, p.name, p.name + "  (" + p.status + ", " + p.ready + ")"); });
        if (want) podName.value = want;
        fillContainers();
      });
  }
  function fillContainers(want) {
    podCont.replaceChildren();
    var p = pods.find(function (x) { return x.name === podName.value; });
    (p ? p.containers : []).forEach(function (c) { option(podCont, c, c); });
    if (want) podCont.value = want;
  }
  function resetConfirm() { confirmToken = ""; document.getElementById("pod-confirm").hidden = true; document.getElementById("pod-error").hidden = true; }
  if (podForm) {
    podEnv.addEventListener("change", function () { resetConfirm(); loadPods(); });
    podApp.addEventListener("change", function () { resetConfirm(); loadPods(); });
    podName.addEventListener("change", function () { resetConfirm(); fillContainers(); });
    podCont.addEventListener("change", resetConfirm);
    document.getElementById("pod-dialog").addEventListener("open-pods", function () { loadPods(); });
    podForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var fd = new FormData(podForm), data = {}; fd.forEach(function (v, k) { data[k] = v; });
      var sz = size(); data.cols = sz.cols; data.rows = sz.rows; if (confirmToken) data.confirm = confirmToken;
      post("/terminal/sessions/pod", data).then(function (r) {
        if (r.confirm) {
          confirmToken = r.confirm;
          document.getElementById("pod-confirm-text").textContent = r.message + (r.warn ? " " + r.warn : "") + " Devam etmek için tekrar Aç'a basın.";
          document.getElementById("pod-confirm").hidden = false;
          document.getElementById("pod-open").textContent = "Evet, Prod'da aç";
          return;
        }
        if (r.error) { confirmToken = ""; document.getElementById("pod-error-text").textContent = r.error; document.getElementById("pod-error").hidden = false; return; }
        resetConfirm(); document.getElementById("pod-dialog").close(); addTab(r, true);
      });
    });
    document.querySelector('[data-target="pod-dialog"]').addEventListener("click", function () { resetConfirm(); loadPods(); });
  }

  // ------------------------------------------------------------------ tema / boyut
  document.addEventListener("themechange", function () {
    tabs.forEach(function (t) { t.term.options.theme = theme(); t.term.options.fontFamily = cssVar("--mono") || "monospace"; });
  });
  var rt; window.addEventListener("resize", function () { clearTimeout(rt); rt = setTimeout(function () { if (active) try { active.fit.fit(); } catch (e) { /* yok */ } }, 120); });

  // ------------------------------------------------------------------ açılış
  var existing = parse("sessions", []), focus = root.dataset.focus, openPod = parse("openPod", {});
  existing.forEach(function (s) { addTab(s, false); });
  if (openPod && openPod.pod) {
    var dlg = document.getElementById("pod-dialog");
    if (openPod.env) podEnv.value = openPod.env;
    if (openPod.app) podApp.value = openPod.app;
    dlg.showModal();
    loadPods(openPod.pod).then(function () { fillContainers(openPod.container); podForm.requestSubmit(); });
  }
  if (focus && tabs.has(focus)) select(focus);
  else if (tabs.size) select(Array.from(tabs.keys()).pop());
  else if (!openPod.pod) openLocal();
  renderInfo();
})();
