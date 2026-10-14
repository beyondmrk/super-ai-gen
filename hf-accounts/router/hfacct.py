"""hf-accounts core: which Higgsfield login a CLI call must use, and whether it may run.

Shared by router.py (runs on every Higgsfield call) and the account checker. Everything that
decides is a plain function here, so it can be tested without touching a real login.
"""
import fnmatch
import json
import os
import re
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
# The Windows profile is the parent of the .claude folder this file lives under. Derived from this
# file's location, never from USERPROFILE: a login launcher points USERPROFILE at a login folder.
def _profile(here):
    p = here
    while os.path.basename(p).lower() != ".claude":
        if os.path.dirname(p) == p:
            # shortcut: outside .claude (a dev checkout) falls back to ~; install under .claude for real use
            return os.path.expanduser("~")
        p = os.path.dirname(p)
    return os.path.dirname(p)


HOME = _profile(_HERE)
REAL_VENDOR = os.path.join(HOME, "AppData", "Roaming", "npm", "node_modules", "@higgsfield", "cli", "vendor")
DEFAULT_ROOT = os.path.join(HOME, ".hf-logins")
HOOKS_DIR = os.path.join(HOME, ".claude", "local-hooks")

AGNOSTIC_GROUPS = {"help", "version", "model", "workflow", "preset", "completion"}
SETUP_GROUPS = {"auth", "workspace", "ws"}
READ_SUBS = {"list", "status", "get", "wait", "cost", "show", "transactions"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class Refusal(Exception):
    """A call the router must not run. The message is shown to Claude, so it says what to do."""


def norm(path):
    """Comparable form of a path. realpath expands 8.3 short names (C:\\Users\\USERNA~1), so the
    same folder written two ways still compares equal."""
    return os.path.normcase(os.path.realpath(path)) if path else ""


def logins_root(vendor):
    """Registry folder. HF_LOGINS_ROOT is honoured only for a test vendor folder, never the real one."""
    override = os.environ.get("HF_LOGINS_ROOT")
    if override and norm(vendor) != norm(REAL_VENDOR):
        return override
    return DEFAULT_ROOT


def load_registry(root):
    with open(os.path.join(root, "logins.json"), encoding="utf-8") as fh:
        reg = json.load(fh)
    logins = reg.get("logins")
    if not isinstance(logins, dict) or not logins:
        raise ValueError("logins.json has no logins")
    for name, entry in logins.items():
        if not NAME_RE.match(name):
            raise ValueError("bad login name %r" % name)
        if entry.get("domain") not in ("EMPLOYER", "AGENCY"):
            raise ValueError("login %r: domain must be EMPLOYER or AGENCY" % name)
        if not UUID_RE.match(entry.get("workspace_id", "")):
            raise ValueError("login %r: workspace_id is not a UUID" % name)
    if reg.get("unmatched", "refuse") not in ("refuse", "default"):
        raise ValueError("unmatched must be 'refuse' or 'default'")
    return reg


def login_home(root, name, entry):
    """The folder the CLI must see as USERPROFILE for this login."""
    return os.path.normpath(entry.get("home") or os.path.join(root, name))


def job_of(path):
    """'EMPLOYER', 'AGENCY' or None, from the boundary gate's own domain rules (one source of truth)."""
    if HOOKS_DIR not in sys.path:
        sys.path.insert(0, HOOKS_DIR)
    try:
        import domain  # noqa: E402  (~/.claude/local-hooks/domain.py)
    except ImportError:  # no boundary gate installed: folders carry no job, HF_CLIENT decides
        return None
    return domain.job_of(path)


def classify(argv):
    """'agnostic' | 'setup' | 'read' | 'spend' for CLI arguments (program name excluded).

    Anything not recognised as harmless counts as 'spend', so a new CLI command is guarded.
    """
    words = [a for a in argv if not a.startswith("-")]
    flags = {a.split("=", 1)[0] for a in argv if a.startswith("-")}
    if not words or flags & {"-h", "--help", "-v", "--version"}:
        return "agnostic"
    group = words[0].lower()
    sub = words[1].lower() if len(words) > 1 else ""
    if group in AGNOSTIC_GROUPS:
        return "agnostic"
    if group in SETUP_GROUPS:
        return "setup"
    if group == "account" or sub in READ_SUBS:
        return "read"
    return "spend"


def _folder_match(cwd_n, pattern):
    pat = norm(pattern).replace("\\", "/")
    cwd = cwd_n.replace("\\", "/")
    return fnmatch.fnmatchcase(cwd, pat) or fnmatch.fnmatchcase(cwd, pat + "/*")


def resolve(env, cwd, reg, root, job_fn=job_of):
    """The login a call must use, or None when nothing identifies one. Raises Refusal on conflict.

    Signals: HF_CLIENT (explicit); USERPROFILE already pointing at a login folder (a login
    launcher); the working folder (EMPLOYER folders -> the EMPLOYER login, AGENCY client folder patterns).
    Every signal present must agree, and the login must belong to the folder's job.
    """
    logins = reg["logins"]
    votes = {}
    explicit = (env.get("HF_CLIENT") or "").strip().lower()
    if explicit:
        if explicit not in logins:
            raise Refusal("HF_CLIENT=%s is not a registered login (registered: %s)."
                          % (explicit, ", ".join(sorted(logins))))
        votes["HF_CLIENT"] = explicit

    profile = norm(env.get("USERPROFILE", ""))
    root_n = norm(root)
    for name, entry in logins.items():
        home = norm(login_home(root, name, entry))
        if home.startswith(root_n + os.sep) and profile == home:
            votes["USERPROFILE"] = name

    job = job_fn(cwd) if cwd else None
    if job == "EMPLOYER":
        employer = sorted(n for n, e in logins.items() if e["domain"] == "EMPLOYER")
        if len(employer) != 1:
            raise Refusal("this is an EMPLOYER folder but the registry has %d EMPLOYER logins; it needs exactly one."
                          % len(employer))
        votes["folder"] = employer[0]
    elif cwd:
        cwd_n = norm(cwd)
        hits = sorted({n for n, e in logins.items() for pat in e.get("folders", [])
                       if _folder_match(cwd_n, pat)})
        if len(hits) > 1:
            raise Refusal("folder %s matches several logins (%s)." % (cwd, ", ".join(hits)))
        if hits:
            votes["folder"] = hits[0]

    chosen = set(votes.values())
    if len(chosen) > 1:
        raise Refusal("the signals disagree about which login to use (%s)."
                      % ", ".join("%s says %s" % kv for kv in sorted(votes.items())))
    name = chosen.pop() if chosen else None
    if name and job in ("EMPLOYER", "AGENCY") and logins[name]["domain"] != job:
        raise Refusal("login %s belongs to %s work, but this call runs in a %s folder (%s)."
                      % (name, logins[name]["domain"], job, cwd))
    return name


def selected_workspace(real, home, root, name, ttl=60):
    """The login's selected workspace as the CLI reports it ({'id', 'name', ...}), or None.

    Cached per login for `ttl` seconds and only while that login's config.json is unchanged,
    so a `workspace set` is seen on the very next call.
    """
    cfg = os.path.join(home, ".config", "higgsfield", "config.json")
    try:
        st = os.stat(cfg)
        key = "%d:%d" % (st.st_mtime_ns, st.st_size)
    except OSError:
        key = "missing"
    cache = os.path.join(root, "_ws_cache", name + ".json")
    try:
        with open(cache, encoding="utf-8") as fh:
            hit = json.load(fh)
        if hit.get("key") == key and time.time() - hit.get("t", 0) < ttl:
            return hit.get("ws")
    except (OSError, ValueError):
        pass

    env = {k: v for k, v in os.environ.items() if not k.startswith("HF_ROUTER_")}
    env["USERPROFILE"] = home
    env["HOME"] = home
    try:
        out = subprocess.run([real, "workspace", "status", "--json"], env=env,
                             capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        ws = json.loads(out.stdout.decode("utf-8", "replace"))
    except ValueError:
        return None
    if not isinstance(ws, dict):
        return None
    try:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "w", encoding="utf-8") as fh:
            json.dump({"key": key, "t": time.time(), "ws": ws}, fh)
    except OSError:
        pass
    return ws


def log(root, **fields):
    """One JSON line per decision. Never the prompt text: only the first two command words."""
    fields["t"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        with open(os.path.join(root, "_router.log"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(fields, ensure_ascii=False) + "\n")
    except OSError:
        pass
