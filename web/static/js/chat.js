// Sohbet: mesaj kutusunu sonda tut, rozeti sıfırla, gönderince odağı geri ver.
(function () {
  "use strict";
  var badge = document.getElementById("chat-unread"); if (badge) { badge.hidden = true; badge.textContent = ""; }
  document.body.addEventListener("htmx:afterSwap", function (e) {
    if (e.detail.target.id === "composer") { var t = document.getElementById("chat-text"); if (t) t.focus(); }
  });
})();
