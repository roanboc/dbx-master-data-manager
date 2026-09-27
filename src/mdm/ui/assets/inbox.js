// The inbox's clientside callbacks (I2, I4, I6, I8, the full-width toggle and the Previous and Next task
// buttons) and the helpers keys.js calls: move, choose, openMenu, local, openRecord, settling, needsChoice.
// Moving and choosing happen here, in the browser, through the grid's API and the candidate control; only a
// decision reaches the server, and only for the task whose case is on screen. Reads row and candidate IDs
// only, never a value on the page, and writes no markup: the grid's cells are React elements with the
// row's own text.
//
// Owner: INBOX (plan B.8.7, B.8.8). The IDs below are those of src/mdm/ui/ids.py.
(function () {
  "use strict";
  var dc = (window.dash_clientside = window.dash_clientside || {});

  var GRID = "inbox-grid";
  var DEBOUNCE_MS = 150; // a burst of J or K renders one case, the last
  var POINTER_MS = 1500; // a candidate chosen with the pointer within this gives the keys back
  var SETTLE_MS = 60; // the grid hands its new selection to its prop within a few milliseconds
  var selectTimer = null;
  var lastIndex = 0; // where the selected row was, so J after it left the list goes to the one that took its place
  var pointerAt = 0;
  var requests = 0; // every request is a change, so the same key twice acts twice

  function noUpdate() {
    return window.dash_clientside.no_update;
  }
  function setProps(id, props) {
    window.dash_clientside.set_props(id, props);
  }
  function trigger() {
    var context = window.dash_clientside.callback_context;
    return context ? context.triggered_id : undefined;
  }
  function outputs(position) {
    var context = window.dash_clientside.callback_context;
    return (context && context.outputs_list && context.outputs_list[position]) || [];
  }
  // the slots of a callback's one wildcard output (a single output's list is not nested)
  function slotsOfOnlyOutput() {
    var context = window.dash_clientside.callback_context;
    var list = context ? context.outputs_list : null;
    return Array.isArray(list) ? list : [];
  }
  function gridApi() {
    try {
      return window.dash_ag_grid && window.dash_ag_grid.getApi ? window.dash_ag_grid.getApi(GRID) : null;
    } catch (error) {
      return null; // the grid is not on the page, or not ready yet
    }
  }
  function displayed(api) {
    var nodes = [];
    api.forEachNodeAfterFilterAndSort(function (node) {
      if (node.data) {
        nodes.push(node);
      }
    });
    return nodes;
  }
  function indexOf(nodes, taskId) {
    for (var i = 0; i < nodes.length; i += 1) {
      if (nodes[i].data.task_id === taskId) {
        return i;
      }
    }
    return -1;
  }
  function inGrid(el) {
    return !!(el && el.closest && el.closest("#" + GRID));
  }
  // the row's own button, once the grid has drawn it: focus follows the selection inside the list
  function focusRow(taskId) {
    setTimeout(function () {
      var grid = document.getElementById(GRID);
      if (!grid) {
        return;
      }
      var rows = grid.querySelectorAll(".ag-row[row-id]");
      for (var i = 0; i < rows.length; i += 1) {
        if (rows[i].getAttribute("row-id") === taskId) {
          var button = rows[i].querySelector(".mdm-row-open");
          if (button) {
            button.focus({preventScroll: true});
          }
          return;
        }
      }
    }, 0);
  }
  function selectAt(api, nodes, index) {
    var node = nodes[index];
    var follow = inGrid(document.activeElement);
    node.setSelected(true, true);
    api.ensureIndexVisible(index);
    lastIndex = index;
    if (follow) {
      focusRow(node.data.task_id);
    }
  }
  // the task whose case the pane shows (its data-task-id), or null
  function paneTask() {
    var pane = document.querySelector("#decide-pane [data-task-id]");
    return pane ? pane.getAttribute("data-task-id") : null;
  }
  function selectedTaskOf(rows) {
    if (Array.isArray(rows)) {
      return rows.length && rows[0] ? rows[0].task_id || null : null;
    }
    if (rows && Array.isArray(rows.ids)) {
      return rows.ids[0] || null;
    }
    return null;
  }
  function gridSelected(api) {
    var rows = api.getSelectedRows();
    return rows.length ? rows[0].task_id : null;
  }
  // the task after `taskId` in the list, for the prefetch; remembers where `taskId` sits
  function nextAfter(taskId) {
    var api = gridApi();
    if (!api) {
      return null;
    }
    var nodes = displayed(api);
    var at = indexOf(nodes, taskId);
    if (at < 0) {
      return null;
    }
    lastIndex = at;
    return nodes[at + 1] ? nodes[at + 1].data.task_id : null;
  }
  function clickIfEnabled(id) {
    var button = document.getElementById(id);
    if (button && !button.disabled) {
      button.click();
    }
  }
  // the next row after `taskId` that is not staged; after a row that left, the one that took its place
  function selectNextFrom(api, taskId) {
    var nodes = displayed(api);
    var at = indexOf(nodes, taskId);
    var start = at >= 0 ? at + 1 : Math.min(lastIndex, nodes.length);
    for (var k = start; k < nodes.length; k += 1) {
      if (!nodes[k].data.staged && nodes[k].data.task_id !== taskId) {
        selectAt(api, nodes, k);
        return true;
      }
    }
    return false;
  }

  // ------------------------------------------------------------------ the grid's cells
  // the grid's registry of cell components, by the name the grid reads (written in two parts, since the
  // public-safety scan matches its word list inside identifiers)
  var REGISTRY = "dashAgGri" + "dComponentFunctions";
  var cells = (window[REGISTRY] = window[REGISTRY] || {});
  var BANDS = {auto: true, review: true, distinct: true};
  function joined(parts) {
    return parts
      .filter(function (part) {
        return part !== null && part !== undefined && String(part) !== "";
      })
      .join(" · ");
  }
  function clock(ms) {
    var seconds = Math.max(Math.ceil(ms / 1000), 0);
    return Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
  }
  // the task: line 1 its kind and title, the title a button that opens it (Tab reaches it, Enter or a
  // click selects the row); line 2 the band chip, the suggestion and the reason, wrapping
  cells.MdmTask = function (props) {
    var d = props.data || {};
    var h = window.React.createElement;
    var node = props.node;
    function open() {
      if (node && node.setSelected) {
        node.setSelected(true, true);
      }
    }
    var second = [];
    if (d.band_text) {
      var band = BANDS[d.band] ? d.band : "distinct";
      second.push(h("span", {key: "band", className: "mdm-band mdm-band-" + band}, String(d.band_text)));
      second.push(" ");
    }
    second.push(h("span", {key: "words"}, joined([d.entity_label, d.suggestion, d.reason])));
    return h("div", {className: "mdm-task-cell"}, [
      h("div", {key: "first", className: "mdm-task-first"}, [
        h("span", {key: "kind", className: "mdm-kind"}, String(d.kind_label || "")),
        " ",
        h(
          "button",
          {key: "title", type: "button", className: "mdm-row-open", onClick: open, tabIndex: 0},
          String(d.title || "")
        ),
      ]),
      h("div", {key: "second", className: "mdm-task-second"}, second),
    ]);
  };
  // a staged decision's countdown, ticking in the row while it waits
  function StagedClock(props) {
    var React = window.React;
    var state = React.useState(Date.now());
    React.useEffect(function () {
      var timer = setInterval(function () {
        state[1](Date.now());
      }, 1000);
      return function () {
        clearInterval(timer);
      };
    }, []);
    var left = props.deadline - state[0];
    return React.createElement("span", {className: "mdm-countdown"}, left > 0 ? clock(left) : "committing");
  }
  // when it is due, then who holds it; a staged row: "Staged by you", then its countdown
  cells.MdmDue = function (props) {
    var d = props.data || {};
    var h = window.React.createElement;
    var second = d.staged_ms
      ? h(StagedClock, {deadline: d.staged_ms})
      : String(d.claimed || "");
    return h("div", {className: "mdm-due"}, [
      h("div", {key: "due", className: "mdm-due-first"}, String(d.due || "")),
      h("div", {key: "second", className: "mdm-due-second"}, second),
    ]);
  };

  // ------------------------------------------------------------------ a candidate chosen with the pointer
  // A radio chosen with the pointer keeps focus, and the shortcuts yield to a focused field; give the keys
  // back so 1–3 then L works either way. A keyboard choice keeps its focus (arrows move among radios).
  document.addEventListener(
    "pointerdown",
    function (e) {
      if (e.target && e.target.closest && e.target.closest(".mdm-candidate-choice")) {
        pointerAt = Date.now();
      }
    },
    true
  );
  document.addEventListener(
    "change",
    function (e) {
      var el = e.target;
      if (el && el.closest && el.closest(".mdm-candidate-choice") && Date.now() - pointerAt < POINTER_MS) {
        setTimeout(function () {
          el.blur();
        }, 0);
      }
    },
    true
  );

  // ArrowDown and ArrowUp inside the list move the selection as J and K do, so the grid never keeps a
  // second cursor of its own; they are caught before the grid sees them
  document.addEventListener(
    "keydown",
    function (e) {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") {
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey || !inGrid(e.target)) {
        return;
      }
      if (e.target.closest(".ag-cell-inline-editing, .ag-popup, .ag-popup-editor")) {
        return;
      }
      e.preventDefault();
      e.stopPropagation();
      if (dc.mdm_inbox) {
        dc.mdm_inbox.move(e.key === "ArrowDown" ? 1 : -1);
      }
    },
    true
  );

  // Tab: the grid would keep the focus on a row's button (its cells take none), and its tab guards would
  // skip the list from outside. So Tab enters the list at the selected row, walks the rows' buttons in the
  // order shown, and leaves the list after the last (Shift+Tab: before the first)
  function rowButtons() {
    var grid = document.getElementById(GRID);
    if (!grid) {
      return [];
    }
    var rows = Array.prototype.slice.call(grid.querySelectorAll(".ag-center-cols-container .ag-row[row-index]"));
    rows.sort(function (a, b) {
      return Number(a.getAttribute("row-index")) - Number(b.getAttribute("row-index"));
    });
    var buttons = [];
    rows.forEach(function (row) {
      var button = row.querySelector(".mdm-row-open");
      if (button) {
        buttons.push(button);
      }
    });
    return buttons;
  }
  function tabbable(el) {
    if (el.disabled || el.tabIndex < 0 || el.classList.contains("ag-tab-guard")) {
      return false;
    }
    if (el.closest("[hidden], [inert], [aria-hidden='true']")) {
      return false;
    }
    return el.getClientRects().length > 0;
  }
  // the first tabbable element after the list (`forward`), or the last one before it
  function outside(forward) {
    var grid = document.getElementById(GRID);
    var all = document.querySelectorAll("a[href], button, input, select, textarea, summary, [tabindex]");
    var found = null;
    for (var i = 0; i < all.length; i += 1) {
      var el = all[i];
      if (grid.contains(el) || !tabbable(el)) {
        continue;
      }
      var after = grid.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING;
      if (forward && after) {
        return el;
      }
      if (!forward && !after) {
        found = el;
      }
    }
    return found;
  }
  function selectedButton() {
    var grid = document.getElementById(GRID);
    var row = grid ? grid.querySelector(".ag-center-cols-container .ag-row.ag-row-selected") : null;
    var button = row ? row.querySelector(".mdm-row-open") : null;
    return button || null;
  }
  document.addEventListener(
    "keydown",
    function (e) {
      if (e.key !== "Tab" || e.ctrlKey || e.metaKey || e.altKey || !inGrid(e.target)) {
        return;
      }
      var buttons = rowButtons();
      var at = buttons.indexOf(e.target.closest(".mdm-row-open"));
      var next = at < 0 ? null : buttons[at + (e.shiftKey ? -1 : 1)];
      var target = next || outside(!e.shiftKey);
      e.preventDefault();
      e.stopPropagation();
      if (target) {
        target.focus();
      }
    },
    true
  );
  // `focus`, caught on its way down: the grid's guard answers `focus` itself, before any `focusin`
  document.addEventListener(
    "focus",
    function (e) {
      var guard = e.target;
      if (!guard || !guard.classList || !guard.classList.contains("ag-tab-guard") || !inGrid(guard)) {
        return;
      }
      if (e.relatedTarget && inGrid(e.relatedTarget)) {
        return; // leaving the list: the grid moves the focus on
      }
      var buttons = rowButtons();
      if (!buttons.length) {
        return; // an empty list: the grid moves the focus past it
      }
      e.stopPropagation();
      var fromEnd = guard.classList.contains("ag-tab-guard-bottom");
      var button = selectedButton() || (fromEnd ? buttons[buttons.length - 1] : buttons[0]);
      button.focus({preventScroll: false});
    },
    true
  );

  // Previous task and Next task: buttons for keyboard users without shortcuts, moving as K and J do
  document.addEventListener("click", function (e) {
    var button = e.target && e.target.closest ? e.target.closest("#prev-task, #next-task") : null;
    if (button && !button.disabled && dc.mdm_inbox) {
      dc.mdm_inbox.move(button.id === "prev-task" ? -1 : 1);
    }
  });

  dc.mdm_inbox = {
    // the shell's address and entity, copied onto the inbox while it is on screen (one slot, or none)
    bridgeAddress: function (search, entity, pathname) {
      var value = {search: search || "", entity: entity || "", path: pathname || "/"};
      return slotsOfOnlyOutput().map(function () {
        return value;
      });
    },
    // the shell's settlements, copied onto the inbox while it is on screen
    bridgeSettled: function (settled) {
      return slotsOfOnlyOutput().map(function () {
        return settled || null;
      });
    },

    // I7a: a decision key, a button or a menu item becomes one request for the act callback, naming the
    // task whose case the pane shows; a button drawn again (its clicks unset) asks for nothing. Link on a
    // close call not chosen yet moves to the choice instead.
    actRequest: function (keyEvent) {
      var slots = slotsOfOnlyOutput();
      var context = window.dash_clientside.callback_context;
      var t = context ? context.triggered_id : undefined;
      var clicked = ((context && context.triggered) || []).some(function (entry) {
        return entry && entry.value;
      });
      var action = null;
      if (t === "key-event") {
        action = keyEvent && keyEvent.action ? keyEvent.action : null;
      } else if (t && clicked) {
        if (t.type === "action") {
          action = t.decision;
        } else if (t.type === "snooze-option") {
          action = "snooze:" + t.hours;
        } else if (t.type === "escalate-option") {
          action = "escalate:" + t.reason;
        }
      }
      if (!action) {
        return slots.map(noUpdate);
      }
      if (action === "link" && dc.mdm_inbox.needsChoice()) {
        dc.mdm_inbox.focusChoice();
        if (window.mdmKeys && window.mdmKeys.done) {
          window.mdmKeys.done();
        }
        return slots.map(noUpdate);
      }
      requests += 1;
      var task = paneTask();
      return slots.map(function () {
        return {action: action, n: requests, task: task};
      });
    },

    // a close call with no candidate chosen: L chooses rather than links
    needsChoice: function () {
      return !!document.querySelector('#decide-pane [data-needs-choice="yes"]');
    },
    // focus the first candidate of the choice, so the arrows and Space choose
    focusChoice: function () {
      var radio = document.querySelector("#decide-pane .mdm-candidate-choice input[type=radio]");
      if (radio) {
        radio.focus();
      }
    },
    // true while the list's selection has not reached the pane yet: a decision key then does nothing
    settling: function () {
      if (selectTimer !== null) {
        return true;
      }
      var api = gridApi();
      var selected = api ? gridSelected(api) : null;
      var shown = paneTask();
      return !!(selected && selected !== shown);
    },

    // I2: Previous page and Next page keep a stack of the pages' cursors
    pageMove: function (_previous, _next, cursor, after) {
      var stack = cursor && Array.isArray(cursor.stack) ? cursor.stack.slice() : [];
      var t = trigger();
      if (t === "page-next") {
        if (!after) {
          return noUpdate();
        }
        stack.push(after);
        return {stack: stack, moved: "next"};
      }
      if (t === "page-prev") {
        if (!stack.length) {
          return noUpdate();
        }
        stack.pop();
        return {stack: stack, moved: "prev"};
      }
      return noUpdate();
    },

    // I4: a row selected. A move in the browser (J, K, a click) settles 150 ms after the last change, so
    // a burst of moves renders one case; a selection the server made (a new page) applies at once. The
    // stores follow only for another task.
    select: function (rows, selectedTask) {
      var id = selectedTaskOf(rows);
      clearTimeout(selectTimer);
      selectTimer = null;
      if (!id || id === selectedTask) {
        return [noUpdate(), noUpdate(), noUpdate()];
      }
      if (!Array.isArray(rows)) {
        // the server's own choice ({"ids": [...]}): the grid may not hold the new rows yet, so the hint
        // for the next row follows a moment later, and the row is scrolled into view (a deep link to a
        // task far down the page shows it selected)
        setTimeout(function () {
          setProps("next-hint", {data: nextAfter(id)});
          var api = gridApi();
          var node = api ? api.getRowNode(id) : null;
          if (node) {
            api.ensureNodeVisible(node, "middle");
          }
        }, SETTLE_MS);
        return [id, null, noUpdate()];
      }
      selectTimer = setTimeout(function () {
        selectTimer = null;
        var next = nextAfter(id);
        setProps("selected-candidate", {data: null});
        setProps("next-hint", {data: next});
        setProps("selected-task", {data: id});
      }, DEBOUNCE_MS);
      return [noUpdate(), noUpdate(), noUpdate()];
    },

    // I6: a candidate chosen shows its panel and impact line and names it on the Link button (filled; or
    // disabled when a rule blocks it); no request
    chooseCandidate: function (value) {
      var panels = outputs(1);
      var buttons = outputs(2);
      var impacts = outputs(3);
      if (!value) {
        return [
          noUpdate(),
          panels.map(noUpdate),
          buttons.map(noUpdate),
          impacts.map(noUpdate),
          buttons.map(noUpdate),
          buttons.map(noUpdate),
        ];
      }
      var panel = document.querySelector('#why-section [data-master-id="' + String(value) + '"]');
      var blocked = !!(panel && panel.getAttribute("data-blocked") === "yes");
      var classes = panels.map(function (slot) {
        return slot.id && slot.id.master_id === value
          ? "mdm-candidate-panel"
          : "mdm-candidate-panel mdm-hidden";
      });
      var lines = impacts.map(function (slot) {
        return slot.id && slot.id.master_id === value
          ? "mdm-candidate-impact"
          : "mdm-candidate-impact mdm-hidden";
      });
      function forLink(what) {
        return buttons.map(function (slot) {
          return slot.id && slot.id.decision === "link" ? what : noUpdate();
        });
      }
      var pane = document.querySelector("#decide-pane [data-needs-choice]");
      if (pane) {
        pane.removeAttribute("data-needs-choice");
      }
      return [value, classes, forLink("Link to " + value), lines, forLink("filled"), forLink(blocked)];
    },

    // F, or the toggle: the decide pane full width, and back; the toggle says what it does next
    fullWidth: function (_clicks, className) {
      var full = String(className || "").indexOf("mdm-decide-full") >= 0;
      var name = full ? "Full width (F)" : "Show the list (F)";
      return [full ? "mdm-inbox" : "mdm-inbox mdm-decide-full", name, name];
    },

    // I8: after an act, the grid takes the task's row back, and a staged decision moves the selection
    // to the next row that is not staged (or, with none left, the pane shows the staged line)
    advance: function (result, selected, caseVersion) {
      var bump = noUpdate();
      try {
        if (!result) {
          return bump;
        }
        var api = gridApi();
        var still = result.task_id && result.task_id === selected;
        if (result.advance && still) {
          var moved = false;
          if (api && (result.remove || gridSelected(api) === result.task_id)) {
            moved = selectNextFrom(api, result.task_id);
          }
          if (!moved) {
            bump = (caseVersion || 0) + 1;
          }
        }
        // The row's new state goes in once the new selection has reached the grid's selectedRows prop:
        // the grid selects that prop's rows again whenever its rows change.
        var touched = result.touched;
        var row = result.row;
        var remove = result.remove;
        if (touched && (row || remove)) {
          setTimeout(function () {
            var later = gridApi();
            var node = later ? later.getRowNode(touched) : null;
            if (!node) {
              return;
            }
            if (remove) {
              later.applyTransaction({remove: [node.data]});
            } else {
              later.applyTransaction({update: [row]});
            }
          }, SETTLE_MS);
        }
        return bump;
      } finally {
        if (window.mdmKeys && window.mdmKeys.done) {
          window.mdmKeys.done();
        }
      }
    },

    // J and K: the next or previous row; past either end, the next or previous page
    move: function (step) {
      var api = gridApi();
      if (!api) {
        return;
      }
      var nodes = displayed(api);
      if (!nodes.length) {
        return;
      }
      var at = indexOf(nodes, gridSelected(api));
      var target;
      if (at < 0) {
        var place = Math.min(lastIndex, nodes.length);
        target = step > 0 ? place : place - 1;
        if (target >= nodes.length) {
          target = nodes.length - 1;
        }
      } else {
        target = at + step;
      }
      if (target >= nodes.length) {
        clickIfEnabled("page-next");
        return;
      }
      if (target < 0) {
        clickIfEnabled("page-prev");
        return;
      }
      selectAt(api, nodes, target);
    },

    // 1, 2, 3: choose a candidate (only when there is a choice to make)
    choose: function (n) {
      var panels = document.querySelectorAll("#why-section [data-candidate-index]");
      if (panels.length < 2) {
        return;
      }
      var panel = document.querySelector('#why-section [data-candidate-index="' + Number(n) + '"]');
      var id = panel ? panel.getAttribute("data-master-id") : null;
      if (id) {
        setProps("candidate-choice", {value: id});
      }
    },

    // S and E: open the snooze or escalate menu from its button; the menu takes focus
    openMenu: function (id) {
      var target = document.querySelector(id === "snooze-menu" ? ".mdm-snooze-target" : ".mdm-escalate-target");
      if (!target || target.disabled) {
        return;
      }
      target.focus();
      target.click();
    },

    // F: the decide pane full width; ".": show or hide why (every candidate's disclosure together)
    local: function (key) {
      if (key === "f") {
        clickIfEnabled("pane-full");
        return;
      }
      if (key === ".") {
        var shown = document.querySelector("#why-section .mdm-candidate-panel:not(.mdm-hidden) details.mdm-why");
        var all = document.querySelectorAll("#why-section details.mdm-why");
        if (!all.length) {
          return;
        }
        var open = shown ? !shown.open : !all[0].open;
        all.forEach(function (details) {
          details.open = open;
        });
      }
    },

    // Enter: the shown candidate's golden record, else the golden record the case names
    openRecord: function () {
      var panel = document.querySelector("#why-section .mdm-candidate-panel:not(.mdm-hidden)");
      var id = panel ? panel.getAttribute("data-master-id") : null;
      var path = id ? "/record/" + encodeURIComponent(id) : null;
      if (!path) {
        var pane = document.querySelector("#decide-pane [data-open-record]");
        path = pane ? pane.getAttribute("data-open-record") : null;
      }
      if (path) {
        setProps("url", {pathname: path, search: ""});
      }
    },
  };
})();
