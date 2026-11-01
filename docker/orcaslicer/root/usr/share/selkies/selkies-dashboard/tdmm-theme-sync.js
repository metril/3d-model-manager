/* Keeps the Selkies dashboard theme in step with the 3D Model Manager app theme.
 * The app writes "dark" | "light" | "system" to localStorage["tdmm-theme"]; this clicks
 * Selkies' own .theme-toggle (no stream reconnect) whenever the resolved theme differs.
 * Selkies' own toggle writes localStorage["theme"], which the app follows in the other direction. */
(function () {
  var KEY = "tdmm-theme";
  var RETRIES = 40;
  var INTERVAL_MS = 500;
  var timer = null;

  function wanted() {
    var v = null;
    try { v = localStorage.getItem(KEY); } catch (e) { /* storage unavailable */ }
    if (v === "dark" || v === "light") return v;
    var mq = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)");
    return mq && mq.matches ? "dark" : "light";
  }

  // Returns false only while the toggle (or its state) isn't there yet.
  function apply() {
    var toggle = document.querySelector(".theme-toggle");
    if (!toggle) return false;
    var cl = toggle.classList;
    var current = cl.contains("dark") ? "dark" : cl.contains("light") ? "light" : null;
    if (current === null) return false;
    if (current !== wanted()) toggle.click();
    return true;
  }

  function sync() {
    if (timer !== null) clearTimeout(timer);
    var tries = 0;
    (function attempt() {
      timer = null;
      if (apply() || ++tries >= RETRIES) return;
      timer = setTimeout(attempt, INTERVAL_MS);
    })();
  }

  window.addEventListener("storage", function (e) {
    if (e.key === KEY) sync();
  });
  var mq = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)");
  if (mq) {
    if (mq.addEventListener) mq.addEventListener("change", sync);
    else if (mq.addListener) mq.addListener(sync);
  }
  sync();
})();
