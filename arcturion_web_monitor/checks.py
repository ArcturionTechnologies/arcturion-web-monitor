"""HTTP surface checks. Standard library only; every check returns a dict and never raises."""
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "ArcturionWebMonitor/0.1 (+https://github.com/ArcturionTechnologies/arcturion-web-monitor)"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface redirects as HTTPError so the check can compare the target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, newurl, headers, fp)


def _default_opener():
    return urllib.request.build_opener(_NoRedirect)


def check_url(url, expect_status=200, must_contain=None, expect_redirect_to=None,
              must_not_match=None, timeout=15, resolver=socket.getaddrinfo, opener=None):
    """Describe one URL's health.

    `expect_redirect_to` marks an alias host (for example www): a correct 3xx to the
    canonical host is ideal; a bare 200 is serving fine but has no canonical
    redirect, which is an advisory rather than an outage.
    `must_not_match` is a regex that must NOT appear in the body. It catches silent
    breakage (a dead call-to-action) that still returns 200.
    `resolver` and `opener` are injectable so tests never touch the network.
    """
    out = {"url": url, "ok": False, "status": None, "ms": None, "detail": "",
           "advisory": "", "kind": "http"}
    t0 = time.time()
    host = urllib.parse.urlparse(url).hostname
    try:
        resolver(host, None)
    except socket.gaierror:
        out["detail"] = "DNS NXDOMAIN: hostname does not resolve"
        return out

    opener = opener or _default_opener()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with opener.open(req, timeout=timeout) as r:
            body = r.read(200_000).decode("utf-8", "replace")
            out["status"] = r.status
            out["ms"] = int((time.time() - t0) * 1000)
            if r.status != expect_status:
                out["detail"] = f"expected {expect_status}, got {r.status}"
                return out
            if expect_redirect_to:
                out["ok"] = True
                out["advisory"] = (f"serves 200 directly; no canonical redirect to "
                                   f"{expect_redirect_to} (duplicate-content risk)")
                return out
            if must_contain and must_contain not in body:
                out["detail"] = f"page missing expected content {must_contain!r}"
                return out
            if must_not_match and re.search(must_not_match, body):
                out["detail"] = "functional check failed: forbidden pattern present"
                return out
            out["ok"] = True
            return out
    except urllib.error.HTTPError as e:
        e.close()  # release the connection; status and redirect target stay readable
        out["status"] = e.code
        out["ms"] = int((time.time() - t0) * 1000)
        if 300 <= e.code < 400:
            target = e.reason if isinstance(e.reason, str) else ""
            if expect_redirect_to:
                if target.rstrip("/") == expect_redirect_to.rstrip("/"):
                    out["ok"] = True
                    out["detail"] = f"{e.code} -> {target}"
                else:
                    out["detail"] = f"{e.code} -> {target} (expected {expect_redirect_to})"
                return out
            out["detail"] = f"unexpected redirect {e.code} -> {target}"
            return out
        out["detail"] = f"HTTP {e.code}"
        if e.code == 522:
            out["detail"] = "HTTP 522: CDN cannot reach origin"
        return out
    except ssl.SSLError as e:
        out["detail"] = f"TLS handshake failed: {type(e).__name__}"
        return out
    except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, ssl.SSLError) or "SSL" in str(reason).upper():
            out["detail"] = "TLS handshake failed"
        elif isinstance(reason, (socket.timeout, TimeoutError)) or isinstance(e, (socket.timeout, TimeoutError)):
            out["detail"] = f"timeout after {timeout}s"
        else:
            out["detail"] = str(reason)[:120]
        return out
    except Exception as e:  # one surface must never kill the run
        out["detail"] = f"{type(e).__name__}: {str(e)[:100]}"
        return out


def run_checks(register, timeout=15, page_checks=False, launcher=None, **inject):
    """Check every surface (and its aliases). Optionally add Playwright page checks.

    `inject` forwards `resolver` / `opener` to check_url for tests.
    """
    results = []
    for s in register["surfaces"]:
        r = check_url(s["url"], s.get("expect_status", 200), s.get("must_contain"),
                      must_not_match=s.get("must_not_match"), timeout=timeout, **inject)
        r.update(name=s["name"], brand=s.get("brand", ""), platform=s.get("platform", ""),
                 known_issue=s.get("known_issue"))
        results.append(r)
        for a in s.get("aliases", []):
            ar = check_url(a["url"], expect_redirect_to=a.get("expect_redirect_to"),
                           timeout=timeout, **inject)
            ar.update(name=f"{s['name']} (alias)", brand=s.get("brand", ""),
                      platform=s.get("platform", ""), known_issue=a.get("known_issue"))
            results.append(ar)
        if page_checks and s.get("page_checks"):
            from .pwcheck import page_check
            pr = page_check(s["url"], s["page_checks"], launcher=launcher)
            pr.update(name=f"{s['name']} (page)", brand=s.get("brand", ""),
                      platform=s.get("platform", ""), known_issue=s["page_checks"].get("known_issue"))
            results.append(pr)
    return results
