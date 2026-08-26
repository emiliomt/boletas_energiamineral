/**
 * Progressive enhancement for the server-rendered UI.
 * Forms and links keep working without this file.
 */
(function () {
  "use strict";

  function onReady(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  function toggleFolioMode() {
    var select = document.getElementById("mode-select");
    var sequential = document.getElementById("sequential-fields");
    var imported = document.getElementById("imported-fields");
    if (!select || !sequential || !imported) return;
    var mode = select.value;
    sequential.hidden = mode !== "sequential";
    imported.hidden = mode !== "imported";
  }

  function bindSelectAll(master) {
    master.addEventListener("change", function () {
      var form = master.closest("form");
      if (!form) return;
      form.querySelectorAll('tbody input[type="checkbox"][name="ids"]').forEach(function (cb) {
        cb.checked = master.checked;
      });
    });
  }

  function bindBusyForm(form) {
    form.addEventListener("submit", function () {
      var btn = form.querySelector("button[type='submit']:not(.btn-danger)");
      if (!btn || btn.disabled) return;
      btn.disabled = true;
      btn.classList.add("is-busy");
      btn.setAttribute("aria-busy", "true");
      var busyLabel = btn.getAttribute("data-busy-label") || "Procesando…";
      btn.setAttribute("data-original-label", btn.textContent.trim());
      btn.textContent = busyLabel;
    });
  }

  function bindBoletaTemplates() {
    var select = document.getElementById("boleta-template-select");
    var dataEl = document.getElementById("boleta-templates-data");
    var form = document.getElementById("folio-batch-form");
    if (!select || !dataEl || !form) return;
    var templates;
    try {
      templates = JSON.parse(dataEl.textContent || "{}");
    } catch (err) {
      return;
    }
    select.addEventListener("change", function () {
      var payload = templates[select.value];
      if (!payload) return;
      Object.keys(payload).forEach(function (name) {
        var input = form.elements.namedItem(name);
        if (input && "value" in input) {
          input.value = payload[name] == null ? "" : String(payload[name]);
        }
      });
    });
  }

  onReady(function () {
    var modeSelect = document.getElementById("mode-select");
    if (modeSelect) {
      modeSelect.addEventListener("change", toggleFolioMode);
      toggleFolioMode();
    }

    bindBoletaTemplates();
    document.querySelectorAll(".js-select-all").forEach(bindSelectAll);
    document.querySelectorAll("form[data-busy-on-submit]").forEach(bindBusyForm);
  });
})();
