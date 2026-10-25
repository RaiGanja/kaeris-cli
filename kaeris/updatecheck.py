"""A quiet "newer version on PyPI" hint, at most once a day.

Stdlib only. Everything that can go wrong (no network, odd JSON, unwritable cache) ends in
silence: this must never slow down or break the command it is attached to. The fetch and the
clock are parameters so tests never touch the network."""
import json
import os
import re
import sys
import time
import urllib.request

CHECK_EVERY = 24 * 3600
TIMEOUT = 1.5
RETRY_AFTER = 3600  # after a failed request: a dead network is not retried on every run
_CACHE_FILE = "update-check.json"


def _parse(v):
    """A comparable version, or None. packaging when present, plain release numbers otherwise."""
    if not isinstance(v, str):
        return None
    try:
        from packaging.version import Version
        return Version(v)
    except ImportError:
        m = re.fullmatch(r"\d+(\.\d+)*", v.strip())
        return tuple(int(x) for x in v.strip().split(".")) if m else None
    except Exception:
        return None


def _fetch_latest(url, timeout):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "kaeris-update-check"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)["info"]["version"]


def cache_dir(env=None):
    env = os.environ if env is None else env
    if os.name == "nt":
        base = env.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    else:
        base = env.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "kaeris")


def _disabled(env):
    if env.get("CI"):
        return True
    return env.get("KAERIS_NO_UPDATE_CHECK", "").strip().lower() not in ("", "0", "false", "no")


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(stream, text):
    """Write even to a console whose code page cannot encode the text (cp1251 on Windows)."""
    try:
        stream.write(text)
    except UnicodeEncodeError:
        enc = getattr(stream, "encoding", None) or "ascii"
        stream.write(text.encode(enc, "replace").decode(enc, "replace"))
    try:
        stream.flush()
    except Exception:
        pass


def maybe_notify(package, current, *, stream=None, env=None, now=time.time, fetch=_fetch_latest,
                 cache_dir=None, quiet=False, require_tty=True):
    """Print one line to `stream` (stderr) when PyPI has a newer `package`. Returns the line or None."""
    try:
        env = os.environ if env is None else env
        stream = sys.stderr if stream is None else stream
        if quiet or _disabled(env):
            return None
        if require_tty and not (hasattr(stream, "isatty") and stream.isatty()):
            return None
        d = cache_dir or globals()["cache_dir"](env)
        path = os.path.join(d, _CACHE_FILE)
        data = _read(path)
        t = now()
        last = data.get(package, {}).get("checked_at")
        if isinstance(last, (int, float)) and 0 <= t - last < CHECK_EVERY:
            return None
        latest = None
        failed = False
        try:
            latest = fetch(f"https://pypi.org/pypi/{package}/json", TIMEOUT)
        except Exception:
            failed = True
        try:
            data[package] = {"checked_at": t - CHECK_EVERY + RETRY_AFTER if failed else t}
            os.makedirs(d, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass
        a, b = _parse(current), _parse(latest)
        if a is None or b is None or not b > a:
            return None
        line = f"A new version of {package} is available: {current} -> {latest}. Update: pip install -U {package}"
        stream.write(line + "\n")
        return line
    except Exception:
        return None
