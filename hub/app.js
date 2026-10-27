"use strict";

var $ = function (id) { return document.getElementById(id); };
var token = "";
try { token = localStorage.getItem("pihub.token") || ""; } catch (e) {}
// Desktop launchers open /agents#token=...; keep it, then drop it from the address bar.
var hashToken = /^#token=(.+)$/.exec(location.hash);
if (hashToken) {
  token = decodeURIComponent(hashToken[1]);
  try { localStorage.setItem("pihub.token", token); } catch (e) {}
  history.replaceState(null, "", location.pathname);
}
var tab = location.pathname === "/agents" ? "agents" : "overview";
var timer = null;
var stream = null, streamT = null;  // live push from /api/stream on the Agents tab

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}
function fmtSize(n) {
  if (!n) return "0 B";
  if (n < 1024) return n + " B";
  var u = ["KB", "MB", "GB", "TB"], i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
  return (n < 10 ? n.toFixed(1) : Math.round(n)) + " " + u[i];
}
var rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
function ago(sec) {
  if (!sec) return "never";
  var d = sec - Date.now() / 1000, a = Math.abs(d);
  if (a < 45) return d < 0 ? "just now" : "in a moment";
  var units = [[3600, 60, "minute"], [86400, 3600, "hour"], [2629800, 86400, "day"], [Infinity, 2629800, "month"]];
  for (var i = 0; i < units.length; i++) if (a < units[i][0]) return rtf.format(Math.round(d / units[i][1]), units[i][2]);
}
var toastT;
function toast(msg, bad) {
  var t = $("toast"); t.textContent = msg; t.classList.toggle("error", !!bad); t.classList.add("show");
  clearTimeout(toastT); toastT = setTimeout(function () { t.classList.remove("show"); }, bad ? 5000 : 2500);
}

function api(path, opts) {
  opts = opts || {};
  opts.headers = Object.assign({ "X-Token": token, "Content-Type": "application/json" }, opts.headers || {});
  return fetch(path, opts).then(function (r) {
    return r.json().catch(function () { return {}; }).then(function (d) {
      if (r.status === 401) { showGate(); throw new Error("locked"); }
      if (!r.ok) throw new Error(d.error || "PiHub answered " + r.status);
      return d;
    });
  });
}

// ---------- token gate ----------
function showGate(msg) {
  document.querySelectorAll(".tab").forEach(function (s) { s.hidden = true; });
  $("gate").hidden = false;
  $("gateErr").textContent = msg || "";
  $("tokenInput").focus();
}
$("gate").addEventListener("submit", function (e) {
  e.preventDefault();
  token = $("tokenInput").value.trim();
  fetch("/api/overview", { headers: { "X-Token": token } }).then(function (r) {
    if (r.status === 401) return showGate("That token didn’t work. Check /etc/pihub.env on the Pi.");
    try { localStorage.setItem("pihub.token", token); } catch (e2) {}
    $("gate").hidden = true;
    setTab(tab);
  });
});

