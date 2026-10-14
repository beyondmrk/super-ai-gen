"""Build and install the hf-accounts router in place of the Higgsfield CLI binary.

  python install.py status              what is installed now
  python install.py install             build the launcher, move the real hf.exe to hf.real.exe
                                        and put the launcher in its place. Safe to rerun: after a
                                        Higgsfield update it wraps the new binary again.
  python install.py uninstall           put the real hf.exe back
  --vendor DIR                          a different vendor folder (tests); default is the real one
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hfacct  # noqa: E402

CSC = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Microsoft.NET", "Framework64",
                   "v4.0.30319", "csc.exe")
ROUTER = os.path.join(HERE, "router.py")
HFACCT = os.path.join(HERE, "hfacct.py")
# Every module the router can load, so a check stamp and `status` cover the whole build.
MODULES = [os.path.join(HERE, m) for m in ("router.py", "hfacct.py", "stamps.py", "strict.py")]
TEMPLATE = os.path.join(HERE, "hfstub.cs")
REAL_MIN_BYTES = 1_000_000  # the real CLI is ~19 MB, the launcher a few KB: never wrap the launcher


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def compile_cs(source_text, out_exe):
    os.makedirs(os.path.dirname(out_exe), exist_ok=True)
    src = out_exe[:-4] + ".gen.cs"
    with open(src, "w", encoding="utf-8") as fh:
        fh.write(source_text)
    done = subprocess.run([CSC, "/nologo", "/target:exe", "/optimize+", "/out:" + out_exe, src],
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit("csc failed:\n" + done.stdout + done.stderr)
    return out_exe


def stub_source(python=sys.executable, router=ROUTER):
    with open(TEMPLATE, encoding="utf-8") as fh:
        return fh.read().replace("__PYTHON__", python).replace("__ROUTER__", router)


def build_stub(out_dir, python=sys.executable, router=ROUTER):
    return compile_cs(stub_source(python, router), os.path.join(out_dir, "hfstub.exe"))


def _paths(vendor):
    return os.path.join(vendor, "hf.exe"), os.path.join(vendor, "hf.real.exe")


def _manifest_path(vendor, root=None):
    return os.path.join(root or hfacct.logins_root(vendor), "_install.json")


def read_manifest(vendor, root=None):
    try:
        with open(_manifest_path(vendor, root), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def module_hashes():
    """Manifest keys for the router modules: router_sha256, hfacct_sha256, stamps_sha256, ..."""
    return {os.path.basename(p)[:-3] + "_sha256": sha256(p) for p in MODULES}


def install(vendor):
    hf, real = _paths(vendor)
    root = hfacct.logins_root(vendor)
    old = read_manifest(vendor)
    src_hash = hashlib.sha256(stub_source().encode("utf-8")).hexdigest()
    # The launcher only changes when its source or paths change; a router.py edit needs no new
    # launcher, and replacing hf.exe while a generation runs through it would fail.
    launcher_current = (os.path.isfile(hf) and os.path.getsize(hf) < REAL_MIN_BYTES
                        and sha256(hf) == old.get("stub_sha256")
                        and old.get("stub_source_sha256", src_hash) == src_hash)
    if not launcher_current:
        stub = build_stub(os.path.join(HERE, "build"))
        if os.path.isfile(hf) and os.path.getsize(hf) > REAL_MIN_BYTES:
            # hf.exe is a real Higgsfield binary: first install, or an update put a new one back.
            backup = os.path.join(root, "_backup", "hf.%s.exe" % sha256(hf)[:12])
            if not os.path.isfile(backup):
                os.makedirs(os.path.dirname(backup), exist_ok=True)
                shutil.copy2(hf, backup)
            try:
                os.replace(hf, real)
            except PermissionError:
                raise SystemExit("hf.exe is in use. Wait until no Higgsfield command is running, then rerun.")
    if not os.path.isfile(real) or os.path.getsize(real) < REAL_MIN_BYTES:
        raise SystemExit("No real Higgsfield binary at %s. Reinstall @higgsfield/cli first." % real)
    if not launcher_current:
        try:
            shutil.copy2(stub, hf)
        except PermissionError:
            raise SystemExit("hf.exe is in use. Wait until no Higgsfield command is running, then rerun.")
    manifest = {
        "stub_source_sha256": src_hash,
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "vendor": vendor,
        "python": sys.executable,
        "stub_sha256": sha256(hf),
        "real_sha256": sha256(real),
    }
    manifest.update(module_hashes())
    os.makedirs(root, exist_ok=True)
    with open(_manifest_path(vendor), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return manifest


def uninstall(vendor):
    hf, real = _paths(vendor)
    if not os.path.isfile(real):
        raise SystemExit("Nothing to restore: %s is missing." % real)
    os.replace(real, hf)
    try:
        os.remove(_manifest_path(vendor))
    except OSError:
        pass


def status(vendor, root=None):
    hf, real = _paths(vendor)
    m = read_manifest(vendor, root)
    rows = {
        "hf.exe is the router launcher": os.path.isfile(hf) and sha256(hf) == m.get("stub_sha256"),
        "hf.real.exe present": os.path.isfile(real) and os.path.getsize(real) > REAL_MIN_BYTES,
        "hf.real.exe unchanged since install": os.path.isfile(real) and sha256(real) == m.get("real_sha256"),
    }
    for key, digest in module_hashes().items():
        rows["%s.py unchanged since install" % key[:-len("_sha256")]] = digest == m.get(key)
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["status", "install", "uninstall"])
    ap.add_argument("--vendor", default=hfacct.REAL_VENDOR)
    a = ap.parse_args(argv)
    if a.action == "install":
        m = install(a.vendor)
        print("installed: launcher %s..., real %s..." % (m["stub_sha256"][:12], m["real_sha256"][:12]))
    elif a.action == "uninstall":
        uninstall(a.vendor)
        print("restored the real hf.exe")
    rows = status(a.vendor)
    for label, ok in rows.items():
        print("%-40s %s" % (label, "yes" if ok else "NO"))
    return 0 if all(rows.values()) or a.action == "uninstall" else 1


if __name__ == "__main__":
    sys.exit(main())
