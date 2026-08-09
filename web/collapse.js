/* Expand/collapse behaviour. No dependencies.
 *
 * Delegated on document so panels injected later (SSE renders, gallery rows)
 * work without re-initialisation. aria-expanded is the single source of truth
 * for state - the stylesheet renders from it, this script only flips it.
 */
(function () {
  "use strict";

  function panelFor(toggle) {
    var id = toggle.getAttribute("aria-controls");
    if (id) {
      var el = document.getElementById(id);
      if (el) return el;
    }
    var next = toggle.nextElementSibling;
    return next && next.classList.contains("collapse-panel") ? next : null;
  }

  function set(toggle, open) {
    var panel = panelFor(toggle);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (panel) panel.classList.toggle("is-open", !!open);
  }

  function toggle(el) {
    var open = el.getAttribute("aria-expanded") !== "true";
    var group = el.getAttribute("data-collapse-group");
    if (group && open) {
      document.querySelectorAll('[data-collapse-group="' + group + '"]').forEach(function (other) {
        if (other !== el) set(other, false);
      });
    }
    set(el, open);
  }

  document.addEventListener("click", function (event) {
    var el = event.target.closest(".collapse-toggle, .chev-btn[aria-controls]");
    if (!el) return;
    event.preventDefault();
    toggle(el);
  });

  function refresh(root) {
    (root || document).querySelectorAll(".collapse-toggle, .chev-btn[aria-controls]").forEach(function (el) {
      set(el, el.getAttribute("aria-expanded") === "true");
    });
  }

  // Server-rendered aria-expanded decides the initial state.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { refresh(); });
  } else {
    refresh();
  }

  window.ZerothCollapse = { set: set, refresh: refresh };
})();
