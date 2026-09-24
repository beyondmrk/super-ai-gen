#!/usr/bin/env python3
"""super-ai-gen/prefire.py - the START-OF-GEN gate. Runs BEFORE any credit, on every project.

Three checks (Mark, 2026-09-24), in this order, then the marker:

  1. DRIVE   --drive <Google Drive folder link or id> with EDIT permission. Creatives/ and
             Elements/{Stills,Clips,Audio} must exist in that folder; missing ones are created,
             and a failed create IS the edit-permission test (a view-only share cannot mkdir).
             The same layout is scaffolded locally under --project, and --script is copied
             into Creatives/ (local + Drive).
  2. ACCOUNT Is the Higgsfield CLI installed and signed in? Is the signed-in email the client's
             account, and is the client's workspace the selected one? Any miss = FLAG.
  3. MODELS  Exactly ONE image model and ONE video model, given as --model, both inside the
             client's models_allowed. Missing = the gate prints the ask (models + rates) and
             exits 2. The choice is the editor's, never the script's.

Writes <project>/Creatives/_preflight.json = {client, models, drive_folder_id, rows}. fire.py
reads it as the project's client marker and refuses any model not recorded here.

    python prefire.py --client acme --project "D:/Ads/ACME/09.24.26 - Launch" --drive "https://drive.google.com/drive/folders/<id>" --model nano_banana_flash --model kling3_0 --script "<brief.pdf>"

Exit 0 = gate open. Exit 2 = FLAG(s), do not fire. Exit 3 = config (unknown client, bad args).
Needs: the Higgsfield CLI (npm i -g @higgsfield/cli) and rclone with a Google Drive remote
(default name `gdrive`, override with --rclone-remote). See README.md.
"""
import argparse, datetime, json, os, re, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fire  # noqa: E402  - STILL_MODELS / MOTION_MODELS / print_models / registry path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CANON = ("Creatives", "Elements", "Elements/Stills", "Elements/Clips", "Elements/Audio")
SCRIPT_EXT = {".docx", ".pdf", ".md", ".txt", ".json", ".py", ".rtf"}


class Gate:
    def __init__(self):
        self.rows = []

    def ok(self, item, detail=""):
        self.rows.append(("OK", item, detail))

    def flag(self, item, detail=""):
        self.rows.append(("FLAG", item, detail))

    def note(self, item, detail=""):
        self.rows.append(("NOTE", item, detail))

    @property
    def flagged(self):
        return [r for r in self.rows if r[0] == "FLAG"]


def run(cmd, timeout=120):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except FileNotFoundError:
        return 127, "%s: not found" % cmd[0]
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def jload(out):
    """rclone can prefix NOTICE lines and a BOM; parse from the first bracket."""
    t = (out or "").lstrip("\ufeff")
    i = min([x for x in (t.find("["), t.find("{")) if x >= 0], default=-1)
    if i < 0:
        return []
    try:
        return json.loads(t[i:])
    except Exception:
        return []


# ---------------------------------------------------------------- tools
def hf_bin():
    p = shutil.which("higgsfield") or shutil.which("higgsfield.cmd")
    if p:
        return p
    for cand in (os.path.expandvars(r"%APPDATA%\npm\higgsfield.cmd"),
                 os.path.expandvars(r"%LOCALAPPDATA%\npm\higgsfield.cmd"),
                 os.path.expandvars(r"%APPDATA%\npm\node_modules\@higgsfield\cli\vendor\hf.exe")):
        if os.path.exists(cand):
            return cand
    return None


def rclone_bin():
    p = shutil.which("rclone")
    if p:
        return p
    base = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")
    for root, _d, files in os.walk(base) if os.path.isdir(base) else []:
        if "rclone.exe" in files:
            return os.path.join(root, "rclone.exe")
    return None


# ---------------------------------------------------------------- 1. drive
def drive_id(text):
    """A Drive folder LINK or a bare id -> the id. None when neither."""
    if not text:
        return None
    t = text.strip()
    m = re.search(r"/folders/([A-Za-z0-9_-]{10,})", t) or re.search(r"[?&]id=([A-Za-z0-9_-]{10,})", t)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", t):
        return t
    return None


