#!/usr/bin/env python3
"""super-ai-gen/fire.py - the ONE Higgsfield fire path for every client on the spine.

The brand is never inferred. It is DECLARED (--client, the manifest's project.client, or the
Creatives/_preflight.json marker the start-of-gen gate wrote) and every declared source must
agree, or nothing fires. What the brand means - account, workspace, allowed models, rates,
product registry - comes from clients.json beside this script and nowhere else.

    python fire.py --manifest <m.json> --stage stills --dry-run
    python fire.py --manifest <m.json> --stage stills
    python fire.py --manifest <m.json> --stage motion --only C04,C06 --takes 2
    python fire.py --shotlist <shotlist_x.py> --stage motion          # pestlab-shaped input
    python fire.py --list-clients

Refusals (exit 2 = a gate closed, exit 3 = config, exit 1 = the shots themselves):
  * no client declared anywhere ............................ exit 3
  * two sources declare different clients .................. exit 2  (never guess)
  * client unknown to clients.json ......................... exit 3
  * client fires through the MCP or Flow, not the CLI ...... exit 2  (use its own skill)
  * real fire with no Creatives/_preflight.json ............ exit 2  (gate never ran)
  * signed-in account / workspace is not the client's ...... exit 2  (never another client's credits)
  * a model outside the client's models_allowed ............ exit 1  (Mark edits the registry, not the run)
  * a model Mark never chose at preflight (marker.models) .. exit 2  (dry-run warns; --models <client> prints the ask)

Guards, in order of how much money they save:
  * --dry-run prints the cost math plus one live `generate cost` probe, and fires nothing.
  * resume: an output already on disk over MIN_BYTES is skipped, never re-fired.
  * regen = next vN in place: --takes / --from-take (motion) and --reroll (stills) land beside
    the prior take. Nothing here ever overwrites, renames or moves a file.
  * lockfile: a second concurrent run of the same stage exits instead of double-spending.
  * preflight lints: bad duration for the model, missing seed / end frame, missing ref or
    never-cut panel, static motion prompt, off-pace dialogue, ALL-CAPS words, dialogue with no
    voice lock - all fail or warn BEFORE the first credit.
  * <root>/_gen_run.json: run state (job ids, files, prompts) so a half-dead run resumes.

Engine facts are in MODELS below. Rates are MEASURED and move; a client may override any rate
in clients.json ("rates": {"veo3_1_lite": {"off": 1.0, "on": 1.5}}) and the live probe on every
dry-run is the check that catches a moved one.
"""
import argparse, importlib.util, json, os, re, shutil, subprocess, sys, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
QC = os.path.join(os.path.dirname(HERE), "asset-qc-local")   # the BLC spine, when this skill sits beside it
sys.path.insert(0, HERE)
from panels import expand as panel_expand, split_angle  # noqa: E402  (vendored copy in this folder)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HF = shutil.which("higgsfield") or os.path.expandvars(
    r"%APPDATA%\npm\node_modules\@higgsfield\cli\vendor\hf.exe")
# The registry: env override, then this folder's clients.json (the packaged install), then the
# BLC spine's copy when this skill sits beside asset-qc-local. clients.example.json is the template.
CLIENTS_PATH = (os.environ.get("SUPER_AI_GEN_CLIENTS")
                or next((p for p in (os.path.join(HERE, "clients.json"), os.path.join(QC, "clients.json"))
                         if os.path.isfile(p)), os.path.join(HERE, "clients.json")))
MARKER = os.path.join("Creatives", "_preflight.json")
MIN_BYTES = 10 * 1024
WPS_LO, WPS_HI = 1.8, 3.6
LOCK_TTL = 7200

# ---------------------------------------------------------------- engines
# cost: credits per still by resolution; None = never measured here, the live probe answers.
STILL_MODELS = {
    "gpt_image_2":       {"cost": {"1k": None, "2k": 6.5, "4k": None}, "quality": "high",
                          "note": "6.5 @2k/high measured 2026-09-21 (was 8.5, was 14.7)"},
    "nano_banana_flash": {"cost": {"1k": 1.5, "2k": 2.0, "4k": 3.0},
                          "note": "= Nano Banana 2 on the CLI; measured 2026-09-03"},
    "nano_banana_2":     {"cost": {"1k": None, "2k": None, "4k": None}},
    "nano_banana_pro":   {"cost": {"1k": None, "2k": None, "4k": None}},
}
# dur: ("range", lo, hi) integer seconds, or ("grid", (a, b, c)) fixed menu.
# audio: which CLI flag carries sound - "generate_audio" (true/false), "sound" (on/off), None (prompt only).
# rate: credits per second keyed by (mode, sound) - "" mode where the model has none.
MOTION_MODELS = {
    "seedance_2_5": {"dur": ("range", 4, 30), "mode": "omni_reference", "resolution": "720p",
                     "audio": "generate_audio", "prompt": "json",
                     "rate": {("omni_reference", "off"): 7.0, ("omni_reference", "on"): 7.0},
                     "note": "7.0 cr/s flat, measured 2026-09-21; omni_reference is REQUIRED for start/end images"},
    "veo3_1_lite":  {"dur": ("grid", (4, 6, 8)), "mode": None, "resolution": None,
                     "audio": "generate_audio", "prompt": "prose", "bounded_forces": 8,
                     "rate": {("", "off"): 1.5, ("", "on"): 1.5},
                     "note": "1.0 silent / 1.5 audio on Pestlab 2026-09-16; 1.5 both on HYDROLABS 2026-09-22 - probe it"},
    "gemini_omni_flash_1_1": {"dur": ("range", 4, 10), "mode": "image-to-video", "resolution": "720p",
                     "audio": None, "prompt": "json",
                     "rate": {("image-to-video", "off"): 3.0, ("image-to-video", "on"): 3.0},
                     "note": "no generate_audio param - audio lives in the prompt JSON only"},
    "kling3_0":     {"dur": ("range", 3, 15), "mode": "std", "resolution": None,
                     "audio": "sound", "prompt": "json",
                     "rate": {("std", "off"): 1.25, ("std", "on"): 1.75, ("pro", "off"): 1.5, ("pro", "on"): 2.0},
                     "note": "std = 720p lane; measured 2026-09-03"},
}
STATIC_WORDS = ("holds still", "does not move", "camera is static", "static camera", "locked off",
                "locked-off", "motionless camera", "camera holds")
