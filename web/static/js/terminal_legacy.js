// Terminal sayfası: SSE ile gelen çıktıyı (textContent) konsola ekler, geçmiş (↑/↓), Çalıştır/Durdur durumu.
(function () {
  "use strict";
  var con = document.getElementById("console"), input = document.getElementById("command"),
      run = document.getElementById("run-btn"), stop = document.getElementById("stop-btn"),
      kubeSel = document.getElementById("kube-select"), kubeIn = document.getElementById("kube-input");
  if (!con) return;
  var hist = []; try { hist = JSON.parse(input.dataset.history || "[]"); } catch (e) { hist = []; }
  var pos = hist.length;

  function busy(on) { run.disabled = on; stop.disabled = !on; }
  function append(text) {
    var atBottom = con.scrollHeight - con.scrollTop - con.clientHeight < 40;
    con.appendChild(document.createTextNode(text));
    if (atBottom) con.scrollTop = con.scrollHeight;
  }
  con.scrollTop = con.scrollHeight;

  document.body.addEventListener("terminal_output", function (e) { append((e.detail || {}).text || ""); });
  document.body.addEventListener("terminal_done", function () { busy(false); input.focus(); });
  document.body.addEventListener("terminal_started", function () { busy(true); });
  document.body.addEventListener("terminal_clear", function () { con.textContent = ""; });

  document.getElementById("term-form").addEventListener("htmx:beforeRequest", function (e) {
    if (e.detail.elt.id !== "term-form") return;
    var v = input.value.trim(); if (!v) { e.preventDefault(); return; }
    if (hist[hist.length - 1] !== v) hist.push(v);
    pos = hist.length;
  });
  document.getElementById("term-form").addEventListener("htmx:afterRequest", function (e) {
    if (e.detail.elt.id === "term-form" && e.detail.successful) { input.value = ""; document.getElementById("hint").textContent = ""; }
  });
  input.addEventListener("keydown", function (e) {
    if (e.key === "ArrowUp" && hist.length) { pos = Math.max(0, pos - 1); input.value = hist[pos]; e.preventDefault(); }
    else if (e.key === "ArrowDown" && hist.length) { pos = Math.min(hist.length, pos + 1); input.value = pos < hist.length ? hist[pos] : ""; e.preventDefault(); }
  });
  if (kubeSel) kubeSel.addEventListener("change", function () { kubeIn.value = kubeSel.value; });
  document.body.addEventListener("htmx:afterSwap", function (e) {
    if (e.detail.target.id === "shell-badge") {
      var b = e.detail.target.querySelector("[data-prompt]");
      if (b) document.getElementById("prompt").textContent = b.dataset.prompt;
    }
  });
  input.focus();
})();
