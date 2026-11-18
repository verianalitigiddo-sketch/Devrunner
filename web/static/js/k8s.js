// Kubernetes: canlı log akışı (WebSocket, yalnızca ekleme) ve görünen log'u indirme.
(function () {
  "use strict";
  var live = null;
  function stop() { if (live) { live.close(); live = null; } document.querySelectorAll(".live-dot").forEach(function (d) { d.hidden = true; }); }
  document.addEventListener("change", function (e) {
    var cb = e.target.closest("[data-follow]"); if (!cb) return;
    var box = document.getElementById("log-box"); if (!box) return;
    stop();
    if (!cb.checked) return;
    box.textContent = "";
    var dot = cb.closest("form").querySelector(".live-dot"); if (dot) dot.hidden = false;
    live = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + box.dataset.ws);
    live.onmessage = function (ev) {
      var atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
      box.appendChild(document.createTextNode(ev.data));
      // tarayıcıyı yormamak için en çok ~1 MB tut
      while (box.firstChild && box.textContent.length > 1000000) box.removeChild(box.firstChild);
      if (atBottom) box.scrollTop = box.scrollHeight;
    };
    live.onclose = function () { if (dot) dot.hidden = true; };
  });
  // Sekme / pod değişince açık akışı kapat
  document.body.addEventListener("htmx:beforeSwap", function (e) {
    var t = e.detail.target; if (t && (t.id === "pod-tab" || t.id === "pod-detail")) stop();
  });
  window.addEventListener("beforeunload", stop);
  document.addEventListener("click", function (e) {
    var a = e.target.closest("[data-log-download]"); if (!a) return;
    e.preventDefault();
    var box = document.getElementById("log-box"); if (!box) return;
    var blob = new Blob([box.textContent], { type: "text/plain;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a"); link.href = url; link.download = (box.dataset.name || "pod") + ".log";
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
  });
})();
