import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import OfflineTestCase, REPO
from arcturion_web_monitor import config, notify
from arcturion_web_monitor.notify import NotifierConfigError, build_notifier, register_notifier
from arcturion_web_monitor.state import StateStore, diff_state


class Config(OfflineTestCase):
    def test_sample_register_is_valid_and_uses_placeholder_hosts_only(self):
        reg = config.load_register(REPO / "examples" / "surfaces.json")
        self.assertGreaterEqual(len(reg["surfaces"]), 3)
        text = (REPO / "examples" / "surfaces.json").read_text()
        for host in ("example.com", "example.org", "example.net"):
            self.assertIn(host, text)
        import re
        hosts = set(re.findall(r"https?://([^/\"]+)", text))
        for h in hosts:
            self.assertRegex(h, r"(^|\.)example\.(com|org|net)$")

    def test_rejects_non_register(self):
        with self.assertRaises(config.ConfigError):
            config.validate([])
        with self.assertRaises(config.ConfigError):
            config.validate({"surfaces": []})

    def test_rejects_missing_fields_bad_urls_duplicates_regex(self):
        bad = [
            {"surfaces": [{"url": "https://example.com"}]},
            {"surfaces": [{"name": "a", "url": "ftp://example.com"}]},
            {"surfaces": [{"name": "a", "url": "https://example.com"}, {"name": "a", "url": "https://example.org"}]},
            {"surfaces": [{"name": "a", "url": "https://example.com", "must_not_match": "("}]},
            {"surfaces": [{"name": "a", "url": "https://example.com", "aliases": [{"url": "nope"}]}]},
            {"surfaces": [{"name": "a", "url": "https://example.com", "page_checks": []}]},
        ]
        for reg in bad:
            with self.subTest(reg=reg), self.assertRaises(config.ConfigError):
                config.validate(reg)

    def test_load_errors(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(config.ConfigError):
                config.load_register(os.path.join(d, "missing.json"))
            p = os.path.join(d, "bad.json")
            Path(p).write_text("{nope")
            with self.assertRaises(config.ConfigError):
                config.load_register(p)


class State(OfflineTestCase):
    def results(self, ok_a=True, ok_b=True):
        return [{"url": "https://example.com", "name": "A", "ok": ok_a, "detail": "" if ok_a else "HTTP 500", "status": 200},
                {"url": "https://example.org", "name": "B", "ok": ok_b, "detail": "", "status": 200}]

    def test_save_load_roundtrip_and_atomic(self):
        with tempfile.TemporaryDirectory() as d:
            s = StateStore(os.path.join(d, "nested"))
            self.assertEqual(s.load(), {})
            s.save(self.results())
            self.assertEqual(set(s.load()), {"https://example.com", "https://example.org"})
            self.assertFalse(os.path.exists(s.state_file + ".tmp"))

    def test_corrupt_state_reads_as_empty(self):
        with tempfile.TemporaryDirectory() as d:
            s = StateStore(d)
            Path(s.state_file).write_text("{{{")
            self.assertEqual(s.load(), {})

    def test_first_sight_is_not_a_change(self):
        self.assertEqual(diff_state(self.results(ok_a=False), {}), [])

    def test_flip_down_and_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            s = StateStore(d)
            s.save(self.results())
            down = diff_state(self.results(ok_a=False), s.load())
            self.assertEqual([(c["name"], c["now_ok"]) for c in down], [("A", False)])
            s.save(self.results(ok_a=False))
            up = diff_state(self.results(), s.load())
            self.assertEqual([(c["name"], c["now_ok"], c["was"]) for c in up], [("A", True, "HTTP 500")])

    def test_steady_state_has_no_changes_even_when_broken(self):
        with tempfile.TemporaryDirectory() as d:
            s = StateStore(d)
            s.save(self.results(ok_a=False))
            self.assertEqual(diff_state(self.results(ok_a=False), s.load()), [])

    def test_lock_acquire_release(self):
        with tempfile.TemporaryDirectory() as d:
            s = StateStore(d)
            self.assertTrue(s.acquire())
            self.assertTrue(os.path.exists(s.lock_file))
            s.release()
            self.assertFalse(os.path.exists(s.lock_file))

    def test_live_lock_blocks_second_run(self):
        with tempfile.TemporaryDirectory() as d:
            s = StateStore(d)
            Path(s.lock_file).write_text("424242")
            fake = subprocess.CompletedProcess([], 0, stdout="python -m arcturion_web_monitor --digest")
            with mock.patch("arcturion_web_monitor.state.subprocess.run", return_value=fake):
                self.assertFalse(s.acquire())

    def test_recycled_pid_lock_is_reclaimed(self):
        with tempfile.TemporaryDirectory() as d:
            s = StateStore(d)
            Path(s.lock_file).write_text("424242")
            fake = subprocess.CompletedProcess([], 0, stdout="/usr/bin/some-other-program")
            with mock.patch("arcturion_web_monitor.state.subprocess.run", return_value=fake):
                self.assertTrue(s.acquire())
            self.assertEqual(Path(s.lock_file).read_text(), str(os.getpid()))

    def test_release_leaves_someone_elses_lock(self):
        with tempfile.TemporaryDirectory() as d:
            s = StateStore(d)
            Path(s.lock_file).write_text("1")
            s.release()
            self.assertTrue(os.path.exists(s.lock_file))


class Notify(OfflineTestCase):
    def test_default_is_stdout(self):
        self.assertIsInstance(build_notifier(env={}), notify.StdoutNotifier)

    def test_env_selects_notifier(self):
        n = build_notifier(env={"WEBMON_NOTIFIER": "webhook", "WEBMON_WEBHOOK_URL": "https://hooks.example.com/x"})
        self.assertIsInstance(n, notify.WebhookNotifier)

    def test_unknown_notifier(self):
        with self.assertRaises(NotifierConfigError):
            build_notifier("pigeon", env={})

    def test_missing_env_is_a_clear_error(self):
        for name, var in (("webhook", "WEBMON_WEBHOOK_URL"), ("file", "WEBMON_NOTIFY_FILE"),
                          ("telegram", "WEBMON_TELEGRAM_BOT_TOKEN")):
            with self.subTest(name), self.assertRaises(NotifierConfigError) as cm:
                build_notifier(name, env={})
            self.assertIn(var, str(cm.exception))

    def test_file_notifier_appends(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "out.txt")
            n = build_notifier("file", env={"WEBMON_NOTIFY_FILE": p})
            self.assertTrue(n.send("one"))
            self.assertTrue(n.send("two"))
            self.assertEqual(Path(p).read_text().split(), ["one", "two"])

    def test_webhook_posts_json_and_reports_status(self):
        sent = []

        def transport(url, body, headers, timeout):
            sent.append((url, json.loads(body), headers))
            return 200, b"ok"
        n = build_notifier("webhook", env={"WEBMON_WEBHOOK_URL": "https://hooks.example.com/x"}, transport=transport)
        self.assertTrue(n.send("hello"))
        self.assertEqual(sent[0][0], "https://hooks.example.com/x")
        self.assertEqual(sent[0][1], {"text": "hello"})
        bad = build_notifier("webhook", env={"WEBMON_WEBHOOK_URL": "https://hooks.example.com/x"},
                             transport=lambda *a: (500, b""))
        self.assertFalse(bad.send("x"))

    def test_webhook_rejects_non_http_url(self):
        with self.assertRaises(NotifierConfigError):
            build_notifier("webhook", env={"WEBMON_WEBHOOK_URL": "file:///etc/passwd"})

    def test_telegram_builds_request_from_env_only(self):
        sent = []

        def transport(url, body, headers, timeout):
            sent.append((url, body.decode()))
            return 200, b"{}"
        env = {"WEBMON_TELEGRAM_BOT_TOKEN": "TESTTOKEN", "WEBMON_TELEGRAM_CHAT_ID": "TESTCHAT"}
        self.assertTrue(build_notifier("telegram", env=env, transport=transport).send("hi there"))
        self.assertEqual(sent[0][0], "https://api.telegram.org/botTESTTOKEN/sendMessage")
        self.assertIn("chat_id=TESTCHAT", sent[0][1])
        self.assertIn("text=hi+there", sent[0][1])

    def test_custom_notifier_can_be_registered(self):
        class Mine:
            def __init__(self, env):
                self.seen = []

            def send(self, text):
                self.seen.append(text)
                return True
        register_notifier("mine", Mine)
        try:
            n = build_notifier("mine", env={})
            self.assertTrue(n.send("x"))
            self.assertEqual(n.seen, ["x"])
        finally:
            notify.NOTIFIERS.pop("mine")


if __name__ == "__main__":
    unittest.main()
