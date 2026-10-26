"use strict";

var $ = function (id) { return document.getElementById(id); };
var audio = $("player");
var listEl = $("list"), listWrap = $("listWrap");
var stage = $("stage");
var playBtn = $("play"), seek = $("seek");
var ring = document.querySelector(".ring-fg");
var CIRC = 2 * Math.PI * 55;
ring.style.strokeDasharray = CIRC;
ring.style.strokeDashoffset = CIRC;

var tracks = [], byId = {}, root = "";
var view = "songs", drill = null, query = "";
var queue = [], orig = null, qi = -1, shuffle = false, repeat = "off";
var shown = [];      // song ids currently listed, used when a row is played
var loaded = false;
// ?pi=1: the Pi's 3.5" resistive touchscreen, where a tap is the only input
var PI = /[?&]pi=1(&|$)/.test(location.search);
if (PI) document.body.classList.add("pi");

// ---------- helpers ----------
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}
function fmt(s) {
  if (!isFinite(s) || s < 0 || s == null) return "";
  var m = Math.floor(s / 60), ss = Math.floor(s % 60);
  return m + ":" + (ss < 10 ? "0" : "") + ss;
}
function hue(s) {
  var h = 0;
  for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
  return 8 + (h % 40); // stay in the warm ember range
}
function initial(s) { return (s || "?").trim().charAt(0).toUpperCase(); }
function coverHTML(t, label) {
  if (t && t.cover) return '<img src="/cover/' + t.id + '" alt="" loading="lazy" decoding="async">';
  var name = label || (t ? t.title : "?");
  return '<span class="ph" style="--h:' + hue(name) + '">' + esc(initial(name)) + "</span>";
}
function say(msg, isError) {
  $("status").textContent = msg || "";
  $("status").classList.toggle("error", !!isError);
}
function current() { return qi >= 0 ? byId[queue[qi]] : null; }
function matches(t, q) {
  return (t.title + " " + t.artist + " " + t.album).toLowerCase().indexOf(q) !== -1;
}

// ---------- persistence ----------
function save() {
  try {
    localStorage.setItem("musik.state", JSON.stringify({
      queue: queue, orig: orig, qi: qi, shuffle: shuffle, repeat: repeat,
      t: Math.floor(audio.currentTime || 0)
    }));
  } catch (e) { /* storage blocked: Musik still works, it just forgets */ }
}
function restore() {
  var s;
  try { s = JSON.parse(localStorage.getItem("musik.state") || "null"); } catch (e) { s = null; }
  if (!s) return;
  shuffle = !!s.shuffle;
  repeat = ["off", "all", "one"].indexOf(s.repeat) >= 0 ? s.repeat : "off";
  var cur = s.queue && s.queue[s.qi];
  queue = (s.queue || []).filter(function (id) { return byId[id]; });
  orig = s.orig ? s.orig.filter(function (id) { return byId[id]; }) : null;
  qi = queue.indexOf(cur);
  if (qi >= 0) load(false, s.t || 0);
}

// ---------- library ----------
function fetchLibrary() {
  renderSkeleton();
  return fetch("/api/library", { cache: "no-store" })
    .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(function (data) {
      tracks = data.tracks; root = data.root; byId = {};
      tracks.forEach(function (t) { byId[t.id] = t; });
      loaded = true;
      $("count").textContent = tracks.length + (tracks.length === 1 ? " song" : " songs");
      document.querySelector('[data-view="albums"]').hidden =
        !tracks.some(function (t) { return t.album; });
      render();
    })
    .catch(function () {
      listEl.innerHTML = '<div class="empty"><h2>Can’t reach the library</h2>' +
        "<p>Musik on the Pi didn’t answer. Check that the <code>songpage</code> service is running, then try again.</p>" +
        '<button class="pill" type="button" data-act="reload">Try again</button></div>';
    });
}

