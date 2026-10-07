"""Command line: check every surface, alert on change, optionally send a full digest."""
import argparse
import os
import sys
from datetime import datetime

from . import cloudflare
from .checks import run_checks
from .config import ConfigError, load_register
from .digest import format_change_alert, format_digest
from .notify import NotifierConfigError, build_notifier
from .state import StateStore, default_state_dir, diff_state


def log(msg):
    print(f"{datetime.now().isoformat(timespec='seconds')} {msg}", file=sys.stderr, flush=True)


def run(args, env=None, notifier=None, check_kwargs=None, traffic_fn=None):
    """Execute one pass. Returns a process exit code. All collaborators are injectable."""
    env = os.environ if env is None else env
    try:
        register = load_register(args.config)
    except ConfigError as e:
        log(f"config error: {e}")
        return 2
    if args.validate:
        log(f"{len(register['surfaces'])} surface(s) valid")
        return 0
    try:
        notifier = notifier or build_notifier(args.notifier, env)
    except NotifierConfigError as e:
        log(f"notifier error: {e}")
        return 2

    store = StateStore(args.state_dir or default_state_dir(env))
    if not store.acquire(log=log):
        return 0
    try:
        log(f"checking {len(register['surfaces'])} surface(s)")
        results = run_checks(register, timeout=args.timeout, page_checks=args.playwright,
                             **(check_kwargs or {}))
        for r in results:
            log(f"  {'OK ' if r['ok'] else 'BAD'} {r['name']:<28} {r['detail'] or r['status']}")

        changes = diff_state(results, store.load())

        traffic, traffic_note = None, None
        if args.digest:
            zones, missing = cloudflare.zones_from_register(register, env)
            if zones or missing:
                fn = traffic_fn or cloudflare.cloudflare_traffic
                traffic, traffic_note = fn(zones, env=env) if zones else ({}, None)
                if missing and not traffic_note:
                    traffic_note = "zone tag not set for: " + ", ".join(missing)

        ok = True
        if args.dry_run:
            if changes:
                print("--- change alert (not sent) ---\n" + format_change_alert(changes) + "\n")
            if args.digest:
                print("--- digest (not sent) ---\n" + format_digest(results, traffic, traffic_note, changes) + "\n")
            if not changes and not args.digest:
                log("no changes; nothing would be sent")
            log("dry run: state not saved")
            return 0

        if changes:
            log(f"{len(changes)} state change(s)")
            ok &= notifier.send(format_change_alert(changes))
        if args.digest:
            ok &= notifier.send(format_digest(results, traffic, traffic_note, changes))
        if not changes and not args.digest:
            log("no changes; nothing to send")
        if ok:
            store.save(results)  # only after delivery, so a failed send is retried next run
        else:
            log("delivery failed; state not saved so the change is re-detected next run")
        return 0 if ok else 1
    finally:
        store.release()


def build_parser():
    ap = argparse.ArgumentParser(prog="arcturion-web-monitor", description=__doc__)
    ap.add_argument("--config", default=os.environ.get("WEBMON_CONFIG", "surfaces.json"),
                    help="surface register (default: WEBMON_CONFIG or ./surfaces.json)")
    ap.add_argument("--digest", action="store_true", help="also send the full digest")
    ap.add_argument("--dry-run", action="store_true", help="print instead of sending; do not save state")
    ap.add_argument("--playwright", action="store_true", help="run browser page checks where configured")
    ap.add_argument("--notifier", default=None, help="stdout, file, webhook, telegram (default: WEBMON_NOTIFIER or stdout)")
    ap.add_argument("--state-dir", default=None, help="default: WEBMON_STATE_DIR or ./.webmon-state")
    ap.add_argument("--timeout", type=int, default=15, help="per-request timeout in seconds")
    ap.add_argument("--validate", action="store_true", help="validate the register and exit")
    return ap


def main(argv=None):
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
