#!/usr/bin/env python3
"""Build the PiNest overview deck (8 slides, 16:9) with python-pptx.

    pip install python-pptx
    python3 docs/make_presentation.py [output.pptx]

Palette: dark blue #0F172A (background), teal #0EA5E9 (accent).
Every slide has speaker notes. No title slide: it opens on the pitch.
"""
import sys

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

OUT = sys.argv[1] if len(sys.argv) > 1 else "/home/soumil/Downloads/PiNest - Homelab on a Raspberry Pi.pptx"

NAVY = RGBColor(0x0F, 0x17, 0x2A)      # primary / background
TEAL = RGBColor(0x0E, 0xA5, 0xE9)      # accent
PANEL = RGBColor(0x1E, 0x29, 0x3B)     # raised surface
LINE = RGBColor(0x33, 0x41, 0x55)      # hairlines
WHITE = RGBColor(0xF8, 0xFA, 0xFC)
BODY = RGBColor(0xCB, 0xD5, 0xE1)      # body text on navy
MUTED = RGBColor(0x94, 0xA3, 0xB8)
FONT = "Calibri"
MONO = "Consolas"

prs = Presentation()
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
BLANK = prs.slide_layouts[6]
W, H = prs.slide_width, prs.slide_height
MARGIN = Inches(0.7)


# ---------- building blocks ----------

def new_slide(title, notes, number):
    s = prs.slides.add_slide(BLANK)
    s.background.fill.solid()
    s.background.fill.fore_color.rgb = NAVY
    if title:
        tb = s.shapes.add_textbox(MARGIN, Inches(0.45), W - 2 * MARGIN, Inches(0.9))
        tf = tb.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        run(p, title, 32, WHITE, bold=True)
        bar = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, MARGIN, Inches(1.32), Inches(1.1), Inches(0.07))
        fill(bar, TEAL)
    # footer: project name + slide number
    ft = s.shapes.add_textbox(MARGIN, H - Inches(0.5), W - 2 * MARGIN, Inches(0.3))
    p = ft.text_frame.paragraphs[0]
    run(p, "PiNest", 11, TEAL, bold=True)
    run(p, "   ·   Raspberry Pi 5 homelab   ·   %d / 8" % number, 11, MUTED)
    s.notes_slide.notes_text_frame.text = notes
    return s


def run(p, text, size, color, bold=False, font=FONT):
    r = p.add_run()
    r.text = text
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.color.rgb = color
    r.font.name = font
    return r


def fill(shape, color, line=None):
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    if line is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = line
        shape.line.width = Pt(1.25)
    shape.shadow.inherit = False
    return shape


def text_box(s, x, y, w, h, anchor=MSO_ANCHOR.TOP):
    tb = s.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    for side in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
        setattr(tf, side, Inches(0.05))
    return tf


def lead_list(tf, items, size=17, gap=10, lead_color=TEAL):
    """Bullets with a bold lead-in: [("Lead-in", "rest of the sentence"), ...]."""
    for i, (lead, rest) in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(gap)
        run(p, lead + "  ", size, lead_color, bold=True)
        run(p, rest, size, BODY)


def card(s, x, y, w, h, title, lines, title_color=WHITE, border=None, size=14):
    box = fill(s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h), PANEL, border)
    box.adjustments[0] = 0.06
    tf = text_box(s, x + Inches(0.22), y + Inches(0.16), w - Inches(0.44), h - Inches(0.3))
    p = tf.paragraphs[0]
    run(p, title, size + 3, title_color, bold=True)
    p.space_after = Pt(6)
    for ln in lines:
        p = tf.add_paragraph()
        p.space_after = Pt(3)
        if isinstance(ln, tuple):
            run(p, ln[0] + "  ", size, TEAL, bold=True)
            run(p, ln[1], size, BODY)
        else:
            run(p, ln, size, BODY)
    return box


def arrow(s, x1, y1, x2, y2, label=None):
    c = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, x1, y1, x2, y2)
    c.line.color.rgb = TEAL
    c.line.width = Pt(2)
    ln = c.line._get_or_add_ln()
    tail = ln.makeelement("{http://schemas.openxmlformats.org/drawingml/2006/main}tailEnd", {"type": "triangle"})
    ln.append(tail)
    if label:
        tf = text_box(s, min(x1, x2) - Inches(0.2), min(y1, y2) - Inches(0.62), abs(x2 - x1) + Inches(0.4), Inches(0.55),
                      MSO_ANCHOR.BOTTOM)
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        run(p, label, 12, MUTED)


