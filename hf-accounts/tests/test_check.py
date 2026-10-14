"""Stamps and the account check (hf_check.py), against a temp vendor, registry and login folders."""
import contextlib
import io
import json
import os
import sys
import time
import unittest

import fixtures

sys.path.insert(0, os.path.join(fixtures.SKILL, "router"))
import hf_check  # noqa: E402
import hfacct  # noqa: E402
import stamps  # noqa: E402

EMPLOYER_ID = "11111111-1111-1111-1111-111111111111"
ACME_ID = "22222222-2222-2222-2222-222222222222"
BLANK_ID = "33333333-3333-3333-3333-333333333333"
MAIL_ID = "44444444-4444-4444-4444-444444444444"
WS_ID = "55555555-5555-5555-5555-555555555555"
OTHER_ID = "66666666-6666-6666-6666-666666666666"
MM_ID = "77777777-7777-7777-7777-777777777777"
OUT_ID = "88888888-8888-8888-8888-888888888888"


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


class Stamps(unittest.TestCase):
    """fingerprint / write / valid, with plain files (no binaries needed)."""

    def setUp(self):
        self.root = fixtures.temp_dir("hfstamp-")
        self.vendor = os.path.join(self.root, "vendor")
        write(os.path.join(self.vendor, "hf.exe"), "launcher v1")
        fixtures.write_registry(self.root, {"acme": {"domain": "AGENCY", "workspace_id": ACME_ID}})
        self.entry = hfacct.load_registry(self.root)["logins"]["acme"]
        self.cfg = write(os.path.join(self.root, "acme", ".config", "higgsfield", "config.json"), '{"w": 1}')

    def stamp(self, **kw):
        fp = stamps.fingerprint(self.root, "acme", self.entry, self.vendor)
        return stamps.write(self.root, "acme", fp, [], **kw)

    def valid(self, hours=3):
        return stamps.valid(self.root, "acme", self.entry, self.vendor, hours)

    def test_valid_right_after_the_check(self):
        self.stamp()
        ok, why = self.valid()
        self.assertTrue(ok, why)

    def test_missing(self):
        ok, why = self.valid()
        self.assertFalse(ok)
        self.assertIn("no account check", why)

    def test_expired(self):
        self.stamp(now=time.time() - 3 * 3600 - 5)
        ok, why = self.valid()
        self.assertFalse(ok)
        self.assertIn("limit is 3 h", why)

    def test_future_dated_stamp_is_not_believed(self):
        self.stamp(now=time.time() + 3600)
        self.assertFalse(self.valid()[0])

    def test_workspace_change_voids_it(self):
        self.stamp()
        write(self.cfg, '{"w": 22}')
        ok, why = self.valid()
        self.assertFalse(ok)
        self.assertIn("config", why)

    def test_registry_change_voids_it(self):
        self.stamp()
        fixtures.write_registry(self.root, {"acme": {"domain": "AGENCY", "workspace_id": ACME_ID}}, stamp_hours=4)
        ok, why = self.valid()
        self.assertFalse(ok)
        self.assertIn("registry", why)

    def test_launcher_change_voids_it(self):
        self.stamp()
        write(os.path.join(self.vendor, "hf.exe"), "launcher v2")
        ok, why = self.valid()
        self.assertFalse(ok)
        self.assertIn("launcher", why)

    def test_fingerprint_covers_every_router_module(self):
        fp = stamps.fingerprint(self.root, "acme", self.entry, self.vendor)
        for key in ("registry", "launcher", "config", "router", "hfacct", "stamps", "strict"):
            self.assertIn(key, fp)
            self.assertNotEqual(fp[key], "missing", key)

    def test_connector_name_is_reserved(self):
        with self.assertRaises(ValueError):
            stamps.write(self.root, "connector", {}, [])


