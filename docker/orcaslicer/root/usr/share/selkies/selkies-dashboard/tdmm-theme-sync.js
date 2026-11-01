/* Keeps the Selkies dashboard theme in step with the 3D Model Manager app theme.
 * The app writes "dark" | "light" | "system" to localStorage["tdmm-theme"]; this clicks
 * Selkies' own .theme-toggle (no stream reconnect) whenever the resolved theme differs.
 * A click by the user (not ours) is written back to localStorage["tdmm-theme"], so the choice
 * sticks with no app tab open; the app follows it through that key. */
(function () {
  var KEY = "tdmm-theme";
  var RETRIES = 40;
  var INTERVAL_MS = 500;
  var timer = null;
  var ours = false;

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
    if (current !== wanted()) {
      ours = true;
      try { toggle.click(); } finally { ours = false; }
    }
    return true;
  }

  // The toggle's class flips after the click is handled, so read it on the next tick.
  document.addEventListener("click", function (e) {
    var t = e.target;
    if (ours || !t || !t.closest || !t.closest(".theme-toggle")) return;
    setTimeout(function () {
      var toggle = document.querySelector(".theme-toggle");
      var cl = toggle && toggle.classList;
      var mode = cl && (cl.contains("dark") ? "dark" : cl.contains("light") ? "light" : null);
      if (!mode) return;
      try { if (localStorage.getItem(KEY) !== mode) localStorage.setItem(KEY, mode); } catch (err) { /* storage unavailable */ }
    }, 0);
  }, true);

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
