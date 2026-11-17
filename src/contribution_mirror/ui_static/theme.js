"use strict";
// Loaded before the page is drawn (not deferred), so a chosen theme never flashes the other one first.
// "light" / "dark" are stored when chosen on the page; no value (or "system") follows the operating system.
(function () {
  let theme = null;
  try { theme = localStorage.getItem("mirror-theme"); } catch (_) { /* storage blocked: follow the system */ }
  if (theme === "light" || theme === "dark") document.documentElement.setAttribute("data-theme", theme);
})();
