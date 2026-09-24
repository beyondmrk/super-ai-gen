# super-ai-gen

A guarded command-line pipeline for generating AI ad assets (stills and video clips) through the [Higgsfield CLI](https://www.npmjs.com/package/@higgsfield/cli), built for agencies that fire on several clients' accounts and cannot afford to bill the wrong one.

It is packaged as a [Claude Code](https://claude.com/claude-code) skill, but the two scripts run on their own from any terminal.

## Why it exists

When one machine fires generations for many brands, the expensive mistakes are always the same: credits spent on the wrong workspace, a model the client never approved, a take overwritten by its regen, a half-finished run fired twice. `super-ai-gen` makes every one of those a refusal instead of a surprise.

The core idea is that **the brand is declared, never inferred**. You name the client once at the gate. From then on, up to three sources can claim a brand for a run (the command line, the manifest, and the marker the gate wrote), and if any two disagree, nothing fires.

## How a project runs

```
prefire.py  -->  manifest  -->  fire.py --dry-run  -->  fire.py  -->  results
 (the gate)      (the shots)     (cost, no spend)      (stills, then motion)
```

1. **The gate** (`prefire.py`) checks three things and writes `Creatives/_preflight.json` into the project:
   - the Google Drive delivery folder exists, is editable, and has the standard `Creatives/` and `Elements/{Stills,Clips,Audio}` layout (created if missing, locally and on Drive)
   - the Higgsfield CLI is installed, signed in, and on the client's account and workspace
   - exactly one image model and one video model were chosen, both from the client's allowed list
2. **The manifest** (`manifest_template.json`) describes the shots: reference images, a verbatim voice lock for speaking shots, placeholder tokens, one entry per still and per clip.
3. **Dry run** prints the cost of the stage from a measured rate table plus one live `generate cost` probe. Nothing is spent.
4. **Fire** runs the stage for real. Results stream to `Elements/Stills/<tag>.png` and `Elements/Clips/<prefix>_<tag>_vNN.mp4`, and run state goes to `_gen_run.json` so an interrupted run resumes.

## Quick start

Requirements: Python 3.10+ (standard library only), the Higgsfield CLI, and [rclone](https://rclone.org/) with a Google Drive remote.

```bash
npm i -g @higgsfield/cli && higgsfield auth login
rclone config            # create a Google Drive remote named "gdrive"

git clone https://github.com/beyondmrk/super-ai-gen.git ~/.claude/skills/super-ai-gen
cd ~/.claude/skills/super-ai-gen
cp clients.example.json clients.json    # fill one entry per client
```

Then, for a project:

```bash
# see which models this client may run on, with rates
python fire.py --models acme

# open the gate
python prefire.py --client acme --project "D:/Ads/ACME/09.24.26 - Launch" \
  --drive "https://drive.google.com/drive/folders/<id>" \
  --model nano_banana_flash --model kling3_0 --script "brief.pdf"

# copy manifest_template.json to <project>/Creatives/<slug>.json and fill it, then:
python fire.py --manifest "<project>/Creatives/<slug>.json" --stage stills --dry-run
python fire.py --manifest "<project>/Creatives/<slug>.json" --stage stills
python fire.py --manifest "<project>/Creatives/<slug>.json" --stage motion --dry-run
python fire.py --manifest "<project>/Creatives/<slug>.json" --stage motion
```

## The client registry

`clients.json` is the only place a brand is defined. It is git-ignored because it holds account emails and workspace IDs. Each entry carries:

| Key | Meaning |
|---|---|
| `account` | the email `higgsfield account status` must print before any spend |
| `workspace_id`, `workspace_name` | the workspace that must be selected in `higgsfield workspace list` |
| `models_allowed` | the models this client may be fired on |
| `rates` | optional measured credit prices that override the built-in table |
| `fire_lints` | optional path to a module with the brand's own craft checks |
| `product_registry` | optional folder of product reference sheets, so products are never generated |

Adding a brand is one registry entry. No code changes.

## Supported models

| Stage | Models |
|---|---|
| Stills | `gpt_image_2`, `nano_banana_flash`, `nano_banana_2`, `nano_banana_pro` |
| Motion | `seedance_2_5`, `veo3_1_lite`, `gemini_omni_flash_1_1`, `kling3_0` |

Each model's duration grid, resolution, audio flag, prompt format (JSON or prose) and CLI quirks live in the `MODELS` table at the top of `fire.py`. Rates are measured values and drift; the dry-run's live probe is what catches a moved one.

## What stops a fire

| Condition | Exit |
|---|---|
| No client declared anywhere | 3 |
| Two sources name different clients | 2 |
| Client unknown to `clients.json`, or not a Higgsfield CLI client | 3 / 2 |
| Real fire with no gate marker, or a marker with failed rows | 2 |
| A model the gate never recorded (dry-run only warns) | 2 |
| Signed-in account or workspace is not the client's | 2 |
| A model outside `models_allowed`, or a shot that fails a lint | 1 |

Every refusal names its fix. Lints run before the first credit: wrong duration for the model, missing seed or end frame, missing reference, static motion prompt, off-pace dialogue, dialogue with no voice lock.

## Guarantees about files

- **Resume, never re-fire.** An output already on disk is skipped.
- **Regen in place.** A new take lands as the next `vNN` beside the prior one (`--takes` / `--from-take` for motion, `--reroll` for stills). Nothing is overwritten, renamed or moved.
- **One run per stage.** A lockfile makes a second concurrent run of the same stage exit instead of double-spending.
- **Reference panels win.** If a `<name>_panels/` folder sits beside a reference image, its cropped panels are used instead of the flat file (`panels.py`).

## Files

| File | Role |
|---|---|
| `prefire.py` | the start-of-generation gate |
| `fire.py` | the fire script: stills and motion, cost math, resume, regen, job recovery |
| `panels.py` | reference-panel helper |
| `clients.example.json` | registry template; copy to `clients.json` |
| `manifest_template.json` | the per-project manifest contract |
| `SKILL.md` | the checklist Claude Code follows when the skill is invoked |
| `tests/` | 27 tests covering every refusal path, the cost math and the command shapes |

## Tests

```bash
python -m pytest tests -q
```

No test ever spends a credit. The CLI is stubbed, and the stub fails any test that reaches a real `generate create`.

## License

[MIT](LICENSE)