function renderSkeleton() {
  var h = "";
  for (var i = 0; i < 9; i++) h += '<div class="skel" aria-hidden="true"><span></span><div><b style="width:' + (50 + (i * 37) % 40) + '%"></b><b></b></div></div>';
  listEl.innerHTML = h;
}

function songRow(t, i, ctx) {
  var isCur = current() && current().id === t.id;
  return '<div class="row' + (isCur ? " current" : "") + '"' + (isCur ? ' aria-current="true"' : "") + ">" +
    '<button class="row-main" type="button" data-act="play" data-i="' + i + '" data-ctx="' + ctx + '">' +
      '<span class="cover">' + coverHTML(t) + "</span>" +
      '<span class="row-text"><span class="row-title">' + esc(t.title) + "</span>" +
      '<span class="row-sub">' + esc(t.artist) + (t.album ? " · " + esc(t.album) : "") + "</span></span>" +
      '<span class="row-time">' + fmt(t.duration) + "</span>" +
      '<span class="eq" aria-hidden="true"><i></i><i></i><i></i></span>' +
    "</button>" +
    '<button class="heart" type="button" data-act="fav" data-id="' + t.id + '" aria-pressed="' + t.fav + '" aria-label="' + (t.fav ? "Unlike " : "Like ") + esc(t.title) + '">' +
      '<svg class="icon" aria-hidden="true"><use href="#i-heart"/></svg></button>' +
    "</div>";
}

function groupRow(name, items, kind) {
  var withCover = items.filter(function (t) { return t.cover; })[0];
  var n = items.length;
  return '<div class="row"><button class="row-main" type="button" data-act="open" data-name="' + esc(name) + '">' +
    '<span class="cover' + (kind === "artist" ? " round" : "") + '">' + coverHTML(withCover, name) + "</span>" +
    '<span class="row-text"><span class="row-title">' + esc(name || "Singles") + "</span>" +
    '<span class="row-sub">' + n + (n === 1 ? " song" : " songs") + "</span></span>" +
    '<span class="row-time"></span></button></div>';
}

function emptyBlock(title, body, action) {
  return '<div class="empty"><h2>' + title + "</h2><p>" + body + "</p>" + (action || "") + "</div>";
}

