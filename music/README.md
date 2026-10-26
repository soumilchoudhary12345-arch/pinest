# Musik: the house music player

Single-file stdlib Python server (`app.py`, port 8080) plus a static front end. Scans `MUSIC_ROOT`
(on the Pi: `/srv/nas/music`), reads tags and covers, and streams with real **HTTP Range** support
(`206 Partial Content`), so playback starts instantly and seeking works without downloading the file.
Likes and the queue sync across devices; the Media Session API gives lock-screen controls; the web
manifest makes it installable on phones. `?pi=1` switches to a tap-only layout for the Pi's 3.5″ screen.

Run: `MUSIC_ROOT=~/Music python3 app.py` then open http://localhost:8080 · Deploy: `musik.service` (systemd), `musik.desktop` (Pi launcher).