// ---------- tabs ----------
function setTab(t) {
  tab = t;
  document.querySelectorAll("#tabs button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.tab === t)); });
  document.querySelectorAll(".tab").forEach(function (s) { s.hidden = s.id !== "tab-" + t; });
  history.replaceState(null, "", t === "agents" ? "/agents" : "/");
  clearInterval(timer);
  if (stream) { stream.close(); stream = null; }
  if (t === "overview") { loadOverview(); timer = setInterval(loadOverview, 10000); }
  if (t === "agents") {
    loadAgents();
    timer = setInterval(loadAgents, 2000);
    // Live push: redraw as soon as an agent does something, not on the next 2 s tick.
    if (window.EventSource) {
      stream = new EventSource("/api/stream?token=" + encodeURIComponent(token));
      stream.onmessage = function () { clearTimeout(streamT); streamT = setTimeout(loadAgents, 80); };
    }
  }
  if (t === "search") $("q").focus();
}
$("tabs").addEventListener("click", function (e) {
  var b = e.target.closest("[data-tab]");
  if (b) setTab(b.dataset.tab);
});

// ---------- overview ----------
function loadOverview() {
  api("/api/overview").then(function (o) {
    $("disk").textContent = fmtSize(o.disk.free) + " free on the Pi";
    $("roundLine").textContent = o.next_round
      ? "Everything syncs every " + o.interval_days + " days. Last round " + ago(o.last_round) + ", next " + ago(o.next_round) + "."
      : "Everything syncs every " + o.interval_days + " days. The first round starts tonight at 02:00, or press Sync all now.";
    renderDevices(o.devices);
    renderTasks(o.tasks);
  }).catch(function (e) { if (e.message !== "locked") toast(e.message, true); });
}

function renderDevices(list) {
  if (!list.length) {
    $("devices").innerHTML = '<div class="empty"><h3>No computers yet</h3>A computer appears here the first time its pihub-client checks in. Install the client on each PC (see the README in pihub/).</div>';
    return;
  }
  $("devices").innerHTML = list.map(function (d) {
    var stale = !d.last_sync || Date.now() / 1000 - d.last_sync > 5 * 86400;
    return '<article class="device"><div class="device-top"><svg class="icon" aria-hidden="true"><use href="#i-pc"/></svg>' +
      '<span class="device-name">' + esc(d.name) + "</span>" +
      (d.online ? '<span class="chip ok">online</span>' : '<span class="chip">offline</span>') + "</div>" +
      "<dl><dt>Last seen</dt><dd>" + ago(d.last_seen) + "</dd>" +
      "<dt>Last sync</dt><dd>" + (stale ? '<span class="chip warn">' + ago(d.last_sync) + "</span>" : ago(d.last_sync)) + "</dd>" +
      "<dt>Files on the Pi</dt><dd>" + (d.files || 0).toLocaleString() + " · " + fmtSize(d.bytes) + "</dd></dl>" +
      '<button class="btn small" type="button" data-sync="' + esc(d.name) + '"><svg class="icon" aria-hidden="true"><use href="#i-sync"/></svg>Sync now</button></article>';
  }).join("");
}

var STATUS = {
  queued: ["waiting for the PC", ""], running: ["in progress", "live"], upload_wait: ["waiting for the sender", ""],
  ready: ["ready on the Pi", "live"], done: ["done", "ok"], failed: ["failed", "bad"]
};
function renderTasks(list) {
  if (!list.length) {
    $("tasks").innerHTML = '<div class="empty">Nothing yet. Syncs and file requests from your agents show up here.</div>';
    return;
  }
  $("tasks").innerHTML = list.map(function (t) {
    var st = STATUS[t.status] || [t.status, ""];
    var main = t.action === "sync"
      ? '<div class="task-path">Sync ' + esc(t.device) + " → Pi</div>"
      : '<div class="task-path" title="' + esc(t.src_path) + '">' + esc(t.src_path) + "</div>" +
        '<div class="task-route">' + esc(t.src_device) + '<svg class="icon" aria-hidden="true"><use href="#i-arrow"/></svg>' + esc(t.dst_device) + " · " + esc(t.dst_path) + "</div>";
    var note = t.error ? '<div class="task-note err">' + esc(t.error) + "</div>" : (t.note ? '<div class="task-note">' + esc(t.note) + "</div>" : "");
    return '<div class="task"><div class="task-kind">' + (t.action === "sync" ? "Sync" : "File request") + "</div>" +
      '<div class="task-main">' + main + note + "</div>" +
      '<div class="task-side"><span class="chip ' + st[1] + '">' + st[0] + "</span>" + ago(t.updated) + "</div></div>";
  }).join("");
}

document.addEventListener("click", function (e) {
  var b = e.target.closest("[data-sync]");
  if (!b) return;
  b.disabled = true;
  api("/api/sync-now", { method: "POST", body: JSON.stringify({ device: b.dataset.sync }) })
    .then(function (r) { toast(r.created.length ? "Sync queued for " + b.dataset.sync : b.dataset.sync + " is already syncing"); loadOverview(); })
    .catch(function (err) { toast(err.message, true); })
    .then(function () { b.disabled = false; });
});
$("syncAll").addEventListener("click", function () {
  api("/api/sync-now", { method: "POST", body: "{}" })
    .then(function (r) { toast(r.created.length ? "Sync queued for " + r.created.length + " computer(s)" : "Everything is already syncing"); loadOverview(); })
    .catch(function (err) { toast(err.message, true); });
});

// ---------- search ----------
var searchT;
$("q").addEventListener("input", function (e) {
  clearTimeout(searchT);
  var q = e.target.value.trim();
  if (!q) { $("results").innerHTML = ""; return; }
  searchT = setTimeout(function () {
    api("/api/search?q=" + encodeURIComponent(q)).then(function (d) {
      if (!d.results.length) {
        $("results").innerHTML = '<div class="empty">No file named like “' + esc(q) + "” on any computer. The inventory updates after each sync.</div>";
        return;
      }
      $("results").innerHTML = d.results.map(function (r) {
        var cut = r.path.length - r.name.length;
        var href = "/api/file?device=" + encodeURIComponent(r.device) + "&path=" + encodeURIComponent(r.path) + "&token=" + encodeURIComponent(token);
        return '<div class="result"><span class="dev">' + esc(r.device) + '</span><span class="path" title="' + esc(r.path) + '">' +
          esc(r.path.slice(0, cut)) + "<b>" + esc(r.name) + "</b></span>" +
          '<span class="num">' + fmtSize(r.size) + '</span><span class="num">' + ago(r.mtime) + "</span>" +
          '<a class="icon-btn" href="' + href + '" download="' + esc(r.name) + '" aria-label="Download ' + esc(r.name) + '"><svg class="icon" aria-hidden="true"><use href="#i-download"/></svg></a></div>';
      }).join("");
    }).catch(function (err) { if (err.message !== "locked") toast(err.message, true); });
  }, 200);
});

// ---------- agents ----------
var STATE = { running: ["working", "live"], waiting: ["waiting for you", "warn"], idle: ["idle", ""], ended: ["ended", ""] };
function loadAgents() {
  api("/api/agents").then(function (d) {
    var s = d.sessions;
    var active = s.filter(function (x) { return x.state === "running" || x.state === "waiting"; }).length;
    $("agentsLine").textContent = s.length ? active + " active · refreshes every 2 s" : "";
    if (!s.length) {
      $("agents").innerHTML = '<div class="empty"><h3>No agent activity in the last 12 hours</h3>Sessions appear here once the Claude Code hooks or the OpenCode plugin on a PC start reporting.</div>';
      return;
    }
    $("agents").innerHTML = s.map(function (a) {
      var st = STATE[a.state] || [a.state, ""];
      var now = a.state === "waiting" ? (a.summary || "Needs your attention")
        : a.state === "running" ? (a.tool ? a.tool + (a.summary ? ": " + a.summary : "") : (a.summary || a.event))
        : (a.event === "Stop" ? "Finished its turn" : "Last: " + (a.tool || a.event));
      return '<article class="agent ' + a.state + '"><div class="agent-top"><svg class="icon" aria-hidden="true"><use href="#i-bot"/></svg>' +
        '<div class="agent-name">' + esc(a.agent) + "<small>" + esc(a.device) + (a.cwd ? " · " + esc(a.cwd) : "") + "</small></div>" +
        '<span class="chip ' + st[1] + '">' + st[0] + "</span></div>" +
        '<div class="agent-now">' + esc(now) + "</div>" +
        '<div class="agent-meta">' + ago(a.ts) + " · " + a.events + (a.events === 1 ? " event" : " events") + " this session</div></article>";
    }).join("");
  }).catch(function (e) { if (e.message !== "locked") toast(e.message, true); });
}

document.addEventListener("visibilitychange", function () { if (!document.hidden) setTab(tab); else clearInterval(timer); });
setTab(tab);
