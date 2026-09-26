#!/usr/bin/env python3
"""castlock.py - one master still + one voice per on-camera character, enforced before motion spend.

Why (a client's four ads, 2026-09-25/26): 7 of the 16 review notes were "not the same face and
the same voice". Both fixes that worked were invented mid-project (two desk masters on one ad,
Wan 2.7 driven by the master VO on another's revision). This module makes them the rule.

Manifest contract (project.entities, kind == "character"):

    {"id": "CHAR_OWNER", "kind": "character", "panels": "...",
     "master": "TH_A",                                  # still TAG every on-camera shot starts from
     "voice": {"vo": "Elements/Audio/VO_master.mp3"}   # lines are cut from this file -> audio-track model
              | {"lock": "owner"}}                     # or a named voice lock the shot must carry

A still may declare `"derived_from": "TH_A"` (a face pass or punch-in cut from the master); it then
counts as the master. Rerolls of the master (`TH_A_v02`) count too.

Rules (errors block a real fire):
  R1  a character on camera in 2+ shots must declare master + voice
  R2  an on-camera shot for a locked character starts from the master (or a derived still)
  R3  voice.vo  -> the shot runs on an audio-track model with an audio_file
      voice.lock -> the shot's `voice` names that lock
  R4  a line longer than the audio-track model can hold is split at a sentence break, never squeezed
  R5  the master tag must be a still in the manifest

Callers pass `audio_mode(shot) -> "track" | "sound" | "generate_audio" | None` because the model
tables live in each fire script. Pure functions, no I/O.
"""
import re

WORDS_PER_SEC = 2.5        # a natural read; the split rule uses it, not the fire lint's pace band
TRACK_MAX_SEC = 15         # wan2_7's ceiling


def characters(entities):
    return {e["id"]: e for e in (entities or []) if isinstance(e, dict) and e.get("kind") == "character" and e.get("id")}


def on_camera(shot, on_camera_kinds):
    return shot.get("kind") in (on_camera_kinds or ("TH", "VO")) or bool(shot.get("dialogue"))


def _stem(tag):
    return re.sub(r"_v\d+$", "", tag or "")


def counts_as_master(seed, master, stills):
    """seed is the master, a reroll of it, or a still derived from it (one level)."""
    if not seed or not master:
        return False
    if _stem(seed) == master:
        return True
    spec = next((s for s in (stills or []) if s.get("tag") == seed), None)
    return bool(spec) and _stem(spec.get("derived_from") or "") == master


def lint(entities, shots, stills, on_camera_kinds=None, audio_mode=None):
    errs, warns = [], []
    chars = characters(entities)
    still_tags = {s.get("tag") for s in (stills or [])}
    audio_mode = audio_mode or (lambda s: None)

    # R1: who is on camera, and how often
    appearances = {}
    for s in shots or []:
        if not on_camera(s, on_camera_kinds):
            continue
        for cid in s.get("entities") or []:
            if cid in chars:
                appearances.setdefault(cid, []).append(s["tag"])
    for cid, tags in appearances.items():
        e = chars[cid]
        if len(tags) >= 2 and not (e.get("master") and e.get("voice")):
            missing = [k for k in ("master", "voice") if not e.get(k)]
            errs.append("cast lock: %s is on camera in %d shots (%s) but declares no %s - one master still + "
                        "one voice per character, before any motion spend"
                        % (cid, len(tags), ", ".join(tags[:4]) + ("..." if len(tags) > 4 else ""), " + ".join(missing)))

    # R5: the master exists
    for cid, e in chars.items():
        if e.get("master") and e["master"] not in still_tags:
            errs.append("cast lock: %s master %r is not a still in this manifest" % (cid, e["master"]))

    # R2-R4 per shot
    for s in shots or []:
        if not on_camera(s, on_camera_kinds):
            continue
        tag = s["tag"]
        locked = [chars[c] for c in (s.get("entities") or []) if c in chars and chars[c].get("master")]
        for e in locked:
            if not counts_as_master(s.get("seed"), e["master"], stills):
                errs.append("%s: %s speaks here but the shot starts from %r, not the master %r (or a still "
                            "derived_from it)" % (tag, e["id"], s.get("seed"), e["master"]))
        voiced = [chars[c] for c in (s.get("entities") or []) if c in chars and chars[c].get("voice")]
        if s.get("dialogue"):
            for e in voiced:
                v = e["voice"] or {}
                if v.get("vo"):
                    if audio_mode(s) != "track":
                        errs.append("%s: %s's voice is the VO master %s, so the line must be fired on an "
                                    "audio-track model (wan2_7) - not %s"
                                    % (tag, e["id"], v["vo"], s.get("model") or "the manifest's motion model"))
                    elif not s.get("audio_file"):
                        errs.append("%s: %s's line must be cut from %s and passed as audio_file" % (tag, e["id"], v["vo"]))
                elif v.get("lock"):
                    if s.get("voice") != v["lock"]:
                        errs.append("%s: %s speaks with voice lock %r but the shot carries voice=%r"
                                    % (tag, e["id"], v["lock"], s.get("voice")))
            if audio_mode(s) == "track":
                need = len(s["dialogue"].split()) / WORDS_PER_SEC
                if need > TRACK_MAX_SEC:
                    errs.append("%s: %d words need ~%.0fs, over the %ds an audio-track clip holds - split the "
                                "line at a sentence break into two shots off the same master"
                                % (tag, len(s["dialogue"].split()), need, TRACK_MAX_SEC))
    return errs, warns
