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
  cards();

  // Agent cards: virtual endpoints (GET /x/...) that return {"card": {...}} render as a row on the host page.
  // Spec: docs/specs/hook-contract.md (Cards). Every error is swallowed: the host page must never break.
  function cards() {
    var row = null;
    function get(path) {
      return fetch(path, { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) { if (!r.ok) throw r.status; return r.json(); });
    }
    function el(tag, cls, text) {
      var e = document.createElement(tag);
      e.className = cls;
      if (text != null) e.textContent = String(text);
      return e;
    }
    function fmt(c) {
      if (typeof c.value !== "number") return c.value;
      if (c.format === "currency" && c.currency) {
        try { return c.value.toLocaleString(undefined, { style: "currency", currency: c.currency }); } catch (e) {}
      }
      return c.value.toLocaleString();
    }
    function render(list) {
      if (!list.length) return;
      var css = el("style", "");
      css.textContent =
        ".fitit-card-row{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:14px;" +
        "margin:0 0 20px;font:inherit;color:var(--text-color,#171717)}" +
        ".fitit-card{position:relative;box-sizing:border-box;min-height:80px;padding:12px 16px;border-radius:12px;" +
        "border:2px solid var(--border-color,#ededed);background:var(--card-bg,#fff);font-size:14px;line-height:1.5}" +
        ".fitit-card-title{font-size:13px;color:var(--text-muted,#7c7c7c);white-space:nowrap;overflow:hidden;" +
        "text-overflow:ellipsis;padding-right:44px}" +
        ".fitit-card-value{font-size:17px;font-weight:600;color:var(--heading-color,#171717);margin-top:6px;" +
        "overflow-wrap:anywhere}" +
        ".fitit-card-sub{font-size:12px;color:var(--text-muted,#7c7c7c);margin-top:2px}" +
        ".fitit-card-rows{margin-top:8px;font-size:12px}" +
        ".fitit-card-r{display:flex;justify-content:space-between;gap:8px;padding:2px 0;" +
        "border-top:1px solid var(--border-color,#ededed)}" +
        ".fitit-card-r span:first-child{color:var(--text-muted,#7c7c7c)}" +
        ".fitit-card-badge{position:absolute;top:12px;right:12px;font-size:10px;font-weight:600;line-height:16px;" +
        "padding:0 6px;border-radius:8px;background:#6d28d9;color:#fff}";
      row = el("div", "fitit-card-row");
      row.appendChild(css);
      list.forEach(function (c) {
        var card = el("div", "fitit-card");
        card.appendChild(el("div", "fitit-card-badge", "Fit-it"));
        card.appendChild(el("div", "fitit-card-title", c.title));
        card.appendChild(el("div", "fitit-card-value", fmt(c)));
        if (c.subtitle) card.appendChild(el("div", "fitit-card-sub", c.subtitle));
        if (Array.isArray(c.rows) && c.rows.length) {
          var rows = el("div", "fitit-card-rows");
          c.rows.forEach(function (r) {
            var line = el("div", "fitit-card-r");
            line.appendChild(el("span", "", r && r.label));
            line.appendChild(el("span", "", r && r.value));
            rows.appendChild(line);
          });
          card.appendChild(rows);
        }
        row.appendChild(card);
      });
      place();
      setInterval(place, 1000); // SPA hosts (Frappe) swap the main content without a page load
    }
    function place() {
      try {
        // Frappe/ERPNext keeps one .desk-page per visited workspace; only the visible one counts.
        var pages = document.querySelectorAll(".desk-page.page-main-content"), host = null;
        for (var i = 0; i < pages.length; i++) if (pages[i].offsetParent) host = pages[i];
        if (!host && !pages.length) host = document.querySelector("main") || document.body;
        if (!host) { if (row.parentNode) row.parentNode.removeChild(row); return; }
        if (row.parentNode !== host) host.insertBefore(row, host.firstChild);
      } catch (e) {}
    }
    get("/fitit/features").then(function (features) {
      var routes = features.filter(function (f) {
        return f.enabled && typeof f.route === "string" && f.route.indexOf("GET /x/") === 0;
      }).map(function (f) { return f.route.slice(4); });
      return Promise.all(routes.map(function (path) {
        return get(path).then(function (body) {
          var c = body && body.card;
          return c && typeof c === "object" && c.title != null && c.value != null ? c : null;
        }, function () { return null; });
      }));
    }).then(function (list) { render(list.filter(Boolean)); }).catch(function () {});
  }
})();
