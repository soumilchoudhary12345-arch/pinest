"""Tests for Musik's tag reader, title cleaner and helpers. Run: python3 -m unittest songpage/test_app.py"""
import os
import struct
import sys
import tempfile
import unittest

TMP = tempfile.mkdtemp(prefix="musik-test-")
os.environ["MUSIC_ROOT"] = TMP
os.environ["MUSIK_DB"] = os.path.join(TMP, "library.db")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app  # noqa: E402


def id3_frame(fid, data):
    return fid + struct.pack(">I", len(data)) + b"\x00\x00" + data


def synchsafe(n):
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def fake_mp3(path, frames=1000):
    """ID3v2.3 tag (title, artist, PNG cover) + one MPEG-1 Layer III frame with a Xing header."""
    body = (id3_frame(b"TIT2", b"\x03Mark Forster - Sowieso (Official Video) [jP4-XrbGt3M]")
            + id3_frame(b"TPE1", b"\x01" + "Uploader Channel".encode("utf-16"))
            + id3_frame(b"APIC", b"\x00image/png\x00\x03cover\x00" + b"\x89PNG-bytes"))
    tag = b"ID3\x03\x00\x00" + synchsafe(len(body)) + body
    header = bytes([0xFF, 0xFB, 0x90, 0x00])            # MPEG-1 L3, 128 kbps, 44.1 kHz, stereo
    frame = header + b"\x00" * 32 + b"Xing" + struct.pack(">II", 1, frames) + b"\x00" * 300
    with open(path, "wb") as f:
        f.write(tag + frame)


class Tags(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(TMP, "song.mp3")
        fake_mp3(self.path)

    def test_text_frames(self):
        tags, end = app.read_id3(self.path)
        self.assertEqual(tags["artist"], "Uploader Channel")
        self.assertTrue(tags["title"].startswith("Mark Forster - Sowieso"))
        self.assertTrue(tags["cover"])
        self.assertGreater(end, 10)

    def test_cover_bytes(self):
        mime, img = app.read_id3(self.path, want_cover=True)[0]["cover"]
        self.assertEqual(mime, "image/png")
        self.assertEqual(img, b"\x89PNG-bytes")

    def test_duration_from_xing(self):
        _, end = app.read_id3(self.path)
        self.assertAlmostEqual(app.mp3_duration(self.path, end), 1000 * 1152 / 44100, places=2)

    def test_describe_uses_title_artist_over_uploader(self):
        m = app.describe(self.path)
        self.assertEqual((m["title"], m["artist"]), ("Sowieso", "Mark Forster"))

    def test_no_tag(self):
        p = os.path.join(TMP, "plain.mp3")
        with open(p, "wb") as f:
            f.write(b"\x00" * 64)
        self.assertEqual(app.read_id3(p), ({}, 0))


class CleanTitle(unittest.TestCase):
    def test_cases(self):
        cases = {
            "C418: Aria Math": ("Aria Math", "C418"),
            "AYLIVA, APACHE 207 - Wunder": ("Wunder", "AYLIVA, APACHE 207"),
            "Mark Forster - Sowieso (Official Video)": ("Sowieso", "Mark Forster"),
            "Nina Chuba – Fata Morgana (Official Music Video)": ("Fata Morgana", "Nina Chuba"),
            "KITSCHKRIEG - Gut Genug (Lyrics) Feat. Shirin David": ("Gut Genug Feat. Shirin David", "KITSCHKRIEG"),
            "Act One: The Return": ("Act One: The Return", "Tagged"),
            "Fata Morgana": ("Fata Morgana", "Tagged"),
        }
        for raw, want in cases.items():
            self.assertEqual(app.clean_title(raw, "Tagged"), want, raw)


class ParseRange(unittest.TestCase):
    def test_forms(self):
        self.assertIsNone(app.parse_range(None, 100))
        self.assertEqual(app.parse_range("bytes=0-9", 100), (0, 9))
        self.assertEqual(app.parse_range("bytes=-10", 100), (90, 99))
        self.assertEqual(app.parse_range("bytes=100-", 100), "bad")


class Library(unittest.TestCase):
    def test_scan_and_favourite(self):
        fake_mp3(os.path.join(TMP, "song.mp3"))
        app.init_db()
        self.assertGreaterEqual(app.scan(), 1)
        tid = [t for t in app.library() if t["title"] == "Sowieso"][0]["id"]
        with app.db() as c:
            c.execute("INSERT OR IGNORE INTO favorites VALUES (?, 0)", (tid,))
        self.assertTrue([t for t in app.library() if t["id"] == tid][0]["fav"])
        self.assertTrue(app.track_path(tid).endswith("song.mp3"))
        self.assertIsNone(app.track_path("nope"))


if __name__ == "__main__":
    unittest.main()
