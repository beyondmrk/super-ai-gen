#!/usr/bin/env python3
"""What a reference actually contributes to a fire — the panels, not the flat file.

Shared by every generation skill (Mark, 2026-09-21: "anchors are sheets").

REF_LOCKS.md: a recurring character locks to a model sheet cropped into per-angle panels, and
a product to a product sheet. The PANELS are what a shot inherits — never the hero still (one
angle, so the model invents every other one, which is where identity and scale drift come
from) and never the whole 16:9 sheet (its studio sweep and panel layout can bleed into a 9:16
scene).

This lives here rather than in one skill because the bug it fixes was exactly that the rule
lived in the shared gates while each skill's fire path still attached a flat file. One
implementation, four callers.

    from panels import expand, split_angle
    expand("…/_anchors/CHAR_X.png")            -> [front.png, three_quarter.png]
    expand("…/_anchors/CHAR_X.png", "closeup") -> [closeup.png]
    expand("…/_anchors/set_office.png")        -> [set_office.png]   (no panels dir)

A ref may name its angle inline as "CHAR_X@closeup"; split_angle() parses that.
A named angle that was never cut returns [] — a typo is a build error, not a reason to
quietly fire the wrong reference.
"""
import os

# Identity from the front, body form from the angle, without over-constraining pose.
PANEL_DEFAULT = ("front", "three_quarter")
# Angles charsheet.py / productsheet.py can cut. Not a contract, just what a caller may ask for.
KNOWN_ANGLES = ("front", "three_quarter", "profile", "closeup", "seated", "height",
                "face", "scale", "plugged", "top_down", "box", "back", "detail")


def split_angle(spec):
    """"CHAR_X@closeup" -> ("CHAR_X", "closeup"); "CHAR_X" -> ("CHAR_X", None)."""
    name, sep, angle = str(spec).partition("@")
    return name, (angle or None) if sep else None


def panels_dir_for(flat_path):
    """The <base>_panels folder beside a flat ref file, or None."""
    if not flat_path:
        return None
    base = os.path.splitext(str(flat_path))[0]
    d = base + "_panels"
    return d if os.path.isdir(d) else None


def expand(flat_path, angle=None, default=PANEL_DEFAULT):
    """Every file this reference contributes, in attach order.

    Panels win over the flat file whenever they exist. Falls back to the flat file when the
    entity has no panels, so projects that predate sheets keep working untouched. Returns []
    only when a specifically named angle was never cut."""
    pdir = panels_dir_for(flat_path)
    if pdir:
        wanted = (angle,) if angle else tuple(default)
        found = [os.path.join(pdir, a + ".png") for a in wanted]
        found = [p for p in found if os.path.isfile(p)]
        if found:
            return found
        if angle:
            return []          # named but never cut - the caller should refuse
    return [flat_path] if flat_path and os.path.isfile(str(flat_path)) else []


def missing_angle_message(ref, flat_path, angle):
    """Why expand() came back empty, in words a gate can print."""
    pdir = panels_dir_for(flat_path)
    if angle and pdir:
        return ("ref '%s': panel '%s.png' was never cut from the sheet - run "
                "charsheet.py --crop (or productsheet.py --crop) on %s"
                % (ref, angle, os.path.basename(pdir)))
    return "ref '%s' not found on disk: %s" % (ref, flat_path)
