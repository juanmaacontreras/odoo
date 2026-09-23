"""Offline test suite against mock_odoo.py. Run: python selftest.py"""

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

import core
import mock_odoo
import server

ROWS = [
    {"ai": 1948, "row": 2, "status": "", "date": "", "model": "CheckPoint 4", "serial": "11262443", "version": "", "customer": "Remotti"},
    {"ai": 1114, "row": 3, "status": "", "date": "", "model": "H5K5", "serial": "62181114", "version": "", "customer": "Aysa"},
    {"ai": 1120, "row": 4, "status": "", "date": "", "model": "H5K5", "serial": "62189999", "version": "", "customer": "Aysa"},
    {"ai": 609, "row": 5, "status": "", "date": "", "model": "Aquaphon", "serial": "01102004429", "version": "", "customer": "AySA DRN"},
    {"ai": 1494, "row": 6, "status": "", "date": "", "model": "Aquaphon", "serial": "01102004429", "version": "", "customer": "AySa - DRN"},
    {"ai": 447, "row": 7, "status": "", "date": "", "model": "Bomba", "serial": "NO TIENE", "version": "", "customer": "x"},
    {"ai": 1953, "row": 8, "status": "", "date": "", "model": "Aquaphon", "serial": "A6 553311", "version": "", "customer": "y"},
    {"ai": 700, "row": 9, "status": "", "date": "", "model": "Aquaphon", "serial": "0110-2004429 – A6", "version": "", "customer": "z"},
    {"ai": 1, "row": 11, "status": "", "date": "", "model": "SecorRphon", "serial": "104 15 005048  06.21", "version": "", "customer": "z"},
    {"ai": 116, "row": 12, "status": "", "date": "", "model": "Secorr", "serial": "00903000938 05.05", "version": "", "customer": "z"},
    {"ai": 148, "row": 13, "status": "", "date": "", "model": "Secorr", "serial": "030 02 000334 09.09", "version": "", "customer": "z"},
    {"ai": 800, "row": 10, "status": "", "date": "", "model": "?", "serial": "99.887.766", "version": "", "customer": "z"},
]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mock = mock_odoo.MockOdoo()
        cls.url = cls.mock.start()
        cls.tmp = tempfile.mkdtemp()
        server.LOG_PATH = os.path.join(cls.tmp, "log.jsonl")

    @classmethod
    def tearDownClass(cls):
        cls.mock.stop()

    def make_app(self, live=False):
        cfg = dict(server.CONFIG_DEFAULTS, odoo_url=self.url, username="juanma", api_key=mock_odoo.API_KEY,
                   spreadsheet=os.path.join(self.tmp, "missing.xls"), live_mode=live, port=0)
        app = server.App(cfg)
        app.index = core.EquipmentIndex(ROWS)
        return app


class TestParsing(unittest.TestCase):
    def test_ai(self):
        for s in ("1948", "AI1948", "ai 1948", "01948", " AI-1948\n"):
            self.assertEqual(core.parse_ai(s), 1948, s)
        for s in ("", "abc", "19 48", "0", None):
            self.assertIsNone(core.parse_ai(s), s)

    def test_junk(self):
        for s in ("----", "NO TIENE", "BOMBA", "AIRE", "no tiene numero de serie", ""):
            self.assertTrue(core.is_junk_serial(s), s)
        self.assertFalse(core.is_junk_serial("A6 553311"))

    def test_variants(self):
        self.assertEqual(core.serial_variants("11.26-24 43"), ["11.26-24 43", "11.26-2443", "11262443"])
        self.assertIn("0110-2004429", core.serial_variants("0110-2004429 – A6"))
        self.assertEqual(core.longest_digit_run("SN 12-1234567"), "1234567")
        self.assertIsNone(core.longest_digit_run("99.887.766"))

    def test_sheet_warnings(self):
        idx = core.EquipmentIndex(ROWS)
        self.assertTrue(any("also listed for AI 1494" in w for w in idx.lookup(609)["warnings"]))
        self.assertTrue(any("H5K5" in w for w in idx.lookup(1120)["warnings"]))
        self.assertFalse(idx.lookup(1114)["warnings"])
        self.assertIsNone(idx.lookup(447)["serial"])