MOVE_WORDS = ("push", "pull", "dolly", "track", "pan", "tilt", "drift", "descend", "rise", "orbit",
              "arc", "glide", "travel", "move", "creep", "zoom", "crane", "sweep")
DEFAULT_CAMERA = ("smooth controlled move exactly as described; no whip pans, no orbits, no zoom "
                  "snaps, no handheld shake")

_print_lock = threading.Lock()


def say(*a):
    with _print_lock:
        print(*a, flush=True)


# ---------------------------------------------------------------- client (the brand)
def load_registry(path=None):
    with open(path or CLIENTS_PATH, encoding="utf-8") as f:
        return json.load(f)


def marker_client(root):
    p = os.path.join(root, "Creatives", "_preflight.json")
    if not os.path.isfile(p):
        return None, p
    try:
        with open(p, encoding="utf-8") as f:
            return (json.load(f).get("client") or None), p
    except Exception:
        return None, p


def models_declared(root):
    """The models preflight.py recorded (its --model flags), lower-cased; None when no marker."""
    p = os.path.join(root, "Creatives", "_preflight.json")
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return {str(x).lower() for x in (json.load(f).get("models") or [])}
    except Exception:
        return set()


def print_models(client_key, reg):
    """What to put in front of Mark for the model ask: the client's allowed models with rates."""
    c = reg[client_key]
    print("client %s - models_allowed (choose ONE image + ONE video model at preflight):" % client_key)
    for mdl in c.get("models_allowed", []):
        if mdl in STILL_MODELS:
            cost = {r: still_cost(c, mdl, r) for r in ("1k", "2k", "4k")}
            print("  image  %-22s %s  %s" % (mdl, " ".join("%s=%s" % (r, v if v is not None else "?") for r, v in cost.items()),
                                              STILL_MODELS[mdl].get("note", "")))
        elif mdl in MOTION_MODELS:
            sp = MOTION_MODELS[mdl]
            offr, onr = rate_for(c, mdl, sp["mode"] or "", "off"), rate_for(c, mdl, sp["mode"] or "", "on")
            d = sp["dur"]
            print("  video  %-22s %s/s silent, %s/s audio, dur %s  %s" % (
                mdl, offr, onr, "%s-%s" % d[1:] if d[0] == "range" else "/".join(map(str, d[1])), sp.get("note", "")))
        else:
            print("  ?      %-22s not driven by this script" % mdl)
    if c.get("models_note"):
        print("  note: " + c["models_note"])


def resolve_client(arg_client, P, dry, registry=None):
    """Every declared source must name the same client. Nothing is inferred from the script,
    the folder name or the prompts. Returns (key, client_dict, marker_present)."""
    reg = registry or load_registry()
    root = P["root"]
    sources = {}
    if arg_client:
        sources["--client"] = arg_client.strip().lower()
    if P.get("client"):
        sources["manifest project.client"] = str(P["client"]).strip().lower()
    mk, mpath = marker_client(root)
    if mk:
        sources[os.path.relpath(mpath, root)] = mk.strip().lower()

    if not sources:
        print("NO CLIENT DECLARED - pass --client <key>, or run the start-of-gen gate first:\n"
              "  python %s --client <key> --project \"%s\" --script <script> --model ...\n"
              "(it writes %s). This script never guesses the brand."
              % (os.path.join(QC, "preflight.py"), root, MARKER))
        sys.exit(3)
    if len(set(sources.values())) > 1:
        print("CLIENT MISMATCH - refusing to fire:")
        for k, v in sources.items():
            print("  %-32s %s" % (k, v))
        print("Fix the source that is wrong. Nothing is inferred here.")
        sys.exit(2)
    key = next(iter(sources.values()))
    known = [k for k in reg if not k.startswith("_")]
    if key not in reg:
        print("unknown client %r - known: %s (add it to %s)" % (key, known, CLIENTS_PATH))
        sys.exit(3)
    client = reg[key]
    if client.get("engine") != "higgsfield-cli":
        print("client %s fires through %s (skill %s), not the Higgsfield CLI - use that skill."
              % (key, client.get("engine"), client.get("skill")))
        sys.exit(2)
    if not mk:
        msg = ("no %s under %s - the start-of-gen gate has not run for this project" % (MARKER, root))
        if dry:
            print("WARN  " + msg + " (dry-run continues; a real fire refuses)")
        else:
            print("REFUSED: " + msg + ". Run prefire.py --client %s first." % key)
            sys.exit(2)
    else:
        # prefire.py writes the marker even when it FLAGs (so the rows are on disk); "open" says
        # whether every row passed. A closed gate never fires. Markers from the BLC spine's
        # preflight.py carry no "open" key and count as open (it exits 2 before writing? no -
        # it writes then exits; its FLAG rows are inside "rows", checked here too).
        try:
            with open(mpath, encoding="utf-8") as f:
                rec = json.load(f)
        except Exception:
            rec = {}
        closed = rec.get("open") is False or any(r.get("status") == "FLAG" for r in rec.get("rows", []))
        if closed:
            msg = "the gate is CLOSED: %s has FLAG rows - fix them and re-run prefire.py" % os.path.relpath(mpath, root)
            if dry:
                print("WARN  " + msg)
            else:
                print("REFUSED: " + msg)
                sys.exit(2)
    return key, client, bool(mk)


