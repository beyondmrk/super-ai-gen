"""Unit tests for hfacct: command classes and login resolution (no processes, no real logins)."""
import os
import sys
import unittest

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL, "router"))
import hfacct  # noqa: E402

A = "11111111-1111-1111-1111-111111111111"
B = "22222222-2222-2222-2222-222222222222"
C = "33333333-3333-3333-3333-333333333333"
ROOT = r"C:\Users\X\.hf-logins"
REG = {"logins": {
    "employer": {"domain": "EMPLOYER", "workspace_id": A, "folders": []},
    "acme": {"domain": "AGENCY", "workspace_id": B, "folders": [r"D:\Agency\ACME_*"]},
    "globex": {"domain": "AGENCY", "workspace_id": C, "folders": [r"D:\Agency\GLOBEX"], "home": r"C:\Users\X"},
}}


def jobs(path):
    p = path.lower()
    if p.startswith(r"d:\employer") or p.startswith(r"e:\employer-share"):
        return "EMPLOYER"
    if p.startswith(r"d:\agency"):
        return "AGENCY"
    return None


class Classify(unittest.TestCase):
    def test_table(self):
        cases = {
            ("generate", "create", "veo"): "spend",
            ("upload", "create", "a.png"): "spend",
            ("soul-id", "train"): "spend",
            ("generate", "list"): "read",
            ("generate", "cost", "veo"): "read",
            ("account", "status"): "read",
            ("voices", "list"): "read",
            ("workspace", "status"): "setup",
            ("--json", "workspace", "list"): "setup",
            ("ws", "set", A): "setup",
            ("auth", "login"): "setup",
            ("model", "list", "--video"): "agnostic",
            ("version",): "agnostic",
            ("--help",): "agnostic",
            ("generate", "create", "--help"): "agnostic",
            (): "agnostic",
        }
        for argv, want in cases.items():
            self.assertEqual(hfacct.classify(list(argv)), want, argv)


class Resolve(unittest.TestCase):
    def r(self, cwd, reg=REG, **env):
        return hfacct.resolve(env, cwd, reg, ROOT, jobs)

    def test_employer_local_folder(self):
        self.assertEqual(self.r(r"D:\Employer\_work"), "employer")

    def test_employer_lucid_folder(self):
        self.assertEqual(self.r(r"E:\employer-share\Proj"), "employer")

    def test_agency_root_identifies_nothing(self):
        self.assertIsNone(self.r(r"D:\Agency"))

    def test_client_pattern_and_below(self):
        self.assertEqual(self.r(r"D:\Agency\ACME_FLEAS\Creatives"), "acme")

    def test_exact_folder_and_below_but_not_siblings(self):
        self.assertEqual(self.r(r"D:\Agency\GLOBEX"), "globex")
        self.assertEqual(self.r(r"D:\Agency\GLOBEX\09.25.26 - Ad"), "globex")
        self.assertIsNone(self.r(r"D:\Agency\GLOBEXX"))

    def test_explicit_client(self):
        self.assertEqual(self.r(r"D:\Agency", HF_CLIENT=" Acme "), "acme")

    def test_explicit_agrees_with_folder(self):
        self.assertEqual(self.r(r"D:\Agency\ACME_X", HF_CLIENT="acme"), "acme")

    def test_unknown_client_refused(self):
        with self.assertRaises(hfacct.Refusal):
            self.r(r"D:\Agency", HF_CLIENT="nope")

    def test_conflict_refused(self):
        with self.assertRaises(hfacct.Refusal):
            self.r(r"D:\Employer", HF_CLIENT="acme")
        with self.assertRaises(hfacct.Refusal):
            self.r(r"D:\Agency\GLOBEX", HF_CLIENT="acme")

    def test_other_jobs_login_refused(self):
        with self.assertRaises(hfacct.Refusal):
            self.r(r"D:\Agency", HF_CLIENT="employer")

    def test_launcher_profile_selects_login(self):
        self.assertEqual(self.r(r"C:\Windows", USERPROFILE=ROOT + r"\employer"), "employer")

    def test_profile_outside_logins_root_is_not_a_signal(self):
        self.assertIsNone(self.r(r"C:\Windows", USERPROFILE=r"C:\Users\X"))

    def test_two_employer_logins_refused(self):
        reg = {"logins": dict(REG["logins"], employer2={"domain": "EMPLOYER", "workspace_id": A, "folders": []})}
        with self.assertRaises(hfacct.Refusal):
            self.r(r"D:\Employer", reg=reg)


class Root(unittest.TestCase):
    def test_override_only_for_a_test_vendor(self):
        os.environ["HF_LOGINS_ROOT"] = r"C:\elsewhere"
        try:
            self.assertEqual(hfacct.logins_root(hfacct.REAL_VENDOR), hfacct.DEFAULT_ROOT)
            self.assertEqual(hfacct.logins_root(r"C:\tmp\vendor"), r"C:\elsewhere")
        finally:
            del os.environ["HF_LOGINS_ROOT"]


if __name__ == "__main__":
    unittest.main()
