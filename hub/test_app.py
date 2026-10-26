"""Tests for PiHub's core logic. Run: python3 -m unittest pihub/test_app.py"""
import os
import sys
import tempfile
import time
import unittest

NAS = tempfile.mkdtemp(prefix="pihub-test-")
os.environ["PIHUB_NAS"] = NAS
os.environ["PIHUB_DB"] = os.path.join(NAS, "pihub.db")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app  # noqa: E402

app.init_db()


def reset():
    with app.db() as c:
        for t in ("devices", "files", "rounds", "tasks", "events"):
            c.execute("DELETE FROM " + t)


class SyncRounds(unittest.TestCase):
    def setUp(self):
        reset()
        app.touch_device("laptop")
        app.touch_device("win-pc")

    def test_first_round_queues_every_device(self):
        r = app.sync_round()
        self.assertEqual(sorted(t["device"] for t in r["created"]), ["laptop", "win-pc"])

    def test_not_due_until_interval_passes(self):
        app.sync_round()
        self.assertFalse(app.sync_round()["due"])
        with app.db() as c:
            c.execute("UPDATE rounds SET created = ?", (time.time() - 4 * 86400,))
            c.execute("UPDATE tasks SET status = 'done'")
        self.assertEqual(len(app.sync_round()["created"]), 2)

    def test_no_duplicate_while_one_is_pending(self):
        app.sync_round()
        self.assertEqual(app.sync_round(force=True)["created"], [])

    def test_done_updates_last_sync(self):
        tid = app.sync_round()["created"][0]["id"]
        app.update_task(tid, {"status": "done", "files": 3, "bytes": 99})
        dev = [d for d in app.overview()["devices"] if d["last_sync"]]
        self.assertEqual(dev[0]["last_sync_files"], 3)


class Fetch(unittest.TestCase):
    def setUp(self):
        reset()

    def test_online_source_waits_for_upload(self):
        app.touch_device("win-pc")
        t = app.request_file("win-pc", "Desktop/a.txt", "laptop", "~/Downloads")
        self.assertEqual((t["status"], t["source"]), ("upload_wait", "live"))
        self.assertEqual([x["id"] for x in app.tasks_for("win-pc")], [t["id"]])
        self.assertEqual(app.tasks_for("laptop"), [])

    def test_offline_source_uses_synced_copy(self):
        d = os.path.join(NAS, "devices", "win-pc", "Desktop")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "b.txt"), "w").write("x")
        t = app.request_file("win-pc", "Desktop/b.txt", "laptop", "~/Downloads")
        self.assertEqual((t["status"], t["source"]), ("ready", "stored"))
        self.assertEqual([x["id"] for x in app.tasks_for("laptop")], [t["id"]])

    def test_offline_without_copy_is_an_error(self):
        with self.assertRaises(app.ClientError):
            app.request_file("ghost-pc", "Desktop/c.txt", "laptop", "~/Downloads")

    def test_stored_path_blocks_escape(self):
        for dev, path in (("win-pc", "../../etc/passwd"), ("../x", "a"), ("", "a")):
            with self.assertRaises(app.ClientError):
                app.stored_path(dev, path)


class Inventory(unittest.TestCase):
    def test_index_and_search(self):
        reset()
        d = os.path.join(NAS, "devices", "laptop", "Documents")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "report_2026.pdf"), "w").write("pdf")
        app.index_once()
        hits = app.search("report_")
        self.assertEqual(hits[0]["path"], os.path.join("Documents", "report_2026.pdf"))
        self.assertEqual(app.search("report", device="nobody"), [])


class Agents(unittest.TestCase):
    def test_states(self):
        reset()
        app.add_event({"device": "laptop", "agent": "claude-code", "session": "s1", "event": "PreToolUse", "tool": "Bash"})
        app.add_event({"device": "laptop", "agent": "opencode", "session": "s2", "event": "Notification"})
        states = {s["agent"]: s["state"] for s in app.agent_sessions()}
        self.assertEqual(states, {"claude-code": "running", "opencode": "waiting"})


if __name__ == "__main__":
    unittest.main()