function render() {
  if (!loaded) return;
  var q = query.trim().toLowerCase();
  var h = "";
  shown = [];

  if (!tracks.length) {
    listEl.innerHTML = emptyBlock("No songs yet",
      "Musik plays everything in <code>" + esc(root) + "</code>. New songs from your laptop show up after the next sync.",
      '<button class="pill" type="button" data-act="rescan"><svg class="icon" aria-hidden="true"><use href="#i-refresh"/></svg>Rescan now</button>');
    return;
  }

  if (view === "songs" || view === "liked") {
    shown = tracks.filter(function (t) {
      return (view === "songs" || t.fav) && (!q || matches(t, q));
    }).map(function (t) { return t.id; });
    if (view === "liked" && !tracks.some(function (t) { return t.fav; })) {
      h = emptyBlock("Nothing liked yet", "Tap the heart next to a song and it lands here, on every device.");
    } else if (!shown.length) {
      h = emptyBlock("No matches", "Nothing in your library matches “" + esc(query.trim()) + "”.");
    } else {
      h = shown.map(function (id, i) { return songRow(byId[id], i, "list"); }).join("");
    }
  } else if (view === "artists" || view === "albums") {
    var key = view === "artists" ? "artist" : "album";
    if (drill !== null) {
      shown = tracks.filter(function (t) { return t[key] === drill && (!q || matches(t, q)); })
        .map(function (t) { return t.id; });
      h = '<div class="list-head">' +
        '<button class="icon-btn" type="button" data-act="back" aria-label="All ' + view + '"><svg class="icon" aria-hidden="true"><use href="#i-back"/></svg></button>' +
        "<h2>" + esc(drill || "Singles") + "</h2>" +
        '<button class="pill primary" type="button" data-act="playall"><svg class="icon" aria-hidden="true"><use href="#i-play"/></svg>Play</button></div>' +
        (shown.length ? shown.map(function (id, i) { return songRow(byId[id], i, "list"); }).join("")
                      : emptyBlock("No matches", "Nothing here matches “" + esc(query.trim()) + "”."));
    } else {
      var groups = {}, order = [];
      tracks.forEach(function (t) {
        if (view === "albums" && !t.album) return;
        if (!groups[t[key]]) { groups[t[key]] = []; order.push(t[key]); }
        groups[t[key]].push(t);
      });
      order.sort(function (a, b) { return a.localeCompare(b, undefined, { sensitivity: "base" }); });
      var names = order.filter(function (n) {
        return !q || n.toLowerCase().indexOf(q) !== -1 || groups[n].some(function (t) { return matches(t, q); });
      });
      h = names.length ? names.map(function (n) { return groupRow(n, groups[n], key); }).join("")
                       : emptyBlock("No matches", "No " + view + " match “" + esc(query.trim()) + "”.");
    }
  } else if (view === "queue") {
    if (qi < 0) {
      h = emptyBlock("The queue is empty", "Play any song and the rest of that list lines up here.");
    } else {
      shown = queue.slice();
      h = '<p class="list-label">Now playing</p>' + songRow(byId[queue[qi]], qi, "queue");
      var rest = queue.slice(qi + 1);
      h += '<p class="list-label">Up next' + (shuffle ? " · shuffled" : "") + "</p>";
      h += rest.length ? rest.map(function (id, k) { return songRow(byId[id], qi + 1 + k, "queue"); }).join("")
                       : '<p class="empty" style="padding:8px 10px">' + (repeat === "all" ? "Repeat is on, so the queue starts over." : "Nothing after this song.") + "</p>";
    }
  }
  listEl.innerHTML = h;
  paintPager();
}

// ---------- Pi pager: page buttons instead of scrolling ----------
var ROW = 52; // keep in sync with the body.pi row height in style.css
function pageSize() { return Math.max(ROW, Math.floor(listWrap.clientHeight / ROW) * ROW); }
function paintPager() {
  if (!PI) return;
  var size = pageSize();
  var total = 1 + Math.ceil(Math.max(0, listWrap.scrollHeight - listWrap.clientHeight - 2) / size);
  var atEnd = listWrap.scrollTop + listWrap.clientHeight >= listWrap.scrollHeight - 2;
  var cur = atEnd ? total : Math.min(total - 1, Math.round(listWrap.scrollTop / size) + 1);
  $("pgNum").textContent = cur + "/" + total;
  $("pgUp").disabled = listWrap.scrollTop <= 0;
  $("pgDown").disabled = atEnd;
}
function page(dir) { listWrap.scrollTop += dir * pageSize(); paintPager(); }

function setView(v) {
  view = v; drill = null;
  document.querySelectorAll("#tabs button").forEach(function (b) {
    b.setAttribute("aria-pressed", String(b.dataset.view === v));
  });
  listWrap.scrollTop = 0;
  render();
}

// ---------- playback ----------
function shuffleRest() {
  orig = queue.slice();
  var cur = queue[qi];
  var rest = queue.filter(function (_, i) { return i !== qi; });
  for (var i = rest.length - 1; i > 0; i--) {
    var j = Math.floor(Math.random() * (i + 1));
    var tmp = rest[i]; rest[i] = rest[j]; rest[j] = tmp;
  }
  queue = [cur].concat(rest);
  qi = 0;
}

function playList(ids, i) {
  queue = ids.slice(); orig = null; qi = i;
  if (shuffle) shuffleRest();
  load(true);
}

function load(autoplay, at) {
  var t = current();
  if (!t) return;
  audio.src = "/stream/" + t.id;
  if (at) audio.addEventListener("loadedmetadata", function once() {
    audio.currentTime = Math.min(at, (audio.duration || at) - 1);
    audio.removeEventListener("loadedmetadata", once);
  });
  paintNow(t);
  if (autoplay) {
    var p = audio.play();
    if (p && p.catch) p.catch(function () { say("Tap play to start. The browser blocked autoplay.", true); });
  }
  render();
  save();
}

