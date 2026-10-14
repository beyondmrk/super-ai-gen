"""hf-accounts: see every Higgsfield login side by side, add one, finish one.

  python accounts.py status              every login: registered vs selected workspace, check stamp,
                                         connector stamp, install rows
  python accounts.py add <name>          registry entry + <name>-login.cmd. An AGENCY client in
                                         clients.json brings its account and workspace; anything
                                         else needs --domain --account --workspace-id --workspace-name.
                                         --folder PATTERN (repeatable) routes that folder to it.
  python accounts.py finish <name>       after the operator signed in with the launcher: `workspace set` to the
                                         REGISTERED workspace through the router, then the account check
  python accounts.py launcher <name>     (re)write <name>-login.cmd in the HF_CLIENT form
  test options, only with a test --vendor: --root --clients (finish also takes hf_check's)

Nothing here signs in or out. `finish` selects only the workspace the registry already names, for a
login the operator has just signed in himself; it is the operator's step, never one Claude takes on its own.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hf_check  # noqa: E402
import hfacct  # noqa: E402
import install  # noqa: E402
import stamps  # noqa: E402
import strict  # noqa: E402

LAUNCHER = r"""@echo off
rem Signs Higgsfield into the {name} login ({domain}) inside its own folder, through the
rem hf-accounts router: HF_CLIENT picks the login, so no other login is touched.
rem Written by accounts.py; rerun `accounts.py launcher {name}` rather than editing it.
set "HF_CLIENT={name}"
if not exist "{home}" mkdir "{home}"
cd /d "{root}"
echo.
echo Sign in with {who} in the browser that opens.
echo (If the browser is signed into Higgsfield as another account, sign out there first.)
echo.
call higgsfield auth login
echo.
echo Workspaces on this login. The registered one is {ws_name} ({ws_id}):
call higgsfield workspace list
echo.
echo Next: python "{accounts}" finish {name}
echo.
pause
"""


class Stop(Exception):
    """A request this tool will not carry out; the message says why and what to do instead."""


def write_launcher(root, name, entry):
    if entry.get("home"):
        raise Stop("%s signs in to %s (its registry 'home'), so an HF_CLIENT launcher would sign in "
                   "that folder; keep its USERPROFILE-form launcher until 'home' is removed (plan B1)."
                   % (name, entry["home"]))
    path = os.path.join(root, "%s-login.cmd" % name)
    text = LAUNCHER.format(name=name, domain=entry["domain"], root=root,
                           home=hfacct.login_home(root, name, entry),
                           who=entry.get("account") or "the %s account" % name,
                           ws_name=entry.get("workspace_name") or "?", ws_id=entry["workspace_id"],
                           accounts=os.path.join(HERE, "accounts.py"))
    with open(path, "w", encoding="ascii", newline="\r\n") as fh:
        fh.write(text)
    return path


def status(vendor, root, out):
    reg = hfacct.load_registry(root)
    real = os.path.join(vendor, "hf.real.exe")
    hours = strict.stamp_hours(reg)
    out.write("registry %s  unmatched=%s  require_stamp=%s  stamp_hours=%g\n\n"
              % (os.path.join(root, "logins.json"), reg.get("unmatched", "refuse"),
                 bool(reg.get("require_stamp")), hours))
    out.write("%-10s %-4s %-30s %-26s %-14s %s\n"
              % ("LOGIN", "JOB", "ACCOUNT", "REGISTERED WORKSPACE", "SELECTED NOW", "CHECK STAMP"))
    for name in sorted(reg["logins"]):
        entry = reg["logins"][name]
        home = hfacct.login_home(root, name, entry)
        if not strict.signed_in(root, name, entry):
            now = "signed out"
        else:
            ws = hfacct.selected_workspace(real, home, root, name)
            now = ("unknown" if not ws else "match" if ws.get("id") == entry["workspace_id"]
                   else "OTHER " + str(ws.get("id"))[:8])
        ok, why = stamps.valid(root, name, entry, vendor, hours)
        out.write("%-10s %-4s %-30s %-26s %-14s %s\n"
                  % (name, entry["domain"], entry.get("account") or "(none)",
                     ("%s %s" % (entry.get("workspace_name"), entry["workspace_id"][:8]))[:26], now,
                     ("valid, " if ok else "NOT valid: ") + why))
        if entry.get("home"):
            out.write("%-10s home: %s\n" % ("", entry["home"]))
    ok, why = stamps.connector_valid(root, hours)
    out.write("\nconnector stamp: %s%s\n\ninstall:\n" % ("valid, " if ok else "NOT valid: ", why))
    for label, good in install.status(vendor, root).items():
        out.write("  %-40s %s\n" % (label, "yes" if good else "NO"))
    return 0


def add(root, clients_path, a, out):
    name = a.name.strip().lower()
    if not hfacct.NAME_RE.match(name) or name == stamps.CONNECTOR:
        raise Stop("%r cannot be a login name (lowercase letters, digits, - and _; not 'connector')." % a.name)
    reg = hfacct.load_registry(root)
    if name in reg["logins"]:
        raise Stop("%s is already registered; edit logins.json by hand to change it." % name)
    given = {"domain": a.domain, "account": a.account, "workspace_id": a.workspace_id,
             "workspace_name": a.workspace_name}
    client = None
    if a.domain != "EMPLOYER":
        try:
            client = hf_check.load_clients(clients_path).get(name)
        except (OSError, ValueError):
            client = None
    if isinstance(client, dict):
        if client.get("engine") != "higgsfield-cli":
            raise Stop("clients.json runs %s on %s, not the Higgsfield CLI." % (name, client.get("engine")))
        vals = {"domain": "AGENCY", "account": client.get("account") or "",
                "workspace_id": client.get("workspace_id") or "",
                "workspace_name": client.get("workspace_name") or ""}
        clash = [k for k, v in given.items() if v is not None and v.strip().lower() != vals[k].lower()]
        if clash:
            raise Stop("clients.json disagrees on %s; fix clients.json or drop the option." % ", ".join(clash))
    else:
        missing = [k for k in ("domain", "workspace_id", "workspace_name") if not given[k]]
        if missing:
            raise Stop("%s is not a Higgsfield CLI client in clients.json, so give --%s."
                       % (name, " --".join(k.replace("_", "-") for k in missing)))
        vals = {k: (v or "").strip() for k, v in given.items()}
    vals["workspace_id"] = vals["workspace_id"].lower()
    if not hfacct.UUID_RE.match(vals["workspace_id"]):
        raise Stop("%r is not a workspace id." % vals["workspace_id"])
    if vals["domain"] == "EMPLOYER" and any(e["domain"] == "EMPLOYER" for e in reg["logins"].values()):
        raise Stop("the registry already has an EMPLOYER login, and EMPLOYER folders need exactly one.")
    entry = dict(vals, folders=list(a.folder or []))

    path = os.path.join(root, "logins.json")
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    raw["logins"][name] = entry
    pending = os.path.join(root, "_pending")
    os.makedirs(pending, exist_ok=True)
    with open(os.path.join(pending, "logins.json"), "w", encoding="utf-8") as fh:
        json.dump(raw, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    hfacct.load_registry(pending)  # the router's own rules must accept it before it goes live
    os.replace(os.path.join(pending, "logins.json"), path)
    shutil.rmtree(pending, ignore_errors=True)
    os.makedirs(hfacct.login_home(root, name, entry), exist_ok=True)
    launcher = write_launcher(root, name, entry)
    out.write("added %s (%s, %s, %s %s) to %s\n" % (name, entry["domain"], entry["account"] or "no account",
                                                   entry["workspace_name"], entry["workspace_id"], path))
    if not entry["folders"]:
        out.write("no --folder given: calls reach %s only with HF_CLIENT=%s\n" % (name, name))
    out.write("the registry changed, so every check stamp is void until each login is checked again\n"
              "next: 1. the operator double-clicks %s and signs in\n"
              "      2. python \"%s\" finish %s\n" % (launcher, os.path.join(HERE, "accounts.py"), name))
    return 0


def finish(vendor, root, name, check_args, test_vendor, out):
    reg = hfacct.load_registry(root)
    if name not in reg["logins"]:
        raise Stop("%s is not a registered login." % name)
    entry = reg["logins"][name]
    if not strict.signed_in(root, name, entry):
        raise Stop("%s is not signed in yet: the operator runs %s first."
                   % (name, os.path.join(root, "%s-login.cmd" % name)))
    env = {k: v for k, v in os.environ.items() if not k.startswith("HF_ROUTER_")}
    env["HF_CLIENT"] = name
    if test_vendor:
        env["HF_LOGINS_ROOT"] = root
    # Through the router (vendor\hf.exe), from the neutral registry folder, like the launcher.
    p = subprocess.run([os.path.join(vendor, "hf.exe"), "workspace", "set", entry["workspace_id"]],
                       cwd=root, env=env, capture_output=True, timeout=120)
    out.write(p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace"))
    if p.returncode != 0:
        raise Stop("workspace set failed (exit %d); nothing was checked." % p.returncode)
    return hf_check.main([name, "--vendor", vendor] + check_args, out)


def main(argv=None, out=None):
    out = out or sys.stdout
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--vendor", default=hfacct.REAL_VENDOR)
    common.add_argument("--root")
    common.add_argument("--clients")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", parents=[common])
    p = sub.add_parser("add", parents=[common])
    p.add_argument("name")
    p.add_argument("--domain", choices=["EMPLOYER", "AGENCY"])
    p.add_argument("--account")
    p.add_argument("--workspace-id")
    p.add_argument("--workspace-name")
    p.add_argument("--folder", action="append")
    p = sub.add_parser("finish", parents=[common])
    p.add_argument("name")
    p.add_argument("--default-home")
    p.add_argument("--scan-root", action="append")
    p.add_argument("--agency-root", action="append")
    p = sub.add_parser("launcher", parents=[common])
    p.add_argument("name")
    a = ap.parse_args(argv)

    test_vendor = hfacct.norm(a.vendor) != hfacct.norm(hfacct.REAL_VENDOR)
    given = [o for o in ("root", "clients", "default_home", "scan_root", "agency_root")
             if getattr(a, o, None) is not None]
    if given and not test_vendor:
        return hf_check.usage("--%s is a test option and needs a test --vendor" % given[0].replace("_", "-"))
    root = a.root or hfacct.logins_root(a.vendor)
    if test_vendor and hfacct.norm(root) == hfacct.norm(hfacct.DEFAULT_ROOT):
        return hf_check.usage("a test --vendor needs its own --root; it never writes the real registry")
    try:
        if a.cmd == "status":
            return status(a.vendor, root, out)
        if a.cmd == "add":
            return add(root, a.clients or hf_check.CLIENTS, a, out)
        name = a.name.strip().lower()
        if a.cmd == "launcher":
            reg = hfacct.load_registry(root)
            if name not in reg["logins"]:
                raise Stop("%s is not a registered login." % name)
            out.write("wrote %s\n" % write_launcher(root, name, reg["logins"][name]))
            return 0
        extra = []
        for opt in ("root", "clients", "default_home"):
            if getattr(a, opt, None):
                extra += ["--" + opt.replace("_", "-"), getattr(a, opt)]
        for opt in ("scan_root", "agency_root"):
            for value in getattr(a, opt) or []:
                extra += ["--" + opt.replace("_", "-"), value]
        return finish(a.vendor, root, name, extra, test_vendor, out)
    except Stop as exc:
        return hf_check.usage(str(exc))
    except (OSError, ValueError) as exc:
        return hf_check.usage("%s failed: %s" % (a.cmd, exc))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    sys.exit(main())
