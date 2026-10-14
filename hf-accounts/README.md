---
name: hf-accounts
description: the operator's Higgsfield account router. Every Higgsfield CLI call on this laptop runs on one client login (EMPLOYER, Acme, Globex...), side by side, and nothing bills the wrong client. Use when a Higgsfield call fails with "HF ROUTER: refused.", when a generation run starts (dispatch the hf-account-checker subagent), when asked which Higgsfield account or workspace is active or to show every login, after a Higgsfield CLI update, or to add a client login ("add initech"). It never signs in, switches an account or selects a workspace on its own.
---

# hf-accounts

`vendor\hf.exe` of `@higgsfield/cli` is a small launcher that hands every call to
`router/router.py`. The router picks one login folder under `%USERPROFILE%\.hf-logins`
(registry: `logins.json`) and runs the real binary with that folder as its profile.

## Install (Windows)

This folder must live under `%USERPROFILE%\.claude\` (the router finds the real profile from its
own location, because a login launcher points USERPROFILE elsewhere). Cloning super-ai-gen to
`~/.claude/skills/super-ai-gen` does that. Then:

1. `python router/install.py install`. Needs `csc.exe` (.NET Framework) to build the launcher.
2. Copy `hf-account-checker.md` to `~/.claude/agents/`. Its `mcp__higgsfield__list_workspaces`
   tool name assumes your Higgsfield connector is called `higgsfield`; rename it to match yours.
3. `python router/accounts.py add <name> ...` for each client login (see Commands).

Domains: a login is `EMPLOYER` (exactly one; the job you do for someone else) or `AGENCY` (your
own clients, cross-checked against super-ai-gen's `clients.json`). The EMPLOYER/AGENCY folder
split comes from an optional `~/.claude/local-hooks/domain.py` exposing `job_of(path)`; without
it, folders carry no job and `HF_CLIENT` or a folder pattern in the registry picks the login.

## Checklist (read first)

1. **Refused with `HF ROUTER: refused.` (exit 64)?** Do what the message says, nothing else:
   - *No client login is identified* -> the call names no client. Set `HF_CLIENT=<login>` in
     the calling environment, or run it from the client's folder. Unsure which client? Ask the operator.
   - *Run the account check first* -> dispatch the **hf-account-checker** subagent for that
     login, then retry.
   - *Login X is on workspace Y but must be Z* -> STOP and tell the operator. Never `workspace set`.
   - *signals disagree* / *belongs to EMPLOYER (AGENCY) work* -> the folder and HF_CLIENT name
     different clients. Fix the call. Never route around it.
   - *real Higgsfield binary is missing* / *account router is missing* -> a CLI update. Go to
     step 4.
2. **Start of every generation run:** dispatch the hf-account-checker subagent for the run's
   login. Its stamp lasts 3 h. Before Higgsfield connector (MCP) generations, dispatch it with
   "connector check for client <K>".
3. **Never** sign in or out, switch an account, run `workspace set`, call the connector's
   `select_workspace`, run `hf.real.exe`, write a stamp by hand, or change `unmatched` /
   `require_stamp` in `logins.json` (those are the operator's switch-over, plan step B6). A refusal is
   information, not an obstacle.
4. **After any Higgsfield CLI update:** run `python router/install.py install`, then
   `python router/install.py status`. Every row must say `yes`.
5. **Done = a check passed.** Evidence is the `hf_check.py` RESULT line, `install.py status`
   all yes, or the unittest summary. Never call a login fine from reading its files.

## Commands

Run from `%USERPROFILE%\.claude\skills\hf-accounts`:

| Command | What it does |
|---|---|
| `python router/accounts.py status` | every login side by side: registered vs selected workspace, check stamp, connector stamp, install rows |
| `python router/hf_check.py <login>` | the account check; stamps the login for 3 h on a pass (the subagent runs it) |
| `python router/hf_check.py --connector --client K --workspace-id ID` | stamps the connector when ID is client K's workspace |
| `python router/accounts.py add <name> [--folder PATTERN]` | registry entry + `.hf-logins\<name>-login.cmd`. AGENCY clients bring account and workspace from `clients.json`; others need `--domain --account --workspace-id --workspace-name` |
| `python router/accounts.py finish <name>` | the operator's step after they sign in: `workspace set` to the REGISTERED workspace (through the router), then the check |
| `python router/accounts.py launcher <name>` | rewrite `<name>-login.cmd` in the HF_CLIENT form |
| `python router/install.py status` / `install` | router install state / (re)install after a CLI update |

**Add a client login:**
1. `accounts.py add <name>`.
2. the operator double-clicks `.hf-logins\<name>-login.cmd` and signs in.
3. `accounts.py finish <name>`, run by the operator or on their explicit word.

Adding a login changes the registry, so every existing stamp is void until its login is checked
again.

## How a call is placed

1. **Login:** `HF_CLIENT` first. Then a launcher's USERPROFILE (a login folder). Then the working
   folder: EMPLOYER folders via `local-hooks/domain.py`, AGENCY client folder patterns from the registry.
   If the signals disagree, the call is refused. A login from the other job is also refused.
2. **No client named:**
   - `unmatched: "default"` (Phase A, current): the call runs on the default Windows login as
     before. The exception is a spend from a folder of neither job, which is refused.
   - `unmatched: "refuse"` (after B6): help, version and model lists run on the first signed-in
     login. `workspace status|list` and `account status` print every login under `== name ==`.
     Everything else is refused.
3. **Workspace:** the login's selected workspace must equal the registry `workspace_id`. It is
   matched by id only, because three logins have a workspace called "Private". Setup commands
   (`auth`, `workspace`) skip this check so a fresh login can pick its workspace.
4. **Stamp** (only with `require_stamp: true`): a spend needs a valid check stamp,
   `.hf-logins\_checked\<login>.json`. A stamp is valid for `stamp_hours`, and only while the
   registry, every router module, the launcher and the login's `config.json` are unchanged since
   the check.
5. **Connector:** `local-hooks/account_gate.py` (only with `require_stamp`) denies Higgsfield MCP
   generation tools unless `_checked\connector.json` is fresh and was taken for the session's
   job. `select_workspace` voids that stamp. A switch made outside Claude stays invisible.

Every decision is one line in `.hf-logins\_router.log`. Refusals exit 64.

## What `hf_check.py` checks

| Check | Result |
|---|---|
| install | FAIL: launcher, real binary or a router module differs from the install manifest |
| account | FAIL: the login's email differs from the registry. WARN: the registry has no account |
| workspace | FAIL: the selected workspace id differs from the registry |
| default | FAIL (refuse mode only): the default Windows login is still signed in |
| scan | FAIL: a reference to `hf.real.exe`, or a Higgsfield binary, outside this skill. WARN: AGENCY code that calls the CLI without HF_CLIENT |
| clients | FAIL: an AGENCY login disagrees with `clients.json` on account (both set) or workspace_id |

It scans `~/.claude/skills`, `~/.claude/local-hooks` and any `--agency-root` you pass. A FAIL
writes no stamp and removes an older one. `clients.json` is `SUPER_AI_GEN_CLIENTS`, else the
one in the super-ai-gen folder above this one.

## Files

- `router/`: `hfstub.cs` (launcher), `router.py`, `hfacct.py` (decisions), `strict.py` (B6-flag
  behaviour, loaded only under those flags), `stamps.py`, `hf_check.py`, `accounts.py`,
  `install.py`.
- `tests/`: unittest against temp vendors and registries, never the real ones. Set
  `HFACCT_TMP=<scratch dir>`, then run `python -m unittest discover -s tests`.
- Subagent: `hf-account-checker.md`; copy it to `~/.claude/agents/`.