function paintNow(t) {
  stage.classList.remove("idle");
  var art = $("art");
  art.classList.remove("empty-art");
  art.innerHTML = coverHTML(t);
  $("title").textContent = t.title;
  $("artist").textContent = t.artist;
  document.title = t.title + " · " + t.artist + " — Musik";
  $("roomLight").style.backgroundImage = t.cover ? 'url("/cover/' + t.id + '")'
    : "radial-gradient(circle, hsl(" + hue(t.title) + " 80% 45%), transparent 70%)";
  playBtn.disabled = $("prev").disabled = $("next").disabled = false;
  setLike(t);
  $("mini").hidden = false;
  $("miniArt").innerHTML = coverHTML(t);
  $("miniTitle").textContent = t.title;
  $("miniArtist").textContent = t.artist;
  $("dur").textContent = fmt(t.duration) || "0:00";
  $("cur").textContent = "0:00";
  say("");

  if ("mediaSession" in navigator) {
    navigator.mediaSession.metadata = new MediaMetadata({
      title: t.title, artist: t.artist, album: t.album || "Musik",
      artwork: t.cover ? [{ src: "/cover/" + t.id, sizes: "512x512" }] : [{ src: "/icon-512.png", sizes: "512x512", type: "image/png" }]
    });
  }
}

function setLike(t) {
  var like = $("like");
  like.disabled = !t;
  like.setAttribute("aria-pressed", String(!!(t && t.fav)));
  like.setAttribute("aria-label", t && t.fav ? "Unlike" : "Like");
}

function togglePlay() {
  if (qi < 0) {
    if (shown.length) playList(shown, 0);
    return;
  }
  if (audio.paused) {
    var p = audio.play();
    if (p && p.catch) p.catch(function () { say("Tap play again. The browser blocked autoplay.", true); });
  } else {
    audio.pause();
  }
}

function next(auto) {
  if (qi < 0) return;
  if (qi < queue.length - 1) qi++;
  else if (repeat === "all") qi = 0;
  else {
    if (auto) { audio.currentTime = 0; audio.pause(); }
    return;
  }
  load(true);
}

function prev() {
  if (qi < 0) return;
  if (audio.currentTime > 3) { audio.currentTime = 0; return; }
  if (qi > 0) qi--;
  else if (repeat === "all") qi = queue.length - 1;
  else { audio.currentTime = 0; return; }
  load(true);
}

function toggleShuffle() {
  shuffle = !shuffle;
  if (qi >= 0) {
    if (shuffle) shuffleRest();
    else if (orig) { var cur = queue[qi]; queue = orig; orig = null; qi = queue.indexOf(cur); }
  }
  paintModes();
  render();
  save();
}

function cycleRepeat() {
  repeat = { off: "all", all: "one", one: "off" }[repeat];
  paintModes();
  if (view === "queue") render();
  save();
}

function paintModes() {
  var s = $("shuffle"), r = $("repeat");
  s.setAttribute("aria-pressed", String(shuffle));
  s.setAttribute("aria-label", shuffle ? "Shuffle: on" : "Shuffle: off");
  r.setAttribute("aria-pressed", String(repeat !== "off"));
  r.setAttribute("aria-label", "Repeat: " + { off: "off", all: "all songs", one: "this song" }[repeat]);
  r.querySelector("use").setAttribute("href", repeat === "one" ? "#i-repeat-one" : "#i-repeat");
}

function toggleFav(id) {
  var t = byId[id];
  if (!t) return;
  t.fav = !t.fav;
  render();
  if (current() && current().id === id) setLike(t);
  fetch("/api/fav/" + id, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fav: t.fav }) })
    .then(function (r) { if (!r.ok) throw new Error(r.status); })
    .catch(function () {
      t.fav = !t.fav;
      render();
      if (current() && current().id === id) setLike(t);
      say("Couldn’t save that like. Check the connection to the Pi and try again.", true);
    });
}

