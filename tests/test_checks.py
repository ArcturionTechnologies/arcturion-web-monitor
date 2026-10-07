import ssl
import socket
import unittest
import urllib.error

from helpers import (http_error, FakeOpener, FakePage, FakeResponse, FakeMsg, FakeReq, OfflineTestCase,
                     fake_launcher, make_resolver, resolver_ok)
from arcturion_web_monitor.checks import check_url, run_checks
from arcturion_web_monitor.pwcheck import PlaywrightUnavailable, page_check


def redirect_err(url, code, target):
    return http_error(url, code, target)


class CheckUrl(OfflineTestCase):
    def check(self, table, url="https://example.com", **kw):
        return check_url(url, resolver=kw.pop("resolver", resolver_ok),
                         opener=FakeOpener(table), **kw)

    def test_healthy_200(self):
        r = self.check({"https://example.com": FakeResponse(200, "hello")})
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"], 200)
        self.assertIsNotNone(r["ms"])

    def test_wrong_status_expected(self):
        r = self.check({"https://example.com": FakeResponse(204, "")})
        self.assertFalse(r["ok"])
        self.assertIn("expected 200, got 204", r["detail"])

    def test_expect_status_override(self):
        r = self.check({"https://example.com": FakeResponse(204, "")}, expect_status=204)
        self.assertTrue(r["ok"])

    def test_must_contain_present_and_missing(self):
        t = {"https://example.com": FakeResponse(200, "Buy now")}
        self.assertTrue(self.check(t, must_contain="Buy now")["ok"])
        r = self.check(t, must_contain="Checkout")
        self.assertFalse(r["ok"])
        self.assertIn("missing expected content", r["detail"])

    def test_must_not_match_catches_dead_cta(self):
        body = '<a href="#" aria-label="Buy now">x</a>'
        r = self.check({"https://example.com": FakeResponse(200, body)},
                       must_not_match=r'href="#"[^>]*aria-label="Buy now')
        self.assertFalse(r["ok"])
        self.assertIn("forbidden pattern", r["detail"])

    def test_must_not_match_clean_page_passes(self):
        r = self.check({"https://example.com": FakeResponse(200, "fine")}, must_not_match="dead-link")
        self.assertTrue(r["ok"])

    def test_dns_failure_is_reported_without_a_request(self):
        opener = FakeOpener({})
        r = check_url("https://gone.example.org", resolver=make_resolver({"gone.example.org"}), opener=opener)
        self.assertFalse(r["ok"])
        self.assertIn("NXDOMAIN", r["detail"])
        self.assertEqual(opener.calls, [])

    def test_http_503(self):
        r = self.check({"https://example.com": http_error("https://example.com", 503)})
        self.assertEqual((r["ok"], r["status"], r["detail"]), (False, 503, "HTTP 503"))

    def test_http_522_has_plain_message(self):
        r = self.check({"https://example.com": http_error("https://example.com", 522)})
        self.assertIn("cannot reach origin", r["detail"])

    def test_alias_correct_redirect(self):
        r = self.check({"https://www.example.com": redirect_err("https://www.example.com", 301, "https://example.com/")},
                       url="https://www.example.com", expect_redirect_to="https://example.com")
        self.assertTrue(r["ok"])
        self.assertIn("301", r["detail"])

    def test_alias_wrong_redirect_target(self):
        r = self.check({"https://www.example.com": redirect_err("https://www.example.com", 302, "https://other.example.org")},
                       url="https://www.example.com", expect_redirect_to="https://example.com")
        self.assertFalse(r["ok"])
        self.assertIn("expected https://example.com", r["detail"])

    def test_alias_serving_200_is_an_advisory_not_an_outage(self):
        r = self.check({"https://www.example.com": FakeResponse(200, "x")},
                       url="https://www.example.com", expect_redirect_to="https://example.com")
        self.assertTrue(r["ok"])
        self.assertIn("no canonical redirect", r["advisory"])

    def test_unexpected_redirect_on_primary(self):
        r = self.check({"https://example.com": redirect_err("https://example.com", 301, "https://elsewhere.example.org")})
        self.assertFalse(r["ok"])
        self.assertIn("unexpected redirect", r["detail"])

    def test_tls_failure(self):
        r = self.check({"https://example.com": ssl.SSLError("handshake")})
        self.assertIn("TLS handshake failed", r["detail"])

    def test_tls_failure_wrapped_in_urlerror(self):
        r = self.check({"https://example.com": urllib.error.URLError(ssl.SSLError("bad cert"))})
        self.assertIn("TLS", r["detail"])

    def test_timeout(self):
        r = self.check({"https://example.com": socket.timeout()}, timeout=3)
        self.assertEqual(r["detail"], "timeout after 3s")

    def test_connection_refused_message_is_truncated(self):
        r = self.check({"https://example.com": urllib.error.URLError("x" * 500)})
        self.assertLessEqual(len(r["detail"]), 120)

    def test_unexpected_exception_never_escapes(self):
        r = self.check({"https://example.com": RuntimeError("boom")})
        self.assertFalse(r["ok"])
        self.assertIn("RuntimeError", r["detail"])