def rate_for(client, model, mode, sound):
    """clients.json rates override the table: {"rates": {"veo3_1_lite": {"off": 1.0, "on": 1.5}}}."""
    ov = (client.get("rates") or {}).get(model)
    if isinstance(ov, dict) and sound in ov:
        return float(ov[sound])
    spec = MOTION_MODELS[model]
    return spec["rate"].get((mode or "", sound))


def still_cost(client, model, res):
    ov = (client.get("rates") or {}).get(model)
    if isinstance(ov, dict) and res in ov:
        return float(ov[res])
    return STILL_MODELS.get(model, {}).get("cost", {}).get(res)


# ---------------------------------------------------------------- inputs -> one model
def load_manifest(path):
    """The general JSON manifest (hydrolabs-shaped: project / stills / shots)."""
    with open(path, encoding="utf-8") as f:
        m = json.load(f)
    P = m["project"]
    P.setdefault("models", {})
    P["models"].setdefault("still", "nano_banana_flash")
    P["models"].setdefault("still_resolution", "2k")
    P["models"].setdefault("motion", "kling3_0")
    P["models"].setdefault("motion_mode", MOTION_MODELS.get(P["models"]["motion"], {}).get("mode"))
    P["models"].setdefault("aspect_ratio", "9:16")
    P.setdefault("takes_default", 2)
    P.setdefault("refs", {})
    P.setdefault("naming", "prefixed_takes")
    P.setdefault("prefix", P.get("slug", "GEN"))
    P.setdefault("on_camera_kinds", ["TH", "VO"])
    m.setdefault("stills", [])
    m.setdefault("shots", [])
    m["_path"] = os.path.abspath(path)
    return m


def load_shotlist(path):
    """A pestlab-shaped shotlist MODULE, normalised to the same model. Existing projects keep
    their folders and plain <id>.png / <id>.mp4 names, so old runs resume untouched."""
    path = os.path.abspath(path)
    spec = importlib.util.spec_from_file_location("shotlist", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["shotlist"] = mod
    spec.loader.exec_module(mod)
    PR, MO = mod.PROJECT, mod.MODELS
    P = {
        "client": getattr(mod, "CLIENT", None),
        "root": PR["root"], "prefix": os.path.basename(PR["root"].rstrip("\\/")),
        "naming": "plain", "takes_default": 1, "on_camera_kinds": ["TH"],
        "stills_dir": PR.get("stills"), "clips_dir": PR.get("clips"),
        "ref_dirs": [d for d in (PR.get("anchors"), PR.get("product_refs")) if d],
        "models": {"still": MO["still"], "still_resolution": "2k", "still_quality": "high",
                   "motion": MO.get("motion", "seedance_2_5"), "hero": MO.get("hero", "seedance_2_5"),
                   "motion_mode": None, "aspect_ratio": "9:16"},
        "refs": {},
        "locks": {"voice_lock": getattr(mod, "VOICE", None), "offscreen": getattr(mod, "OFFSCREEN", ""),
                  "notext": getattr(mod, "NOTEXT", None), "lens": getattr(mod, "LENS", {}),
                  "motion_style": getattr(mod, "MOTION_STYLE", {}), "style_base": getattr(mod, "CG3D", "")},
    }
    P["models"]["motion_mode"] = MOTION_MODELS.get(P["models"]["motion"], {}).get("mode")
    stills, shots = [], []
    for s in mod.SHOTS:
        tag = s["id"]
        if s.get("still"):
            stills.append({"tag": tag, "prompt": s["still"], "refs": s.get("refs", []),
                           "competitor": s.get("competitor"), "human": s.get("human")})
        model = s.get("model")
        if model in ("hero", "motion", None, ""):
            model = P["models"]["hero"] if model == "hero" else P["models"]["motion"]
        shots.append({"tag": tag, "kind": s.get("kind", "BR"), "duration": s["dur"],
                      "seed": s.get("frame", tag), "end_seed": s.get("end"), "action": s.get("action", ""),
                      "dialogue": s.get("line"), "model": model, "subject": s.get("still", ""),
                      "takes": s.get("takes")})
    return {"project": P, "stills": stills, "shots": shots, "_path": path}


# ---------------------------------------------------------------- paths
def root(m):
    return m["project"]["root"]


def stills_dir(m):
    d = m["project"].get("stills_dir")
    return os.path.join(root(m), d) if d else os.path.join(root(m), "Elements", "Stills")


def clips_dir(m):
    d = m["project"].get("clips_dir")
    return os.path.join(root(m), d) if d else os.path.join(root(m), "Elements", "Clips")


def still_by_tag(m, tag):
    for s in m["stills"]:
        if s["tag"] == tag:
            return s
    return None


def still_path(m, tag, take=1):
    s = still_by_tag(m, tag)
    if s is None:
        return None
    if s.get("out") and take == 1:
        return os.path.join(stills_dir(m), s["out"])
    base = os.path.splitext(s["out"])[0] if s.get("out") else tag.replace("+", "_")
    return os.path.join(stills_dir(m), base + (".png" if take == 1 else "_v%02d.png" % take))


def next_still_take(m, tag):
    n = 2
    while done(still_path(m, tag, n)):
        n += 1
    return n


def clip_path(m, tag, take):
    P = m["project"]
    if P.get("naming") == "plain":
        name = tag.replace("+", "_") + (".mp4" if take == 1 else "_v%02d.mp4" % take)
    else:
        name = "%s_%s_v%02d.mp4" % (P["prefix"], tag, take)
    return os.path.join(clips_dir(m), name)


def done(path):
    return bool(path) and os.path.exists(path) and os.path.getsize(path) > MIN_BYTES


def takes_for(m, s, override=None):
    return int(override or s.get("takes") or m["project"]["takes_default"])


def resolve_ref(m, base):
    """A ref name -> its flat file: project.refs first, then the ref_dirs scan."""
    P = m["project"]
    p = P["refs"].get(base)
    if p:
        return p if os.path.isabs(p) else os.path.join(root(m), p)
    for d in P.get("ref_dirs", []):
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            q = os.path.join(root(m), d, base + ext)
            if os.path.exists(q):
                return q
    return None


def ref_files(m, spec):
    base, angle = split_angle(spec)
    return panel_expand(resolve_ref(m, base), angle)


# ---------------------------------------------------------------- run state
def state_path(m):
    return os.path.join(root(m), "_gen_run.json")


def load_state(m):
    p = state_path(m)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"client": m["project"].get("client"), "stills": {}, "motion": {}}