def check_drive(drive, remote, project, script, g):
    fid = drive_id(drive)
    if not fid:
        g.flag("1. drive", "no Google Drive folder given - pass --drive <folder link with EDIT permission>"
               + ("" if not drive else " (could not read an id from %r)" % drive))
        return None
    rc_ = rclone_bin()
    if not rc_:
        g.flag("1. drive", "rclone is not installed - see README.md (rclone + a Google Drive remote)")
        return fid
    rc, out = run([rc_, "listremotes"], 60)
    if rc != 0 or (remote + ":") not in out:
        g.flag("1. drive", "rclone remote %r is not configured (have: %s) - run `rclone config`" % (remote, out.strip().replace("\n", " ") or "-"))
        return fid
    rc, out = run([rc_, "lsjson", remote + ":", "--drive-root-folder-id", fid, "-R", "--dirs-only"], 180)
    if rc != 0:
        g.flag("1. drive", "folder %s is not readable by the rclone account: %s" % (fid, out.strip()[:200]))
        return fid
    have = {d["Path"].replace("\\", "/") for d in jload(out)}
    created, failed = [], []
    for sub in CANON:
        if sub in have:
            continue
        rc, out = run([rc_, "mkdir", remote + ":" + sub, "--drive-root-folder-id", fid], 120)
        (created if rc == 0 else failed).append(sub if rc == 0 else "%s (%s)" % (sub, out.strip()[-120:]))
    if failed:
        g.flag("1. drive edit permission", "could not create %s - the rclone account needs EDITOR access on this folder"
               % "; ".join(failed))
        return fid
    rc, out = run([rc_, "lsjson", remote + ":", "--drive-root-folder-id", fid, "-R", "--dirs-only"], 180)
    have = {d["Path"].replace("\\", "/") for d in jload(out)} if rc == 0 else set()
    missing = [s for s in CANON if s not in have]
    if missing:
        g.flag("1. drive layout", "still missing after create: %s" % ", ".join(missing))
        return fid
    g.ok("1. drive layout", "%s  Creatives/ + Elements/{Stills,Clips,Audio} present (created: %s)"
         % (fid, ", ".join(created) or "nothing"))
    # script beside the assets, on Drive
    local_script = script
    if not local_script:
        cdir = os.path.join(project, "Creatives")
        cands = [f for f in (os.listdir(cdir) if os.path.isdir(cdir) else [])
                 if os.path.splitext(f)[1].lower() in SCRIPT_EXT and not f.startswith("_")]
        local_script = os.path.join(cdir, cands[0]) if cands else None
    rc, out = run([rc_, "lsjson", remote + ":Creatives", "--drive-root-folder-id", fid], 120)
    on_drive = [f["Name"] for f in jload(out)] if rc == 0 else []
    if local_script and os.path.basename(local_script) not in on_drive:
        rc, out = run([rc_, "copy", local_script, remote + ":Creatives", "--drive-root-folder-id", fid], 300)
        if rc == 0:
            g.ok("1. drive script", "uploaded %s to Creatives/" % os.path.basename(local_script))
        else:
            g.flag("1. drive script", "upload failed: %s" % out.strip()[:200])
    elif local_script:
        g.ok("1. drive script", "%s already in Creatives/" % os.path.basename(local_script))
    else:
        g.note("1. drive script", "no script/brief to upload (pass --script)")
    return fid


def scaffold_local(project, script, g):
    for sub in CANON:
        os.makedirs(os.path.join(project, *sub.split("/")), exist_ok=True)
    if script:
        if not os.path.isfile(script):
            g.flag("1. local script", "--script %s does not exist" % script)
            return
        dest = os.path.join(project, "Creatives", os.path.basename(script))
        if not os.path.exists(dest):
            shutil.copy2(script, dest)
    g.ok("1. local layout", "%s  Creatives/ + Elements/{Stills,Clips,Audio}" % project)


# ---------------------------------------------------------------- 2. account
def check_account(client, g):
    """Installed? Signed in? The client's email? The client's workspace selected?"""
    if client.get("engine") != "higgsfield-cli":
        g.flag("2. account", "client engine is %r - this skill fires the Higgsfield CLI only" % client.get("engine"))
        return
    hf = hf_bin()
    if not hf:
        g.flag("2. higgsfield cli", "not installed / not on PATH - `npm i -g @higgsfield/cli`, then `higgsfield auth login`")
        return
    rc, out = run([hf, "account", "status"], 90)
    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", out or "")
    email = email.group(0).lower() if email else None
    if rc != 0 or not email:
        g.flag("2. higgsfield cli", "installed but not signed in (`higgsfield auth login`): %s" % (out or "").strip()[:200])
        return
    g.ok("2. higgsfield cli", "connected, signed in as %s" % email)
    want = (client.get("account") or "").lower()
    if not want:
        g.flag("2. account", "clients.json has no `account` email for this client - fill it in")
        return
    if email != want:
        g.flag("2. account", "signed in as %s but this client runs on %s - STOP; the editor signs into the right account"
               % (email, want))
        return
    g.ok("2. account", "%s matches the client" % email)
    _, ws_out = run([hf, "workspace", "list"], 90)
    sel = ""
    for ln in (ws_out or "").splitlines():
        if "\u2713" in ln or ln.rstrip().endswith("*"):
            sel = ln
            break
    ws_id, ws_name = client.get("workspace_id", ""), client.get("workspace_name", "")
    if not (ws_id or ws_name):
        g.note("2. workspace", "no workspace recorded in clients.json - selected: %s" % (sel.strip() or "?"))
        return
    if (ws_id and ws_id in sel) or (ws_name and ws_name.lower() in sel.lower()):
        g.ok("2. workspace", ws_name or ws_id)
    else:
        g.flag("2. workspace", "%s (%s) is not the selected workspace - `higgsfield workspace set %s`, then re-run"
               % (ws_name or "-", ws_id or "-", ws_id or ws_name))


