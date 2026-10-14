"""Account-check stamps (hf-accounts): proof that the checker passed, and still applies.

hf_check.py writes one stamp per login when every check passes. A spend may lean on it for
`stamp_hours` (3 by default) and only while nothing it vouched for has changed: the registry,
the router modules, the launcher in vendor\\hf.exe and the login's config.json (which holds the
selected workspace). A change means the check no longer describes what would run, so the check
is redone rather than trusted.

The connector stamp (_checked/connector.json) is the same idea for the Higgsfield MCP connector,
which has one login for the whole Claude app; account_gate.py reads it.
"""
import hashlib
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# Everything the router runs. A stamp taken under one build does not vouch for another.
MODULES = ("router.py", "hfacct.py", "stamps.py", "strict.py")
CONNECTOR = "connector"  # reserved: _checked/connector.json is the connector stamp, never a login
FUTURE_SLACK = 60  # seconds a stamp may be dated ahead of this clock before it counts as forged


def _sha(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return "missing"


def _stat_key(path):
    try:
        st = os.stat(path)
        return "%d:%d" % (st.st_mtime_ns, st.st_size)
    except OSError:
        return "missing"


def _path(root, name):
    return os.path.join(root, "_checked", name + ".json")


def _write_json(path, body):
    """Write-then-rename, so the router never reads half a stamp."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(body, fh, indent=2)
    os.replace(tmp, path)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            body = json.load(fh)
        return body if isinstance(body, dict) else None
    except (OSError, ValueError):
        return None


def ago(seconds):
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return "%d min" % (seconds // 60)
    return "%d h %02d min" % (seconds // 3600, seconds % 3600 // 60)


def config_path(root, login, entry):
    import hfacct  # noqa: E402  (same folder; imported late so this module loads on its own)

    return os.path.join(hfacct.login_home(root, login, entry), ".config", "higgsfield", "config.json")


def fingerprint(root, login, entry, vendor):
    """What a stamp vouches for: sha256 of the registry, each router module and the launcher,
    plus "mtime_ns:size" of the login's config.json (it changes on every `workspace set`)."""
    fp = {"registry": _sha(os.path.join(root, "logins.json")),
          "launcher": _sha(os.path.join(vendor, "hf.exe")),
          "config": _stat_key(config_path(root, login, entry))}
    for mod in MODULES:
        fp[mod[:-3]] = _sha(os.path.join(HERE, mod))
    return fp


def write(root, login, fp, checks, now=None):
    if login == CONNECTOR:
        raise ValueError("'%s' is reserved for the connector stamp" % CONNECTOR)
    now = time.time() if now is None else now
    body = {"login": login, "t": now, "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
            "fingerprint": fp, "checks": checks}
    _write_json(_path(root, login), body)
    return _path(root, login)


def read(root, login):
    return _read_json(_path(root, login))


def clear(root, name):
    """Drop a stamp (a failed re-check must not leave an older pass in force). True if one existed."""
    try:
        os.remove(_path(root, name))
        return True
    except OSError:
        return False


def _age(body, now):
    try:
        return now - float(body.get("t"))
    except (TypeError, ValueError):
        return None


def valid(root, login, entry, vendor, hours, now=None):
    """(ok, why) for the login's stamp: present, younger than `hours`, and nothing changed since."""
    now = time.time() if now is None else now
    body = read(root, login)
    if not body or body.get("login") != login or not isinstance(body.get("fingerprint"), dict):
        return False, "no account check on record"
    age = _age(body, now)
    if age is None or age < -FUTURE_SLACK:
        return False, "the stamp's time is not believable"
    if age > hours * 3600:
        return False, "the last check was %s ago, the limit is %g h" % (ago(age), hours)
    fp, old = fingerprint(root, login, entry, vendor), body["fingerprint"]
    changed = sorted(k for k in set(fp) | set(old) if fp.get(k) != old.get(k))
    if changed:
        return False, "changed since the check: %s" % ", ".join(changed)
    return True, "checked %s ago" % ago(age)


def write_connector(root, client, domain, workspace_id, now=None):
    now = time.time() if now is None else now
    body = {"client": client, "domain": domain, "workspace_id": workspace_id, "t": now,
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))}
    _write_json(_path(root, CONNECTOR), body)
    return _path(root, CONNECTOR)


def read_connector(root):
    return _read_json(_path(root, CONNECTOR))


def connector_valid(root, hours, now=None):
    """(ok, why) for the connector stamp (age only; account_gate.py applies the same rule)."""
    now = time.time() if now is None else now
    body = read_connector(root)
    if not body:
        return False, "no connector check on record"
    age = _age(body, now)
    if age is None or age < -FUTURE_SLACK:
        return False, "the connector stamp's time is not believable"
    if age > hours * 3600:
        return False, "the connector check was %s ago, the limit is %g h" % (ago(age), hours)
    return True, "%s (%s) checked %s ago" % (body.get("client"), body.get("workspace_id"), ago(age))
