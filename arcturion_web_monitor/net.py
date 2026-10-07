"""Tiny HTTP helper so every outbound POST goes through one seam that tests can replace."""
import urllib.error
import urllib.request


def http_post(url, body, headers=None, timeout=15):
    """POST bytes and return (status, response_bytes). HTTP errors return their status."""
    req = urllib.request.Request(url, data=body, headers=headers or {}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
