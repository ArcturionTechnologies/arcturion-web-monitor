"""Plain-text alert and digest formatting (no markup, so any notifier can carry it)."""
from datetime import datetime


def format_change_alert(changes):
    lines = ["Web surface change detected", ""]
    for c in changes:
        if c["now_ok"]:
            lines.append(f"[recovered] {c['name']}")
            lines.append(f"  was: {c['was']}")
        else:
            lines.append(f"[down] {c['name']}")
            lines.append(f"  {c['detail']}")
        lines.append(f"  {c['url']}")
        lines.append("")
    return "\n".join(lines).strip()


def format_digest(results, traffic=None, traffic_note=None, changes=(), now=None):
    now = now or datetime.now()
    up = [r for r in results if r["ok"]]
    down = [r for r in results if not r["ok"] and not r.get("skipped")]
    skipped = [r for r in results if r.get("skipped")]
    new_down = [r for r in down if not r.get("known_issue")]

    if not down:
        head = "All web surfaces healthy"
    elif new_down:
        head = f"{len(new_down)} surface(s) need attention"
    else:
        head = "Healthy apart from known blockers"

    lines = [head, now.strftime("%a %b %d, %H:%M"), ""]

    if changes:
        lines.append("Changed since last run")
        for c in changes:
            lines.append(f"- {c['name']}: {'recovered' if c['now_ok'] else 'went down'}")
        lines.append("")

    lines.append(f"Surfaces ({len(up)}/{len(results) - len(skipped)} up)")
    for r in results:
        if r.get("skipped"):
            mark, note = "[skip]", r["detail"]
        elif r["ok"]:
            mark, note = "[ok]", f"{r['ms']}ms" if r.get("ms") is not None else "ok"
        elif r.get("known_issue"):
            mark, note = "[known]", "known issue"
        else:
            mark, note = "[down]", r["detail"][:60]
        lines.append(f"{mark} {r['name']}: {note}")
    lines.append("")

    if traffic is not None or traffic_note:
        lines.append("Traffic, last 24h")
        if traffic_note:
            lines.append(f"! {traffic_note}")
        elif not traffic:
            lines.append("(no zones configured)")
        else:
            for zone, d in sorted(traffic.items()):
                if "error" in d:
                    lines.append(f"- {zone}: unavailable ({d['error']})")
                else:
                    lines.append(f"- {zone}: {d['pageviews']:,} views, {d['uniques']:,} visitors")
        lines.append("")

    advisories = [r for r in results if r.get("advisory")]
    if advisories:
        lines.append("Advisories (not outages)")
        lines.extend(f"- {r['name']}: {r['advisory']}" for r in advisories)
        lines.append("")

    known = [r for r in down if r.get("known_issue")]
    if known:
        lines.append("Known blockers (unchanged)")
        lines.extend(f"- {r['name']}: {r['known_issue']}" for r in known)

    return "\n".join(lines).strip()