def chip(s, x, y, text, w=None):
    w = w or Inches(0.13 * len(text) + 0.4)
    c = fill(s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, Inches(0.42)), NAVY, TEAL)
    c.adjustments[0] = 0.5
    tf = c.text_frame
    tf.margin_top = tf.margin_bottom = 0
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run(p, text, 13, WHITE, bold=True)
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    return w


# ---------- 1. The pitch (no title slide) ----------

s = new_slide(None, """Open straight on the pitch, no title slide.
PiNest turns a Raspberry Pi 5 into the hub of the house: every laptop's Documents, Pictures and Videos are mirrored to an SSD on the Pi, music streams from it, files are browsable from any device, and every coding agent on every laptop reports what it's doing to a small touchscreen on the Pi.
Key point: it is all LAN-only, uses no cloud service, and the server side is plain Python standard library, so there's nothing to install on the Pi beyond Syncthing.
The numbers on the right are real measurements from this build.""", 1)
tf = text_box(s, MARGIN, Inches(0.9), Inches(7.6), Inches(3.2))
p = tf.paragraphs[0]
run(p, "Every laptop at home, mirrored to a Raspberry Pi.", 34, WHITE, bold=True)
p = tf.add_paragraph()
p.space_before = Pt(14)
run(p, "And every AI coding agent on them, live on a 3.5″ touchscreen.", 34, TEAL, bold=True)

tf = text_box(s, MARGIN, Inches(4.5), Inches(7.4), Inches(2.3))
lead_list(tf, [
    ("Self-hosted NAS.", "Syncthing mirrors each PC onto a 256 GB USB SSD on the Pi."),
    ("One hub, three apps.", "PiHub (coordinator), Musik (music streaming), picloud (file cloud)."),
    ("Zero cloud, zero frameworks.", "LAN-only, token-authenticated, stdlib Python on the server."),
], size=17)

stats = [("7", "coding agents monitored"), ("< 1 s", "for a new PC to appear on the Pi"),
         ("1 click", "to join a laptop (Linux or Windows)"), ("0 files", "re-sent after renaming a device")]
x0, y0 = Inches(8.75), Inches(0.95)
for i, (big, small) in enumerate(stats):
    y = y0 + i * Inches(1.45)
    box = fill(s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x0, y, Inches(3.9), Inches(1.25)), PANEL)
    box.adjustments[0] = 0.08
    t = text_box(s, x0 + Inches(0.25), y + Inches(0.12), Inches(3.5), Inches(1.0), MSO_ANCHOR.MIDDLE)
    p = t.paragraphs[0]
    run(p, big, 30, TEAL, bold=True)
    p = t.add_paragraph()
    run(p, small, 14, BODY)

# ---------- 2. Architecture ----------

