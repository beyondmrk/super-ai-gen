"""prefire.py gate: Drive edit permission, CLI account, model choice. Every external command is
stubbed; nothing touches rclone, Drive or Higgsfield.

    python -m pytest D:/Claude/.claude/skills/super-ai-gen/tests -q
"""
import json, os, sys
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import fire, prefire  # noqa: E402

REG = {"acme": {"skill": "super-ai-gen", "engine": "higgsfield-cli", "account": "acme@example.com",
                "workspace_id": "ws-acme", "workspace_name": "ACME",
                "models_allowed": ["nano_banana_flash", "kling3_0", "veo3_1_lite"]}}
LINK = "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz012345?usp=sharing"


class Fake:
    """Answers rclone / higgsfield calls. `edit` = mkdir succeeds; `email` = who is signed in."""
    def __init__(self, edit=True, email="acme@example.com", ws="\u2713 ACME ws-acme", installed=True):
        self.edit, self.email, self.ws, self.installed = edit, email, ws, installed
        self.dirs = set()
        self.calls = []

    def __call__(self, cmd, timeout=0):
        self.calls.append(cmd)
        exe = os.path.basename(str(cmd[0])).lower()
        if "rclone" in exe:
            if cmd[1] == "listremotes":
                return 0, "gdrive:\n"
            if cmd[1] == "lsjson":
                if cmd[2].endswith(":Creatives"):
                    return 0, "[]"
                return 0, json.dumps([{"Path": d, "Name": d.split("/")[-1]} for d in sorted(self.dirs)])
            if cmd[1] == "mkdir":
                if not self.edit:
                    return 1, "Error: googleapi: Error 403: insufficientFilePermissions"
                self.dirs.add(cmd[2].split(":", 1)[1])
                return 0, ""
            if cmd[1] == "copy":
                return 0, ""
        if cmd[1:3] == ["account", "status"]:
            return (0, "Signed in as %s\nCredits: 100" % self.email) if self.email else (1, "not logged in")
        if cmd[1:3] == ["workspace", "list"]:
            return 0, "  Private\n%s\n" % self.ws
        return 0, ""


@pytest.fixture
def env(tmp_path, monkeypatch):
    reg = tmp_path / "clients.json"
    reg.write_text(json.dumps(REG), encoding="utf-8")
    monkeypatch.setattr(fire, "CLIENTS_PATH", str(reg))
    monkeypatch.setattr(prefire, "rclone_bin", lambda: "rclone")
    monkeypatch.setattr(prefire, "hf_bin", lambda: "higgsfield")
    fake = Fake()
    monkeypatch.setattr(prefire, "run", fake)
    return tmp_path / "PROJ", fake, monkeypatch


def marker(project):
    return json.load(open(project / "Creatives" / "_preflight.json", encoding="utf-8"))


def base(project, *extra):
    return ["--client", "acme", "--project", str(project), "--drive", LINK,
            "--model", "nano_banana_flash", "--model", "kling3_0"] + list(extra)


def test_drive_link_parses_to_id():
    assert prefire.drive_id(LINK) == "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    assert prefire.drive_id("https://drive.google.com/open?id=1AbCdEfGhIjKlMnOpQrStUvWxYz012345") == "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    assert prefire.drive_id("1AbCdEfGhIjKlMnOpQrStUvWxYz012345") == "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    assert prefire.drive_id("not a link") is None


def test_happy_path_opens_gate_and_records_everything(env, capsys):
    project, fake, _ = env
    assert prefire.main(base(project)) == 0
    rec = marker(project)
    assert rec["client"] == "acme" and rec["open"] is True
    assert rec["models"] == ["nano_banana_flash", "kling3_0"]
    assert rec["drive_folder_id"] == "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    assert {"Creatives", "Elements/Stills", "Elements/Clips", "Elements/Audio"} <= fake.dirs   # created on Drive
    for sub in ("Creatives", "Elements/Stills", "Elements/Clips", "Elements/Audio"):
        assert (project / sub).is_dir()                                                          # and locally
    assert "gate OPEN" in capsys.readouterr().out


