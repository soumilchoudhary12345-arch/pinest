"""Tests for picloud's path and request helpers. Run: python3 -m unittest picloud/test_app.py"""
import os
import sys
import tempfile
import unittest

ROOT = tempfile.mkdtemp(prefix="picloud-test-")
os.environ["CLOUD_ROOT"] = ROOT
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app  # noqa: E402


class SafePath(unittest.TestCase):
    def test_inside_root(self):
        self.assertEqual(app.safe_path("a/b.txt"), os.path.join(app.ROOT, "a", "b.txt"))
        self.assertEqual(app.safe_path(""), app.ROOT)

    def test_escapes_are_refused(self):
        for bad in ("../x", "a/../../x", "..\\..\\etc"):
            with self.assertRaises(app.ClientError):
                app.safe_path(bad)

    def test_symlink_escape_is_refused(self):
        link = os.path.join(app.ROOT, "out")
        os.symlink("/etc", link)
        try:
            with self.assertRaises(app.ClientError):
                app.safe_path("out/passwd")
        finally:
            os.remove(link)


class CheckName(unittest.TestCase):
    def test_good(self):
        self.assertEqual(app.check_name("  notes.txt "), "notes.txt")

    def test_bad(self):
        for bad in ("", ".", "..", "a/b", "a\\b", "x.part"):
            with self.assertRaises(app.ClientError):
                app.check_name(bad)


class ParseRange(unittest.TestCase):
    def test_forms(self):
        self.assertIsNone(app.parse_range(None, 100))
        self.assertEqual(app.parse_range("bytes=0-9", 100), (0, 9))
        self.assertEqual(app.parse_range("bytes=90-", 100), (90, 99))
        self.assertEqual(app.parse_range("bytes=-10", 100), (90, 99))
        self.assertEqual(app.parse_range("bytes=50-500", 100), (50, 99))

    def test_unsatisfiable(self):
        self.assertEqual(app.parse_range("bytes=100-", 100), "bad")
        self.assertEqual(app.parse_range("bytes=9-3", 100), "bad")

    def test_garbage_is_ignored(self):
        self.assertIsNone(app.parse_range("bytes=x-y", 100))
        self.assertIsNone(app.parse_range("items=0-1", 100))


if __name__ == "__main__":
    unittest.main()
