"""Leak guard: nothing that names a real account, workspace, Drive folder or token is tracked.

clients.json (emails + workspace ids) and every project artefact (_preflight.json carries a Drive
folder id, _gen_run.json carries prompts and result URLs) must be ignored, and no tracked file may
contain a secret-shaped value that is not a documented placeholder. hooks/pre-commit runs the same
rules before a commit exists; this test catches anything that got past it.

Skips when git is not available or the folder is not a checkout (a zip install still passes).
"""
import os, re, shutil, subprocess
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SECRET = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"                              # email
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"                 # uuid
    r"|/folders/[A-Za-z0-9_-]{20,}"                                                  # Drive folder id
    r"|[Bb]earer [A-Za-z0-9._-]{16,}|hf_[A-Za-z0-9]{20,}")                           # tokens
PLACEHOLDER = re.compile(r"@([A-Za-z0-9-]+\.)*(example|example\.com|anthropic\.com)\b"   # RFC 2606 domains
                         r"|00000000-0000-0000-0000-000000000000|noreply@"
                         r"|/folders/<id>|/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz012345")   # the test fixture link
MUST_BE_IGNORED = ("clients.json", "clients.acme.json", "Creatives/_preflight.json",
                   "PROJ/Creatives/_preflight.json", "_gen_run.json", "_fire_stills.lock",
                   "Elements/Clips/x.mp4", ".env")


def git(*args):
    return subprocess.run(["git", "-C", ROOT] + list(args), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


@pytest.fixture(scope="module")
def tracked():
    if not shutil.which("git"):
        pytest.skip("git not installed")
    r = git("ls-files")
    if r.returncode != 0:
        pytest.skip("not a git checkout")
    return [f for f in r.stdout.splitlines() if f.strip()]


def test_secret_files_are_ignored(tracked):
    for p in MUST_BE_IGNORED:
        assert git("check-ignore", "-q", p).returncode == 0, "%s is NOT ignored by .gitignore" % p
    assert git("check-ignore", "-q", "clients.example.json").returncode == 1, "the example must ship"


def test_registry_is_not_tracked(tracked):
    leaked = [f for f in tracked if re.fullmatch(r"(.*/)?clients.*\.json", f) and not f.endswith("clients.example.json")]
    leaked += [f for f in tracked if f.endswith(("_preflight.json", "_gen_run.json")) or "/Creatives/" in "/" + f]
    assert not leaked, "tracked but must stay local: %s (git rm --cached, then rotate what it held)" % leaked


def test_no_secret_shaped_values_in_tracked_files(tracked):
    hits = []
    for f in tracked:
        p = os.path.join(ROOT, f)
        try:
            with open(p, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except (UnicodeDecodeError, OSError):
            continue                                   # binary or gone
        for n, line in enumerate(lines, 1):
            for m in SECRET.finditer(line):
                if not PLACEHOLDER.search(line):
                    hits.append("%s:%d: %s" % (f, n, m.group(0)))
    assert not hits, "secret-shaped values in tracked files:\n  " + "\n  ".join(hits)


def test_hook_ships_and_is_executable_in_git(tracked):
    assert "hooks/pre-commit" in tracked
    mode = git("ls-files", "-s", "hooks/pre-commit").stdout.split()[0]
    assert mode == "100755", "hooks/pre-commit must be executable in git (git update-index --chmod=+x hooks/pre-commit)"
