"use strict";
// Agents on the Pi's touchscreen: pick a device, see what its coding agents are doing.
// Opened by the desktop icon as /pi#token=...; refreshes every 2 s.

var $ = function (id) { return document.getElementById(id); };
var view = $("view");
var REFRESH_MS = 2000;
var ROW = 76; // device and session rows; keep in sync with pi.css

var token = "";
try { token = localStorage.getItem("pihub.token") || ""; } catch (e) {}
var hashToken = /^#token=(.+)$/.exec(location.hash);
if (hashToken) {
  token = decodeURIComponent(hashToken[1]);
  try { localStorage.setItem("pihub.token", token); } catch (e) {}
  history.replaceState(null, "", location.pathname);  // one history entry keeps window.close() allowed
}

var current = null;         // device name being viewed, or null for the list
var data = null;            // { devices, sessions }
var knownDevices = null;    // names seen so far, to mark new PCs
var fresh = {};             // name -> true while its arrival highlight plays
var lastDoing = {};         // session key -> last description, to mark changes
var failures = 0;

// ---------- helpers ----------
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}
function size(n) {
  if (!n) return "0 MB";
  var u = ["B", "KB", "MB", "GB", "TB"], i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)) + " " + u[i];
}
function ago(sec) {
  if (sec == null) return "";
  if (sec < 45) return "just now";
  if (sec < 3600) return Math.round(sec / 60) + " min ago";
  if (sec < 86400) return Math.round(sec / 3600) + " h ago";
  return Math.round(sec / 86400) + " d ago";
}
function nowSec() { return Date.now() / 1000; }
function isWin(name) { return /_win(_\d+)?$/.test(name); }
function key(s) { return s.device + "|" + s.agent + "|" + s.session; }
var RANK = { waiting: 0, running: 1, idle: 2, ended: 3 };

function api(path) {
  return fetch(path, { headers: { "X-Token": token }, cache: "no-store" }).then(function (r) {
    if (r.status === 401) throw new Error("token");
    if (!r.ok) throw new Error(r.status);
    return r.json();
  });
}

// ---------- data ----------
function refresh() {
  if (!token) return renderNoToken();
  Promise.all([api("/api/overview"), api("/api/agents")]).then(function (res) {
    failures = 0;
    data = { devices: res[0].devices, sessions: res[1].sessions };
    var names = data.devices.map(function (d) { return d.name; });
    if (knownDevices) {
      names.forEach(function (n) {
        if (knownDevices.indexOf(n) === -1) {
          fresh[n] = true;
          setTimeout(function () { delete fresh[n]; }, 1700);
        }
      });
    }
    knownDevices = names;
    if (current && names.indexOf(current) === -1) current = null;
    setLive(true);
    render();
  }).catch(function (err) {
    if (err.message === "token") return renderNoToken();
    failures++;
    setLive(false);
    if (!data) renderOffline();
  }).then(function () {
    setTimeout(refresh, failures ? Math.min(10000, REFRESH_MS * (failures + 1)) : REFRESH_MS);
  });
}

function setLive(ok) {
  var el = $("live");
  el.textContent = ok ? "Live" : "Reconnecting";
  el.classList.toggle("down", !ok);
}

// ---------- rendering ----------
function render() {
  var top = view.scrollTop;
  $("back").hidden = !current;
  $("title").textContent = current || "Agents";
  view.innerHTML = current ? deviceHTML(current) : listHTML();
  view.scrollTop = top;  // the 2 s refresh must not throw you back to page 1
  paintPager();
}

function sessionsOf(name) {
  return data.sessions.filter(function (s) { return s.device === name; });
}

function agentsLine(d, ses) {
  var waiting = ses.filter(function (s) { return s.state === "waiting"; }).length;
  var running = ses.filter(function (s) { return s.state === "running"; }).length;
  if (waiting) return { cls: "needs", text: waiting + (waiting === 1 ? " agent needs you" : " agents need you") + (running ? " · " + running + " working" : "") };
  if (running) return { cls: "busy", text: running + (running === 1 ? " agent working" : " agents working") };
  if (!d.online) return { cls: "", text: "Offline · seen " + ago(nowSec() - d.last_seen) };
  if (ses.length) return { cls: "", text: "Agents quiet" };
  return { cls: "", text: "No agents in the last 12 hours" };
}

function storeLine(d) {
  if (d.syncing) {
    var pct = d.total ? Math.min(100, Math.round(d.on_pi / d.total * 100)) : 0;
    return { cls: "up", pct: pct, text: "Uploading to the Pi · " + size(d.on_pi) + " of " + size(d.total) };
  }
  if (!d.on_pi) return { cls: "", text: "Nothing on the Pi yet" };
  return { cls: "", text: size(d.on_pi) + " on the Pi" + (d.last_sync ? " · synced " + ago(nowSec() - d.last_sync) : "") };
}