s = new_slide("Architecture: pull-based hub-and-spoke on one LAN", """Walk left to right.
Each laptop runs three things: Syncthing in Send Only mode, pihub-client (a small daemon that polls the Pi for work every 10 seconds), and hooks inside every coding agent.
Everything flows to the Pi over the home LAN. Two protocols: Syncthing's Block Exchange Protocol on port 22000 for files, and a token-authenticated REST API on port 8899 for control.
Nothing is ever pushed to a laptop. The Pi queues tasks; laptops poll and pick them up. That makes it work behind any laptop firewall, and a laptop that was off simply catches up when it comes back.
On the Pi: three systemd services, a USB SSD mounted at /srv/nas, and the touchscreen running Chromium in kiosk mode.""", 2)
cy = Inches(1.85)
card(s, MARGIN, cy, Inches(3.6), Inches(4.4), "Laptops  (Linux / Windows)", [
    ("Syncthing", "Send Only folders"),
    ("pihub-client", "polls /api/tasks every 10 s"),
    ("Agent hooks", "Claude Code, Codex, Copilot CLI, Crush, OpenCode, Gemini, Antigravity"),
    ("MCP server", "agents can search & fetch files"),
], border=LINE)
px = Inches(7.15)
card(s, px, cy, Inches(5.45), Inches(3.05), "Raspberry Pi 5  ·  Debian 13  ·  192.168.1.13", [
    ("PiHub :8899", "REST API, SQLite (WAL), agent monitor"),
    ("Musik :8080", "HTTP Range streaming"),
    ("picloud :80", "web file cloud"),
    ("Syncthing", "Receive Only + Staggered versioning"),
    ("/srv/nas", "256 GB USB SSD, ext4, fstab nofail"),
], title_color=TEAL, border=TEAL)
card(s, px, cy + Inches(3.25), Inches(5.45), Inches(1.15), "3.5″ SPI touchscreen  ·  480×320", [
    "Chromium --kiosk: Musik, picloud and Agents apps, tap-only",
], border=LINE)
ax1, ax2 = MARGIN + Inches(3.6) + Inches(0.15), px - Inches(0.15)
arrow(s, ax1, cy + Inches(1.2), ax2, cy + Inches(1.2), "Syncthing BEP  :22000  (files)")
arrow(s, ax1, cy + Inches(2.45), ax2, cy + Inches(2.45), "REST + X-Token  :8899  (tasks, events)")
arrow(s, px + Inches(2.7), cy + Inches(3.05), px + Inches(2.7), cy + Inches(3.25))

# ---------- 3. Storage and sync ----------

s = new_slide("Storage & sync: one-way, scheduled, versioned", """This is the NAS part.
Each PC shares its folders as Send Only and the Pi receives them as Receive Only, so the Pi can never change a laptop's files.
Staggered versioning keeps old copies for 30 days: if a file is deleted on a laptop, the Pi still has it.
Syncing isn't continuous. Every 4 days PiHub queues a sync task; the laptop's client resumes Syncthing, waits for 100 percent, reports the result and pauses again. If the Pi was down, a systemd timer on the laptop with Persistent=true runs the sync anyway.
For search, PiHub indexes the mirrored files into SQLite. For the live screen it reads byte counts straight from Syncthing's REST API, which is why a new device and its upload show up within seconds.""", 3)
tf = text_box(s, MARGIN, Inches(1.8), Inches(7.3), Inches(4.8))
lead_list(tf, [
    ("Send Only → Receive Only.", "Folders flow PC → Pi; the Pi never writes back to a laptop."),
    ("Staggered versioning.", "Deleted or overwritten files stay recoverable on the Pi for 30 days."),
    ("4-day sync rounds.", "A systemd timer queues a sync task; clients resume Syncthing, wait for 100 %, then pause."),
    ("Offline-safe fallback.", "Laptop-side timer with Persistent=true runs a missed round on next boot."),
    ("Searchable inventory.", "Mirrored files indexed into SQLite (WAL mode) for instant search."),
    ("Live storage.", "Bytes on the Pi read from Syncthing's /rest/db/status, cached 2 s."),
], size=17, gap=11)
card(s, Inches(8.4), Inches(1.85), Inches(4.25), Inches(4.4), "On disk", [
    ("/srv/nas/devices/", "<pc>/Documents, Pictures, Videos"),
    ("/srv/nas/music/", "shared music library"),
    ("/srv/nas/inbox/", "files in transit between PCs"),
    ("/srv/nas/pihub.db", "devices, files, tasks, agent events"),
    ("Mount", "UUID in fstab with nofail, so a missing SSD never blocks boot"),
], border=LINE, size=13)

# ---------- 4. Joining a PC ----------

s = new_slide("Joining a PC: one file, two operating systems", """The onboarding story. You clone the repo, open the dependencies folder, and run join-pi.cmd.
That file is a polyglot: Windows reads it as a batch file, Linux reads it as a bash script. Windows skips the lines that start with a colon because it treats them as labels; bash runs them and exits before reaching the Windows part.
It downloads the real installer from the Pi, which arrives with the Pi's address and token filled in.
The Pi names the PC after the user and the OS, for example soumil_linux or soumil_win, and only adds a number for a real duplicate. The name is tied to the machine ID, so running the script again keeps the same name.
Pairing used to need an SSH login to the Pi; now it's a token-protected API call, so there's no password prompt at all.""", 4)
steps = [("Run", "join-pi.cmd", "batch / bash polyglot"),
         ("Name", "/api/register", "user_os, keyed by machine ID"),
         ("Install", "Python + Syncthing", "pacman · apt · dnf · winget"),
         ("Pair", "/api/pair", "Syncthing REST, no SSH"),
         ("Connect", "connect_agents.py", "hooks + Pi Agents app")]
