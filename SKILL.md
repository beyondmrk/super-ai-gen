---
name: super-ai-gen
description: Use when Mark wants AI-generated ad assets fired on the Higgsfield CLI for ANY client that has no dedicated generation skill, or when a brand is added to clients.json and needs a fire path the same day - he says "super-ai-gen", "fire this for <client>", "run the general gen skill", pastes a manifest for a new brand, or asks which client a project is on. Also the shared fire engine that per-client skills can route through. NOT for the Higgsfield MCP or Google Flow clients (starplat-local, flow-local own those), NOT for writing the script, NOT a video editor.
---

# super-ai-gen - one fire path, any client

> Its own repo (`D:\super-ai-gen`, installed by junction at `~/.claude/skills/super-ai-gen`), separate from blc-ad-skills. Inside the BLC repo it also runs the spine `asset-qc-local/PIPELINE.md`. This file adds one thing: a fire script that takes the brand as a DECLARED key and reads everything the brand means from `clients.json`.

## Checklist (on top of the spine)

0. **Collect the three prefire inputs from Mark, every project (Mark, 2026-09-24):** (a) the **Google Drive folder link with EDIT permission** for this project, (b) which **IMAGE model** and which **VIDEO model** to fire on - run `python ~/.claude/skills/super-ai-gen/fire.py --models <client>` and put that list in front of him as a plain question with one pick each. Never pick for him, never default from the template. (c) the client key. Enforced: `prefire.py` closes the gate without them; `fire.py` refuses a real fire of any model the marker does not declare.
1. **Run the gate**: `python ~/.claude/skills/super-ai-gen/prefire.py --client <key> --project "<folder>" --drive "<link>" --model <image> --model <video> --script "<brief>"`. It checks (1) the Drive folder is readable and EDITABLE and holds `Creatives/` + `Elements/{Stills,Clips,Audio}` (created if missing, locally too), (2) the Higgsfield CLI is installed, signed in, on the client's account and workspace, (3) exactly one image + one video model, both allowed. It writes `Creatives/_preflight.json` (the client marker, with `open: true/false`). Exit 2 = closed; fix the FLAG rows and re-run. Inside the BLC repo the spine's `preflight.py --refs` / `--align` rows are additional, not a replacement.
2. **Manifest** at `Creatives/<slug>.json` from `manifest_template.json`, with `project.client` = the same key and `project.models` = exactly the models chosen in step 0.
3. **Dry-run** every stage: `python ~/.claude/skills/super-ai-gen/fire.py --manifest "<m.json>" --stage stills --dry-run` (Gate 1 cost).
4. **Fire** the same command without `--dry-run`, motion detached with a log. Stream results as they land.
5. **Postflight** with the same `--client` (spine step 9). It refuses a key that differs from the marker.

## How the brand is identified

Never inferred. Three sources may declare it, and every one present must agree:

| Source | Written by |
|---|---|
| `--client <key>` | the command |
| `project.client` in the manifest | the manifest author |
| `Creatives/_preflight.json` → `client` | `prefire.py` (or the spine's `preflight.py`) |

Exit codes: a model the marker never declared = 2 on a real fire (warn on dry-run) · no source at all = 3 · two sources disagree = 2 · key not in `clients.json` = 3 · client fires through MCP/Flow = 2 · real fire with no marker = 2 · signed-in account or workspace is not the client's = 2 (the same check as preflight row 2) · a model outside `models_allowed` = 1.

**New brand = one `clients.json` entry + a reference folder. No new skill.** Optional per-brand keys the fire script reads: `rates` (measured credit overrides) and `fire_lints` (a module with `lint(stage, manifest, stills, shots)` for the brand's craft checks).

## Fire script

```
python ~/.claude/skills/super-ai-gen/fire.py --manifest "<m.json>" --stage stills|motion [--dry-run] [--only T1,T2] [--takes N] [--from-take N] [--reroll] [--client key]
python ~/.claude/skills/super-ai-gen/fire.py --shotlist "<shotlist_x.py>" --stage motion --dry-run     # legacy shotlist-module input, same engine
python ~/.claude/skills/super-ai-gen/fire.py --list-clients
```

Engines it drives, with the CLI conventions each one needs baked in: `gpt_image_2`, `nano_banana_flash` / `_2` / `_pro` (stills); `seedance_2_5`, `veo3_1_lite`, `gemini_omni_flash_1_1`, `kling3_0` (motion). Duration grids, bounded-clip rules, audio flag per model, JSON-vs-prose prompt, and the resume / lockfile / job-recovery guards are all in `fire.py` - read its docstring.

Output layout is the spine's: `Elements/Stills/<tag>.png`, `Elements/Clips/<prefix>_<tag>_vNN.mp4`. A regen is the next `vN` beside the prior take (`--takes` / `--from-take` for motion, `--reroll` for stills). Nothing is overwritten, renamed or moved. A legacy shotlist keeps its plain `<id>.png` / `<id>.mp4` names so old projects resume untouched.

## Manifest shape

`manifest_template.json` is the contract. `project.locks.voice_lock` is pasted verbatim into every speaking shot. `project.placeholders` tokens are replaced byte-identically in every prompt. `project.refs` maps a ref name to a flat file; a `<name>_panels` folder beside it wins (spine step 4). Legacy manifests (locks at project level, `<PRODUCT>`-style tokens) load unchanged.

## Verification

```
python -m pytest ~/.claude/skills/super-ai-gen/tests -q
```

27 tests across fire.py and prefire.py: every refusal path above, the three prefire checks (Drive edit permission, CLI account and workspace, model choice), the cost math with a client rate override, the model duration lints, regen-in-place, the legacy shotlist loader, and the per-model command shapes. The stub fails any test that reaches a `generate create`.

## Packaging (GitHub)

This folder is self-contained: `prefire.py`, `fire.py`, `panels.py`, `clients.example.json`, `manifest_template.json`, `tests/`, `README.md`. The real `clients.json` is git-ignored (accounts and workspace ids). Requirements are the Higgsfield CLI, rclone with a Google Drive remote, Python 3.10+. Install = clone into `~/.claude/skills/super-ai-gen` and copy the example registry; `README.md` has the full run.

## Common mistakes

| Mistake | What happens |
|---|---|
| Passing `--client` to "override" the marker | exit 2. Fix the wrong source; the run does not decide the brand |
| Adding a model to a manifest that the brand never allowed | exit 1 until `clients.json` says so |
| `--skip-account-check` on a client fire | tests only. Never on real credits |
| Firing a still model at 1k because it is cheaper | the manifest's `still_resolution` is the brand's doctrine; change it there with Mark |
