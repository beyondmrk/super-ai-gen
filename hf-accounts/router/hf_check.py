"""The account check (hf-accounts): may this login spend right now? Stamps it for 3 h on a pass.

  python hf_check.py <login>                     check one CLI login; write its stamp on a pass
  python hf_check.py --connector --client K --workspace-id ID
                                                 the Higgsfield connector (MCP) is on workspace ID:
                                                 stamp it if that is client K's workspace
  test options, only with a test --vendor:       --root DIR --default-home DIR --clients FILE
                                                 --scan-root DIR --agency-root DIR (repeatable)

Checks (any FAIL withholds the stamp and removes an older one; a WARN is shown, not blocking):
  install    launcher, real binary and every router module match the install manifest
  account    `account status` email is the registry account (WARN while the registry has none)
  workspace  `workspace status` id is the registry workspace_id (ids only: names repeat)
  default    refuse mode only: the default profile is signed out
  scan       nothing outside this skill references hf.real.exe or holds a Higgsfield binary (FAIL);
             AGENCY code that calls the CLI without HF_CLIENT (WARN)
  clients    an AGENCY login agrees with AGENCY's clients.json (account when both are set, workspace_id)
The account queries run the real binary directly on the login's folder, not through the router,
so the router's own decisions are not what vouches for the login.

Exit: 0 pass (stamp written), 1 a check failed (no stamp), 2 the check could not run.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hfacct  # noqa: E402
import install  # noqa: E402
import stamps  # noqa: E402
import strict  # noqa: E402

SKILL_DIR = os.path.dirname(HERE)
CLIENTS = os.environ.get("SUPER_AI_GEN_CLIENTS") or os.path.join(SKILL_DIR, os.pardir, "clients.json")
AGENCY_ROOTS = []  # your own code roots; or pass --agency-root
OTHER_ROOTS = [os.path.join(hfacct.HOME, ".claude", "skills"), hfacct.HOOKS_DIR]
CODE_EXTS = {".py", ".js", ".mjs", ".cjs", ".ts", ".ps1", ".psm1", ".sh", ".bash", ".cmd", ".bat"}
TEXT_EXTS = CODE_EXTS | {".md", ".json"}
BINARIES = {"hf.exe", "hf.real.exe", "higgsfield.exe"}
SKIP_DIRS = {".git", "__pycache__"}
MAX_TEXT = 5_000_000
REAL_RE = re.compile(r"hf\.real\.exe", re.I)
CALL_RE = re.compile(r"""vendor[\\/]+hf\.exe|which\(\s*["']higgsfield""", re.I)
PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"


def query(real, home, words):
    """`<words> --json` on the real binary as the login whose folder is `home`: (dict, "") or (None, why)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("HF_ROUTER_") and k != "HF_CLIENT"}
    env["USERPROFILE"] = home
    env["HOME"] = home
    try:
        p = subprocess.run([real] + words + ["--json"], env=env, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    text = p.stdout.decode("utf-8", "replace").strip()
    if p.returncode != 0:
        err = (p.stderr.decode("utf-8", "replace").strip() or text).splitlines()
        return None, (err[-1][:200] if err else "exit %d" % p.returncode)
    try:
        body = json.loads(text)
    except ValueError:
        return None, "unreadable output"
    return (body, "") if isinstance(body, dict) else (None, "unexpected output")


def check_install(vendor, root):
    bad = [label for label, ok in install.status(vendor, root).items() if not ok]
    if bad:
        return [(FAIL, "install", "NO: %s. After a Higgsfield CLI update run install.py install."
                 % "; ".join(bad))]
    return [(PASS, "install", "launcher, real binary and router modules match the install manifest")]


def check_login(real, root, name, entry):
    home = hfacct.login_home(root, name, entry)
    rows = []
    acct, why = query(real, home, ["account", "status"])
    want = (entry.get("account") or "").strip()
    if acct is None:
        rows.append((FAIL, "account", "account status failed in %s (%s): signed out?" % (home, why)))
    else:
        email = (acct.get("email") or "").strip()
        if not want:
            rows.append((WARN, "account", "the registry has no account for %s; this login is %s"
                         % (name, email or "(no email)")))
        elif email.lower() != want.lower():
            rows.append((FAIL, "account", "signed in as %s, the registry says %s. Do not sign in or "
                         "out yourself: tell the operator." % (email or "(no email)", want)))
        else:
            rows.append((PASS, "account", email))
    ws, why = query(real, home, ["workspace", "status"])
    if ws is None:
        rows.append((FAIL, "workspace", "workspace status failed in %s (%s)" % (home, why)))
    elif ws.get("id") != entry["workspace_id"]:
        rows.append((FAIL, "workspace", "the login is on %s (%s), the registry says %s (%s). Do not "
                     "switch it yourself: tell the operator." % (ws.get("name"), ws.get("id"),
                                                          entry.get("workspace_name"), entry["workspace_id"])))
    else:
        rows.append((PASS, "workspace", "%s (%s)" % (entry.get("workspace_name"), ws.get("id"))))
    return rows


def check_default(real, reg, default_home):
    mode = reg.get("unmatched", "refuse")
    if mode != "refuse":
        return [(SKIP, "default", 'unmatched is "%s": the default login stays signed in until the '
                 "switch-over" % mode)]
    acct, _ = query(real, default_home, ["account", "status"])
    if acct is None:
        return [(PASS, "default", "the default profile %s is signed out" % default_home)]
    return [(FAIL, "default", "the default profile %s is still signed in (%s); in refuse mode it must be "
             "signed out. the operator does that, never Claude." % (default_home, acct.get("email")))]


def _line(text, pos):
    return text.count("\n", 0, pos) + 1


def scan(roots, agency_roots, skip=SKILL_DIR):
    """Router bypasses under the scan roots. AGENCY roots also get the HF_CLIENT call-site check."""
    rows, seen, skip_n = [], set(), hfacct.norm(skip)
    for top, agency in [(r, True) for r in agency_roots] + [(r, False) for r in roots]:
        if not os.path.isdir(top):
            continue
        for dirpath, dirnames, filenames in os.walk(top):
            here = hfacct.norm(dirpath)
            if here == skip_n or here.startswith(skip_n + os.sep):
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                path = os.path.join(dirpath, fn)
                key = os.path.join(here, fn.lower())
                if key in seen:
                    continue
                seen.add(key)
                low = fn.lower()
                if low in BINARIES:
                    rows.append((FAIL, "scan", "%s is a Higgsfield binary outside the npm install; calls "
                                 "through it skip the router" % path))
                    continue
                ext = os.path.splitext(low)[1]
                if ext not in TEXT_EXTS:
                    continue
                try:
                    if os.path.getsize(path) > MAX_TEXT:
                        continue
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        text = fh.read()
                except OSError:
                    continue
                hit = REAL_RE.search(text)
                if hit:
                    rows.append((FAIL, "scan", "%s:%d references hf.real.exe, which skips the router"
                                 % (path, _line(text, hit.start()))))
                    continue
                call = CALL_RE.search(text) if agency and ext in CODE_EXTS else None
                if call and "HF_CLIENT" not in text:
                    rows.append((WARN, "scan", "%s:%d calls the Higgsfield CLI without setting HF_CLIENT"
                                 % (path, _line(text, call.start()))))
    if not rows:
        rows.append((PASS, "scan", "no hf.real.exe references or stray binaries; AGENCY call sites name HF_CLIENT"))
    return rows


def load_clients(path):
    with open(path, encoding="utf-8") as fh:
        clients = json.load(fh)
    if not isinstance(clients, dict):
        raise ValueError("not a JSON object")
    return clients


def check_clients(name, entry, clients_path):
    if entry["domain"] != "AGENCY":
        return [(SKIP, "clients", "an EMPLOYER login: clients.json is AGENCY's registry")]
    try:
        client = load_clients(clients_path).get(name)
    except (OSError, ValueError) as exc:
        return [(FAIL, "clients", "cannot read %s (%s)" % (clients_path, exc))]
    if not isinstance(client, dict):
        return [(FAIL, "clients", "%s has no entry %r, so AGENCY scripts cannot confirm this login"
                 % (clients_path, name))]
    rows = []
    theirs, ours = (client.get("account") or "").strip(), (entry.get("account") or "").strip()
    if theirs and ours and theirs.lower() != ours.lower():
        rows.append((FAIL, "clients", "account: clients.json says %s, the registry says %s" % (theirs, ours)))
    if (client.get("workspace_id") or "") != entry["workspace_id"]:
        rows.append((FAIL, "clients", "workspace_id: clients.json says %s, the registry says %s"
                     % (client.get("workspace_id") or "(none)", entry["workspace_id"])))
    if not rows:
        what = "account and workspace_id" if theirs and ours else "workspace_id (an account is blank)"
        rows.append((PASS, "clients", "clients.json %s agrees on %s" % (name, what)))
    return rows


def report(title, rows, out):
    out.write("%s\n" % title)
    for result, check, detail in rows:
        out.write("  %-4s  %-9s  %s\n" % (result, check, detail))


def check_connector(root, reg, clients_path, client, workspace_id, out):
    """Stamp the connector if its selected workspace is the one on record for `client`."""
    client, wid = client.strip().lower(), workspace_id.strip().lower()
    known, rows = {}, []
    entry = reg["logins"].get(client)
    if entry:
        known["logins.json"] = entry["workspace_id"].lower()
    if not entry or entry["domain"] == "AGENCY":  # clients.json is AGENCY's: an EMPLOYER check never reads it
        try:
            theirs = load_clients(clients_path).get(client)
        except (OSError, ValueError) as exc:
            theirs = None
            rows.append((FAIL, "clients", "cannot read %s (%s)" % (clients_path, exc)))
        if isinstance(theirs, dict):
            known["clients.json"] = (theirs.get("workspace_id") or "").lower()
    if not known and not rows:
        rows.append((FAIL, "client", "%r is neither a registered login nor in clients.json" % client))
    for source, value in sorted(known.items()):
        if not value:
            rows.append((FAIL, "client", "%s has no workspace_id for %s" % (source, client)))
    if len(set(known.values())) > 1:
        rows.append((FAIL, "client", "logins.json and clients.json disagree on %s's workspace" % client))
    if not hfacct.UUID_RE.match(wid):
        rows.append((FAIL, "connector", "%r is not a workspace id" % workspace_id))
    if not rows:
        expected = next(iter(known.values()))
        if wid != expected:
            rows.append((FAIL, "connector", "the connector is on workspace %s, client %s needs %s. Do "
                         "not switch it yourself: tell the operator." % (wid, client, expected)))
        else:
            rows.append((PASS, "connector", "the connector is on %s's workspace %s" % (client, wid)))
    report("hf_check --connector %s  (registry %s)" % (client, root), rows, out)
    if any(r[0] == FAIL for r in rows):
        removed = stamps.clear(root, stamps.CONNECTOR)
        out.write("RESULT: FAIL - no connector stamp%s\n" % ("; the older one was removed" if removed else ""))
        return 1
    domain = entry["domain"] if entry else "AGENCY"
    path = stamps.write_connector(root, client, domain, wid)
    out.write("RESULT: PASS - connector stamp %s, valid %g h\n" % (path, strict.stamp_hours(reg)))
    return 0


def usage(message):
    sys.stderr.write("hf_check: %s\n" % message)
    return 2


def main(argv=None, out=None):
    out = out or sys.stdout
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("login", nargs="?")
    ap.add_argument("--connector", action="store_true")
    ap.add_argument("--client")
    ap.add_argument("--workspace-id")
    ap.add_argument("--vendor", default=hfacct.REAL_VENDOR)
    ap.add_argument("--root")
    ap.add_argument("--default-home")
    ap.add_argument("--clients")
    ap.add_argument("--scan-root", action="append")
    ap.add_argument("--agency-root", action="append")
    a = ap.parse_args(argv)

    # Test options exist so tests never touch real logins. With the real vendor they are refused,
    # and a test vendor never stamps the real registry: a stamp there always means the real binary.
    test_vendor = hfacct.norm(a.vendor) != hfacct.norm(hfacct.REAL_VENDOR)
    given = [o for o in ("root", "default_home", "clients", "scan_root", "agency_root") if getattr(a, o) is not None]
    if given and not test_vendor:
        return usage("--%s is a test option and needs a test --vendor" % given[0].replace("_", "-"))
    root = a.root or hfacct.logins_root(a.vendor)
    if test_vendor and hfacct.norm(root) == hfacct.norm(hfacct.DEFAULT_ROOT):
        return usage("a test --vendor needs its own --root; it never writes the real registry's stamps")
    try:
        reg = hfacct.load_registry(root)
    except Exception as exc:
        return usage("cannot read the registry %s (%s)" % (os.path.join(root, "logins.json"), exc))
    clients_path = a.clients or CLIENTS

    if a.connector:
        if not a.client or not a.workspace_id:
            return usage("--connector needs --client K and --workspace-id ID")
        return check_connector(root, reg, clients_path, a.client, a.workspace_id, out)

    name = (a.login or "").strip().lower()
    if name not in reg["logins"]:
        return usage("%r is not a registered login (registered: %s)" % (a.login, ", ".join(sorted(reg["logins"]))))
    entry = reg["logins"][name]
    real = os.path.join(a.vendor, "hf.real.exe")
    if a.scan_root is None and a.agency_root is None:
        scan_roots, agency_roots = OTHER_ROOTS, AGENCY_ROOTS
    else:
        scan_roots, agency_roots = a.scan_root or [], a.agency_root or []

    before = stamps.fingerprint(root, name, entry, a.vendor)
    rows = check_install(a.vendor, root)
    rows += check_login(real, root, name, entry)
    rows += check_default(real, reg, a.default_home or hfacct.HOME)
    rows += scan(scan_roots, agency_roots)
    rows += check_clients(name, entry, clients_path)
    after = stamps.fingerprint(root, name, entry, a.vendor)
    moved = sorted(k for k in after if after[k] != before.get(k))
    if moved:
        rows.append((FAIL, "stable", "changed while checking: %s. Run the check again." % ", ".join(moved)))

    report("hf_check %s  (registry %s)" % (name, root), rows, out)
    fails = sum(1 for r in rows if r[0] == FAIL)
    if fails:
        removed = stamps.clear(root, name)
        out.write("RESULT: FAIL (%d) - no stamp%s\n" % (fails, "; the older one was removed" if removed else ""))
        return 1
    hours = strict.stamp_hours(reg)
    path = stamps.write(root, name, after, [list(r) for r in rows])
    out.write("RESULT: PASS - stamp %s, valid %g h (until %s)\n"
              % (path, hours, time.strftime("%H:%M", time.localtime(time.time() + hours * 3600))))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    sys.exit(main())