function listHTML() {
  if (!data.devices.length) {
    return '<div class="empty"><h2>No devices yet</h2><p>On a laptop on this Wi-Fi, run <code>join-pi.cmd</code>. It shows up here within a few seconds.</p></div>';
  }
  var rows = data.devices.map(function (d) {
    var ses = sessionsOf(d.name);
    var rank = ses.some(function (s) { return s.state === "waiting"; }) ? 0
             : ses.some(function (s) { return s.state === "running"; }) ? 1 : d.online ? 2 : 3;
    return { d: d, ses: ses, rank: rank };
  }).sort(function (a, b) { return a.rank - b.rank || a.d.name.localeCompare(b.d.name); });

  return rows.map(function (r) {
    var d = r.d, ag = agentsLine(d, r.ses), st = storeLine(d);
    return '<button class="dev' + (d.online ? " on" : "") + (fresh[d.name] ? " fresh" : "") + '" type="button" data-dev="' + esc(d.name) + '">' +
      '<svg class="icon" aria-hidden="true"><use href="#i-' + (isWin(d.name) ? "win" : "linux") + '"/></svg>' +
      '<span class="dev-main">' +
        '<span class="dev-top"><span class="dev-name">' + esc(d.name) + '</span><span class="dot" aria-label="' + (d.online ? "online" : "offline") + '"></span></span>' +
        '<span class="dev-agents ' + ag.cls + '">' + esc(ag.text) + "</span>" +
        '<span class="dev-store ' + st.cls + '">' + esc(st.text) + "</span>" +
      "</span>" +
      '<svg class="icon chev" aria-hidden="true"><use href="#i-chev"/></svg>' +
      (st.pct != null ? '<span class="dev-prog" style="width:' + st.pct + '%"></span>' : "") +
    "</button>";
  }).join("");
}

function stateLabel(s) {
  if (s.state === "running") return "Working";
  if (s.state === "waiting") return "Needs you";
  if (s.state === "ended") return "Ended";
  return s.event === "Stop" ? "Done" : "Quiet";
}

function sessionHTML(s) {
  var k = key(s), changed = lastDoing[k] !== undefined && lastDoing[k] !== s.doing;
  lastDoing[k] = s.doing;
  var quiet = s.state === "idle" || s.state === "ended";
  return '<article class="ses' + (s.state === "waiting" ? " needs" : "") + (quiet ? " quiet" : "") + '">' +
    '<div class="ses-head"><span class="ses-who">' + esc(s.agent_name) + "</span>" +
      (s.cwd ? '<span class="ses-where">in ' + esc(s.cwd) + "</span>" : "") +
      '<span class="state ' + s.state + '">' + stateLabel(s) + (quiet ? " · " + ago(s.age) : "") + "</span></div>" +
    '<p class="ses-doing' + (changed ? " changed" : "") + '">' + esc(s.doing) + "</p>" +
  "</article>";
}

function deviceHTML(name) {
  var ses = sessionsOf(name).slice().sort(function (a, b) { return RANK[a.state] - RANK[b.state] || b.ts - a.ts; });
  if (!ses.length) {
    return '<div class="empty"><h2>No agents on ' + esc(name) + '</h2><p>When a coding agent starts on this PC, what it’s doing shows up here.</p></div>';
  }
  var active = ses.filter(function (s) { return s.state === "running" || s.state === "waiting"; });
  var quiet = ses.filter(function (s) { return s.state === "idle" || s.state === "ended"; }).slice(0, 12);
  var h = active.map(sessionHTML).join("");
  if (quiet.length) h += (active.length ? '<p class="label">Earlier</p>' : "") + quiet.map(sessionHTML).join("");
  return h;
}

function renderNoToken() {
  $("back").hidden = true;
  $("title").textContent = "Agents";
  view.innerHTML = '<div class="empty"><h2>Not connected</h2><p>Open Agents from its desktop icon. The icon carries the PiHub token this screen needs.</p></div>';
  paintPager();
}

function renderOffline() {
  view.innerHTML = '<div class="empty"><h2>Can’t reach PiHub</h2><p>The <code>pihub</code> service on this Pi isn’t answering. This screen keeps trying.</p></div>';
  paintPager();
}

// ---------- page rail ----------
function pageSize() { return Math.max(ROW, Math.floor(view.clientHeight / ROW) * ROW); }
function paintPager() {
  var size = pageSize();
  var total = 1 + Math.ceil(Math.max(0, view.scrollHeight - view.clientHeight - 2) / size);
  var atEnd = view.scrollTop + view.clientHeight >= view.scrollHeight - 2;
  var cur = atEnd ? total : Math.min(total - 1, Math.round(view.scrollTop / size) + 1);
  $("pgNum").textContent = cur + "/" + total;
  $("pgUp").disabled = view.scrollTop <= 0;
  $("pgDown").disabled = atEnd;
}

// ---------- taps ----------
view.addEventListener("click", function (e) {
  var b = e.target.closest("[data-dev]");
  if (!b) return;
  current = b.dataset.dev;
  view.scrollTop = 0;
  render();
});
$("back").addEventListener("click", function () { current = null; view.scrollTop = 0; render(); });
$("quit").addEventListener("click", function () { window.close(); });
$("pgUp").addEventListener("click", function () { view.scrollTop -= pageSize(); paintPager(); });
$("pgDown").addEventListener("click", function () { view.scrollTop += pageSize(); paintPager(); });

refresh();