function paintProgress() {
  var d = audio.duration, p = d && isFinite(d) ? audio.currentTime / d : 0;
  p = Math.max(0, Math.min(1, p));
  ring.style.strokeDashoffset = CIRC * (1 - p);
  seek.style.setProperty("--fill", (p * 100).toFixed(2) + "%");
  $("miniProgress").style.width = (p * 100).toFixed(2) + "%";
}

function setPlayingUI(on) {
  playBtn.classList.toggle("playing", on);
  playBtn.setAttribute("aria-label", on ? "Pause" : "Play");
  var mp = $("miniPlay");
  mp.setAttribute("aria-label", on ? "Pause" : "Play");
  mp.querySelector("use").setAttribute("href", on ? "#i-pause" : "#i-play");
  document.body.classList.toggle("is-playing", on);
  if ("mediaSession" in navigator) navigator.mediaSession.playbackState = on ? "playing" : "paused";
}

// ---------- phone player sheet ----------
function openStage() {
  if (document.body.classList.contains("stage-open")) return;
  document.body.classList.add("stage-open");
  // On the Pi, keep history at one entry so the Close button's window.close() is allowed
  if (!PI) history.pushState({ stage: true }, "");
}
function closeStage(fromHistory) {
  if (!document.body.classList.contains("stage-open")) return;
  document.body.classList.remove("stage-open");
  if (!fromHistory && history.state && history.state.stage) history.back();
}
window.addEventListener("popstate", function () { closeStage(true); });

// ---------- events ----------
listEl.addEventListener("click", function (e) {
  var b = e.target.closest("button");
  if (!b) return;
  var act = b.dataset.act;
  if (act === "play") {
    var i = +b.dataset.i;
    if (b.dataset.ctx === "queue") { qi = i; load(true); }
    else playList(shown, i);
  } else if (act === "fav") toggleFav(b.dataset.id);
  else if (act === "open") { drill = b.dataset.name; listWrap.scrollTop = 0; render(); }
  else if (act === "back") { drill = null; render(); }
  else if (act === "playall") { if (shown.length) playList(shown, 0); }
  else if (act === "reload") fetchLibrary();
  else if (act === "rescan") {
    b.disabled = true;
    fetch("/api/rescan", { method: "POST" }).then(fetchLibrary, fetchLibrary);
  }
});

$("tabs").addEventListener("click", function (e) {
  var b = e.target.closest("button[data-view]");
  if (b) setView(b.dataset.view);
});

$("q").addEventListener("input", function (e) { query = e.target.value; render(); });

playBtn.addEventListener("click", togglePlay);
$("next").addEventListener("click", function () { next(false); });
$("prev").addEventListener("click", prev);
$("shuffle").addEventListener("click", toggleShuffle);
$("repeat").addEventListener("click", cycleRepeat);
$("like").addEventListener("click", function () { if (current()) toggleFav(current().id); });
$("miniPlay").addEventListener("click", togglePlay);
$("miniNext").addEventListener("click", function () { next(false); });
$("miniOpen").addEventListener("click", openStage);
$("closeStage").addEventListener("click", function () { closeStage(false); });

