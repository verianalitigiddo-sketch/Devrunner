// Bilge yer imleri: sürükle-bırak (klasöre taşıma, sıralama, klasör taşıma), çok satırlı yapıştırma.
(function () {
  "use strict";
  function post(url, data) {
    var fd = new FormData(); Object.keys(data).forEach(function (k) { if (data[k] !== null && data[k] !== undefined) fd.append(k, data[k]); });
    return fetch(url, { method: "POST", body: fd, headers: { "X-Devrunner": "1", "HX-Request": "true" } }).then(function (r) {
      var trig = r.headers.get("HX-Trigger");
      if (trig) { try { var ev = JSON.parse(trig); Object.keys(ev).forEach(function (n) { document.body.dispatchEvent(new CustomEvent(n, { detail: ev[n] })); }); } catch (e) { /* yok */ } }
    });
  }
  var drag = null;   // {kind: "bm"|"folder", id}

  document.addEventListener("dragstart", function (e) {
    var bm = e.target.closest && e.target.closest(".bm[data-id]");
    var folder = e.target.closest && e.target.closest(".node[data-folder][draggable]");
    if (bm) { drag = { kind: "bm", id: bm.dataset.id }; bm.classList.add("dragging"); }
    else if (folder) { drag = { kind: "folder", id: folder.dataset.folder }; }
    else return;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", drag.id);
  });
  document.addEventListener("dragend", function () {
    document.querySelectorAll(".dragging, .drop-ok, .drop-before").forEach(function (el) { el.classList.remove("dragging", "drop-ok", "drop-before"); });
    drag = null;
  });
  document.addEventListener("dragover", function (e) {
    if (!drag) return;
    var node = e.target.closest(".node[data-folder]");
    var bm = e.target.closest(".bm[data-id]");
    document.querySelectorAll(".drop-ok, .drop-before").forEach(function (el) { el.classList.remove("drop-ok", "drop-before"); });
    if (node && !(drag.kind === "folder" && node.dataset.folder === drag.id)) { e.preventDefault(); node.classList.add("drop-ok"); }
    else if (bm && drag.kind === "bm" && bm.dataset.id !== drag.id && sortable()) { e.preventDefault(); bm.classList.add("drop-before"); }
  });
  function sortable() { var l = document.getElementById("bm-list"); return l && (l.dataset.view === "folder" || l.dataset.view === "unfiled"); }
  document.addEventListener("drop", function (e) {
    if (!drag) return;
    var node = e.target.closest(".node[data-folder]");
    var bm = e.target.closest(".bm[data-id]");
    e.preventDefault();
    if (node) {
      if (drag.kind === "bm") post("/bilge/place", { request_id: drag.id, folder_id: node.dataset.folder });
      else post("/bilge/folder/" + drag.id + "/move", { parent_id: node.dataset.folder });
    } else if (bm && drag.kind === "bm") {
      var list = document.getElementById("bm-list");
      post("/bilge/place", { request_id: drag.id, folder_id: list.dataset.folder, before_id: bm.dataset.id });
    }
  });

  // Klasör oluşturma / yeniden adlandırma: değer form gövdesinde gider (hx-prompt başlıkta gönderir;
  // HTTP başlıkları Türkçe karakter taşıyamaz)
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-prompt-post]"); if (!b) return;
    var name = window.prompt(b.dataset.prompt || "Ad", b.dataset.default || "");
    if (!name || !name.trim()) return;
    var vals = {}; try { vals = JSON.parse(b.dataset.vals || "{}"); } catch (err) { vals = {}; }
    vals.name = name.trim();
    post(b.dataset.promptPost, vals);
  });

  // Çok satırlı yapıştırma -> toplu ekleme penceresi
  var url = document.getElementById("quick-url");
  if (url) url.addEventListener("paste", function (e) {
    var text = (e.clipboardData || window.clipboardData).getData("text");
    if (text && text.trim().indexOf("\n") >= 0) {
      e.preventDefault();
      var dlg = document.getElementById("bulk-dialog"), area = document.getElementById("bulk-text");
      area.value = text; dlg.showModal(); htmx.trigger(area, "input");
    }
  });
  document.body.addEventListener("quick_added", function () {
    var f = document.getElementById("quick-add"); if (f) f.reset();
    if (url) url.focus();
  });
})();
