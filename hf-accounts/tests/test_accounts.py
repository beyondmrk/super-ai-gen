"""accounts.py (status / add / finish / launcher) against a temp vendor and registry."""
import contextlib
import io
import json
import os
import shutil
import sys
import unittest

import fixtures

sys.path.insert(0, os.path.join(fixtures.SKILL, "router"))
import accounts  # noqa: E402
import hfacct  # noqa: E402
import stamps  # noqa: E402

EMPLOYER_ID = "11111111-1111-1111-1111-111111111111"
ACME_ID = "22222222-2222-2222-2222-222222222222"
NEW_ID = "99999999-9999-9999-9999-999999999999"
FIN_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
HOME_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


class Accounts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = fixtures.temp_dir("hfaccounts-")
        cls.vendor = fixtures.build_vendor(cls.tmp, real_sized=True)
        clients = {
            "_readme": ["test"],
            "newco": {"engine": "higgsfield-cli", "account": "new@example.com", "workspace_id": NEW_ID,
                      "workspace_name": "NewCo"},
            "finco": {"engine": "higgsfield-cli", "account": "fin@example.com", "workspace_id": FIN_ID,
                      "workspace_name": "FinCo"},
            "mcpco": {"engine": "higgsfield-mcp", "account": "", "workspace_id": "", "workspace_name": "M"},
        }
        cls.clients = os.path.join(cls.tmp, "clients.json")
        with open(cls.clients, "w", encoding="utf-8") as fh:
            json.dump(clients, fh)
        cls.empty = os.path.join(cls.tmp, "empty_scan")
        os.makedirs(cls.empty)
        cls.n = 0

    def setUp(self):
        Accounts.n += 1
        self.root = os.path.join(self.tmp, "logins%d" % self.n)
        fixtures.write_registry(self.root, {
            "employer": {"domain": "EMPLOYER", "account": "employer@example.com", "workspace_id": EMPLOYER_ID,
                    "workspace_name": "Employer Co", "folders": []},
            "acme": {"domain": "AGENCY", "account": "acme@example.com", "workspace_id": ACME_ID,
                        "workspace_name": "Acme", "folders": ["D:/Agency/ACME_*"]},
            "homed": {"domain": "AGENCY", "account": "", "workspace_id": HOME_ID, "workspace_name": "Private",
                      "home": os.path.join(self.tmp, "default_home"), "folders": []},
        }, unmatched="default", require_stamp=False)
        with open(os.path.join(self.root, "logins.json"), encoding="utf-8") as fh:
            raw = json.load(fh)
        raw["_readme"] = ["kept"]
        with open(os.path.join(self.root, "logins.json"), "w", encoding="utf-8") as fh:
            json.dump(raw, fh)
        fixtures.write_manifest(self.vendor, self.root)

    def run_cmd(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = accounts.main(list(args) + ["--vendor", self.vendor, "--root", self.root,
                                              "--clients", self.clients], out)
        return rc, out.getvalue() + err.getvalue()

    def registry(self):
        with open(os.path.join(self.root, "logins.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def launcher(self, name):
        with open(os.path.join(self.root, "%s-login.cmd" % name), encoding="ascii") as fh:
            return fh.read()

    def test_add_takes_a_agency_client_from_clients_json(self):
        rc, out = self.run_cmd("add", "NewCo", "--folder", "D:/Agency/NEWCO")
        self.assertEqual(rc, 0, out)
        reg = self.registry()
        self.assertEqual(reg["logins"]["newco"], {"domain": "AGENCY", "account": "new@example.com",
                                                  "workspace_id": NEW_ID, "workspace_name": "NewCo",
                                                  "folders": ["D:/Agency/NEWCO"]})
        self.assertEqual((reg["unmatched"], reg["require_stamp"], reg["_readme"]), ("default", False, ["kept"]))
        self.assertTrue(os.path.isdir(os.path.join(self.root, "newco")))
        text = self.launcher("newco")
        self.assertIn('set "HF_CLIENT=newco"', text)
        self.assertNotIn("USERPROFILE", text)
        self.assertIn("new@example.com", text)
        with open(os.path.join(self.root, "newco-login.cmd"), "rb") as fh:
            self.assertNotIn(b"\n", fh.read().replace(b"\r\n", b""))  # CRLF only, for cmd.exe
        hfacct.load_registry(self.root)  # still a registry the router accepts

    def test_add_with_explicit_values(self):
        rc, out = self.run_cmd("add", "solo", "--domain", "AGENCY", "--account", "s@example.com",
                               "--workspace-id", FIN_ID.upper(), "--workspace-name", "Solo")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.registry()["logins"]["solo"]["workspace_id"], FIN_ID)
        self.assertIn("only with HF_CLIENT=solo", out)

    def test_add_refuses_what_it_cannot_place(self):
        cases = [
            (("add", "acme"), "already registered"),
            (("add", "employer2", "--domain", "EMPLOYER", "--workspace-id", NEW_ID, "--workspace-name", "X"), "EMPLOYER login"),
            (("add", "unknown"), "--domain"),
            (("add", "mcpco"), "higgsfield-mcp"),
            (("add", "newco", "--workspace-id", FIN_ID), "disagrees on workspace_id"),
            (("add", "connector", "--domain", "AGENCY", "--workspace-id", NEW_ID, "--workspace-name", "X"), "cannot be"),
            (("add", "bad", "--domain", "AGENCY", "--workspace-id", "nope", "--workspace-name", "X"), "not a workspace id"),
        ]
        before = self.registry()
        for args, why in cases:
            rc, out = self.run_cmd(*args)
            self.assertEqual(rc, 2, (args, out))
            self.assertIn(why, out, args)
        self.assertEqual(self.registry(), before)

    def test_launcher_rewrites_in_the_hf_client_form(self):
        with open(os.path.join(self.root, "employer-login.cmd"), "w", encoding="ascii") as fh:
            fh.write('set "USERPROFILE=old"\r\n')
        rc, out = self.run_cmd("launcher", "employer")
        self.assertEqual(rc, 0, out)
        text = self.launcher("employer")
        self.assertIn('set "HF_CLIENT=employer"', text)
        self.assertNotIn("USERPROFILE", text)
        self.assertIn('cd /d "%s"' % self.root, text)

    def test_launcher_refuses_a_login_that_still_has_a_home(self):
        rc, out = self.run_cmd("launcher", "homed")
        self.assertEqual(rc, 2, out)
        self.assertIn("B1", out)
        self.assertFalse(os.path.exists(os.path.join(self.root, "homed-login.cmd")))

    def test_finish_sets_the_workspace_through_the_router_then_checks(self):
        self.assertEqual(self.run_cmd("add", "finco")[0], 0)
        fixtures.fake_login(os.path.join(self.root, "finco"), FIN_ID, "fin@example.com")
        rc, out = self.run_cmd("finish", "finco", "--scan-root", self.empty,
                               "--default-home", os.path.join(self.tmp, "nobody"))
        self.assertEqual(rc, 0, out)
        self.assertIn("FAKEHF USERPROFILE=%s" % os.path.join(self.root, "finco"), out)
        self.assertIn("ARG[0]=workspace\nARG[1]=set\nARG[2]=%s" % FIN_ID, out.replace("\r\n", "\n"))
        self.assertIn("RESULT: PASS", out)
        entry = hfacct.load_registry(self.root)["logins"]["finco"]
        self.assertTrue(stamps.valid(self.root, "finco", entry, self.vendor, 3)[0])

    def test_finish_refuses_a_login_that_is_not_signed_in(self):
        rc, out = self.run_cmd("finish", "acme")
        self.assertEqual(rc, 2, out)
        self.assertIn("not signed in", out)

    def test_status_shows_every_login(self):
        fixtures.fake_login(os.path.join(self.root, "acme"), ACME_ID, "acme@example.com")
        rc, out = self.run_cmd("status")
        self.assertEqual(rc, 0, out)
        for text in ("employer ", "acme ", "homed ", "signed out", "match", "connector stamp", "install:",
                     "no account check on record", "require_stamp=False"):
            self.assertIn(text, out)

    def test_test_options_need_a_test_vendor(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = accounts.main(["status", "--root", self.root], io.StringIO())
        self.assertEqual(rc, 2)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