def record(m, state, stage, tag, take=None, **kw):
    with _print_lock:
        key = tag if take is None else "%s_v%02d" % (tag, take)
        state.setdefault(stage, {}).setdefault(key, {}).update(kw)
        with open(state_path(m), "w", encoding="utf-8") as f:
            json.dump(state, f, indent=1, ensure_ascii=False)


# ---------------------------------------------------------------- prompts
def locks(P):
    """Locks live in project.locks; legacy hydrolabs manifests carry them at project level."""
    L = dict(P.get("locks") or {})
    for k in ("voice_lock", "voices", "negative_base", "tablet_negatives", "noprod_guard", "persona",
              "style_base", "shot_base", "setting_base", "offscreen", "notext"):
        if k not in L and k in P:
            L[k] = P[k]
    return L


def placeholders(P):
    ph = dict(P.get("placeholders") or {})
    legacy = {"<TABLET_BRIEF>": "tablet_lock_brief", "<FIZZ_BRIEF>": "fizz_lock_brief",
              "<TABLET>": "tablet_lock", "<FIZZ>": "fizz_lock", "<NOPROD>": "noprod_guard"}
    for tok, key in legacy.items():
        if tok not in ph and P.get(key):
            ph[tok] = P[key]
    return ph


def expand(P, text):
    text = text or ""
    # longest tokens first so <TABLET_BRIEF> is not eaten by <TABLET>
    for tok, val in sorted(placeholders(P).items(), key=lambda kv: -len(kv[0])):
        text = text.replace(tok, val or "")
    return text


def still_prompt(m, s):
    P = m["project"]
    p = expand(P, s["prompt"]).strip()
    guard = locks(P).get("noprod_guard")
    if s.get("product_free") and guard and guard not in p:
        p += " " + guard + "."
    return p


def model_for(m, s):
    return s.get("model") or m["project"]["models"]["motion"]


def mode_for(m, s, model):
    spec = MOTION_MODELS[model]
    if spec["mode"] is None:
        return ""
    return s.get("mode") or (m["project"]["models"].get("motion_mode") if model == m["project"]["models"]["motion"] else None) or spec["mode"]


def sound_for(s):
    return "on" if s.get("dialogue") else "off"


def voice_lock_for(L, s):
    v = s.get("voice")
    if v:
        return (L.get("voices") or {}).get(v, "")
    return L.get("voice_lock", "")


