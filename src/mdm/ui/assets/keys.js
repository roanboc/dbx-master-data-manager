// Single-key shortcuts for the inbox (decision 18), and the shell's clientside callbacks (namespace
// mdm_shell). Moving and choosing happen here, in the browser; a decision goes to Dash through the
// key-event store, one at a time, and only once the case of the selected task is on screen. The listener
// yields to fields, lists, menus, dialogs, grid editors and focused buttons and links, except N, and L on
// another candidate, on the forced sample's "Which comparison misled?", which decide with the comparison
// chosen (naming, below); it is off when the steward turns shortcuts off (WCAG 2.2 success criterion
// 2.1.4), and it acts only on a page that carries data-mdm-keys="on" (the inbox). It reads row and
// candidate IDs only, never a value on the page, and writes no markup.
//
// Owner: SHELL (plan B.8.8). The inbox's own helpers (move, choose, openMenu, local, openRecord, openGroups,
// needsChoice, needsSplit) are in inbox.js, under window.dash_clientside.mdm_inbox; the IDs below are those
// of src/mdm/ui/ids.py. G opens Alike reviews (story 3.3), on the inbox only, like every single key.
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
    // S6b: the tray's poll wakes when the counts see more (or fewer) of this steward's entries moving than
    // the tab's tray shows: staged, or a batch still committing (story 3.3); the tray's own refresh then
    // decides how long it polls
    trayWake: function (live, state) {
      if (typeof live !== "number") {
        return noUpdate();
      }
      var shown = (state || []).filter(function (entry) {
        return entry && (entry.status === "staged" || (entry.batch_id && entry.outcome === "committing"));
      }).length;
      return live !== shown ? false : noUpdate();
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
        // a batch whose chunks still commit keeps the button marked, with nothing to count down
        var committing = (state || []).some(function (entry) {
          return entry && entry.outcome === "committing";
        });
        return committing
          ? ["Tray · committing", "mdm-tray-button mdm-staged", texts]
          : ["Tray", "mdm-tray-button", texts];
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

  // ------------------------------------------------------------------ focus clear of a sticky footer
  // The decide pane's footer and the batch page's actions stay at the foot of their scroller, which keeps
  // room for them (--mdm-foot-room, styles.css), so the browser scrolls a focused control clear of them.
  // A footer taller than that room could still cover one entirely (WCAG 2.2 success criterion 2.4.11): a
  // control focused under a footer is scrolled to the middle of its scroller.
  var FOOTERS = ".mdm-decide-footer, .mdm-batch-actions";
  // whether a footer is what is painted at the middle of the control (a menu drawn over a footer is not)
  function covered(el) {
    var box = el.getBoundingClientRect();
    if (!box.width && !box.height) {
      return false;
    }
    var x = Math.min(Math.max(box.left + box.width / 2, 0), window.innerWidth - 1);
    var y = Math.min(Math.max(box.top + box.height / 2, 0), window.innerHeight - 1);
    var top = document.elementFromPoint(x, y);
    var footer = top && top.closest ? top.closest(FOOTERS) : null;
    return !!(footer && !footer.contains(el) && !el.contains(footer));
  }
  document.addEventListener(
    "focusin",
    function (e) {
      var el = e.target;
      if (!el || el === document.body || !el.getBoundingClientRect) {
        return;
      }
      // after the browser's own scroll to the focused control
      window.requestAnimationFrame(function () {
        if (document.activeElement === el && covered(el)) {
          el.scrollIntoView({block: "center", inline: "nearest"});
        }
      });
    },
    true
  );

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
  // "Which comparison misled?" (story 3.3): N, or L on another candidate, moves focus onto its radios, where
  // the arrow keys choose; the same key then decides with the comparison chosen (the key help says so), so N
  // and L pass through there, and every other key yields to the radios as to any field
  var NAMING_KEYS = {l: true, n: true};
  function naming(el, key) {
    return !!(
      NAMING_KEYS[key] &&
      el &&
      el.closest &&
      el.tagName === "INPUT" &&
      el.type === "radio" &&
      el.closest("#decide-pane .mdm-split-choice")
    );
  }
  function onControl(el) {
    // Enter and Space belong to a focused control (a disclosure's summary among them)
    return !!(
      el &&
      el.closest &&
      el.closest(
        'button, a[href], summary, [role="button"], [role="tab"], [role="menuitem"], [role="radio"], [role="switch"]'
      )
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
    // the page root that asks for the keys (the inbox); the body's own flag says only whether they are on,
    // so it never counts: on Alike reviews, a batch, a record or a source no single key acts (decision 18)
    if (!document.querySelector('[data-mdm-keys="on"]:not(body)')) {
      return;
    }
    var key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    if (typing(e.target) && !naming(e.target, key)) {
      return;
    }
    if ((e.key === "Enter" || e.key === " ") && onControl(e.target)) {
      return;
    }
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
    if (key === "g") {
      // Alike reviews (story 3.3): a navigation, so it sets no busy flag and sends no key event
      e.preventDefault();
      call("openGroups");
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
    if (typeof nav.needsSplit === "function" && nav.needsSplit(action)) {
      nav.focusSplit(); // a forced-sample review that disagrees: N or L names the comparison first
      return;
    }
    busy = true;
    busyTimer = setTimeout(window.mdmKeys.done, BUSY_MS);
    count += 1;
    window.dash_clientside.set_props("key-event", {data: {action: action, n: count}});
  });
})();
