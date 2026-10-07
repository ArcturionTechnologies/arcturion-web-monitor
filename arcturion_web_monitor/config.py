"""Load and validate the surface register (surfaces.json)."""
import json
import re
from urllib.parse import urlparse


class ConfigError(ValueError):
    pass


def _check_url(url, where):
    p = urlparse(url if isinstance(url, str) else "")
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ConfigError(f"{where}: not an http(s) URL: {url!r}")


def validate(register):
    """Raise ConfigError on a malformed register; return it unchanged otherwise."""
    if not isinstance(register, dict) or not isinstance(register.get("surfaces"), list):
        raise ConfigError("register must be an object with a 'surfaces' list")
    if not register["surfaces"]:
        raise ConfigError("'surfaces' is empty")
    seen = set()
    for i, s in enumerate(register["surfaces"]):
        where = f"surfaces[{i}]"
        if not isinstance(s, dict):
            raise ConfigError(f"{where}: must be an object")
        for key in ("name", "url"):
            if not s.get(key):
                raise ConfigError(f"{where}: missing '{key}'")
        _check_url(s["url"], where)
        if s["name"] in seen:
            raise ConfigError(f"{where}: duplicate name {s['name']!r}")
        seen.add(s["name"])
        for pattern_key in ("must_not_match",):
            if s.get(pattern_key):
                try:
                    re.compile(s[pattern_key])
                except re.error as e:
                    raise ConfigError(f"{where}: bad regex in {pattern_key}: {e}")
        for j, a in enumerate(s.get("aliases", [])):
            _check_url(a.get("url"), f"{where}.aliases[{j}]")
        pc = s.get("page_checks")
        if pc is not None and not isinstance(pc, dict):
            raise ConfigError(f"{where}: page_checks must be an object")
    return register


def load_register(path):
    try:
        with open(path, encoding="utf-8") as f:
            register = json.load(f)
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e.strerror}")
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path} is not valid JSON: {e}")
    return validate(register)