def motion_prompt(m, s, model):
    P = m["project"]
    L = locks(P)
    kind = s.get("kind", "TH")
    speaks = bool(s.get("dialogue"))
    on_cam = speaks and kind in P.get("on_camera_kinds", ["TH", "VO"])
    off = L.get("offscreen", "")
    action = expand(P, s.get("action", ""))
    if speaks and not on_cam and off:
        action = action + " " + off
    t2v = not s.get("seed")

    if MOTION_MODELS[model]["prompt"] == "prose":
        prose = (expand(P, s.get("subject", "")) + " " + action).strip()
        if s.get("dialogue"):
            prose += ' The person says: "%s".' % s["dialogue"] if on_cam else ""
        return prose + " The camera move is smooth and controlled, no whip pans, no orbits."

    lens = (L.get("lens") or {})
    style = s.get("style") or (L.get("motion_style") or {}).get(kind) \
        or (L.get("motion_style") or {}).get("_default") or L.get("style_base", "")
    negative = L.get("negative_base", "")
    if s.get("negatives_extra"):
        negative = (negative + ", " + s["negatives_extra"]).strip(", ")
    spec = {
        "shot": {"framing": s.get("framing", "as described in the action") if t2v
                 else "matches the uploaded start frame exactly",
                 "camera": s.get("camera", DEFAULT_CAMERA),
                 "lens_style": lens.get(kind, lens.get("_default", "")),
                 "base": s.get("shot_base", L.get("shot_base", ""))},
        "subject": expand(P, s.get("subject") or L.get("persona", "")),
        "action": action,
        "setting": (s.get("setting") or L.get("setting_base", "")) if t2v else
                   {"location": "exactly as established in the start frame",
                    "lighting": "exactly as established in the start frame"},
        "style": style,
        "negative": negative,
        "duration_seconds": int(s["duration"]),
        "aspect_ratio": P["models"]["aspect_ratio"],
        "frame_rate": 24,
    }
    if s.get("end_seed"):
        spec["shot"]["framing"] = ("starts exactly on the uploaded start frame and ends exactly "
                                   "on the uploaded end frame")
    if speaks:
        spec["dialogue"] = s["dialogue"]
        spec["voice_lock"] = voice_lock_for(L, s)
        spec["audio"] = {"dialogue_mix": "the voice close, dry and prominent" + (", spoken on camera" if on_cam else ""),
                         "ambient": "very faint room tone", "music": "none",
                         "completeness": "every word of the dialogue is spoken fully, exactly once"}
        if s.get("voice") and L.get("voices"):
            who = s.get("speaker") or s["voice"]
            spec["speaker"] = who
            spec["audio"]["single_speaker"] = (
                "exactly one person speaks for the whole clip: %s. Only that person's mouth moves. "
                "Every other person in frame stays completely silent with a closed mouth, listening, "
                "and never speaks, mouths or lip-syncs any word." % who)
    else:
        spec["audio"] = {"dialogue": "none", "narration": "none", "music": "none", "ambient": "silent"}
    for k in ("lens_style", "base"):
        if not spec["shot"][k]:
            del spec["shot"][k]
    for k in ("style", "negative", "subject"):
        if not spec[k]:
            del spec[k]
    # Pure JSON gets auto-parsed by the CLI into an object and the API rejects it. The sentence
    # prefix keeps the prompt a string.
    return "Follow this shot spec exactly. " + json.dumps(spec, ensure_ascii=False)


# ---------------------------------------------------------------- commands
def still_cmd(m, s):
    P = m["project"]
    model = P["models"]["still"]
    cmd = [HF, "generate", "create", model, "--json", "--wait", "--wait-timeout", "20m",
           "--prompt", still_prompt(m, s),
           "--aspect_ratio", s.get("aspect", P["models"]["aspect_ratio"]),
           "--resolution", P["models"]["still_resolution"]]
    q = P["models"].get("still_quality") or STILL_MODELS.get(model, {}).get("quality")
    if q:
        cmd += ["--quality", q]
    for name in s.get("refs", []):
        for r in ref_files(m, name):
            cmd += ["--image-references", r]
    return cmd


def motion_cmd(m, s, take=1):
    P = m["project"]
    model = model_for(m, s)
    spec = MOTION_MODELS[model]
    mode = mode_for(m, s, model)
    sound = sound_for(s)
    cmd = [HF, "generate", "create", model, "--json", "--wait", "--wait-timeout", "25m",
           "--prompt", motion_prompt(m, s, model),
           "--duration", str(int(s["duration"])),
           "--aspect_ratio", P["models"]["aspect_ratio"]]
    if s.get("seed"):
        cmd += ["--start-image", still_path(m, s["seed"])]
    if s.get("end_seed"):
        cmd += ["--end-image", still_path(m, s["end_seed"])]
    if mode:
        cmd += ["--mode", mode]
    if spec["resolution"]:
        cmd += ["--resolution", s.get("resolution", spec["resolution"])]
    if spec["audio"] == "generate_audio":
        cmd += ["--generate-audio", "true" if sound == "on" else "false"]
    elif spec["audio"] == "sound":
        cmd += ["--sound", sound]
    return cmd


