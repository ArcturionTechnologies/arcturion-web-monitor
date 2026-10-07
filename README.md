# ArcturionWebMonitor

A small site-health monitor that stays quiet until something changes.

You list the web pages you care about in one JSON file. Each run checks them,
compares the result with the last run, and tells you only when a page went down
or came back. Once a day you can also ask for a full digest. Known problems you
have already accepted stay in the digest table but never nag you again.

Python 3.10+, standard library only. Browser checks (Playwright) and Cloudflare
traffic numbers are optional extras.

> **Portfolio project.** This is an open-source sample of the tooling behind
> Arcturion's web operations. It is not a commercial product and makes no claims
> about revenue or customers. Every URL in the bundled example is a placeholder.

## Quickstart

```bash
git clone https://github.com/ArcturionTechnologies/arcturion-web-monitor.git
cd arcturion-web-monitor

cp examples/surfaces.json surfaces.json      # then edit the URLs
python3 -m arcturion_web_monitor --validate  # check the file, nothing else
python3 -m arcturion_web_monitor --dry-run --digest
```

`--dry-run` prints what would be sent, sends nothing and does not save state.
Drop it and the first real run records a baseline. From then on you hear about
changes only.

Or install it as a command: `pip install .`, then run `arcturion-web-monitor ...`.

## What it checks

For every surface in the register:

| Check | Setting | Fails when |
| --- | --- | --- |
| Status | `expect_status` (default 200) | any other status |
| DNS | automatic | the host does not resolve |
| TLS and timeouts | automatic | handshake fails or the request times out |
| Content present | `must_contain` | the text is missing from the page |
| Content absent | `must_not_match` (regex) | the pattern appears, e.g. a dead "Buy" button that still returns 200 |
| Alias redirect | `aliases[].expect_redirect_to` | a `www` host redirects somewhere unexpected (a bare 200 is only an advisory) |
| Known issue | `known_issue` | never alerts; shown as a known blocker until its state changes |

### Browser page checks (optional)

Add `page_checks` to a surface and run with `--playwright`:

```json
"page_checks": {
  "title_contains": "Example",
  "selectors": ["h1", "main"],
  "max_load_ms": 8000,
  "fail_on_console_error": true,
  "fail_on_failed_request": false
}
```

This loads the page in headless Chromium and checks the title, that each
selector exists, the load time, console errors and failed sub-requests. Install
with `pip install ".[playwright]"` and `playwright install chromium`. Without
Playwright installed those checks show as skipped, never as failures.

### Cloudflare traffic (optional)

With `--digest`, the digest can include 24-hour page views and visitors. Set
`CLOUDFLARE_API_TOKEN` (a token with Zone:Analytics:Read). A surface names the
environment variable that holds its zone tag, so no zone ID lives in the file:

```json
"cf_zone_tag_env": "WEBMON_CF_ZONE_EXAMPLE_COM", "cf_zone_label": "example.com"
```

If the token or a tag is missing, the digest still ships and says so.

## Notifications

A notifier is chosen with `--notifier` or `WEBMON_NOTIFIER`. Everything is
configured through environment variables:

| Notifier | Variables |
| --- | --- |
| `stdout` (default) | none |
| `file` | `WEBMON_NOTIFY_FILE` |
| `webhook` | `WEBMON_WEBHOOK_URL` (receives `{"text": "..."}`; Slack and Discord incoming webhooks accept this) |
| `telegram` | `WEBMON_TELEGRAM_BOT_TOKEN`, `WEBMON_TELEGRAM_CHAT_ID` |

Add your own in a few lines:

```python
from arcturion_web_monitor.notify import register_notifier

class Mine:
    def __init__(self, env): ...
    def send(self, text): ...   # return True on success

register_notifier("mine", Mine)
```

Secrets are only ever read from the environment. Nothing is read from a
password manager or credential store.

## Change detection

State is a small JSON file in `.webmon-state/` (override with `--state-dir` or
`WEBMON_STATE_DIR`). A change is a surface flipping between healthy and broken.

- The first time a surface is seen it is recorded, not alerted.
- A surface that stays broken does not alert again.
- State is saved only after the message is delivered, so a failed send is
  retried on the next run instead of being lost.
- A pid lock stops two runs overlapping, and checks that the pid really belongs
  to a monitor run before trusting it.

## Scheduling

Run it from cron, a systemd timer or launchd. For example, every 15 minutes for
alerts plus one digest at 08:00:

```cron
*/15 * * * *  cd /path/to/arcturion-web-monitor && python3 -m arcturion_web_monitor
0 8 * * *     cd /path/to/arcturion-web-monitor && python3 -m arcturion_web_monitor --digest
```

Exit codes: `0` ok, `1` a message could not be delivered, `2` configuration error.

## Configuration

| Flag | Environment | Default |
| --- | --- | --- |
| `--config` | `WEBMON_CONFIG` | `./surfaces.json` |
| `--notifier` | `WEBMON_NOTIFIER` | `stdout` |
| `--state-dir` | `WEBMON_STATE_DIR` | `./.webmon-state` |
| `--timeout` | | 15 seconds |
| `--digest`, `--dry-run`, `--playwright`, `--validate` | | off |

## Project layout

```
arcturion_web_monitor/checks.py       HTTP checks, DNS/TLS/redirect/content rules
arcturion_web_monitor/pwcheck.py      optional Playwright page checks
arcturion_web_monitor/cloudflare.py   optional analytics
arcturion_web_monitor/notify.py       pluggable notifiers
arcturion_web_monitor/state.py        change detection, pid lock
arcturion_web_monitor/digest.py       plain-text alert and digest
arcturion_web_monitor/cli.py          command line
examples/surfaces.json                placeholder register
tests/                                73 tests, stdlib unittest
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Everything runs offline. HTTP, DNS, Playwright and Cloudflare are replaced with
fakes, and the suite blocks real socket connections so a stray network call
fails the test. State goes to temporary folders.

## License

MIT. See [LICENSE](LICENSE).

Implementation is AI-assisted; architecture, requirements, and testing directed by Robert Lingoes.