def test_missing_drive_link_closes_gate(env, capsys):
    project, _, _ = env
    argv = [x for x in base(project) if x != LINK and x != "--drive"]
    assert prefire.main(argv) == 2
    out = capsys.readouterr().out
    assert "EDIT permission" in out and marker(project)["open"] is False


def test_view_only_drive_folder_is_flagged(env, capsys):
    project, fake, _ = env
    fake.edit = False
    assert prefire.main(base(project)) == 2
    assert "EDITOR access" in capsys.readouterr().out


def test_cli_not_installed_is_flagged(env, capsys):
    project, _, mp = env
    mp.setattr(prefire, "hf_bin", lambda: None)
    assert prefire.main(base(project)) == 2
    assert "npm i -g @higgsfield/cli" in capsys.readouterr().out


def test_wrong_account_is_flagged(env, capsys):
    project, fake, _ = env
    fake.email = "someone@else.example"
    assert prefire.main(base(project)) == 2
    assert "STOP" in capsys.readouterr().out


def test_wrong_workspace_is_flagged(env, capsys):
    project, fake, _ = env
    fake.ws = "\u2713 OTHER ws-other"
    assert prefire.main(base(project)) == 2
    assert "workspace set ws-acme" in capsys.readouterr().out


def test_workspace_name_prefix_is_not_a_match(env, capsys):
    """"ACME" must not pass on a selected line for "ACME Archive" - the recorded id decides."""
    project, fake, _ = env
    fake.ws = "\u2713 ACME Archive ws-archive"
    assert prefire.main(base(project)) == 2
    assert "workspace set ws-acme" in capsys.readouterr().out


def test_workspace_id_matches_as_a_whole_token_only():
    assert prefire.workspace_selected("\u2713 ACME ws-acme", "ws-acme", "ACME")
    assert prefire.workspace_selected("* ACME (ws-acme)", "ws-acme", "ACME")
    assert not prefire.workspace_selected("\u2713 ACME ws-acme2", "ws-acme", "ACME")
    # no id recorded: the name matches as whole words, never as a prefix
    assert prefire.workspace_selected("\u2713 ACME", "", "ACME")
    assert not prefire.workspace_selected("\u2713 ACMECORP", "", "ACME")


def test_missing_models_prints_the_ask(env, capsys):
    project, _, _ = env
    argv = ["--client", "acme", "--project", str(project), "--drive", LINK]
    assert prefire.main(argv) == 2
    out = capsys.readouterr().out
    assert "ASK the editor" in out and "image  nano_banana_flash" in out and "video  kling3_0" in out
    assert marker(project)["models"] == []


def test_two_video_models_is_not_a_choice(env, capsys):
    project, _, _ = env
    assert prefire.main(base(project, "--model", "veo3_1_lite")) == 2
    assert "exactly ONE image model and ONE video model" in capsys.readouterr().out


def test_model_outside_allowed_is_flagged(env, capsys):
    project, _, _ = env
    argv = ["--client", "acme", "--project", str(project), "--drive", LINK, "--model", "gpt_image_2", "--model", "kling3_0"]
    assert prefire.main(argv) == 2
    assert "models_allowed" in capsys.readouterr().out


def test_closed_gate_marker_refuses_a_real_fire(env, capsys):
    project, fake, _ = env
    fake.edit = False
    prefire.main(base(project))                     # closed: Drive flagged, models chosen
    m = {"project": {"client": "acme", "root": str(project), "prefix": "T",
                     "models": {"still": "nano_banana_flash", "still_resolution": "2k", "motion": "kling3_0", "aspect_ratio": "9:16"}},
         "stills": [{"tag": "S01", "prompt": "a kitchen", "refs": []}], "shots": []}
    mp = project / "Creatives" / "m.json"
    mp.write_text(json.dumps(m), encoding="utf-8")
    try:
        rc = fire.main(["--manifest", str(mp), "--stage", "stills", "--skip-account-check"])
    except SystemExit as e:
        rc = e.code
    assert rc == 2 and "gate is CLOSED" in capsys.readouterr().out