def live_cost_probe(cmd):
    """One free `generate cost` with the real params - catches a moved rate before the bulk."""
    # Media that exists on disk stays in the probe: reference modes (seedance omni_reference,
    # omni image-to-video) refuse to quote without one. Media that is not there yet (a dry-run
    # before the stills landed) is dropped, and so is the mode that needs it.
    media = ("--start-image", "--end-image", "--image-references")
    args, skip, dropped_media = [], False, False
    for i, a in enumerate(cmd[3:], 3):
        if skip:
            skip = False
            continue
        if a == "--wait-timeout":
            skip = True
            continue
        if a in media:
            if i + 1 < len(cmd) and os.path.isfile(str(cmd[i + 1])):
                args += [a, cmd[i + 1]]
            else:
                dropped_media = True
            skip = True
            continue
        if a in ("--wait", "--json"):
            continue
        args.append(a)
    if dropped_media and "--mode" in args:
        j = args.index("--mode")
        if args[j + 1] in ("omni_reference", "image-to-video", "reference-to-video"):
            del args[j:j + 2]
    r = subprocess.run([HF, "generate", "cost"] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    out = (r.stdout or "") + (r.stderr or "")
    return out.strip().splitlines()[-1] if out.strip() else "?"


# ---------------------------------------------------------------- preflight lints
def preflight(m, client, stage, stills, shots, dry):
    """Every check here is free. Anything that would waste a credit dies here."""
    errs, warns = [], []
    P = m["project"]
    L = locks(P)
    allowed = [x.lower() for x in client.get("models_allowed", [])]

    def model_ok(model, where):
        if allowed and model.lower() not in allowed:
            errs.append("%s: model %s is not in %s's models_allowed %s - %s"
                        % (where, model, P.get("client"), allowed,
                           client.get("models_note", "edit clients.json, not the run")))

    seen = set()
    if stage == "stills":
        model_ok(P["models"]["still"], "stills")
        if P["models"]["still"] not in STILL_MODELS:
            errs.append("still model %s is unknown to this script" % P["models"]["still"])
        for s in stills:
            if s["tag"] in seen:
                errs.append("%s duplicate tag" % s["tag"])
            seen.add(s["tag"])
            for name in s.get("refs", []):
                if not ref_files(m, name):
                    base, angle = split_angle(name)
                    if angle and resolve_ref(m, base):
                        errs.append("%s ref %r: panel %r was never cut - run charsheet.py --crop"
                                    % (s["tag"], name, angle))
                    else:
                        errs.append("%s ref %r not found (project.refs or ref_dirs)" % (s["tag"], name))
            nt = L.get("notext")
            if nt and nt not in s["prompt"]:
                warns.append("%s still prompt is missing the NOTEXT lock" % s["tag"])
    else:
        for s in shots:
            tag = s["tag"]
            if tag in seen:
                errs.append("%s duplicate tag" % tag)
            seen.add(tag)
            model = model_for(m, s)
            if model not in MOTION_MODELS:
                errs.append("%s: motion model %s is unknown to this script" % (tag, model))
                continue
            model_ok(model, tag)
            spec = MOTION_MODELS[model]
            dur = int(s["duration"])
            kind, lo_hi = spec["dur"][0], spec["dur"][1:]
            if kind == "grid" and dur not in lo_hi[0]:
                errs.append("%s dur=%s but %s accepts only %s" % (tag, dur, model, "/".join(map(str, lo_hi[0]))))
            if kind == "range" and not (lo_hi[0] <= dur <= lo_hi[1]):
                errs.append("%s dur=%s but %s takes %s-%s" % (tag, dur, model, lo_hi[0], lo_hi[1]))
            if s.get("end_seed") and spec.get("bounded_forces") and dur != spec["bounded_forces"]:
                errs.append("%s is bounded on %s, which forces duration %s" % (tag, model, spec["bounded_forces"]))
            for key, label in (("seed", "start"), ("end_seed", "end")):
                if s.get(key):
                    p = still_path(m, s[key])
                    if p is None:
                        errs.append("%s %s frame %r is not a still in this manifest" % (tag, label, s[key]))
                    elif not done(p):
                        (warns if dry else errs).append("%s %s frame missing: %s" % (tag, label, p))
            act = (s.get("action") or "").lower()
            has_move = any(w in act for w in MOVE_WORDS)
            if any(w in act for w in STATIC_WORDS) and not has_move:
                errs.append("%s action holds the camera still and names no movement - static clips "
                            "are a build error" % tag)
            elif not has_move:
                warns.append("%s action names no camera movement - confirm the subject action is visible" % tag)
            if s.get("dialogue"):
                words = s["dialogue"].split()
                wps = len(words) / float(dur)
                if not (WPS_LO <= wps <= WPS_HI):
                    warns.append("%s %.2f w/s (%d words in %ss) - outside %.1f-%.1f" % (tag, wps, len(words), dur, WPS_LO, WPS_HI))
                for w in words:
                    if len(w) > 1 and w.isupper() and w.isalpha():
                        warns.append("%s ALL-CAPS word %r will be spelled out letter by letter" % (tag, w))
                if not voice_lock_for(L, s):
                    errs.append("%s carries dialogue but no voice lock resolves (project.locks.voice_lock / voices)" % tag)
    # client-owned craft lints, if the registry names a module: def lint(stage, m, stills, shots) -> (errs, warns)
    hook = client.get("fire_lints")
    if hook:
        try:
            sp = importlib.util.spec_from_file_location("client_lints", hook)
            mod = importlib.util.module_from_spec(sp)
            sp.loader.exec_module(mod)
            e2, w2 = mod.lint(stage, m, stills, shots)
            errs += list(e2)
            warns += list(w2)
        except Exception as e:
            errs.append("fire_lints %s failed: %r" % (hook, e))
    return errs, warns


# ---------------------------------------------------------------- fire
def parse_job(stdout):
    try:
        i = stdout.index("[")
        arr = json.loads(stdout[i:])
        return arr[0] if arr else None
    except Exception:
        try:
            i = stdout.index("{")
            return json.loads(stdout[i:])
        except Exception:
            return None


def recover_job(want_prompt):
    """`--wait --json` occasionally returns [] while the job WAS created and completes later.
    Poll the job list and claim the newest completed job whose FULL prompt matches."""
    for _ in range(12):
        time.sleep(45)
        r = subprocess.run([HF, "generate", "list", "--size", "25", "--json"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=300)
        try:
            jobs = json.loads(r.stdout[r.stdout.index("["):])
        except Exception:
            continue
        for j in jobs:
            if (j.get("params", {}).get("prompt", "") == want_prompt and j.get("status") == "completed"
                    and j.get("result_url")):
                return j
    return None


def fire_one(m, state, stage, s, take, dest):
    cmd = still_cmd(m, s) if stage == "stills" else motion_cmd(m, s, take)
    want = cmd[cmd.index("--prompt") + 1]
    label = s["tag"] if (stage == "stills" and take == 1) else "%s v%02d" % (s["tag"], take)
    if os.path.exists(dest):
        return "%s SKIP exists (never overwritten): %s" % (label, dest)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
        job = parse_job(r.stdout or "")
        if job is None:
            err = (r.stderr or r.stdout or "")[-300:]
            if "nsfw" in err.lower():
                record(m, state, stage, s["tag"], take, status="nsfw", downloaded=False)
                return "%s NSFW-FLAG (classifier misfire - reword neutrally and re-fire)" % label
            job = recover_job(want)
            if job is None:
                record(m, state, stage, s["tag"], take, status="error", downloaded=False, err=err)
                return "%s FAIL empty CLI response, no matching job: %s" % (label, err.replace("\n", " "))
        if job.get("status") != "completed" or not job.get("result_url"):
            record(m, state, stage, s["tag"], take, status=job.get("status"), job_id=job.get("id"), downloaded=False)
            return "%s FAIL status=%s %s" % (label, job.get("status"), (r.stderr or "")[-160:].replace("\n", " "))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        urllib.request.urlretrieve(job["result_url"], dest)
        record(m, state, stage, s["tag"], take, status="completed", job_id=job.get("id"),
               file=os.path.basename(dest), downloaded=True, prompt=want, result_url=job["result_url"])
        return "%s OK %dKB  %s" % (label, os.path.getsize(dest) // 1024, job["result_url"])
    except Exception as e:
        record(m, state, stage, s["tag"], take, status="error", downloaded=False, err=repr(e)[:200])
        return "%s FAIL %s %s" % (label, type(e).__name__, str(e)[:160])


def account_gate(client):
    """The SAME test preflight.py row 2 runs: signed-in email == client.account and the client's
    workspace is the selected one. Any FLAG closes the gate."""
    import prefire  # lazy: prefire imports this module for the model tables
    g = prefire.Gate()
    try:
        prefire.check_account(client, g)
    except Exception as e:
        g.flag("2. account", "check raised %r" % e)
    return g


# ---------------------------------------------------------------- main
def plan(m, stage, stills, shots, takes, from_take, reroll):
    todo = []
    if stage == "stills":
        for s in stills:
            if not s.get("prompt") or s.get("local") or s.get("crop_from"):
                continue
            take = next_still_take(m, s["tag"]) if reroll else 1
            p = still_path(m, s["tag"], take)
            if reroll or not done(p):
                todo.append((s, take, p))
    else:
        for s in shots:
            if s.get("local"):
                continue
            n = takes_for(m, s, takes)
            for take in range(from_take, from_take + n):
                p = clip_path(m, s["tag"], take)
                if not done(p):
                    todo.append((s, take, p))
    return todo


def cost(m, client, stage, todo):
    total, unknown = 0.0, []
    P = m["project"]
    for s, take, _ in todo:
        if stage == "stills":
            c = still_cost(client, P["models"]["still"], P["models"]["still_resolution"])
            (unknown.append(s["tag"]) if c is None else None)
            total += c or 0
        else:
            model = model_for(m, s)
            r = rate_for(client, model, mode_for(m, s, model), sound_for(s))
            (unknown.append(s["tag"]) if r is None else None)
            total += (r or 0) * int(s["duration"])
    return total, unknown


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--manifest", help="general JSON manifest (project / stills / shots)")
    src.add_argument("--shotlist", help="pestlab-shaped shotlist module (.py)")
    ap.add_argument("--client", default=None, help="key in clients.json; must agree with the manifest and the preflight marker")
    ap.add_argument("--stage", choices=("stills", "motion"))
    ap.add_argument("--only", default="", help="comma-separated tags")
    ap.add_argument("--takes", type=int, default=None, help="motion: takes per shot")
    ap.add_argument("--from-take", type=int, default=1, help="motion: first take number (re-rolls continue numbering)")
    ap.add_argument("--reroll", action="store_true", help="stills: land the next _vNN beside the existing take")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-account-check", action="store_true", help="tests only - never on a client fire")
    ap.add_argument("--list-clients", action="store_true")
    ap.add_argument("--models", metavar="CLIENT", help="print the client's allowed models with rates - the model ask")
    a = ap.parse_args(argv)

    if a.models:
        reg = load_registry()
        if a.models not in reg:
            print("unknown client %r" % a.models)
            return 3
        print_models(a.models, reg)
        return 0
    if a.list_clients:
        reg = load_registry()
        for k, v in reg.items():
            if k.startswith("_"):
                continue
            print("%-10s engine=%-15s skill=%-16s models=%s" % (k, v.get("engine"), v.get("skill"), ",".join(v.get("models_allowed", []))))
        return 0
    if not (a.manifest or a.shotlist) or not a.stage:
        ap.error("--manifest or --shotlist, and --stage, are required")

    m = load_manifest(a.manifest) if a.manifest else load_shotlist(a.shotlist)
    key, client, _marker = resolve_client(a.client, m["project"], a.dry_run)
    m["project"]["client"] = key

    want = {x.strip() for x in a.only.split(",") if x.strip()}
    stills = [s for s in m["stills"] if not want or s["tag"] in want]
    shots = [s for s in m["shots"] if not want or s["tag"] in want]
    local = [s["tag"] for s in shots if s.get("local")]
    if local:
        print("LOCAL (built by hand, not fired): " + ", ".join(local))
    shots = [s for s in shots if not s.get("local")]
    stills = [s for s in stills if not s.get("local") and not s.get("crop_from")]
    if want:
        known = {s["tag"] for s in m["stills"]} | {s["tag"] for s in m["shots"]}
        if want - known:
            print("unknown tags: %s" % ", ".join(sorted(want - known)))
            return 1

    # MODELS ARE MARK'S CHOICE, MADE AT THE GATE (Mark, 2026-09-24). A general skill has no
    # doctrine model; the image and video engines are asked for and recorded by preflight.py
    # (--model X --model Y -> _preflight.json "models"). A model this stage would fire that was
    # never declared there is refused on a real fire; a dry-run only warns, so candidate models
    # can still be costed for the ask.
    used = {m["project"]["models"]["still"]} if a.stage == "stills" else {model_for(m, s) for s in shots}
    declared = models_declared(root(m))
    undeclared = sorted(x for x in used if declared is not None and x.lower() not in declared)
    if undeclared:
        msg = ("model(s) %s were not chosen at preflight (marker declares %s). Ask Mark which image "
               "and video models to use, then re-run preflight.py --client %s ... --model <image> --model <video>"
               % (", ".join(undeclared), sorted(declared) or "none", key))
        if a.dry_run:
            print("WARN  " + msg)
        else:
            print("REFUSED: " + msg)
            return 2

    errs, warns = preflight(m, client, a.stage, stills, shots, a.dry_run)
    for w in warns:
        print("WARN  " + w)
    for e in errs:
        print("ERROR " + e)
    if errs:
        return 1


    todo = plan(m, a.stage, stills, shots, a.takes, a.from_take, a.reroll)
    total, unknown = cost(m, client, a.stage, todo)
    P = m["project"]
    print("\nclient %s · workspace %s · %s" % (key, client.get("workspace_name", "-"), a.stage))
    if a.stage == "stills":
        print("stills: %d in manifest, %d to fire  ->  %.1f cr (%s @%s)" % (
            len(stills), len(todo), total, P["models"]["still"], P["models"]["still_resolution"]))
    else:
        by = {}
        for s, take, _ in todo:
            by.setdefault(model_for(m, s), []).append(s)
        print("motion: %d shot(s), %d take(s) to fire  ->  %.1f cr" % (len(shots), len(todo), total))
        for model, grp in sorted(by.items()):
            print("  %-22s %3d takes %4ds" % (model, len(grp), sum(int(s["duration"]) for s in grp)))
    if unknown:
        print("  rate unknown for: %s - the live probe below is the only number" % ", ".join(sorted(set(unknown))))

    if a.dry_run:
        for s, take, p in todo:
            if a.stage == "stills":
                print("  %-16s %s refs=%s" % (s["tag"], "v%02d" % take if take > 1 else "   ", ",".join(s.get("refs", [])) or "-"))
            else:
                print("  %-16s v%02d %-6s %2ss %-22s sound=%s seed=%s%s" % (
                    s["tag"], take, s.get("kind", "TH"), s["duration"], model_for(m, s), sound_for(s),
                    s.get("seed") or "t2v", " end=" + s["end_seed"] if s.get("end_seed") else ""))
        if todo:
            s = todo[0][0]
            try:
                cmd = still_cmd(m, s) if a.stage == "stills" else motion_cmd(m, s)
                print("\nlive rate probe (%s): %s" % (s["tag"], live_cost_probe(cmd)))
            except Exception as e:
                print("\nlive rate probe failed: %r" % e)
        print("\nDRY RUN - nothing fired.")
        return 0

    if not a.skip_account_check:
        g = account_gate(client)
        for st, item, detail in g.rows:
            print("%-4s %-16s %s" % (st, item, detail.replace("\n", " ")[:160]))
        if g.flagged:
            print("REFUSED: the signed-in Higgsfield account is not %s's. Nothing fired." % key)
            return 2
    if not todo:
        print("nothing to fire - everything on disk.")
        return 0

    lock = os.path.join(root(m), "_fire_%s.lock" % a.stage)
    if os.path.exists(lock) and time.time() - os.path.getmtime(lock) < LOCK_TTL:
        print("another %s run is live (%s) - delete it if stale" % (a.stage, lock))
        return 2
    os.makedirs(root(m), exist_ok=True)
    with open(lock, "w") as f:
        f.write(str(os.getpid()))
    state = load_state(m)
    try:
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            futs = [ex.submit(fire_one, m, state, a.stage, s, take, p) for s, take, p in todo]
            for f in futs:
                say(f.result())
    finally:
        os.remove(lock)
    print("\nDONE. Spend is on the ledger: higgsfield account transactions --size 40")
    return 0


if __name__ == "__main__":
    sys.exit(main())
