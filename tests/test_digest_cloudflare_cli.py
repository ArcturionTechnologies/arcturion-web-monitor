import io
import json
import os
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from datetime import datetime, timezone

from helpers import http_error, FakeOpener, FakeResponse, OfflineTestCase, REPO, resolver_ok
from arcturion_web_monitor import cli, cloudflare
from arcturion_web_monitor.digest import format_change_alert, format_digest

NOW = datetime(2026, 1, 5, 8, 30)


def res(name, ok=True, detail="", known=None, advisory="", skipped=False, ms=120):
    return {"name": name, "url": "https://" + name.lower().replace(" ", "-") + ".example.com", "ok": ok,
            "detail": detail, "known_issue": known, "advisory": advisory, "ms": ms if ok else None,
            "status": 200 if ok else 500, **({"skipped": True} if skipped else {})}


class Digest(OfflineTestCase):
    def test_all_healthy_headline(self):
        out = format_digest([res("A"), res("B")], now=NOW)
        self.assertTrue(out.startswith("All web surfaces healthy"))
        self.assertIn("Surfaces (2/2 up)", out)

    def test_new_outage_headline_and_detail(self):
        out = format_digest([res("A"), res("B", ok=False, detail="HTTP 500")], now=NOW)
        self.assertIn("1 surface(s) need attention", out)
        self.assertIn("[down] B: HTTP 500", out)

    def test_known_blockers_do_not_escalate_the_headline(self):
        out = format_digest([res("A"), res("B", ok=False, detail="x", known="origin flaky")], now=NOW)
        self.assertIn("Healthy apart from known blockers", out)
        self.assertIn("Known blockers (unchanged)", out)
        self.assertIn("origin flaky", out)

    def test_changes_advisories_and_skips(self):
        changes = [{"name": "A", "url": "u", "now_ok": True, "detail": "", "was": "HTTP 500"}]
        out = format_digest([res("A", advisory="no redirect"), res("A (page)", ok=False, detail="no playwright", skipped=True)],
                            changes=changes, now=NOW)
        self.assertIn("- A: recovered", out)
        self.assertIn("Advisories (not outages)", out)
        self.assertIn("[skip] A (page)", out)
        self.assertIn("Surfaces (1/1 up)", out)

    def test_traffic_section(self):
        t = {"example.com": {"pageviews": 12345, "uniques": 678}, "example.org": {"error": "HTTP 403"}}
        out = format_digest([res("A")], traffic=t, now=NOW)
        self.assertIn("example.com: 12,345 views, 678 visitors", out)
        self.assertIn("example.org: unavailable (HTTP 403)", out)
        noted = format_digest([res("A")], traffic={}, traffic_note="token missing", now=NOW)
        self.assertIn("! token missing", noted)

    def test_change_alert_text(self):
        out = format_change_alert([
            {"name": "A", "url": "https://a.example.com", "now_ok": False, "detail": "HTTP 500", "was": ""},
            {"name": "B", "url": "https://b.example.com", "now_ok": True, "detail": "", "was": "HTTP 503"}])
        self.assertIn("[down] A", out)
        self.assertIn("[recovered] B", out)
        self.assertIn("was: HTTP 503", out)


class Cloudflare(OfflineTestCase):
    def test_zones_come_from_env_names_not_the_register(self):
        reg = {"surfaces": [{"name": "A", "url": "https://example.com", "cf_zone_tag_env": "ZONE_A", "cf_zone_label": "example.com"},
                            {"name": "B", "url": "https://example.org", "cf_zone_tag_env": "ZONE_B"},
                            {"name": "C", "url": "https://example.net"}]}
        zones, missing = cloudflare.zones_from_register(reg, {"ZONE_A": "tag-a"})
        self.assertEqual(zones, {"example.com": "tag-a"})
        self.assertEqual(missing, ["B (ZONE_B unset)"])

    def test_no_token_is_a_note_not_a_crash(self):
        data, err = cloudflare.cloudflare_traffic({"z": "t"}, env={})
        self.assertEqual(data, {})
        self.assertIn("CLOUDFLARE_API_TOKEN", err)

    def test_success_parses_and_sends_bearer_token(self):
        seen = {}

        def transport(url, body, headers, timeout):
            seen.update(url=url, auth=headers["Authorization"], vars=json.loads(body)["variables"])
            return 200, json.dumps({"data": {"viewer": {"zones": [{"httpRequests1dGroups": [
                {"sum": {"requests": 10, "pageViews": 7, "bytes": 1, "threats": 0}, "uniq": {"uniques": 3}}]}]}}}).encode()
        data, err = cloudflare.cloudflare_traffic({"example.com": "tag"}, env={"CLOUDFLARE_API_TOKEN": "TEST"},
                                                  transport=transport, now=datetime(2026, 1, 2, tzinfo=timezone.utc))
        self.assertIsNone(err)
        self.assertEqual(data["example.com"], {"requests": 10, "pageviews": 7, "uniques": 3, "threats": 0})
        self.assertEqual(seen["auth"], "Bearer TEST")
        self.assertEqual(seen["vars"], {"zone": "tag", "since": "2026-01-01T00:00:00+00:00"})

    def test_http_error_malformed_and_empty_are_per_zone_errors(self):
        env = {"CLOUDFLARE_API_TOKEN": "TEST"}
        d, _ = cloudflare.cloudflare_traffic({"a": "t"}, env=env, transport=lambda *x: (403, b""))
        self.assertEqual(d["a"], {"error": "HTTP 403"})
        d, _ = cloudflare.cloudflare_traffic({"a": "t"}, env=env, transport=lambda *x: (200, b"not json"))
        self.assertIn("error", d["a"])
        empty = json.dumps({"data": {"viewer": {"zones": [{"httpRequests1dGroups": []}]}}}).encode()
        d, _ = cloudflare.cloudflare_traffic({"a": "t"}, env=env, transport=lambda *x: (200, empty))
        self.assertEqual(d["a"], {"error": "no data"})