class Join(unittest.TestCase):
    def setUp(self):
        reset()

    def reg(self, user, os_, m):
        return app.register_device({"user": user, "os": os_, "machine": m * 32})["device"]

    def test_name_is_user_and_os(self):
        self.assertEqual(self.reg("Soumil", "win", "a"), "soumil_win")
        self.assertEqual(self.reg("soumil", "linux", "b"), "soumil_linux")

    def test_number_only_for_a_duplicate(self):
        self.assertEqual([self.reg("soumil", "win", m) for m in "abc"], ["soumil_win", "soumil_win_2", "soumil_win_3"])
        self.assertEqual(self.reg("soumil", "linux", "d"), "soumil_linux")

    def test_os_is_required(self):
        with self.assertRaises(app.ClientError):
            app.register_device({"user": "soumil", "os": "mac", "machine": "a" * 32})

    def test_same_machine_keeps_its_name(self):
        first = app.register_device({"user": "soumil", "os": "win", "machine": "a" * 32})
        again = app.register_device({"user": "someone-else", "os": "win", "machine": "A" * 32})
        self.assertEqual(again, {"device": first["device"], "new": False})

    def test_odd_user_names_are_cleaned(self):
        self.assertEqual(self.reg("Soumil Choudhary!", "win", "d"), "soumil-choudhary_win")
        self.assertEqual(self.reg("日本", "linux", "e"), "pc_linux")

    def test_existing_device_folder_is_never_reused(self):
        os.makedirs(os.path.join(app.DEVICES_DIR, "ravi_linux"), exist_ok=True)
        self.assertEqual(self.reg("ravi", "linux", "f"), "ravi_linux_2")

    def test_bad_machine_id_is_refused(self):
        with self.assertRaises(app.ClientError):
            app.register_device({"user": "soumil", "os": "win", "machine": "../../etc"})

    def test_pair_refuses_bad_input_before_touching_syncthing(self):
        good_id = "-".join(["ABCDEFG"] * 8)
        for body in ({"device": "../x", "pc_id": good_id, "folders": []},
                     {"device": "soumil", "pc_id": "nope", "folders": []},
                     {"device": "soumil", "pc_id": good_id, "folders": ["/etc"]},
                     {"device": "soumil", "pc_id": good_id, "folders": [".ssh"]}):
            with self.assertRaises(app.ClientError):
                app.pair_device(body)

    def test_join_script_placeholders_exist(self):
        for key, names in app.JOIN_SCRIPTS.items():
            text = open(app.find(*names), encoding="utf-8").read()
            if key.endswith(".cmd"):  # runs straight from a clone: real default address, no token
                self.assertIn("http://192.168.1.13:8899", text)
                self.assertNotIn("__PIHUB_TOKEN__", text)
            else:
                self.assertIn("__PIHUB_URL__", text)
                self.assertIn("__PIHUB_TOKEN__", text)
        for names in app.JOIN_FILES.values():
            self.assertTrue(os.path.isfile(app.find(*names)), names)


class Describe(unittest.TestCase):
    def test_tools_read_as_plain_sentences(self):
        self.assertEqual(app.describe("PreToolUse", "Edit", "app.py", "running"), "Editing app.py")
        self.assertEqual(app.describe("PreToolUse", "Bash", "Run the tests", "running"), "Run the tests")
        self.assertEqual(app.describe("PreToolUse", "WebFetch", "https://syncthing.net/docs", "running"), "Reading syncthing.net")
        self.assertEqual(app.describe("PreToolUse", "mcp__pihub__search_files", "", "running"), "Using pihub: search files")

    def test_waiting_and_done(self):
        self.assertEqual(app.describe("Notification", None, "Claude needs your permission", "waiting"), "Claude needs your permission")
        self.assertEqual(app.describe("Stop", None, None, "idle", ("Write", "pi.js")), "Done. Last step: writing pi.js")
        self.assertEqual(app.describe("Stop", None, None, "idle"), "Done. Waiting for your next message.")

    def test_quiet_agent_says_its_last_step(self):
        self.assertEqual(app.describe("PreToolUse", "Read", "README.md", "idle"), "Quiet for a while. Last step: reading README.md")

    def test_sessions_carry_a_description(self):
        reset()
        for ev, tool, summ in (("SessionStart", None, None), ("PreToolUse", "Edit", "app.py"), ("Stop", None, None)):
            app.add_event({"device": "soumil_linux", "agent": "claude-code", "session": "s1", "event": ev, "tool": tool, "summary": summ})
        s = app.agent_sessions()[0]
        self.assertEqual((s["agent_name"], s["doing"]), ("Claude Code", "Done. Last step: editing app.py"))