class TestLookup(Base):
    def test_exact_serial_and_customer_from_last_repair(self):
        app = self.make_app()
        r = core.lookup(app.index, app.odoo, "AI01948")
        self.assertTrue(r["match"].startswith("same serial"))
        e, = r["lots"]
        self.assertEqual(e["lot"]["id"], 100)
        self.assertEqual(e["product"]["default_code"], "CP4")
        self.assertEqual(e["customer"]["id"], 11)
        self.assertIn("RMA/03221", e["customer_source"])

    def test_ai_on_lot_ref(self):
        app = self.make_app()
        r = core.lookup(app.index, app.odoo, "1114")
        self.assertTrue(r["match"].startswith("AI on lot"))
        self.assertEqual(r["lots"][0]["lot"]["id"], 101)

    def test_duplicate_serial_and_open_repair(self):
        app = self.make_app()
        r = core.lookup(app.index, app.odoo, "609")
        self.assertTrue(any("1494" in w for w in r["warnings"]))
        self.assertEqual(r["lots"][0]["open_repairs"], ["RMA/03300"])

    def test_multiple_lots(self):
        app = self.make_app()
        r = core.lookup(app.index, app.odoo, "1953")
        self.assertEqual(sorted(e["lot"]["id"] for e in r["lots"]), [104, 105])

    def test_partial(self):
        app = self.make_app()
        r = core.lookup(app.index, app.odoo, "700")
        self.assertTrue(r["match"].startswith("possible"))

    def test_messy_real_world_lots(self):
        app = self.make_app()
        r = core.lookup(app.index, app.odoo, "1")  # ref AI00001 + serial with extra spaces and date
        self.assertEqual(r["lots"][0]["lot"]["id"], 106)
        self.assertEqual(len(r["lots"][0]["match"]), 2)
        r = core.lookup(app.index, app.odoo, "116")  # multi-serial lot, AI in a ref list
        self.assertEqual(r["lots"][0]["lot"]["id"], 107)
        self.assertIn("note", r["lots"][0])
        self.assertTrue(any("listed" in m for m in r["lots"][0]["match"]))  # unspaced sheet vs spaced lot
        r = core.lookup(app.index, app.odoo, "148")  # only a different serial sharing digits
        self.assertTrue(r["lots"][0]["match"][0].startswith("possible"))
        self.assertTrue(any("partial" in w for w in r["warnings"]))

    def test_serial_match(self):
        self.assertEqual(core.serial_match("034 02 002361  12.09", "034 02 002361"), "exact")
        self.assertEqual(core.serial_match("00903000938 05.05", "009 03 000922 05.05 , 009 03 000938 05.05"), "listed")
        self.assertIsNone(core.serial_match("096 01 007646", "033120076 - 46725"))
        self.assertTrue(core.ai_in_ref(1948, "AI01948"))
        self.assertFalse(core.ai_in_ref(1948, "87219480"))

    def test_not_found(self):
        app = self.make_app()
        for ai in ("447", "800", "5555"):
            r = core.lookup(app.index, app.odoo, ai)
            self.assertFalse(r["lots"], ai)
            self.assertTrue(any("No lot/serial" in w for w in r["warnings"]))

    def test_partner_search_only_active(self):
        app = self.make_app()
        names = [p["display_name"] for p in core.search_partners(app.odoo, "remotti")]
        self.assertIn("Remotti S.A., Daniel Biglio", names)
        self.assertFalse(core.search_partners(app.odoo, "old customer"))


class TestGuard(Base):
    def vals(self, **kw):
        v = {"partner_id": 11, "product_id": 5, "lot_id": 100, "product_qty": 1.0}
        v.update(kw)
        return v

    def test_rejects_bad_values(self):
        odoo = self.make_app().odoo
        bad = [
            {"partner_id": "Remotti S.A."}, {"partner_id": True}, {"partner_id": 11.0}, {"partner_id": [11, "x"]},
            {"partner_id": 999}, {"partner_id": 30},  # missing, archived
            {"lot_id": {"name": "NEW"}}, {"nonexistent_field": 1},
            {"tag_ids": [[0, 0, {"name": "new tag"}]]}, {"tag_ids": [1, 2]}, {"tag_ids": [[6, 0, [99]]]},
        ]
        before = len(self.mock.creates)
        for b in bad:
            with self.assertRaises(core.GuardError, msg=str(b)):
                odoo.create_repair_order(self.vals(**b), dry_run=False)
        self.assertEqual(len(self.mock.creates), before)

    def test_forbidden_writes(self):
        odoo = self.make_app().odoo
        for model in ("res.partner", "product.product", "stock.lot"):
            with self.assertRaises(core.GuardError):
                odoo.execute(model, "create", [{"name": "x"}])
        with self.assertRaises(core.GuardError):
            odoo.execute("repair.order", "unlink", [[1]])

    def test_readonly_dropped(self):
        res = self.make_app().odoo.create_repair_order(self.vals(state="done"), dry_run=True)
        self.assertNotIn("state", res["payload"])
        self.assertTrue(res["warnings"])

    def test_missing_required(self):
        odoo = self.make_app().odoo
        res = odoo.create_repair_order(self.vals(), dry_run=True)  # no schedule_date
        self.assertTrue(any("schedule_date" in w for w in res["warnings"]))
        before = len(self.mock.creates)
        with self.assertRaises(core.GuardError):
            odoo.create_repair_order(self.vals(), dry_run=False)
        self.assertEqual(len(self.mock.creates), before)

    def test_dry_run_creates_nothing(self):
        before = len(self.mock.creates)
        res = self.make_app().odoo.create_repair_order(self.vals(tag_ids=[[6, 0, [1]]]), dry_run=True)
        self.assertTrue(res["dry_run"])
        self.assertEqual(len(self.mock.creates), before)


