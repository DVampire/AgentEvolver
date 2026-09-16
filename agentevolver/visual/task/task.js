// Authored HTML task briefs: preserve the document's existing semantic markup.
// Navigation works without JavaScript; this only marks the selected anchor.
(function () {
  "use strict";

  function init() {
    var links = document.querySelectorAll('[data-task-layout="brief"] nav a[href^="#"]');
    function updateLocation() {
      links.forEach(function (link) {
        if (link.hash === window.location.hash) {
          link.setAttribute("aria-current", "location");
        } else {
          link.removeAttribute("aria-current");
        }
      });
    }
    updateLocation();
    window.addEventListener("hashchange", updateLocation);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
