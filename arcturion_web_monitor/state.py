"""Change detection state and a single-run lock."""
import json
import os
import subprocess
from datetime import datetime, timezone


def default_state_dir(env=None):
    env = os.environ if env is None else env
    return env.get("WEBMON_STATE_DIR") or os.path.join(os.getcwd(), ".webmon-state")


class StateStore:
    def __init__(self, directory):
        self.dir = directory
        self.state_file = os.path.join(directory, "surface_state.json")
        self.lock_file = os.path.join(directory, "monitor.pid")

    # ---- state
    def load(self):
        try:
            with open(self.state_file, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self, results, now=None):
        os.makedirs(self.dir, exist_ok=True)
        stamp = (now or datetime.now(timezone.utc)).isoformat()
        state = {r["url"]: {"ok": r["ok"], "detail": r["detail"], "status": r["status"], "seen": stamp}
                 for r in results}
        tmp = self.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, self.state_file)

    # ---- lock (verifies the pid belongs to a monitor process, not a recycled pid)
    def acquire(self, me="arcturion_web_monitor", log=lambda m: None):
        os.makedirs(self.dir, exist_ok=True)
        if os.path.exists(self.lock_file):
            try:
                with open(self.lock_file, encoding="utf-8") as f:
                    old = int(f.read().strip())
                cmd = subprocess.run(["ps", "-p", str(old), "-o", "command="],
                                     capture_output=True, text=True, timeout=5).stdout
                if me in cmd:
                    log(f"another run is live (pid {old}); exiting")
                    return False
                log(f"stale lock (pid {old} is not a monitor run); reclaiming")
            except (ValueError, OSError, subprocess.SubprocessError):
                pass
        with open(self.lock_file, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        return True

    def release(self):
        try:
            with open(self.lock_file, encoding="utf-8") as f:
                if int(f.read().strip()) == os.getpid():
                    os.remove(self.lock_file)
        except (ValueError, OSError):
            pass


def diff_state(results, prev):
    """Surfaces whose ok-state flipped since the last run. First sight is not a change."""
    changes = []
    for r in results:
        was = prev.get(r["url"])
        if was is None:
            continue
        if was.get("ok") != r["ok"]:
            changes.append({"name": r["name"], "url": r["url"], "now_ok": r["ok"],
                            "detail": r["detail"], "was": was.get("detail", "")})
    return changes