class Check(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = fixtures.temp_dir("hfcheck-")
        cls.vendor = fixtures.build_vendor(cls.tmp, real_sized=True)
        cls.root = os.path.join(cls.tmp, "logins")
        logins = {
            "employer": ("EMPLOYER", "employer@example.com", EMPLOYER_ID, EMPLOYER_ID, "employer@example.com"),
            "acme": ("AGENCY", "acme@example.com", ACME_ID, ACME_ID, "Acme@Example.com"),
            "blank": ("AGENCY", "", BLANK_ID, BLANK_ID, "blank@example.com"),
            "wrongmail": ("AGENCY", "right@example.com", MAIL_ID, MAIL_ID, "other@example.com"),
            "wrongws": ("AGENCY", "ws@example.com", WS_ID, OTHER_ID, "ws@example.com"),
            "mismatch": ("AGENCY", "mm@example.com", MM_ID, MM_ID, "mm@example.com"),
            "signedout": ("AGENCY", "out@example.com", OUT_ID, None, None),
        }
        reg = {}
        for name, (dom, acct, wid, live_ws, live_mail) in logins.items():
            reg[name] = {"domain": dom, "account": acct, "workspace_id": wid, "workspace_name": name.title()}
            fixtures.fake_login(os.path.join(cls.root, name), live_ws, live_mail, signed_in=bool(live_ws))
        fixtures.write_registry(cls.root, reg, unmatched="default", require_stamp=False)
        fixtures.write_manifest(cls.vendor, cls.root)
        clients = {name: {"engine": "higgsfield-cli", "account": acct, "workspace_id": wid}
                   for name, (dom, acct, wid, _, _) in logins.items() if dom == "AGENCY"}
        clients["blank"]["account"] = "someone@example.com"  # registry blank: not compared
        clients["mismatch"]["workspace_id"] = OTHER_ID
        cls.clients = write(os.path.join(cls.tmp, "clients.json"), json.dumps(clients))

        cls.scan_clean = os.path.dirname(write(os.path.join(cls.tmp, "scan_clean", "a", "ok.py"), "print('hi')\n"))
        cls.agency_clean = os.path.dirname(write(os.path.join(cls.tmp, "agency_clean", "fire.py"),
                                              'env["HF_CLIENT"] = "acme"\nexe = shutil.which("higgsfield")\n'))
        cls.scan_bad = os.path.dirname(write(os.path.join(cls.tmp, "scan_bad", "x", "SKILL.md"),
                                             "line one\nrun C:\\npm\\vendor\\hf.real.exe directly\n"))
        cls.scan_bin = os.path.dirname(write(os.path.join(cls.tmp, "scan_bin", "tools", "hf.exe"), "MZ"))
        cls.agency_warn = os.path.dirname(write(os.path.join(cls.tmp, "agency_warn", "vo_stage.py"),
                                             "import os\nHF = r'%APPDATA%\\npm\\node_modules\\@higgsfield\\cli\\vendor\\hf.exe'\n"))
        write(os.path.join(cls.agency_warn, "NOTES.md"), "which('higgsfield') in a doc is not a call site\n")
        cls.default_out = os.path.join(cls.tmp, "default_out")
        os.makedirs(cls.default_out)
        cls.default_in = fixtures.fake_login(os.path.join(cls.tmp, "default_in"), ACME_ID, "default@example.com")

        cls.root_refuse = os.path.join(cls.tmp, "logins_refuse")
        fixtures.write_registry(cls.root_refuse, {"acme": reg["acme"]}, unmatched="refuse")
        fixtures.fake_login(os.path.join(cls.root_refuse, "acme"), ACME_ID, "acme@example.com")
        fixtures.write_manifest(cls.vendor, cls.root_refuse)

        cls.root_badinstall = os.path.join(cls.tmp, "logins_badinstall")
        fixtures.write_registry(cls.root_badinstall, {"acme": reg["acme"]}, unmatched="default")
        fixtures.fake_login(os.path.join(cls.root_badinstall, "acme"), ACME_ID, "acme@example.com")
        fixtures.write_manifest(cls.vendor, cls.root_badinstall)
        with open(os.path.join(cls.root_badinstall, "_install.json"), encoding="utf-8") as fh:
            m = json.load(fh)
        m["real_sha256"] = "0" * 64
        with open(os.path.join(cls.root_badinstall, "_install.json"), "w", encoding="utf-8") as fh:
            json.dump(m, fh)

    def check(self, *args, root=None, scan=None, agency=None, default_home=None):
        argv = list(args) + ["--vendor", self.vendor, "--root", root or self.root, "--clients", self.clients,
                             "--default-home", default_home or self.default_out,
                             "--scan-root", scan or self.scan_clean, "--agency-root", agency or self.agency_clean]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = hf_check.main(argv, out)
        return rc, out.getvalue() + err.getvalue()

    def stamp_file(self, name, root=None):
        return os.path.join(root or self.root, "_checked", name + ".json")

    def test_pass_writes_a_valid_stamp(self):
        rc, out = self.check("acme")
        self.assertEqual(rc, 0, out)
        self.assertIn("RESULT: PASS", out)
        entry = hfacct.load_registry(self.root)["logins"]["acme"]
        ok, why = stamps.valid(self.root, "acme", entry, self.vendor, 3)
        self.assertTrue(ok, why)

    def test_blank_registry_account_warns_but_passes(self):
        rc, out = self.check("blank")
        self.assertEqual(rc, 0, out)
        self.assertIn("WARN  account", out)
        self.assertIn("blank@example.com", out)

    def test_wrong_email_fails(self):
        rc, out = self.check("wrongmail")
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL  account", out)
        self.assertFalse(os.path.exists(self.stamp_file("wrongmail")))

    def test_wrong_workspace_fails(self):
        rc, out = self.check("wrongws")
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL  workspace", out)
        self.assertIn(OTHER_ID, out)

    def test_signed_out_login_fails(self):
        rc, out = self.check("signedout")
        self.assertEqual(rc, 1, out)
        self.assertIn("signed out", out)

    def test_clients_json_mismatch_fails(self):
        rc, out = self.check("mismatch")
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL  clients", out)

    def test_employer_login_does_not_read_clients_json(self):
        rc, out = self.check("employer")
        self.assertEqual(rc, 0, out)
        self.assertIn("SKIP  clients", out)

    def test_hf_real_reference_fails(self):
        rc, out = self.check("acme", scan=self.scan_bad)
        self.assertEqual(rc, 1, out)
        self.assertIn("SKILL.md:2 references hf.real.exe", out)

    def test_binary_copy_fails(self):
        rc, out = self.check("acme", scan=self.scan_bin)
        self.assertEqual(rc, 1, out)
        self.assertIn("is a Higgsfield binary", out)

    def test_agency_call_site_without_hf_client_warns(self):
        rc, out = self.check("acme", agency=self.agency_warn)
        self.assertEqual(rc, 0, out)
        self.assertIn("WARN  scan", out)
        self.assertIn("vo_stage.py:2", out)
        self.assertNotIn("NOTES.md", out)

    def test_call_site_outside_agency_roots_is_not_warned(self):
        rc, out = self.check("acme", scan=self.agency_warn)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("WARN  scan", out)

    def test_failed_check_removes_an_older_stamp(self):
        entry = hfacct.load_registry(self.root)["logins"]["wrongmail"]
        stamps.write(self.root, "wrongmail", stamps.fingerprint(self.root, "wrongmail", entry, self.vendor), [])
        rc, out = self.check("wrongmail")
        self.assertEqual(rc, 1, out)
        self.assertIn("older one was removed", out)
        self.assertFalse(os.path.exists(self.stamp_file("wrongmail")))

    def test_install_mismatch_fails(self):
        rc, out = self.check("acme", root=self.root_badinstall)
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL  install", out)

    def test_default_login_is_left_alone_in_default_mode(self):
        rc, out = self.check("acme", default_home=self.default_in)
        self.assertEqual(rc, 0, out)
        self.assertIn("SKIP  default", out)

    def test_refuse_mode_needs_the_default_login_signed_out(self):
        rc, out = self.check("acme", root=self.root_refuse, default_home=self.default_in)
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL  default", out)
        rc, out = self.check("acme", root=self.root_refuse, default_home=self.default_out)
        self.assertEqual(rc, 0, out)
        self.assertIn("PASS  default", out)

    def test_unknown_login_cannot_run(self):
        self.assertEqual(self.check("nope")[0], 2)

    def test_test_options_need_a_test_vendor(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = hf_check.main(["acme", "--root", self.root], io.StringIO())
        self.assertEqual(rc, 2)
        self.assertIn("test option", err.getvalue())

    def test_a_test_vendor_never_stamps_the_real_registry(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = hf_check.main(["acme", "--vendor", self.vendor, "--root", hfacct.DEFAULT_ROOT], io.StringIO())
        self.assertEqual(rc, 2)
        self.assertIn("own --root", err.getvalue())

    # --- connector mode -------------------------------------------------------------------

    def connector(self, client, wid):
        return self.check("--connector", "--client", client, "--workspace-id", wid)

    def test_connector_on_the_clients_workspace_is_stamped(self):
        rc, out = self.connector("acme", ACME_ID.upper())
        self.assertEqual(rc, 0, out)
        body = stamps.read_connector(self.root)
        self.assertEqual((body["client"], body["domain"], body["workspace_id"]), ("acme", "AGENCY", ACME_ID))

    def test_connector_for_employer_uses_the_registry(self):
        rc, out = self.connector("employer", EMPLOYER_ID)
        self.assertEqual(rc, 0, out)
        self.assertEqual(stamps.read_connector(self.root)["domain"], "EMPLOYER")

    def test_connector_on_another_workspace_fails_and_clears(self):
        stamps.write_connector(self.root, "acme", "AGENCY", ACME_ID)
        rc, out = self.connector("acme", OTHER_ID)
        self.assertEqual(rc, 1, out)
        self.assertIsNone(stamps.read_connector(self.root))

    def test_connector_unknown_client_fails(self):
        self.assertEqual(self.connector("nobody", ACME_ID)[0], 1)

    def test_connector_when_registry_and_clients_disagree_fails(self):
        rc, out = self.connector("mismatch", MM_ID)
        self.assertEqual(rc, 1, out)
        self.assertIn("disagree", out)


if __name__ == "__main__":
    unittest.main()
