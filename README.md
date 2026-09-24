# super-ai-gen

One Higgsfield fire path for any client. The brand is a **declared key**, never a guess: you name the client once, a gate checks Drive, the CLI account, and your model choice, and the fire script refuses anything that disagrees with what the gate recorded.

Everything the skill needs is in this folder. It has no dependency on any other skill.

## What is in the folder

| File | Role |
|---|---|
| `SKILL.md` | The checklist Claude follows. Read this first. |
| `prefire.py` | START-OF-GEN gate: Drive folder with edit permission, Higgsfield CLI connected and on the right account, one image + one video model chosen. Writes `Creatives/_preflight.json`. |
| `fire.py` | The fire script. Stills and motion, cost math, live rate probe, resume, regen-in-place, job recovery. Refuses when the gate is closed or the brand disagrees. |
| `clients.example.json` | The registry template. Copy to `clients.json` (git-ignored) and fill one entry per client. |
| `manifest_template.json` | The per-project manifest contract. |
| `panels.py` | Reference-panel helper: a `<name>_panels/` folder beside a ref wins over the flat file. |
| `tests/` | Refusal paths, cost math, command shapes. No credit is ever spent by a test. |

## Requirements

- Python 3.10+ (standard library only; `pytest` for the tests)
- [Higgsfield CLI](https://www.npmjs.com/package/@higgsfield/cli): `npm i -g @higgsfield/cli`, then `higgsfield auth login`
- [rclone](https://rclone.org/) with a Google Drive remote named `gdrive` (`rclone config`), or pass `--rclone-remote <name>`

## Install as a Claude Code skill

```bash
git clone <this repo> ~/.claude/skills/super-ai-gen
cp ~/.claude/skills/super-ai-gen/clients.example.json ~/.claude/skills/super-ai-gen/clients.json
```

Fill `clients.json`: the account email, the workspace, and the models each client may run on. Project-level installs work the same under `<repo>/.claude/skills/super-ai-gen`.

## Run one project

```bash
# 1. the gate: Drive + account + models. Missing models -> it prints the menu and exits 2.
python prefire.py --client acme --project "D:/Ads/ACME/09.24.26 - Launch" \
  --drive "https://drive.google.com/drive/folders/<id>" \
  --model nano_banana_flash --model kling3_0 --script "brief.pdf"

# 2. the manifest: copy manifest_template.json to <project>/Creatives/<slug>.json, fill it

# 3. cost, then fire
python fire.py --manifest "<project>/Creatives/<slug>.json" --stage stills --dry-run
python fire.py --manifest "<project>/Creatives/<slug>.json" --stage stills
python fire.py --manifest "<project>/Creatives/<slug>.json" --stage motion --dry-run
python fire.py --manifest "<project>/Creatives/<slug>.json" --stage motion
```

`python fire.py --models acme` prints a client's allowed models with rates and duration rules, which is what you put in front of the editor for the model choice.

## What stops a fire

| Condition | Exit |
|---|---|
| No client declared (no `--client`, no `project.client`, no marker) | 3 |
| Two sources name different clients | 2 |
| Client unknown to `clients.json`, or not a Higgsfield CLI client | 3 / 2 |
| Real fire with no marker, or a marker with FLAG rows | 2 |
| A model the marker does not declare (dry-run only warns) | 2 |
| Signed-in account or workspace is not the client's | 2 |
| A model outside `models_allowed`, or a shot that fails a lint | 1 |

Every refusal names its fix.

## Regen rule

A regen is the next `vNN` beside the prior take: `--takes` / `--from-take` for motion, `--reroll` for stills. Nothing is overwritten, renamed or moved.

## Tests

```bash
python -m pytest tests -q
```
