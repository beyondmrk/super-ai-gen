"""Higgsfield account router (hf-accounts).

Runs in place of the Higgsfield CLI binary: vendor\\hf.exe is a small launcher (hfstub.cs) that
hands every call here, and the real binary sits beside it as hf.real.exe. For each call this
decides which client login it must use, refuses what it cannot place safely, and runs the real
binary with USERPROFILE and HOME pointed at that login's folder.

Exit code: the real binary's own, or 64 when the router refuses.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfacct  # noqa: E402

REFUSED = 64


def refuse(message, root=None, **fields):
    sys.stderr.write("HF ROUTER: refused. %s\n" % message)
    sys.stderr.flush()
    if root:
        hfacct.log(root, decision="refused", reason=message, **fields)
    return REFUSED


def _job(cwd):
    try:
        return hfacct.job_of(cwd)
    except Exception:
        return None  # unreadable domain rules: treated as "no job", so a spend is refused


def _handle(stream, fallback):
    try:
        stream.fileno()
        return stream
    except Exception:
        return fallback


def run_real(real, raw, env):
    """Run the real binary with the ORIGINAL command-line text; never re-quote it."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    done = subprocess.run('"%s" %s' % (real, raw), env=env,
                          stdin=_handle(sys.stdin, subprocess.DEVNULL),
                          stdout=_handle(sys.stdout, None), stderr=_handle(sys.stderr, None))
    return done.returncode


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    vendor = os.environ.get("HF_ROUTER_VENDOR") or hfacct.REAL_VENDOR
    real = os.path.join(vendor, "hf.real.exe")
    raw = os.environ.get("HF_ROUTER_RAW")
    if raw is None:
        raw = subprocess.list2cmdline(argv)
    root = hfacct.logins_root(vendor)
    cwd = os.getcwd()
    kind = hfacct.classify(argv)
    words = [a for a in argv if not a.startswith("-")][:2]
    info = {"kind": kind, "cmd": " ".join(words), "cwd": cwd}

    if not os.path.isfile(real):
        return refuse("The real Higgsfield binary is missing (%s). A Higgsfield update may have "
                      "replaced the router: run the hf-accounts install." % real)
    try:
        reg = hfacct.load_registry(root)
    except Exception as exc:
        return refuse("Cannot read the login registry %s (%s)."
                      % (os.path.join(root, "logins.json"), exc))

    env = {k: v for k, v in os.environ.items() if not k.startswith("HF_ROUTER_")}
    try:
        name = hfacct.resolve(env, cwd, reg, root)
    except hfacct.Refusal as exc:
        return refuse("%s Fix that, or name the client explicitly with HF_CLIENT=<login>." % exc,
                      root, **info)
    except Exception as exc:  # domain rules unreadable: never guess an account
        return refuse("Could not classify the folder %s (%s)." % (cwd, exc), root, **info)

    if name is None:
        if reg.get("unmatched", "refuse") == "default":
            # Phase A: a call in an AGENCY folder that names no client keeps today's behaviour. A spend
            # from a folder that belongs to neither job (a skill folder, say) is refused: it could be
            # EMPLOYER work that cd'd out of D:\Employer, and the default login is not EMPLOYER's. Once stamps are
            # required a spend must name its client, since the default login has no stamp.
            if kind != "spend" or (_job(cwd) == "AGENCY" and not reg.get("require_stamp")):
                hfacct.log(root, decision="default", **info)
                return run_real(real, raw, env)
        else:
            import strict  # noqa: E402  (loaded only under the switch-over flags)

            if kind == "agnostic":
                first = strict.first_signed_in(reg, root)
                if first:
                    env = strict.login_env(env, hfacct.login_home(root, first, reg["logins"][first]))
                hfacct.log(root, decision="agnostic", login=first, **info)
                return run_real(real, raw, env)
            if strict.is_fanout(argv):
                hfacct.log(root, decision="fanout", **info)
                return strict.fan_out(reg, root, raw, env, lambda r, e: run_real(real, r, e))
        return refuse("No client login is identified for this call (folder %s). Set HF_CLIENT to "
                      "one of: %s." % (cwd, ", ".join(sorted(reg["logins"]))), root, **info)

    entry = reg["logins"][name]
    home = hfacct.login_home(root, name, entry)
    if kind not in ("agnostic", "setup"):
        ws = hfacct.selected_workspace(real, home, root, name)
        if not ws or ws.get("id") != entry["workspace_id"]:
            got = "%s (%s)" % (ws.get("name"), ws.get("id")) if ws else "unknown (not signed in?)"
            return refuse("Login %s is on workspace %s but must be %s (%s). Do not switch it "
                          "yourself: tell the operator." % (name, got, entry.get("workspace_name"),
                                                      entry["workspace_id"]),
                          root, login=name, **info)
    if kind == "spend" and reg.get("require_stamp"):
        import strict  # noqa: E402  (loaded only under the switch-over flags)

        problem = strict.stamp_problem(reg, root, name, vendor)
        if problem:
            return refuse(problem, root, login=name, **info)

    env["USERPROFILE"] = home
    env["HOME"] = home
    hfacct.log(root, decision="routed", login=name, **info)
    return run_real(real, raw, env)


if __name__ == "__main__":
    sys.exit(main())
