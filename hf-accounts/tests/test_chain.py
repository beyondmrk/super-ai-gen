"""End to end: hfstub.exe -> router.py -> fake hf.real.exe, in a temp vendor folder.

Nothing here touches the real Higgsfield install or any real login. The temp folder comes
from HFACCT_TMP when set (use the session scratchpad), else the system temp.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL, "router"))
import fixtures  # noqa: E402
import hfacct  # noqa: E402
import install  # noqa: E402
import stamps  # noqa: E402

EMPLOYER_DIR = r"D:\Employer"      # a real EMPLOYER folder per domains.json (only used as a working directory)
AGENCY_DIR = r"D:\Agency"   # a real AGENCY folder, likewise
if not (os.path.isfile(os.path.join(hfacct.HOOKS_DIR, "domain.py"))
        and os.path.isdir(EMPLOYER_DIR) and os.path.isdir(AGENCY_DIR)):
    raise unittest.SkipTest("needs the boundary gate (~/.claude/local-hooks/domain.py) and its real folders")
EMPLOYER_ID = "11111111-1111-1111-1111-111111111111"
ACME_ID = "22222222-2222-2222-2222-222222222222"
WRONG_ID = "33333333-3333-3333-3333-333333333333"


class Chain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="hfacct-", dir=os.environ.get("HFACCT_TMP"))
        cls.vendor = os.path.join(cls.tmp, "vendor")
        cls.root = os.path.join(cls.tmp, "logins")
        os.makedirs(cls.vendor)
        os.makedirs(cls.root)
        with open(os.path.join(SKILL, "tests", "fakehf.cs"), encoding="utf-8") as fh:
            install.compile_cs(fh.read(), os.path.join(cls.tmp, "fake", "fakehf.exe"))
        shutil.copy2(os.path.join(cls.tmp, "fake", "fakehf.exe"), os.path.join(cls.vendor, "hf.real.exe"))
        stub = install.build_stub(os.path.join(cls.tmp, "build"))
        cls.hf = os.path.join(cls.vendor, "hf.exe")
        shutil.copy2(stub, cls.hf)

        cls.bare_vendor = os.path.join(cls.tmp, "bare_vendor")  # launcher only, no real binary
        os.makedirs(cls.bare_vendor)
        shutil.copy2(stub, os.path.join(cls.bare_vendor, "hf.exe"))

        reg = {"version": 1, "unmatched": "default", "require_stamp": False, "logins": {
            "employer": {"domain": "EMPLOYER", "workspace_id": EMPLOYER_ID, "workspace_name": "Employer Co", "folders": []},
            "acme": {"domain": "AGENCY", "workspace_id": ACME_ID, "workspace_name": "Acme",
                        "folders": [os.path.join(cls.tmp, "projects", "ACME_*")]},
            "wrongws": {"domain": "AGENCY", "workspace_id": ACME_ID, "workspace_name": "Acme", "folders": []},
        }}
        with open(os.path.join(cls.root, "logins.json"), "w", encoding="utf-8") as fh:
            json.dump(reg, fh)
        for name, wid in (("employer", EMPLOYER_ID), ("acme", ACME_ID), ("wrongws", WRONG_ID)):
            d = os.path.join(cls.root, name, ".config", "higgsfield")
            os.makedirs(d)
            with open(os.path.join(d, "fake_ws.json"), "w", encoding="utf-8") as fh:
                json.dump({"id": wid, "name": name}, fh)
        cls.acme_project = os.path.join(cls.tmp, "projects", "ACME_TEST", "Creatives")
        os.makedirs(cls.acme_project)

    def call(self, args, cwd, hf=None, **env_over):
        env = {k: v for k, v in os.environ.items() if k != "HF_CLIENT"}
        env["HF_LOGINS_ROOT"] = self.root
        env.update(env_over)
        p = subprocess.run([hf or self.hf] + args, cwd=cwd, env=env, capture_output=True, timeout=120)
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")

    def assertRouted(self, out, name):
        self.assertIn("FAKEHF USERPROFILE=%s" % os.path.join(self.root, name), out)

    def assertRefused(self, rc, out, err):
        self.assertEqual(rc, 64, out + err)
        self.assertIn("HF ROUTER: refused.", err)
        self.assertNotIn("FAKEHF", out)

    def test_employer_folder_routes_to_employer_login(self):
        rc, out, err = self.call(["generate", "create", "m", "--prompt", "x"], EMPLOYER_DIR)
        self.assertEqual(rc, 0, err)
        self.assertRouted(out, "employer")

    def test_agency_root_passes_through_unchanged(self):
        rc, out, err = self.call(["generate", "create", "m"], AGENCY_DIR)
        self.assertEqual(rc, 0, err)
        self.assertIn("FAKEHF USERPROFILE=%s" % os.environ["USERPROFILE"], out)

    def test_spend_from_a_folder_of_neither_job_refused(self):
        self.assertRefused(*self.call(["generate", "create", "m"], self.tmp))

    def test_read_from_a_folder_of_neither_job_passes_through(self):
        rc, out, err = self.call(["generate", "list"], self.tmp)
        self.assertEqual(rc, 0, err)
        self.assertIn("FAKEHF USERPROFILE=%s" % os.environ["USERPROFILE"], out)

    def test_explicit_client_routes(self):
        rc, out, err = self.call(["generate", "create", "m"], AGENCY_DIR, HF_CLIENT="acme")
        self.assertEqual(rc, 0, err)
        self.assertRouted(out, "acme")

    def test_client_folder_pattern_routes(self):
        rc, out, err = self.call(["generate", "create", "m"], self.acme_project)
        self.assertEqual(rc, 0, err)
        self.assertRouted(out, "acme")

    def test_launcher_style_profile_routes(self):
        rc, out, err = self.call(["auth", "login"], self.tmp, USERPROFILE=os.path.join(self.root, "employer"))
        self.assertEqual(rc, 0, err)
        self.assertRouted(out, "employer")

    def test_conflict_refused(self):
        self.assertRefused(*self.call(["generate", "create", "m"], EMPLOYER_DIR, HF_CLIENT="acme"))

    def test_other_jobs_login_refused(self):
        self.assertRefused(*self.call(["generate", "create", "m"], AGENCY_DIR, HF_CLIENT="employer"))

    def test_unknown_client_refused(self):
        self.assertRefused(*self.call(["generate", "create", "m"], AGENCY_DIR, HF_CLIENT="nope"))

    def test_wrong_workspace_refused(self):
        self.assertRefused(*self.call(["generate", "create", "m"], AGENCY_DIR, HF_CLIENT="wrongws"))

    def test_setup_command_skips_workspace_check(self):
        rc, out, err = self.call(["workspace", "set", ACME_ID], AGENCY_DIR, HF_CLIENT="wrongws")
        self.assertEqual(rc, 0, err)
        self.assertRouted(out, "wrongws")

    def test_missing_real_binary_refused(self):
        hf = os.path.join(self.bare_vendor, "hf.exe")
        self.assertRefused(*self.call(["generate", "create", "m"], AGENCY_DIR, hf=hf))

    def test_exit_code_passes_through(self):
        rc, out, err = self.call(["fail"], AGENCY_DIR, HF_CLIENT="acme")
        self.assertEqual(rc, 3, out + err)

    def test_arguments_arrive_untouched(self):
        args = ["generate", "create", "m", "--prompt",
                'line one\nline "two"\n\tC:\\path with space\\ end\\',
                "--empty", "", "caf\u00e9 \u2014 ok", 'back\\\\slash\\"quote', "trail\\"]
        rc, out, err = self.call(args, AGENCY_DIR, HF_CLIENT="acme")
        self.assertEqual(rc, 0, err)
        want = ["ARG[%d]=%s" % (i, a.replace("\r", "\\r").replace("\n", "\\n")) for i, a in enumerate(args)]
        got = [line for line in out.splitlines() if line.startswith("ARG[")]
        self.assertEqual(got, want)

    def test_agnostic_in_default_mode_stays_on_the_default_login(self):
        rc, out, err = self.call(["model", "list"], self.tmp)
        self.assertEqual(rc, 0, err)
        self.assertIn("FAKEHF USERPROFILE=%s" % os.environ["USERPROFILE"], out)


class StrictChain(unittest.TestCase):
    """The switch-over flags (plan B6): unmatched "refuse" and require_stamp, end to end."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = fixtures.temp_dir("hfstrict-")
        cls.vendor = fixtures.build_vendor(cls.tmp)
        cls.hf = os.path.join(cls.vendor, "hf.exe")
        cls.root = os.path.join(cls.tmp, "logins")
        fixtures.write_registry(cls.root, {
            "alpha": {"domain": "AGENCY", "workspace_id": EMPLOYER_ID, "workspace_name": "Alpha", "folders": []},
            "beta": {"domain": "AGENCY", "workspace_id": ACME_ID, "workspace_name": "Beta", "folders": []},
        }, unmatched="refuse", require_stamp=True)
        fixtures.fake_login(os.path.join(cls.root, "alpha"), signed_in=False)
        fixtures.fake_login(os.path.join(cls.root, "beta"), ACME_ID, "beta@example.com")
        cls.root_default = os.path.join(cls.tmp, "logins_default")
        fixtures.write_registry(cls.root_default, {
            "gamma": {"domain": "AGENCY", "workspace_id": ACME_ID, "workspace_name": "Gamma", "folders": []},
        }, unmatched="default", require_stamp=True)
        cls.neutral = os.path.join(cls.tmp, "work")
        os.makedirs(cls.neutral)

    def tearDown(self):
        stamps.clear(self.root, "beta")

    def call(self, args, cwd=None, root=None, **env_over):
        env = {k: v for k, v in os.environ.items() if k != "HF_CLIENT"}
        env["HF_LOGINS_ROOT"] = root or self.root
        env.update(env_over)
        p = subprocess.run([self.hf] + args, cwd=cwd or self.neutral, env=env, capture_output=True, timeout=120)
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")

    def stamp(self):
        entry = hfacct.load_registry(self.root)["logins"]["beta"]
        stamps.write(self.root, "beta", stamps.fingerprint(self.root, "beta", entry, self.vendor), [])

    def test_spend_without_a_stamp_is_refused(self):
        rc, out, err = self.call(["generate", "create", "m"], HF_CLIENT="beta")
        self.assertEqual(rc, 64, out + err)
        self.assertIn("Run the account check first", err)
        self.assertIn("hf-account-checker subagent for login beta", err)

    def test_spend_with_a_valid_stamp_runs(self):
        self.stamp()
        rc, out, err = self.call(["generate", "create", "m"], HF_CLIENT="beta")
        self.assertEqual(rc, 0, err)
        self.assertIn("FAKEHF USERPROFILE=%s" % os.path.join(self.root, "beta"), out)

    def test_a_stamp_goes_stale_when_the_workspace_selection_changes(self):
        self.stamp()
        with open(os.path.join(self.root, "beta", ".config", "higgsfield", "config.json"), "a") as fh:
            fh.write(" ")
        rc, out, err = self.call(["generate", "create", "m"], HF_CLIENT="beta")
        self.assertEqual(rc, 64, out + err)
        self.assertIn("changed since the check: config", err)

    def test_reads_need_no_stamp(self):
        rc, out, err = self.call(["generate", "list"], HF_CLIENT="beta")
        self.assertEqual(rc, 0, err)
        self.assertIn("FAKEHF USERPROFILE=%s" % os.path.join(self.root, "beta"), out)

    def test_no_client_refuses_spend_read_and_setup(self):
        for args in (["generate", "create", "m"], ["generate", "list"], ["auth", "login"],
                     ["workspace", "set", ACME_ID]):
            rc, out, err = self.call(args)
            self.assertEqual(rc, 64, (args, out, err))
            self.assertIn("No client login is identified", err)
            self.assertNotIn("FAKEHF", out)

    def test_agnostic_runs_on_the_first_signed_in_login(self):
        rc, out, err = self.call(["model", "list"])
        self.assertEqual(rc, 0, err)
        self.assertIn("FAKEHF USERPROFILE=%s" % os.path.join(self.root, "beta"), out)

    def test_account_reads_fan_out_over_every_login(self):
        rc, out, err = self.call(["workspace", "status", "--json"])
        self.assertEqual(rc, 1, out + err)  # alpha is signed out
        self.assertLess(out.index("== alpha =="), out.index("== beta =="))
        self.assertIn(ACME_ID, out.split("== beta ==")[1])
        self.assertIn("not logged in", err)
        rc, out, err = self.call(["account", "status"])
        self.assertIn("beta@example.com", out.split("== beta ==")[1])

    def test_default_mode_with_stamps_required_refuses_an_unnamed_spend(self):
        self.assertEqual(self.call(["generate", "create", "m"], cwd=AGENCY_DIR, root=self.root_default)[0], 64)
        rc, out, err = self.call(["generate", "list"], cwd=AGENCY_DIR, root=self.root_default)
        self.assertEqual(rc, 0, err)
        self.assertIn("FAKEHF USERPROFILE=%s" % os.environ["USERPROFILE"], out)


if __name__ == "__main__":
    unittest.main()
