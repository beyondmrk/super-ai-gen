"""Shared test setup: a temp vendor (built launcher + fake real binary) and fake login folders.

Nothing here touches the real Higgsfield install, the real registry or any real login.
"""
import json
import os
import shutil
import sys
import tempfile

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL, "router"))
import install  # noqa: E402

PAD = install.REAL_MIN_BYTES + 1  # the fake gets padded past the size a real CLI binary must have


def temp_dir(prefix):
    return tempfile.mkdtemp(prefix=prefix, dir=os.environ.get("HFACCT_TMP"))


def build_vendor(tmp, name="vendor", real_sized=False):
    """tmp/<name> with hf.exe = the router launcher and hf.real.exe = the fake binary.

    real_sized pads the fake past REAL_MIN_BYTES (a PE file runs fine with trailing bytes), so the
    install status sees a plausible real binary.
    """
    fake = os.path.join(tmp, "fake", "fakehf.exe")
    if not os.path.isfile(fake):
        with open(os.path.join(SKILL, "tests", "fakehf.cs"), encoding="utf-8") as fh:
            install.compile_cs(fh.read(), fake)
    stub = os.path.join(tmp, "build", "hfstub.exe")
    if not os.path.isfile(stub):
        install.build_stub(os.path.join(tmp, "build"))
    vendor = os.path.join(tmp, name)
    os.makedirs(vendor)
    real = os.path.join(vendor, "hf.real.exe")
    shutil.copy2(fake, real)
    if real_sized:
        with open(real, "ab") as fh:
            fh.write(b"\0" * PAD)
    shutil.copy2(stub, os.path.join(vendor, "hf.exe"))
    return vendor


def write_manifest(vendor, root):
    """An install manifest in `root` that matches `vendor` and the router modules as they are."""
    manifest = {"stub_sha256": install.sha256(os.path.join(vendor, "hf.exe")),
                "real_sha256": install.sha256(os.path.join(vendor, "hf.real.exe"))}
    manifest.update(install.module_hashes())
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "_install.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)


def write_registry(root, logins, unmatched="refuse", require_stamp=True, stamp_hours=3):
    os.makedirs(root, exist_ok=True)
    reg = {"version": 1, "unmatched": unmatched, "require_stamp": require_stamp,
           "stamp_hours": stamp_hours, "logins": logins}
    with open(os.path.join(root, "logins.json"), "w", encoding="utf-8") as fh:
        json.dump(reg, fh, indent=2)


def fake_login(home, ws_id=None, email=None, signed_in=True):
    """A login folder the fake binary answers for: fake_ws.json, fake_account.json, config.json and
    (when signed_in) a credentials.json that only has to exist."""
    cfg = os.path.join(home, ".config", "higgsfield")
    os.makedirs(cfg, exist_ok=True)
    if ws_id:
        with open(os.path.join(cfg, "fake_ws.json"), "w", encoding="utf-8") as fh:
            json.dump({"id": ws_id, "name": None, "plan_type": "creator", "credits": 1.0,
                       "is_selected": True, "user_role": "owner"}, fh)
        with open(os.path.join(cfg, "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"workspace_id": ws_id}, fh)
    if email:
        with open(os.path.join(cfg, "fake_account.json"), "w", encoding="utf-8") as fh:
            json.dump({"credits": 1.0, "email": email, "subscription_plan_type": "creator"}, fh)
    if signed_in:
        with open(os.path.join(cfg, "credentials.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")
    return home