class Recorder:
    def __init__(self, ok=True):
        self.ok, self.sent = ok, []

    def send(self, text):
        self.sent.append(text)
        return self.ok


class Cli(OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = os.path.join(self.tmp.name, "surfaces.json")
        with open(self.cfg, "w") as f:
            json.dump({"surfaces": [
                {"name": "Site", "url": "https://example.com", "cf_zone_tag_env": "ZONE_X", "cf_zone_label": "example.com"},
                {"name": "Docs", "url": "https://example.org", "known_issue": "flaky"}]}, f)
        self.state = os.path.join(self.tmp.name, "state")

    def args(self, *extra):
        return cli.build_parser().parse_args(["--config", self.cfg, "--state-dir", self.state, *extra])

    def kw(self, site=200, docs=200):
        table = {"https://example.com": FakeResponse(site, "x") if site == 200 else http_error("https://example.com", site),
                 "https://example.org": FakeResponse(docs, "x") if docs == 200 else http_error("https://example.org", docs)}
        return {"resolver": resolver_ok, "opener": FakeOpener(table)}

    def go(self, args, notifier, **kw):
        return cli.run(args, env={}, notifier=notifier, check_kwargs=self.kw(**kw))

    def test_first_run_sends_nothing_but_records_state(self):
        n = Recorder()
        self.assertEqual(self.go(self.args(), n), 0)
        self.assertEqual(n.sent, [])
        self.assertTrue(os.path.exists(os.path.join(self.state, "surface_state.json")))

    def test_alerts_only_on_change_then_goes_quiet(self):
        n = Recorder()
        self.go(self.args(), n)
        self.go(self.args(), n, site=500)
        self.assertEqual(len(n.sent), 1)
        self.assertIn("[down] Site", n.sent[0])
        self.go(self.args(), n, site=500)  # still down: no repeat
        self.assertEqual(len(n.sent), 1)
        self.go(self.args(), n)            # recovered
        self.assertEqual(len(n.sent), 2)
        self.assertIn("[recovered] Site", n.sent[1])

    def test_dry_run_prints_and_does_not_save_state_or_send(self):
        n = Recorder()
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(self.go(self.args("--dry-run", "--digest"), n), 0)
        self.assertEqual(n.sent, [])
        self.assertIn("digest (not sent)", buf.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.state, "surface_state.json")))

    def test_failed_delivery_keeps_old_state_so_change_is_retried(self):
        good, bad = Recorder(), Recorder(ok=False)
        self.go(self.args(), good)
        self.assertEqual(self.go(self.args(), bad, site=500), 1)
        self.assertEqual(len(bad.sent), 1)
        self.assertEqual(self.go(self.args(), good, site=500), 0)
        self.assertEqual(len(good.sent), 1)

    def test_digest_includes_traffic_note_when_zone_env_missing(self):
        n = Recorder()
        self.assertEqual(self.go(self.args("--digest"), n), 0)
        self.assertEqual(len(n.sent), 1)
        self.assertIn("zone tag not set for: example.com (ZONE_X unset)", n.sent[0])
        self.assertIn("Surfaces (2/2 up)", n.sent[0])

    def test_digest_uses_injected_traffic(self):
        n = Recorder()
        env = {"ZONE_X": "tag"}
        calls = []

        def traffic(zones, env=None):
            calls.append(zones)
            return {"example.com": {"pageviews": 5, "uniques": 2}}, None
        rc = cli.run(self.args("--digest"), env=env, notifier=n, check_kwargs=self.kw(), traffic_fn=traffic)
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [{"example.com": "tag"}])
        self.assertIn("5 views, 2 visitors", n.sent[0])

    def test_known_issue_surface_appears_in_digest_without_alert(self):
        n = Recorder()
        self.go(self.args(), n, docs=500)
        self.go(self.args("--digest"), n, docs=500)
        self.assertEqual(len(n.sent), 1)  # digest only, no change alert
        self.assertIn("Healthy apart from known blockers", n.sent[0])

    def test_validate_and_bad_config(self):
        self.assertEqual(cli.run(self.args("--validate"), env={}), 0)
        bad = cli.build_parser().parse_args(["--config", os.path.join(self.tmp.name, "nope.json")])
        self.assertEqual(cli.run(bad, env={}), 2)

    def test_notifier_misconfiguration_exits_2(self):
        self.assertEqual(cli.run(self.args("--notifier", "webhook"), env={}), 2)

    def test_lock_is_released_after_run(self):
        self.go(self.args(), Recorder())
        self.assertFalse(os.path.exists(os.path.join(self.state, "monitor.pid")))

    def test_sample_register_validates_through_the_cli(self):
        args = cli.build_parser().parse_args(["--config", str(REPO / "examples" / "surfaces.json"), "--validate"])
        self.assertEqual(cli.run(args, env={}), 0)


if __name__ == "__main__":
    unittest.main()