class TestHTTP(Base):
    def start(self, live):
        app = self.make_app(live)
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(app))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return app, "http://127.0.0.1:%d" % httpd.server_address[1]

    def call(self, base, path, body=None, token=None, host=None):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None)
        if token:
            req.add_header("X-Intake-Token", token)
        if host:
            req.add_header("Host", host)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_token_and_host(self):
        app, base = self.start(False)
        self.assertEqual(self.call(base, "/api/status")[0], 403)
        self.assertEqual(self.call(base, "/api/status", token=app.token, host="evil.example:80")[0], 403)
        code, st = self.call(base, "/api/status", token=app.token)
        self.assertEqual(code, 200)
        self.assertTrue(st["odoo_ok"])
        self.assertNotIn(mock_odoo.API_KEY, json.dumps(st))

    def test_dry_run_endpoint(self):
        app, base = self.start(False)
        before = len(self.mock.creates)
        code, r = self.call(base, "/api/create", {"ai": "1948", "lot_id": 100, "partner_id": 11,
                                                  "tag_ids": [2], "schedule_date": "2026-09-23T10:30",
                                                  "notes": "Display broken", "confirm": True, "confirm_ai": "1948"},
                            token=app.token)
        self.assertEqual(code, 200, r)
        self.assertTrue(r["dry_run"])
        p = r["payload"]
        self.assertEqual((p["partner_id"], p["product_id"], p["lot_id"], p["product_qty"]), (11, 5, 100, 1.0))
        self.assertEqual(p["tag_ids"], [[6, 0, [2]]])
        self.assertRegex(p["schedule_date"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertIn("ALFA AI 1948", p["internal_notes"])
        self.assertEqual(len(self.mock.creates), before)

    def test_partner_must_be_int(self):
        app, base = self.start(False)
        code, r = self.call(base, "/api/create", {"ai": "1948", "lot_id": 100, "partner_id": "Remotti"},
                            token=app.token)
        self.assertEqual(code, 400)

    def test_live_mode(self):
        app, base = self.start(True)
        before = len(self.mock.creates)
        body = {"ai": "1948", "lot_id": 100, "partner_id": 11}
        self.assertEqual(self.call(base, "/api/create", body, token=app.token)[0], 400)  # no confirmation
        self.assertEqual(self.call(base, "/api/create", dict(body, confirm=True, confirm_ai="1949"),
                                   token=app.token)[0], 400)  # wrong AI typed
        code, r = self.call(base, "/api/create", dict(body, preview=True), token=app.token)
        self.assertTrue(r["dry_run"])
        self.assertIn("schedule_date", r["payload"])  # required -> defaulted to now
        self.assertFalse(r["warnings"])
        code, r = self.call(base, "/api/create", dict(body, confirm=True, confirm_ai="AI1948"), token=app.token)
        self.assertEqual(code, 200, r)
        self.assertFalse(r["dry_run"])
        self.assertTrue(r["name"].startswith("RMA/"))
        self.assertEqual(len(self.mock.creates), before + 1)
        self.assertTrue(all(m == "repair.order" for m, _ in self.mock.creates))
        # double-submit guard
        code, r = self.call(base, "/api/create", dict(body, confirm=True, confirm_ai="1948",
                                                      allow_open_duplicate=True), token=app.token)
        self.assertEqual(code, 400)
        self.assertEqual(len(self.mock.creates), before + 1)

    def test_live_open_repair_guard(self):
        app, base = self.start(True)
        body = {"ai": "609", "lot_id": 102, "partner_id": 21, "confirm": True, "confirm_ai": "609"}
        code, r = self.call(base, "/api/create", body, token=app.token)
        self.assertEqual(code, 400)
        self.assertIn("RMA/03300", r["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
