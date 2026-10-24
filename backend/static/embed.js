// Fit-it loader: mounts the widget iframe (/fitit/ui/). Spec: docs/specs/embed-widget.md.
(function () {
  if (window.__fitit) return;
  window.__fitit = true;
  var f = document.createElement("iframe");
  f.src = "/fitit/ui/";
  f.title = "Fit-it";
  f.setAttribute("allowtransparency", "true");
  f.style.cssText = "position:fixed;right:16px;bottom:16px;width:64px;height:64px;border:0;" +
    "background:transparent;color-scheme:normal;z-index:2147483647";
  function size(w, h) { f.style.width = w; f.style.height = h; }
  window.addEventListener("message", function (e) {
    if (e.source !== f.contentWindow || e.origin !== location.origin || !e.data) return;
    if (e.data.fitit === "open") size("min(420px, calc(100vw - 32px))", "min(640px, calc(100vh - 32px))");
    if (e.data.fitit === "close") size("64px", "64px");
    if (e.data.fitit === "resize" && isFinite(e.data.width) && isFinite(e.data.height) &&
        typeof e.data.width === "number" && typeof e.data.height === "number") {
      size(clamp(e.data.width, innerWidth) + "px", clamp(e.data.height, innerHeight) + "px");
    }
  });
  function clamp(v, viewport) { return Math.round(Math.max(64, Math.min(v, viewport - 32))); }
  document.body.appendChild(f);
})();