audio.addEventListener("play", function () { setPlayingUI(true); say(""); });
audio.addEventListener("pause", function () { setPlayingUI(false); save(); });
audio.addEventListener("ended", function () {
  if (repeat === "one") { audio.currentTime = 0; audio.play(); }
  else next(true);
});
audio.addEventListener("loadedmetadata", function () {
  $("dur").textContent = fmt(audio.duration) || "0:00";
  seek.max = audio.duration || 100;
  seek.disabled = false;
  paintProgress();
});
audio.addEventListener("waiting", function () { say("Buffering…"); });
audio.addEventListener("playing", function () { say(""); });
var lastSave = 0;
audio.addEventListener("timeupdate", function () {
  if (!dragging) seek.value = audio.currentTime;
  $("cur").textContent = fmt(audio.currentTime) || "0:00";
  paintProgress();
  if (Date.now() - lastSave > 5000) { lastSave = Date.now(); save(); }
  if ("mediaSession" in navigator && audio.duration && isFinite(audio.duration) && navigator.mediaSession.setPositionState) {
    try { navigator.mediaSession.setPositionState({ duration: audio.duration, position: audio.currentTime, playbackRate: 1 }); } catch (e) {}
  }
});
audio.addEventListener("error", function () {
  if (!audio.getAttribute("src")) return;
  say("Couldn’t stream this song. It may have moved; try Rescan, or check that the Pi is on.", true);
  setPlayingUI(false);
});

var dragging = false;
seek.addEventListener("input", function () {
  dragging = true;
  $("cur").textContent = fmt(+seek.value) || "0:00";
  var d = audio.duration, p = d && isFinite(d) ? seek.value / d : 0;
  ring.style.strokeDashoffset = CIRC * (1 - p);
  seek.style.setProperty("--fill", (p * 100).toFixed(2) + "%");
});
seek.addEventListener("change", function () { audio.currentTime = +seek.value; dragging = false; });

if ("mediaSession" in navigator) {
  var ms = navigator.mediaSession;
  ms.setActionHandler("play", function () { audio.play(); });
  ms.setActionHandler("pause", function () { audio.pause(); });
  ms.setActionHandler("previoustrack", prev);
  ms.setActionHandler("nexttrack", function () { next(false); });
  try {
    ms.setActionHandler("seekto", function (d) { audio.currentTime = d.seekTime; });
    ms.setActionHandler("seekbackward", function () { audio.currentTime = Math.max(0, audio.currentTime - 10); });
    ms.setActionHandler("seekforward", function () { audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + 10); });
  } catch (e) { /* older browsers lack these actions */ }
}

document.addEventListener("keydown", function (e) {
  var typing = e.target.tagName === "INPUT" && e.target.type !== "range";
  if (e.key === "Escape") {
    if (typing && query) { e.target.value = ""; query = ""; render(); return; }
    if (typing) { e.target.blur(); return; }
    closeStage(false);
    return;
  }
  if (typing || e.target === seek || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === "/") { e.preventDefault(); $("q").focus(); }
  else if (e.code === "Space") { e.preventDefault(); togglePlay(); }
  else if (e.code === "ArrowRight" && e.shiftKey) next(false);
  else if (e.code === "ArrowLeft" && e.shiftKey) prev();
  else if (e.code === "ArrowRight") audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + 5);
  else if (e.code === "ArrowLeft") audio.currentTime = Math.max(0, audio.currentTime - 5);
});
window.addEventListener("pagehide", save);

// ---------- start ----------
if (PI) {
  $("pager").hidden = $("quit").hidden = false;
  $("pgUp").addEventListener("click", function () { page(-1); });
  $("pgDown").addEventListener("click", function () { page(1); });
  $("quit").addEventListener("click", function () { save(); window.close(); });
  listWrap.addEventListener("scroll", paintPager);
  if (window.ResizeObserver) new ResizeObserver(paintPager).observe(listWrap);
}
stage.classList.add("idle");
$("prev").disabled = $("next").disabled = true;
$("art").classList.add("empty-art");
$("art").innerHTML = '<svg viewBox="0 0 32 32" aria-hidden="true"><path d="M16 4 28 11v10L16 28 4 21V11z" fill="none" stroke="#3a2f26" stroke-width="1.4" stroke-linejoin="round"/><path d="M16 4v10l12-3M16 14 4 11m12 3v14" fill="none" stroke="#3a2f26" stroke-width="1.2" stroke-linejoin="round"/></svg>';
paintModes();
fetchLibrary().then(restore);
