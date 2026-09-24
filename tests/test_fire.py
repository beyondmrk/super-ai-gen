"""Refusal paths and dry-run math for super-ai-gen/fire.py. No credit is ever spent here:
subprocess.run is replaced with a stub that FAILS the test if a `generate create` is attempted.

    python -m pytest D:/Claude/.claude/skills/super-ai-gen/tests -q
"""
import json, os, sys, subprocess
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import fire  # noqa: E402

REG = {
    "_readme": ["test registry"],
    "acme": {"skill": "super-ai-gen", "engine": "higgsfield-cli", "account": "acme@example.com",
             "workspace_id": "ws-acme", "workspace_name": "ACME",
             "models_allowed": ["nano_banana_flash", "kling3_0", "veo3_1_lite"],
             "rates": {"veo3_1_lite": {"off": 1.0, "on": 1.5}}},
    "mcpclient": {"skill": "starplat-local", "engine": "higgsfield-mcp", "account": "",
                  "models_allowed": ["nano_banana_2"]},
}


def manifest(root, client="acme", motion="kling3_0"):
    return {
        "project": {"client": client, "root": root, "prefix": "T", "takes_default": 1,
                    "models": {"still": "nano_banana_flash", "still_resolution": "2k",
                               "motion": motion, "aspect_ratio": "9:16"},
                    "locks": {"voice_lock": "warm mid-40s American man, dry room"},
                    "refs": {}},
        "stills": [{"tag": "S01", "prompt": "a kitchen at dawn, no people", "refs": []},
                   {"tag": "S02", "prompt": "the same kitchen, closer", "refs": []}],
        "shots": [{"tag": "S01", "kind": "BR", "duration": 5, "seed": "S01",
                   "action": "the camera pushes in slowly toward the window"},
                  {"tag": "S02", "kind": "TH", "duration": 5, "seed": "S02",
                   "action": "she talks to the phone, camera drifts left",
                   "dialogue": "by day ninety she stopped asking"}],
    }