sw, gap = Inches(2.24), Inches(0.18)
for i, (verb, what, how) in enumerate(steps):
    x = MARGIN + i * (sw + gap)
    shp = fill(s.shapes.add_shape(MSO_SHAPE.CHEVRON if i else MSO_SHAPE.PENTAGON, x, Inches(1.95), sw, Inches(1.0)),
               TEAL if i == 0 else PANEL)
    t = shp.text_frame
    t.word_wrap = False
    p = t.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run(p, "%d  %s" % (i + 1, verb), 15, NAVY if i == 0 else WHITE, bold=True)
    tf = text_box(s, x, Inches(3.1), sw, Inches(1.0))
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run(p, what, 14, WHITE, bold=True, font=MONO)
    p = tf.add_paragraph()
    p.alignment = PP_ALIGN.CENTER
    run(p, how, 13, MUTED)
tf = text_box(s, MARGIN, Inches(4.45), Inches(7.2), Inches(2.3))
lead_list(tf, [
    ("Idempotent.", "Re-running keeps the config, the name and the pairing; it only adds what's missing."),
    ("Least privilege.", "The Pi decides every storage path; a PC can't point a share elsewhere."),
    ("Starts at login.", "systemd --user units on Linux, Task Scheduler on Windows."),
], size=16, gap=9)
card(s, Inches(8.4), Inches(4.4), Inches(4.25), Inches(2.0), "Device naming", [
    ("soumil_linux", "first Linux PC of user soumil"),
    ("soumil_win", "first Windows PC"),
    ("soumil_win_2", "only when the name is taken"),
], border=LINE, size=13)

# ---------- 5. Agent monitor ----------

s = new_slide("Agent monitor: seven agents, one event schema", """Every coding agent on a laptop reports to the Pi.
Each agent has its own extension point: Claude Code, Codex, Copilot CLI, Crush and Gemini use lifecycle hooks configured in JSON; OpenCode uses a JavaScript plugin. Gemini CLI and Antigravity share one settings file, so the reporter looks at the parent process to tell them apart.
All of them call the same small script, report_event.py. It normalises event names, field names and tool names into one schema, strips secrets like tokens and passwords with regular expressions, and posts a few hundred bytes to the Pi with a one-second timeout. It always exits zero and prints nothing, so it can never slow down or break an agent.
On the Pi, a describe function turns the raw event into a sentence such as 'Editing app.py' or 'Needs you: permission to run Bash'.""", 5)
rows = [("Agent", "Integration", "Signals"),
        ("Claude Code", "hooks · settings.json", "SessionStart · PreToolUse · Notification · Stop"),
        ("Codex", "hooks.json (trusted via /hooks)", "PreToolUse · PermissionRequest · Stop"),
        ("Copilot CLI", "~/.copilot/hooks/*.json", "preToolUse · permissionRequest · agentStop"),
        ("Crush", "crush.json hooks", "PreToolUse"),
        ("OpenCode", "JS plugin (Bun)", "tool.execute.before · session.idle"),
        ("Gemini / Antigravity", "~/.gemini/settings.json", "BeforeTool · Notification · AfterAgent")]
tbl = s.shapes.add_table(len(rows), 3, MARGIN, Inches(1.85), Inches(7.6), Inches(4.2)).table
for c, wd in enumerate((Inches(2.1), Inches(2.6), Inches(2.9))):
    tbl.columns[c].width = wd
for r, row in enumerate(rows):
    for c, val in enumerate(row):
        cell = tbl.cell(r, c)
        cell.fill.solid()
        cell.fill.fore_color.rgb = TEAL if r == 0 else (PANEL if r % 2 else NAVY)
        cell.margin_left = cell.margin_right = Inches(0.1)
        p = cell.text_frame.paragraphs[0]
        run(p, val, 13 if r else 14, NAVY if r == 0 else (WHITE if c == 0 else BODY), bold=(r == 0 or c == 0))
