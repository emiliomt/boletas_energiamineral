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

  function waitForClerk(timeoutMs) {
    return new Promise(function (resolve, reject) {
      var start = Date.now();
      (function poll() {
        if (window.Clerk) return resolve(window.Clerk);
        if (Date.now() - start > (timeoutMs || 6000)) return reject(new Error("timeout"));
        setTimeout(poll, 50);
      })();
    });
  }

  function toggleFolioMode() {
    var select = document.getElementById("mode-select");
    var sequential = document.getElementById("sequential-fields");
    var imported = document.getElementById("imported-fields");
    if (!select || !sequential || !imported) return;
    var mode = select.value;
    var isSequential = mode === "sequential";
    sequential.hidden = !isSequential;
    imported.hidden = isSequential;
    sequential.querySelectorAll("input").forEach(function (el) {
      el.required = isSequential;
      el.disabled = !isSequential;
    });
    imported.querySelectorAll("textarea").forEach(function (el) {
      el.required = !isSequential;
      el.disabled = isSequential;
    });
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

  function toggleEntradaFields() {
    var kindSelect = document.getElementById("batch-kind");
    var proveedorSelect = document.getElementById("batch-producer");
    if (!kindSelect || !proveedorSelect) return;
    // The select is wrapped by a label.field container — hide/show that block.
    var fieldContainer = proveedorSelect.closest("label");
    if (!fieldContainer) fieldContainer = proveedorSelect.parentElement;
    var isEntrada = kindSelect.value === "entrada";
    fieldContainer.hidden = !isEntrada;
    // UX nicety: make it required only when visible; disable when hidden.
    proveedorSelect.required = isEntrada;
    proveedorSelect.disabled = !isEntrada;
    // If hiding the field, clear any accidental selection.
    if (!isEntrada) {
      proveedorSelect.value = "";
    }
  }

  function toggleProveedorModoPago(selectEl) {
    if (!selectEl) return;
    var form = selectEl.closest("form");
    if (!form) return;
    var mode = selectEl.value || "flete";
    var cajaField = form.querySelector(".precio-caja-field");
    var transporteField = form.querySelector(".precio-transporte-field");
    var pesoField = form.querySelector(".precio-peso-field");
    var fletePesoField = form.querySelector(".precio-flete-peso-field");
    var cajaInput = cajaField ? cajaField.querySelector("input") : null;
    var transporteInput = transporteField ? transporteField.querySelector("input") : null;
    var pesoInput = pesoField ? pesoField.querySelector("input") : null;
    var fletePesoInput = fletePesoField ? fletePesoField.querySelector("input") : null;
    var isPeso = mode === "peso";
    // Show/hide
    if (cajaField) cajaField.hidden = isPeso;
    if (transporteField) transporteField.hidden = isPeso;
    if (pesoField) pesoField.hidden = !isPeso;
    if (fletePesoField) fletePesoField.hidden = !isPeso;
    // Disable irrelevant inputs to avoid accidental submission
    if (cajaInput) cajaInput.disabled = isPeso;
    if (transporteInput) transporteInput.disabled = isPeso;
    if (pesoInput) pesoInput.disabled = !isPeso;
    if (fletePesoInput) fletePesoInput.disabled = !isPeso;
  }

  onReady(function () {
    var modeSelect = document.getElementById("mode-select");
    if (modeSelect) {
      modeSelect.addEventListener("change", toggleFolioMode);
      toggleFolioMode();
    }

    var kindSelect = document.getElementById("batch-kind");
    if (kindSelect) {
      kindSelect.addEventListener("change", toggleEntradaFields);
      // Initialize visibility based on the preselected option (Salida by default)
      toggleEntradaFields();
    }

    bindBoletaTemplates();
    document.querySelectorAll(".js-select-all").forEach(bindSelectAll);
    document.querySelectorAll("form[data-busy-on-submit]").forEach(bindBusyForm);

    // Configuración → Proveedores: toggle price fields based on modo de pago
    document.querySelectorAll("select.modo-pago-select").forEach(function (sel) {
      sel.addEventListener("change", function () {
        toggleProveedorModoPago(sel);
      });
      toggleProveedorModoPago(sel);
    });

    // Clerk: mount UserButton and toggle sign-in visibility if Clerk is available
    if (window.__CLERK_PUBLISHABLE_KEY__) {
      waitForClerk(6000)
        .then(function () {
          return window.Clerk.load({ publishableKey: window.__CLERK_PUBLISHABLE_KEY__ });
        })
        .then(function () {
          var mountPoint = document.getElementById("clerk-userbutton");
          var signInLink = document.getElementById("clerk-signin-link");
          if (!mountPoint) return;
          // Render a UserButton (includes sign-out)
          window.Clerk.mountUserButton(mountPoint, {
            userProfileUrl: window.location.origin + "/login", // keep simple; Clerk modal opens by default
          });
          // Toggle visibility based on current session
          function syncVisibility() {
            var isSignedIn = !!(window.Clerk.user && window.Clerk.session);
            if (signInLink) signInLink.style.display = isSignedIn ? "none" : "";
            mountPoint.style.display = isSignedIn ? "" : "none";
          }
          syncVisibility();
          window.Clerk.addListener && window.Clerk.addListener(syncVisibility);
        })
        .catch(function () {
          // If Clerk fails to load, leave the fallback "Ingresar" link visible.
        });
    }
  });
})();
