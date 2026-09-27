// Single-key shortcuts for the inbox (decision 18), and the shell's clientside callbacks (namespace
// mdm_shell). Moving and choosing happen here, in the browser; a decision goes to Dash through the
// key-event store, one at a time, and only once the case of the selected task is on screen. The listener yields to fields, lists, menus, dialogs, grid editors and
// focused buttons and links; it is off when the steward turns shortcuts off (WCAG 2.2 success criterion
// 2.1.4), and it acts only on a page that carries data-mdm-keys="on" (the inbox). It reads row and
// candidate IDs only, never a value on the page, and writes no markup.
//
// Owner: SHELL (plan B.8.8). The inbox's own helpers (move, choose, openMenu, local, openRecord) are in
// inbox.js, under window.dash_clientside.mdm_inbox; the IDs below are those of src/mdm/ui/ids.py.
(function () {
  "use strict";
  var dc = (window.dash_clientside = window.dash_clientside || {});

  // ------------------------------------------------------------------ the shell's clientside callbacks
  function noUpdate() {
    return window.dash_clientside.no_update;
  }
  function trigger() {
    var context = window.dash_clientside.callback_context;
    return context ? context.triggered_id : undefined;
  }
  function clock(ms) {
    var seconds = Math.max(Math.ceil(ms / 1000), 0);
    return Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
  }
  function setKeysFlag(on) {
    if (document.body) {
      document.body.dataset.mdmKeys = on ? "on" : "off";
    }
  }

  dc.mdm_shell = {
    // S1 and S2: a first visit takes the browser's preference; afterwards the kept choice wins; either
    // way the scheme is applied (one callback: a chain would skip the second step when the first has
    // nothing to change)
    scheme: function (value, small) {
      var chosen = trigger() === "scheme-toggle-nav" ? small : value;
      if (chosen === "dark" || chosen === "light") {
        return [chosen, chosen === value ? noUpdate() : chosen, chosen === small ? noUpdate() : chosen];
      }
      var dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
      var seeded = dark ? "dark" : "light";
      return [seeded, seeded, seeded];
    },
    // the burger opens the navigation on a narrow screen
    burger: function (opened, navbar) {
      var next = Object.assign({}, navbar || {});
      next.collapsed = Object.assign({}, next.collapsed || {}, {mobile: !opened});
      return next;
    },
    // S3: the persona select and its session store, kept in step (a tab's choice survives a reload)
    syncPersona: function (chosen, kept) {
      if (trigger() === "persona-select") {
        return [chosen || null, noUpdate()];
      }
      if (kept && kept !== chosen) {
        return [noUpdate(), kept];
      }
      return [noUpdate(), noUpdate()];
    },
    // S4: the entity select ("all") and its session store ("" for every entity), kept in step
    syncEntity: function (chosen, kept) {
      if (trigger() === "entity-select") {
        return [!chosen || chosen === "all" ? "" : chosen, noUpdate()];
      }
      var want = kept ? kept : "all";
      return [noUpdate(), want !== chosen ? want : noUpdate()];
    },
    // S11: the "?" button opens the key help
    openHelp: function (clicks, small) {
      return clicks || small ? true : noUpdate();
    },
    // S12: the shortcuts switch and its store (kept per browser), and the body flag the listener reads
    keysSwitch: function (checked, kept) {
      if (trigger() === "keys-switch") {
        var chosen = checked !== false;
        setKeysFlag(chosen);
        return [chosen, noUpdate()];
      }
      var on = kept !== false;
      setKeysFlag(on);
      return [noUpdate(), checked === on ? noUpdate() : on];
    },
    // S9b: the tray button says whether its popover is open
    trayExpanded: function (opened) {
      return opened ? "true" : "false";
    },
    // S9: the tray button's count and countdown, and each staged entry's countdown in the popover
    trayCountdown: function (_ticks, state) {
      var now = Date.now();
      var staged = (state || []).filter(function (entry) {
        return entry && entry.status === "staged";
      });
      var byId = {};
      staged.forEach(function (entry) {
        byId[entry.entry_id] = entry;
      });
      var context = window.dash_clientside.callback_context;
      var slots = (context && context.outputs_list && context.outputs_list[2]) || [];
      var texts = slots.map(function (slot) {
        var entry = byId[slot.id && slot.id.entry];
        if (!entry) {
          return noUpdate();
        }
        var left = entry.deadline_ms - now;
        return left > 0 ? clock(left) : "Committing…";
      });
      if (!staged.length) {
        return ["Tray", "mdm-tray-button", texts];
      }
      var soonest = Math.min.apply(
        null,
        staged.map(function (entry) {
          return entry.deadline_ms;
        })
      );
      var left = soonest - now;
      var label = "Tray " + staged.length + " · " + (left > 0 ? clock(left) : "committing");
      return [label, "mdm-tray-button mdm-staged", texts];
    },
  };

  // ------------------------------------------------------------------ popup targets
  // Dash's Mantine components draw a plain div round a menu's or popover's target button, and Mantine
  // puts aria-haspopup and aria-expanded on that div, where ARIA does not allow them (axe:
  // aria-allowed-attr). They belong to the button inside: move them there, each time Mantine sets them.
  var POPUP_ATTRIBUTES = ["aria-haspopup", "aria-expanded", "aria-controls"];
  function liftPopupAttributes(el) {
    if (!el || el.tagName !== "DIV" || el.hasAttribute("role")) {
      return;
    }
    if (!el.hasAttribute("aria-haspopup") && !el.hasAttribute("aria-expanded")) {
      return;
    }
    var child = el.firstElementChild;
    if (!child || el.children.length !== 1) {
      return;
    }
    if (child.tagName !== "BUTTON" && child.getAttribute("role") !== "button") {
      return;
    }
    POPUP_ATTRIBUTES.forEach(function (name) {
      if (el.hasAttribute(name)) {
        child.setAttribute(name, el.getAttribute(name));
        el.removeAttribute(name);
      }
    });
  }
  function liftWithin(node) {
    if (node.nodeType !== 1) {
      return;
    }
    liftPopupAttributes(node);
    node.querySelectorAll("div[aria-haspopup], div[aria-expanded]").forEach(liftPopupAttributes);
  }
  new MutationObserver(function (records) {
    records.forEach(function (record) {
      if (record.type === "attributes") {
        liftPopupAttributes(record.target);
      } else {
        record.addedNodes.forEach(liftWithin);
      }
    });
  }).observe(document.documentElement, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ["aria-haspopup", "aria-expanded"],
  });

  // ------------------------------------------------------------------ the key listener
  var DECIDE = {l: "link", n: "not_a_match", a: "approve", r: "reject", c: "claim", u: "undo"};
  var BUSY_MS = 10000; // a decision that never answers frees the listener after this
  var count = 0;
  var busy = false;
  var busyTimer = null;

  function typing(el) {
    if (!el || el === document.body || !el.closest) {
      return false;
    }
    if (el.isContentEditable) {
      return true;
    }
    var tag = el.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
      return true;
    }
    return !!el.closest(
      '[role="dialog"], [role="listbox"], [role="combobox"], [role="menu"], [role="alertdialog"], ' +
        ".mantine-Popover-dropdown, .mantine-Menu-dropdown, " +
        ".ag-cell-inline-editing, .ag-popup, .ag-popup-editor"
    );
  }
  function onControl(el) {
    // Enter and Space belong to a focused control
    return !!(
      el &&
      el.closest &&
      el.closest('button, a[href], [role="button"], [role="tab"], [role="menuitem"], [role="radio"], [role="switch"]')
    );
  }
  function inbox() {
    var nav = window.dash_clientside && window.dash_clientside.mdm_inbox;
    return nav || {};
  }
  function call(name) {
    var nav = inbox();
    if (typeof nav[name] === "function") {
      nav[name].apply(nav, Array.prototype.slice.call(arguments, 1));
    }
  }

  window.mdmKeys = {
    // the inbox calls this once a decision has answered (plan I8), so the next key may act
    done: function () {
      busy = false;
      clearTimeout(busyTimer);
    },
    enabled: function () {
      return !document.body || document.body.dataset.mdmKeys !== "off";
    },
  };

  document.addEventListener("keydown", function (e) {
    if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || e.repeat || e.isComposing) {
      return;
    }
    if (!window.mdmKeys.enabled()) {
      return;
    }
    if (!document.querySelector('[data-mdm-keys="on"]')) {
      return;
    }
    if (typing(e.target)) {
      return;
    }
    if ((e.key === "Enter" || e.key === " ") && onControl(e.target)) {
      return;
    }
    var key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    if (key === "j" || key === "k") {
      e.preventDefault();
      call("move", key === "j" ? 1 : -1);
      return;
    }
    if (key === "1" || key === "2" || key === "3") {
      e.preventDefault();
      call("choose", Number(key));
      return;
    }
    if (key === "s" || key === "e") {
      e.preventDefault();
      call("openMenu", key === "s" ? "snooze-menu" : "escalate-menu");
      return;
    }
    if (e.key === "?") {
      e.preventDefault();
      window.dash_clientside.set_props("help-modal", {opened: true});
      return;
    }
    if (key === "f" || key === ".") {
      e.preventDefault();
      call("local", key);
      return;
    }
    if (key === "Enter") {
      e.preventDefault();
      call("openRecord");
      return;
    }
    var action = DECIDE[key];
    if (!action) {
      return;
    }
    e.preventDefault();
    if (busy) {
      return; // one decision at a time
    }
    var nav = inbox();
    if (action !== "undo" && typeof nav.settling === "function" && nav.settling()) {
      return; // the next case is still loading: a decision acts only on a case the steward has seen
    }
    if (action === "link" && typeof nav.needsChoice === "function" && nav.needsChoice()) {
      nav.focusChoice(); // a close call not chosen yet: L moves to the choice
      return;
    }
    busy = true;
    busyTimer = setTimeout(window.mdmKeys.done, BUSY_MS);
    count += 1;
    window.dash_clientside.set_props("key-event", {data: {action: action, n: count}});
  });
})();