# ---------------------------------------------------------------- 3. models
def check_models(client, key, models, g):
    """Exactly one image + one video model, both allowed. Missing -> print the ask, FLAG."""
    allowed = [m.lower() for m in client.get("models_allowed", [])]
    given = [m.strip().lower() for m in (models or []) if m.strip()]
    images = [m for m in given if m in fire.STILL_MODELS]
    videos = [m for m in given if m in fire.MOTION_MODELS]
    unknown = [m for m in given if m not in fire.STILL_MODELS and m not in fire.MOTION_MODELS]
    bad = [m for m in given if allowed and m not in allowed]
    if unknown:
        g.flag("3. models", "%s: not a model this skill drives (%s)" % (", ".join(unknown),
               ", ".join(list(fire.STILL_MODELS) + list(fire.MOTION_MODELS))))
    if bad:
        g.flag("3. models", "%s not in %s's models_allowed %s - edit clients.json, not the run" % (", ".join(bad), key, allowed))
    if len(images) != 1 or len(videos) != 1:
        g.flag("3. models", "ASK the editor: choose exactly ONE image model and ONE video model (got image=%s video=%s). "
               "Pass them as --model <image> --model <video>. The menu is printed below." % (images or "-", videos or "-"))
        return None
    if not (unknown or bad):
        g.ok("3. models", "image %s · video %s (the editor's choice, recorded in the marker)" % (images[0], videos[0]))
    return [images[0], videos[0]]


# ---------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--client", required=True, help="key in clients.json")
    ap.add_argument("--project", required=True, help="local project folder (created if missing)")
    ap.add_argument("--drive", default=None, help="Google Drive folder LINK (or id) with EDIT permission")
    ap.add_argument("--model", action="append", default=[], help="repeat: one image model, one video model")
    ap.add_argument("--script", default=None, help="script / brief to copy into Creatives/ (local + Drive)")
    ap.add_argument("--rclone-remote", default="gdrive")
    a = ap.parse_args(argv)

    reg = fire.load_registry()
    key = a.client.strip().lower()
    if key not in reg or key.startswith("_"):
        print("unknown client %r - known: %s (add it to %s)" % (key, [k for k in reg if not k.startswith("_")], fire.CLIENTS_PATH))
        return 3
    client = reg[key]
    project = os.path.abspath(a.project)
    g = Gate()

    scaffold_local(project, a.script, g)
    fid = check_drive(a.drive, a.rclone_remote, project, a.script, g)
    check_account(client, g)
    chosen = check_models(client, key, a.model, g)

    print("\nPREFIRE GATE  ·  client %s  ·  %s" % (key, project))
    print("-" * 78)
    for status, item, detail in g.rows:
        print("%-4s  %-28s %s" % ({"OK": "ok", "FLAG": "FLAG", "NOTE": "note"}[status], item,
                                   detail.replace("\n", "\n" + " " * 35)))
    print("-" * 78)
    if chosen is None:
        print()
        fire.print_models(key, reg)

    rec = {"when": datetime.datetime.now().isoformat(timespec="seconds"), "client": key, "project": project,
           "models": chosen or [], "drive_folder_id": fid, "open": not g.flagged,
           "rows": [{"status": s, "item": i, "detail": d} for s, i, d in g.rows]}
    os.makedirs(os.path.join(project, "Creatives"), exist_ok=True)
    with open(os.path.join(project, "Creatives", "_preflight.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1, ensure_ascii=False)

    if g.flagged:
        print("\n%d FLAG(s) - the gate is CLOSED. Nothing fires until every row is ok." % len(g.flagged))
        return 2
    print("\ngate OPEN - client %s · models %s · drive %s" % (key, " + ".join(chosen), fid))
    return 0


if __name__ == "__main__":
    sys.exit(main())
