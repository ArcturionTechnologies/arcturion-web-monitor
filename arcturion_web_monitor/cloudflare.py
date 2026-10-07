"""Optional 24-hour traffic numbers from the Cloudflare GraphQL analytics API.

The token comes from the CLOUDFLARE_API_TOKEN environment variable (a token with
Zone:Analytics:Read). Zone tags are never stored in the register: a surface names the
environment variable that holds its tag with `cf_zone_tag_env`.
If anything is missing the digest still ships and says so.
"""
import json
import os
from datetime import datetime, timedelta, timezone

from .net import http_post

ENDPOINT = "https://api.cloudflare.com/client/v4/graphql"
QUERY = """
query($zone: String!, $since: Time!) {
  viewer { zones(filter: {zoneTag: $zone}) {
    httpRequests1dGroups(limit: 1, filter: {datetime_geq: $since}) {
      sum { requests pageViews bytes threats }
      uniq { uniques }
    } } }
}"""


def zones_from_register(register, env=None):
    """Return ({label: zone_tag}, [labels whose env var is unset])."""
    env = os.environ if env is None else env
    zones, missing = {}, []
    for s in register["surfaces"]:
        var = s.get("cf_zone_tag_env")
        if not var:
            continue
        label = s.get("cf_zone_label") or s["name"]
        if env.get(var):
            zones[label] = env[var]
        else:
            missing.append(f"{label} ({var} unset)")
    return zones, missing


def cloudflare_traffic(zones, env=None, transport=http_post, timeout=15, now=None):
    """Best-effort 24h traffic per zone. Returns (data, error_message)."""
    env = os.environ if env is None else env
    token = env.get("CLOUDFLARE_API_TOKEN")
    if not token:
        return {}, "CLOUDFLARE_API_TOKEN is not set; traffic section skipped"
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=1)).replace(microsecond=0).isoformat()
    out = {}
    for label, tag in zones.items():
        try:
            body = json.dumps({"query": QUERY, "variables": {"zone": tag, "since": since}}).encode()
            status, raw = transport(ENDPOINT, body, {"Authorization": f"Bearer {token}",
                                                     "Content-Type": "application/json"}, timeout)
            if status != 200:
                out[label] = {"error": f"HTTP {status}"}
                continue
            d = json.loads(raw)
            groups = d["data"]["viewer"]["zones"][0]["httpRequests1dGroups"]
            if groups:
                g = groups[0]
                out[label] = {"requests": g["sum"]["requests"], "pageviews": g["sum"]["pageViews"],
                              "uniques": g["uniq"]["uniques"], "threats": g["sum"]["threats"]}
            else:
                out[label] = {"error": "no data"}
        except Exception as e:
            out[label] = {"error": type(e).__name__}
    return out, None