flow = [("Hook / plugin", "agent event"), ("report_event.py", "normalise · redact · 1 s timeout"),
        ("POST /api/agent-events", "SQLite events table"), ("describe()", "“Editing app.py”")]
fx, fy = Inches(8.75), Inches(1.85)
for i, (a, b) in enumerate(flow):
    y = fy + i * Inches(1.08)
    box = fill(s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, fx, y, Inches(3.9), Inches(0.82)),
               PANEL, TEAL if i == len(flow) - 1 else LINE)
    box.adjustments[0] = 0.12
    t = text_box(s, fx + Inches(0.2), y + Inches(0.06), Inches(3.5), Inches(0.72), MSO_ANCHOR.MIDDLE)
    p = t.paragraphs[0]
    run(p, a, 15, WHITE, bold=True, font=MONO if i else FONT)
    p = t.add_paragraph()
    run(p, b, 12, MUTED)
    if i:
        arrow(s, fx + Inches(1.95), y - Inches(0.26), fx + Inches(1.95), y)

# ---------- 6. Musik and picloud ----------

s = new_slide("Musik & picloud: stdlib Python, real HTTP semantics", """Two self-contained web apps on the Pi, both built on Python's http.server with no third-party packages.
Musik streams music progressively. It implements HTTP Range requests and answers with 206 Partial Content, which is what lets the browser start playing immediately and seek to any point without downloading the whole file. It also integrates with the Media Session API, so lock-screen and keyboard media keys work.
picloud is a file cloud: browse, upload with progress, rename, delete, and download, with a path-traversal guard so nobody can escape the storage root with ../ tricks.
Both run as systemd services, and both have a tap-only Pi mode for the touchscreen.""", 6)
card(s, MARGIN, Inches(1.85), Inches(5.85), Inches(4.4), "Musik  ·  :8080", [
    ("HTTP Range / 206.", "Progressive streaming and instant seeking."),
    ("Library scan.", "Tags, covers, artists and albums from /srv/nas/music."),
    ("Likes & queue.", "Shared across devices via a small JSON API."),
    ("Media Session API.", "Lock-screen and media-key controls."),
    ("PWA manifest.", "Installable on phones like a native app."),
], title_color=TEAL, border=LINE, size=15)
card(s, MARGIN + Inches(6.15), Inches(1.85), Inches(5.85), Inches(4.4), "picloud  ·  :80", [
    ("Browse & download.", "Range-capable downloads, inline preview."),
    ("Upload with progress.", "XMLHttpRequest progress events, folder uploads."),
    ("Path-traversal guard.", "Every path resolved and checked against the root."),
    ("Disk usage.", "Free / total space from the SSD, live."),
    ("Zero dependencies.", "One app.py, one systemd unit."),
], title_color=TEAL, border=LINE, size=15)

# ---------- 7. Touch-first UI on the Pi ----------

s = new_slide("Touch-first UI: designed for a 480×320 resistive screen", """The Pi's screen is tiny and resistive: a tap is the only input. No keyboard, no hover, and drag-to-scroll doesn't work well.
So each app has a Pi mode, switched on by ?pi=1 in the URL from the desktop launchers, while laptops and phones keep the normal UI.
Every target is at least 48 pixels, scrolling is replaced by page buttons, and anything that needs typing is hidden.
Two gotchas worth mentioning. Kiosk windows have no title bar, so each app has its own close button; window.close only works while the window has one history entry, so navigation uses location.replace. And the 'frozen screen' bug turned out to be X11 DPMS blanking after 10 minutes, which an SPI display can't show, so it just froze on the last frame. Disabling DPMS fixed it.""", 7)
tf = text_box(s, MARGIN, Inches(1.8), Inches(7.4), Inches(4.9))
lead_list(tf, [
    ("Kiosk mode.", "chromium --kiosk --app, launched from desktop icons; no browser chrome."),
    ("Tap-only design.", "48 px+ targets, page buttons instead of drag-scrolling, no typing."),
    ("Progressive enhancement.", "?pi=1 switches the layout; laptops and phones are untouched."),
    ("Live polling.", "Agents screen refreshes every 2 s; a new PC appears in under a second."),
    ("History hygiene.", "location.replace keeps one history entry so window.close() is allowed."),
    ("DPMS fix.", "X11 screen blanking froze the SPI panel; disabled with xset -dpms at login."),
], size=16, gap=10)
# a phone-sized mock of the Agents screen
mx, my, mw, mh = Inches(8.6), Inches(1.95), Inches(4.0), Inches(2.67)
fill(s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, mx - Inches(0.15), my - Inches(0.15), mw + Inches(0.3), mh + Inches(0.3)),
     RGBColor(0x02, 0x06, 0x17), LINE).adjustments[0] = 0.05
