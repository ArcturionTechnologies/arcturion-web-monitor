"""Optional browser-level page checks using Playwright.

Playwright is imported lazily, so the rest of the tool works without it. Install it
with `pip install "arcturion-web-monitor[playwright]"` and `playwright install chromium`.

A surface opts in with a `page_checks` object:

    "page_checks": {
      "title_contains": "Example",        # <title> must contain this
      "selectors": ["h1", "main"],        # each must exist after load
      "max_load_ms": 8000,                # slower than this is a failure
      "fail_on_console_error": true,      # any console.error fails the check
      "fail_on_failed_request": false     # any failed sub-request fails the check
    }

`launcher` is injectable: a zero-argument callable returning a context manager that
yields an object with the Playwright browser interface. Tests use a fake.
"""
import time
from contextlib import contextmanager


class PlaywrightUnavailable(RuntimeError):
    pass


@contextmanager
def default_launcher():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise PlaywrightUnavailable("playwright is not installed")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            yield browser
        finally:
            browser.close()


def page_check(url, spec, launcher=None, timeout_ms=30_000):
    """Load `url` in a browser and apply `spec`. Returns a result dict; never raises."""
    out = {"url": url + "#page", "ok": False, "status": None, "ms": None,
           "detail": "", "advisory": "", "kind": "page"}
    console_errors, failed = [], []
    launcher = launcher or default_launcher
    t0 = time.time()
    try:
        with launcher() as browser:
            page = browser.new_page()
            page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
            page.on("requestfailed", lambda r: failed.append(r.url))
            response = page.goto(url, wait_until="load", timeout=timeout_ms)
            out["ms"] = int((time.time() - t0) * 1000)
            out["status"] = response.status if response is not None else None
            problems = []
            if out["status"] is not None and out["status"] >= 400:
                problems.append(f"HTTP {out['status']}")
            title_needle = spec.get("title_contains")
            if title_needle and title_needle not in (page.title() or ""):
                problems.append(f"title missing {title_needle!r}")
            for sel in spec.get("selectors", []):
                if page.query_selector(sel) is None:
                    problems.append(f"selector not found: {sel}")
            max_ms = spec.get("max_load_ms")
            if max_ms and out["ms"] > max_ms:
                problems.append(f"slow load: {out['ms']}ms > {max_ms}ms")
            if spec.get("fail_on_console_error") and console_errors:
                problems.append(f"{len(console_errors)} console error(s)")
            if spec.get("fail_on_failed_request") and failed:
                problems.append(f"{len(failed)} failed request(s)")
            elif failed:
                out["advisory"] = f"{len(failed)} sub-request(s) failed"
            if problems:
                out["detail"] = "; ".join(problems)
            else:
                out["ok"] = True
    except PlaywrightUnavailable as e:
        out["detail"] = str(e)
        out["skipped"] = True
    except Exception as e:  # a browser crash must not kill the whole run
        out["detail"] = f"{type(e).__name__}: {str(e)[:100]}"
    return out