@pytest.fixture
def project(tmp_path, monkeypatch):
    reg = tmp_path / "clients.json"
    reg.write_text(json.dumps(REG), encoding="utf-8")
    monkeypatch.setattr(fire, "CLIENTS_PATH", str(reg))
    root = tmp_path / "PROJ"
    (root / "Creatives").mkdir(parents=True)
    (root / "Elements" / "Stills").mkdir(parents=True)
    (root / "Elements" / "Clips").mkdir(parents=True)

    def no_spend(cmd, *a, **k):
        if len(cmd) > 2 and cmd[1:3] == ["generate", "create"]:
            raise AssertionError("a generate create was attempted: %s" % cmd[:4])
        if len(cmd) > 2 and cmd[1:3] == ["generate", "cost"]:
            return subprocess.CompletedProcess(cmd, 0, "cost 2.0 credits\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(fire.subprocess, "run", no_spend)
    return root


def write_manifest(root, m):
    p = root / "Creatives" / "m.json"
    p.write_text(json.dumps(m), encoding="utf-8")
    return str(p)


def marker(root, client):
    (root / "Creatives" / "_preflight.json").write_text(json.dumps({"client": client}), encoding="utf-8")


def run(argv):
    try:
        return fire.main(argv)
    except SystemExit as e:
        return e.code


def test_no_client_anywhere_exits_3(project, capsys):
    m = manifest(str(project), client=None)
    assert run(["--manifest", write_manifest(project, m), "--stage", "stills", "--dry-run"]) == 3
    assert "NO CLIENT DECLARED" in capsys.readouterr().out


def test_mismatch_between_flag_and_marker_exits_2(project, capsys):
    marker(project, "acme")
    m = manifest(str(project), client="acme")
    assert run(["--client", "mcpclient", "--manifest", write_manifest(project, m), "--stage", "stills", "--dry-run"]) == 2
    assert "CLIENT MISMATCH" in capsys.readouterr().out


def test_unknown_client_exits_3(project):
    m = manifest(str(project), client="nobody")
    assert run(["--manifest", write_manifest(project, m), "--stage", "stills", "--dry-run"]) == 3


def test_non_cli_engine_exits_2(project, capsys):
    m = manifest(str(project), client="mcpclient")
    assert run(["--manifest", write_manifest(project, m), "--stage", "stills", "--dry-run"]) == 2
    assert "not the Higgsfield CLI" in capsys.readouterr().out


def test_real_fire_without_marker_refuses(project, capsys):
    m = manifest(str(project))
    assert run(["--manifest", write_manifest(project, m), "--stage", "stills", "--skip-account-check"]) == 2
    assert "gate has not run" in capsys.readouterr().out


def test_model_outside_allowed_list_is_an_error(project, capsys):
    marker(project, "acme")
    m = manifest(str(project), motion="seedance_2_5")
    assert run(["--manifest", write_manifest(project, m), "--stage", "motion", "--dry-run"]) == 1
    assert "models_allowed" in capsys.readouterr().out


def test_dry_run_stills_math_and_no_spend(project, capsys):
    marker(project, "acme")
    m = manifest(str(project))
    assert run(["--manifest", write_manifest(project, m), "--stage", "stills", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "2 to fire  ->  4.0 cr" in out and "DRY RUN" in out and "live rate probe" in out


def test_dry_run_motion_uses_client_rate_override(project, capsys):
    marker(project, "acme")
    m = manifest(str(project), motion="veo3_1_lite")
    for s in m["shots"]:
        s["duration"] = 6
    # 6s silent @1.0 + 6s dialogue @1.5 = 6 + 9 = 15.0 (client override, not the 1.5/1.5 table)
    assert run(["--manifest", write_manifest(project, m), "--stage", "motion", "--dry-run"]) == 0
    assert "->  15.0 cr" in capsys.readouterr().out


def test_veo_grid_and_kling_range_lints(project, capsys):
    marker(project, "acme")
    m = manifest(str(project), motion="veo3_1_lite")
    m["shots"][0]["duration"] = 5
    assert run(["--manifest", write_manifest(project, m), "--stage", "motion", "--dry-run"]) == 1
    assert "accepts only 4/6/8" in capsys.readouterr().out


def test_dialogue_without_voice_lock_is_an_error(project, capsys):
    marker(project, "acme")
    m = manifest(str(project))
    m["project"]["locks"] = {}
    assert run(["--manifest", write_manifest(project, m), "--stage", "motion", "--dry-run"]) == 1
    assert "no voice lock" in capsys.readouterr().out


def test_regen_lands_next_take_in_place(project):
    marker(project, "acme")
    m = manifest(str(project))
    mm = fire.load_manifest(write_manifest(project, m))
    v1 = project / "Elements" / "Clips" / "T_S01_v01.mp4"
    v1.write_bytes(b"x" * (fire.MIN_BYTES + 1))
    todo = fire.plan(mm, "motion", [], mm["shots"][:1], None, 1, False)
    assert todo == []                                  # v01 on disk = skipped, never re-fired
    todo = fire.plan(mm, "motion", [], mm["shots"][:1], 1, 2, False)
    assert [os.path.basename(p) for _, _, p in todo] == ["T_S01_v02.mp4"]
    assert v1.exists()                                  # the prior take is untouched
    s1 = project / "Elements" / "Stills" / "S01.png"
    s1.write_bytes(b"x" * (fire.MIN_BYTES + 1))
    todo = fire.plan(mm, "stills", mm["stills"][:1], [], None, 1, True)
    assert [os.path.basename(p) for _, _, p in todo] == ["S01_v02.png"]


def test_shotlist_input_normalises_to_plain_names(project, tmp_path):
    marker(project, "acme")
    sl = tmp_path / "shotlist_t.py"
    sl.write_text(
        'PROJECT = {"root": %r, "stills": "Elements/Stills", "clips": "Elements/Clips", "anchors": "Elements/Stills/_anchors"}\n'
        'MODELS = {"still": "gpt_image_2", "motion": "veo3_1_lite", "hero": "kling3_0"}\n'
        'VOICE = {"tone": "warm"}\n'
        'SHOTS = [{"id": "H1", "kind": "TH", "dur": 6, "refs": [], "still": "a face", "action": "camera pushes in", "line": "hello there friend"},\n'
        '         {"id": "B02", "kind": "BR", "dur": 5, "model": "hero", "refs": [], "still": "a room", "action": "slow drift left"}]\n'
        % str(project), encoding="utf-8")
    mm = fire.load_shotlist(str(sl))
    assert mm["project"]["naming"] == "plain"
    assert mm["shots"][1]["model"] == "kling3_0"      # "hero" resolved
    assert os.path.basename(fire.clip_path(mm, "B02", 1)) == "B02.mp4"
    assert os.path.basename(fire.clip_path(mm, "B02", 2)) == "B02_v02.mp4"
    cmd = fire.motion_cmd(mm, mm["shots"][0])
    assert "--generate-audio" in cmd and cmd[cmd.index("--generate-audio") + 1] == "true"
    cmd = fire.motion_cmd(mm, mm["shots"][1])
    assert "--sound" in cmd and cmd[cmd.index("--sound") + 1] == "off" and "--mode" in cmd


def test_model_not_chosen_at_preflight_refuses_real_fire(project, capsys):
    (project / "Creatives" / "_preflight.json").write_text(
        json.dumps({"client": "acme", "models": ["nano_banana_flash", "veo3_1_lite"]}), encoding="utf-8")
    m = manifest(str(project))                       # motion = kling3_0, allowed but never chosen
    assert run(["--manifest", write_manifest(project, m), "--stage", "motion", "--skip-account-check"]) == 2
    assert "not chosen at preflight" in capsys.readouterr().out


def test_model_not_chosen_only_warns_on_dry_run(project, capsys):
    (project / "Creatives" / "_preflight.json").write_text(
        json.dumps({"client": "acme", "models": ["nano_banana_flash", "veo3_1_lite"]}), encoding="utf-8")
    m = manifest(str(project))
    assert run(["--manifest", write_manifest(project, m), "--stage", "motion", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "WARN  model(s) kling3_0 were not chosen" in out and "DRY RUN" in out


def test_models_ask_lists_allowed_with_rates(project, capsys):
    assert run(["--models", "acme"]) == 0
    out = capsys.readouterr().out
    assert "image  nano_banana_flash" in out and "video  veo3_1_lite" in out and "1.0/s silent, 1.5/s audio" in out


def test_command_shapes_per_model(project):
    m = manifest(str(project), motion="seedance_2_5")
    mm = fire.load_manifest(write_manifest(project, m))
    cmd = fire.motion_cmd(mm, mm["shots"][1])
    assert cmd[3] == "seedance_2_5"
    assert cmd[cmd.index("--mode") + 1] == "omni_reference"
    assert cmd[cmd.index("--resolution") + 1] == "720p"
    assert cmd[cmd.index("--generate-audio") + 1] == "true"
    assert cmd[cmd.index("--prompt") + 1].startswith("Follow this shot spec exactly. {")
    still = fire.still_cmd(mm, mm["stills"][0])
    assert still[3] == "nano_banana_flash" and "--quality" not in still