fill(s.shapes.add_shape(MSO_SHAPE.RECTANGLE, mx, my, mw, mh), RGBColor(0x08, 0x07, 0x0A))
t = text_box(s, mx + Inches(0.12), my + Inches(0.08), mw - Inches(0.25), Inches(0.4))
run(t.paragraphs[0], "Agents", 15, WHITE, bold=True)
for i, (name, line1, line2, col) in enumerate([
        ("soumil_linux", "1 agent working", "107 MB on the Pi", RGBColor(0xFF, 0x9F, 0x0A)),
        ("soumil_win", "1 agent needs you", "Uploading · 48 of 107 MB", RGBColor(0xFF, 0x5A, 0x3C))]):
    y = my + Inches(0.6) + i * Inches(0.98)
    fill(s.shapes.add_shape(MSO_SHAPE.RECTANGLE, mx, y - Inches(0.04), mw, Emu(9525)), RGBColor(0x24, 0x1D, 0x18))
    t = text_box(s, mx + Inches(0.15), y, mw - Inches(0.3), Inches(0.92))
    run(t.paragraphs[0], name, 13, WHITE, bold=True)
    run(t.add_paragraph(), line1, 11, col)
    run(t.add_paragraph(), line2, 10, RGBColor(0x8E, 0x81, 0x76))
t = text_box(s, mx - Inches(0.15), my + mh + Inches(0.3), mw + Inches(0.3), Inches(0.5))
p = t.paragraphs[0]
p.alignment = PP_ALIGN.CENTER
run(p, "Agents app on the Pi (simplified)", 12, MUTED)

# ---------- 8. Security, operations, next ----------

s = new_slide("Security, operations & what's next", """Close on how it's kept safe and what comes next.
Security is layered: the firewall only accepts the home subnet, every API call needs a token compared in constant time, SSH is key-only, secrets are redacted on the laptop before any agent event leaves it, and a sensitive-file deny list stops the file-fetch feature from ever touching keys or password stores.
Operationally everything is systemd: services restart on failure, and before every deploy the old code is archived to a backup folder on the Pi. There are 37 unit tests covering naming, pairing, sync rounds, event normalisation and redaction.
Next steps: roll out the Windows PC, make the Pi portable to any network using DHCP and mDNS instead of a fixed IP, and add secure remote access with HTTPS or a WireGuard-based tunnel such as Tailscale.""", 8)
card(s, MARGIN, Inches(1.85), Inches(3.75), Inches(3.5), "Security", [
    ("ufw", "LAN-only: 192.168.1.0/24"),
    ("X-Token", "hmac.compare_digest"),
    ("SSH", "key-based auth only"),
    ("Redaction", "tokens & passwords masked before upload"),
    ("Deny list", ".ssh, .gnupg, .env, key files"),
], title_color=TEAL, border=LINE, size=15)
card(s, MARGIN + Inches(4.0), Inches(1.85), Inches(3.75), Inches(3.5), "Operations", [
    ("systemd", "Restart=on-failure, timers"),
    ("Backups", "code archived before each deploy"),
    ("Tests", "37 unit tests, stdlib unittest"),
    ("Logs", "journalctl -u pihub"),
    ("Idempotent setup", "every installer is safe to rerun"),
], title_color=TEAL, border=LINE, size=15)
card(s, MARGIN + Inches(8.0), Inches(1.85), Inches(4.2), Inches(3.5), "Next", [
    ("Windows rollout.", "join-pi.cmd on the second PC."),
    ("Network-portable.", "DHCP + mDNS (piscreen.local) instead of a fixed IP."),
    ("Remote access.", "HTTPS or a WireGuard tunnel (Tailscale)."),
    ("More agents.", "connect_agents.py picks up new ones automatically."),
], title_color=WHITE, border=TEAL, size=15)

prs.save(OUT)
print("saved", OUT)
