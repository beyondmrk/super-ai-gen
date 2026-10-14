"""Router behaviour behind the switch-over flags (hf-accounts, Phase B).

router.py imports this module only when the registry has `require_stamp` true or `unmatched`
"refuse". Under the Phase A flags it is never even loaded, so nothing here can change a live call
until the operator flips the flags (plan step B6).
"""
import os
import sys

import hfacct
import stamps

# Account-specific but read-only: with no client named they show every login side by side.
FANOUT = {("workspace", "status"), ("workspace", "list"), ("ws", "status"), ("ws", "list"),
          ("account", "status")}


def stamp_hours(reg):
    try:
        hours = float(reg.get("stamp_hours") or 3)
    except (TypeError, ValueError):
        hours = 3.0
    return hours if hours > 0 else 3.0


def stamp_problem(reg, root, name, vendor):
    """None when the login's check stamp is valid, else the refusal text (it says what to do)."""
    ok, why = stamps.valid(root, name, reg["logins"][name], vendor, stamp_hours(reg))
    if ok:
        return None
    return ("Run the account check first: login %s has no valid check stamp (%s). Dispatch the "
            "hf-account-checker subagent for login %s, then retry this call." % (name, why, name))


def is_fanout(argv):
    words = [a.lower() for a in argv if not a.startswith("-")][:2]
    return tuple(words) in FANOUT


def signed_in(root, name, entry):
    """A login folder that holds CLI credentials (only the file's presence is looked at)."""
    home = hfacct.login_home(root, name, entry)
    return os.path.isfile(os.path.join(home, ".config", "higgsfield", "credentials.json"))


def first_signed_in(reg, root):
    """Where an account-agnostic call (help, version, model lists) runs once the default login is
    signed out: the first registered login, by name, that is signed in. None if there is none."""
    for name in sorted(reg["logins"]):
        if signed_in(root, name, reg["logins"][name]):
            return name
    return None


def login_env(env, home):
    out = dict(env)
    out["USERPROFILE"] = home
    out["HOME"] = home
    return out


def fan_out(reg, root, raw, env, run, out=None):
    """Run one read-only account command once per login, each under an '== name ==' header.

    `run(raw, env)` runs the real binary and returns its exit code. Returns 0 when every login
    answered, else the first failing exit code.
    """
    out = out or sys.stdout
    rc = 0
    for name in sorted(reg["logins"]):
        out.write("== %s ==\n" % name)
        out.flush()
        code = run(raw, login_env(env, hfacct.login_home(root, name, reg["logins"][name])))
        rc = rc or code
    return rc