class RunChecks(OfflineTestCase):
    REG = {"surfaces": [
        {"name": "Site", "url": "https://example.com", "brand": "B", "known_issue": "flaky",
         "aliases": [{"url": "https://www.example.com", "expect_redirect_to": "https://example.com",
                      "known_issue": "alias dns"}],
         "page_checks": {"title_contains": "Example"}},
        {"name": "Other", "url": "https://example.org"},
    ]}
    TABLE = {
        "https://example.com": FakeResponse(200, "ok"),
        "https://www.example.com": redirect_err("https://www.example.com", 301, "https://example.com"),
        "https://example.org": FakeResponse(500, ""),
    }

    def test_results_cover_surfaces_and_aliases_and_carry_metadata(self):
        res = run_checks(self.REG, resolver=resolver_ok, opener=FakeOpener(self.TABLE))
        self.assertEqual([r["name"] for r in res], ["Site", "Site (alias)", "Other"])
        self.assertEqual(res[0]["known_issue"], "flaky")
        self.assertEqual(res[1]["known_issue"], "alias dns")
        self.assertFalse(res[2]["ok"])

    def test_page_checks_only_when_enabled(self):
        res = run_checks(self.REG, resolver=resolver_ok, opener=FakeOpener(self.TABLE),
                         page_checks=False)
        self.assertNotIn("Site (page)", [r["name"] for r in res])
        res = run_checks(self.REG, resolver=resolver_ok, opener=FakeOpener(self.TABLE),
                         page_checks=True, launcher=fake_launcher(FakePage()))
        page = [r for r in res if r["name"] == "Site (page)"][0]
        self.assertTrue(page["ok"])
        self.assertEqual(page["url"], "https://example.com#page")


class PageCheck(OfflineTestCase):
    def run_pc(self, page, spec):
        return page_check("https://example.com", spec, launcher=fake_launcher(page))

    def test_pass(self):
        r = self.run_pc(FakePage(), {"title_contains": "Example", "selectors": ["h1"]})
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"], 200)

    def test_http_error_status(self):
        r = self.run_pc(FakePage(status=404), {})
        self.assertFalse(r["ok"])
        self.assertIn("HTTP 404", r["detail"])

    def test_title_mismatch(self):
        r = self.run_pc(FakePage(title="Other"), {"title_contains": "Example"})
        self.assertIn("title missing", r["detail"])

    def test_missing_selector(self):
        r = self.run_pc(FakePage(selectors=("h1",)), {"selectors": ["h1", "#checkout"]})
        self.assertFalse(r["ok"])
        self.assertIn("#checkout", r["detail"])

    def test_console_errors_only_fail_when_asked(self):
        page = lambda: FakePage(console=[FakeMsg("error", "x"), FakeMsg("log", "y")])
        self.assertTrue(self.run_pc(page(), {})["ok"])
        r = self.run_pc(page(), {"fail_on_console_error": True})
        self.assertFalse(r["ok"])
        self.assertIn("1 console error", r["detail"])

    def test_failed_requests_advisory_then_failure(self):
        page = lambda: FakePage(failed=[FakeReq("https://cdn.example.net/a.js")])
        r = self.run_pc(page(), {})
        self.assertTrue(r["ok"])
        self.assertIn("1 sub-request", r["advisory"])
        self.assertFalse(self.run_pc(page(), {"fail_on_failed_request": True})["ok"])

    def test_slow_load_budget(self):
        import time
        from unittest import mock
        times = iter([100.0, 110.0, 110.0, 110.0])
        with mock.patch("arcturion_web_monitor.pwcheck.time.time", lambda: next(times)):
            r = self.run_pc(FakePage(), {"max_load_ms": 500})
        self.assertFalse(r["ok"])
        self.assertIn("slow load", r["detail"])

    def test_navigation_error_is_contained(self):
        r = self.run_pc(FakePage(goto_error=TimeoutError("nav")), {})
        self.assertFalse(r["ok"])
        self.assertIn("TimeoutError", r["detail"])

    def test_playwright_missing_is_skipped_not_failed(self):
        def launcher():
            raise PlaywrightUnavailable("playwright is not installed")
        r = page_check("https://example.com", {}, launcher=launcher)
        self.assertTrue(r["skipped"])
        self.assertFalse(r["ok"])

    def test_browser_always_closed(self):
        holder = {}
        from contextlib import contextmanager

        @contextmanager
        def launcher():
            from helpers import FakeBrowser
            b = holder["b"] = FakeBrowser(FakePage(goto_error=RuntimeError("x")))
            try:
                yield b
            finally:
                b.close()
        page_check("https://example.com", {}, launcher=launcher)
        self.assertTrue(holder["b"].closed)


if __name__ == "__main__":
    unittest.main()
