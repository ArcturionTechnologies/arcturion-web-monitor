"""Shared fakes. Nothing here opens a socket."""
import io
import socket
import sys
import urllib.error
import unittest
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


class OfflineTestCase(unittest.TestCase):
    """Fails loudly if any test tries to open a real network connection."""

    def setUp(self):
        def _blocked(*a, **k):
            raise AssertionError("test attempted real network access")
        p = unittest.mock.patch.object(socket.socket, "connect", _blocked)
        p.start()
        self.addCleanup(p.stop)


class FakeResponse:
    def __init__(self, status=200, body="", ):
        self.status = status
        self._body = body.encode()

    def read(self, n=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    """Maps URL -> FakeResponse or an exception to raise."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def open(self, req, timeout=None):
        self.calls.append(req.full_url)
        item = self.table[req.full_url]
        if isinstance(item, BaseException):
            raise item
        return item


def resolver_ok(host, port):
    return [("fake",)]


def make_resolver(bad_hosts=()):
    def resolver(host, port):
        if host in bad_hosts:
            raise socket.gaierror("no such host")
        return [("fake",)]
    return resolver


# Fake Playwright objects -------------------------------------------------------
class FakeMsg:
    def __init__(self, type_, text):
        self.type, self.text = type_, text


class FakeReq:
    def __init__(self, url):
        self.url = url


class FakeResp:
    def __init__(self, status):
        self.status = status


class FakePage:
    def __init__(self, status=200, title="Example Domain", selectors=("h1", "main"),
                 console=(), failed=(), goto_error=None, delay=0.0):
        self.status, self._title, self.selectors = status, title, set(selectors)
        self.console, self.failed, self.goto_error = list(console), list(failed), goto_error
        self.handlers = {}
        self.delay = delay

    def on(self, event, cb):
        self.handlers[event] = cb

    def goto(self, url, wait_until=None, timeout=None):
        if self.goto_error:
            raise self.goto_error
        for m in self.console:
            self.handlers["console"](m)
        for r in self.failed:
            self.handlers["requestfailed"](r)
        return FakeResp(self.status)

    def title(self):
        return self._title

    def query_selector(self, sel):
        return object() if sel in self.selectors else None


class FakeBrowser:
    def __init__(self, page):
        self.page, self.closed = page, False

    def new_page(self):
        return self.page

    def close(self):
        self.closed = True


def fake_launcher(page):
    from contextlib import contextmanager

    @contextmanager
    def launcher():
        b = FakeBrowser(page)
        try:
            yield b
        finally:
            b.close()
    return launcher


def http_error(url, code, target="x"):
    """HTTPError with an in-memory body so no temp files are created."""
    return urllib.error.HTTPError(url, code, target, {}, io.BytesIO(b""))
