"""Local equipment intake server: http://127.0.0.1:<port>/

Stdlib only (plus xlrd for the spreadsheet). Binds to 127.0.0.1.
"""

import json
import os
import secrets
import sys
import threading
import time
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import core
import ui

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "config.json")
LOG_PATH = os.path.join(HERE, "intake_log.jsonl")

CONFIG_DEFAULTS = {
    "odoo_url": "http://45.79.204.23:8069",
    "database": "alfa-v1",
    "username": "",
    "api_key": "",
    "spreadsheet": "equipos.xls",
    "live_mode": False,
    "host": "127.0.0.1",
    "port": 8765,
    "lot_ai_fields": ["ref", "barcode"],
    "company_id": None,
    "default_tag_ids": [],
    "timeout_seconds": 20,
}


def load_config(path=DEFAULT_CONFIG):
    cfg = dict(CONFIG_DEFAULTS)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    # environment overrides (handy for testing without editing the file)
    if os.environ.get("ODOO_API_KEY"):
        cfg["api_key"] = os.environ["ODOO_API_KEY"]
    if os.environ.get("ODOO_USERNAME"):
        cfg["username"] = os.environ["ODOO_USERNAME"]
    cfg["live_mode"] = cfg.get("live_mode") is True  # anything but literal true = dry-run
    sheet = cfg.get("spreadsheet") or ""
    if sheet and not os.path.isabs(sheet):
        cfg["spreadsheet"] = os.path.join(os.path.dirname(os.path.abspath(path)), sheet)
    return cfg


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.token = secrets.token_urlsafe(24)
        self.create_lock = threading.Lock()
        self.recent_creates = {}  # lot_id -> timestamp
        self.index = None
        self.index_error = None
        self.odoo = None
        self.reload()

    def reload(self):
        try:
            self.index = core.EquipmentIndex.from_xls(self.cfg["spreadsheet"])
            self.index_error = None
        except Exception as e:  # missing file, xlrd missing, bad format
            self.index = core.EquipmentIndex([], source=self.cfg["spreadsheet"])
            self.index_error = "%s: %s" % (type(e).__name__, e)
        self.odoo = core.OdooClient(self.cfg["odoo_url"], self.cfg["database"], self.cfg["username"],
                                    self.cfg["api_key"], timeout=self.cfg["timeout_seconds"])

    def log(self, entry):
        entry = dict(entry, ts=datetime.now().isoformat(timespec="seconds"))
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # -- endpoints ---------------------------------------------------------
    def status(self):
        out = {"live_mode": self.cfg["live_mode"], "odoo_url": self.cfg["odoo_url"],
               "database": self.cfg["database"], "username": self.cfg["username"],
               "api_key_set": bool(self.cfg["api_key"]),
               "spreadsheet": self.index.stats(), "spreadsheet_error": self.index_error}
        try:
            uid = self.odoo.login()
            out.update(odoo_ok=True, uid=uid, server_version=self.odoo.server_version)
            me = self.odoo.search_read("res.users", [("id", "=", uid)], ["name", "company_id"])
            out["user_name"] = me[0]["name"] if me else None
            out["field_map"] = self.odoo.repair_field_map()
            tag_model = self.odoo.fields("repair.order").get("tag_ids", {}).get("relation")
            out["tags"] = self.odoo.search_read(tag_model, [], ["name"], limit=200) if tag_model else []
            out["default_tag_ids"] = self.cfg["default_tag_ids"]
        except Exception as e:
            out.update(odoo_ok=False, odoo_error=str(e))
        return out

    def diagnose(self):
        out = {}
        for model in ("repair.order", "stock.lot"):
            meta = self.odoo.fields(model)
            out[model] = {k: {a: v.get(a) for a in ("string", "type", "relation", "required", "readonly")}
                          for k, v in sorted(meta.items())}
        out["field_map"] = self.odoo.repair_field_map()
        return out

    def do_create(self, body):
        ai = core.parse_ai(body.get("ai"))
        lot_id = body.get("lot_id")
        if ai is None or type(lot_id) is not int:
            raise core.GuardError("AI and lot_id are required.")
        # re-read the lot: product always comes from Odoo, never from the browser
        lots = self.odoo.search_read("stock.lot", [("id", "=", lot_id)], ["name", "product_id"])
        if not lots or not lots[0].get("product_id"):
            raise core.GuardError("Lot %s not found in Odoo." % lot_id)
        lot = lots[0]
        product_id = lot["product_id"][0]
        prod = self.odoo.search_read("product.product", [("id", "=", product_id)], ["uom_id"])
        form = {
            "partner_id": body.get("partner_id"),
            "product_id": product_id,
            "lot_id": lot_id,
            "user_id": body.get("user_id") or self.odoo.uid,
            "tag_ids": body.get("tag_ids") or [],
            "schedule_date": body.get("schedule_date"),
            "under_warranty": body.get("under_warranty") is True,
            "notes": body.get("notes") or "",
            "company_id": self.cfg.get("company_id"),
            "uom_id": prod[0]["uom_id"][0] if prod and prod[0].get("uom_id") else None,
        }
        vals = core.build_repair_vals(self.odoo, form, notes_prefix="ALFA AI %d (lot %s)" % (ai, lot["name"]))

        live = self.cfg["live_mode"]
        if not live or body.get("preview") is True:
            res = self.odoo.create_repair_order(vals, dry_run=True)
            self.log({"mode": "dry-run", "ai": ai, "payload": res["payload"]})
            return res

        # live mode: explicit confirmation + duplicate guards
        if body.get("confirm") is not True or core.parse_ai(body.get("confirm_ai")) != ai:
            raise core.GuardError("Live mode: confirmation missing (type the AI number to confirm).")
        fmap = self.odoo.repair_field_map()
        open_reps = self.odoo.search_read(
            "repair.order", [(fmap["lot"], "=", lot_id), ("state", "not in", list(core.CLOSED_REPAIR_STATES))],
            ["name"])
        if open_reps and body.get("allow_open_duplicate") is not True:
            raise core.GuardError("This lot already has open repair(s): %s. Tick 'create anyway' to proceed."
                                  % ", ".join(r["name"] for r in open_reps))
        with self.create_lock:
            last = self.recent_creates.get(lot_id)
            if last and time.time() - last < 120:
                raise core.GuardError("A repair for this lot was created less than 2 minutes ago.")
            res = self.odoo.create_repair_order(vals, dry_run=False)
            self.recent_creates[lot_id] = time.time()
        self.log({"mode": "LIVE", "ai": ai, "payload": res["payload"], "id": res["id"], "name": res["name"]})
        return res


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "EquipmentIntake/1.0"

        def log_message(self, fmt, *args):  # keep the console quiet; never log bodies
            sys.stderr.write("%s %s\n" % (self.command, self.path.split("?")[0]))

        def _send(self, code, payload, ctype="application/json; charset=utf-8"):
            data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(data)

        def _host_ok(self):
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def _api_ok(self):
            return self._host_ok() and secrets.compare_digest(self.headers.get("X-Intake-Token", ""), app.token)

        def _run(self, fn):
            try:
                self._send(200, fn())
            except core.GuardError as e:
                self._send(400, {"error": str(e), "kind": "guard"})
            except core.OdooError as e:
                self._send(502, {"error": str(e), "kind": "odoo"})
            except Exception as e:
                traceback.print_exc()
                self._send(500, {"error": "%s: %s" % (type(e).__name__, e)})

        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path == "/":
                if not self._host_ok():
                    return self._send(403, {"error": "bad host"})
                return self._send(200, ui.render(app.token, app.cfg["live_mode"]).encode(), "text/html; charset=utf-8")
            if not self._api_ok():
                return self._send(403, {"error": "forbidden"})
            if url.path == "/api/status":
                return self._run(app.status)
            if url.path == "/api/lookup":
                return self._run(lambda: core.lookup(app.index, app.odoo, q.get("ai", ""),
                                                     app.cfg["lot_ai_fields"]))
            if url.path == "/api/partners":
                return self._run(lambda: core.search_partners(app.odoo, q.get("q", "")))
            if url.path == "/api/diagnose":
                return self._run(app.diagnose)
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._api_ok():
                return self._send(403, {"error": "forbidden"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except Exception:
                return self._send(400, {"error": "invalid JSON"})
            path = urlparse(self.path).path
            if path == "/api/create":
                return self._run(lambda: app.do_create(body))
            if path == "/api/reload":
                def reload():
                    app.cfg = load_config(app.cfg_path)
                    app.reload()
                    return app.status()
                return self._run(reload)
            self._send(404, {"error": "not found"})

    return Handler


def serve(cfg_path=DEFAULT_CONFIG, open_browser=True):
    cfg = load_config(cfg_path)
    app = App(cfg)
    app.cfg_path = cfg_path
    if cfg["host"] not in ("127.0.0.1", "localhost"):
        raise SystemExit("Refusing to bind to %s: this tool must only listen on 127.0.0.1." % cfg["host"])
    httpd = ThreadingHTTPServer((cfg["host"], cfg["port"]), make_handler(app))
    url = "http://127.0.0.1:%d/" % httpd.server_address[1]
    print("Equipment intake running at %s  (mode: %s)" % (url, "LIVE" if cfg["live_mode"] else "dry-run"))
    if app.index_error:
        print("WARNING spreadsheet: %s" % app.index_error)
    if open_browser:
        import webbrowser
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return httpd


if __name__ == "__main__":
    paths = [a for a in sys.argv[1:] if not a.startswith("--")]
    serve(paths[0] if paths else DEFAULT_CONFIG, open_browser="--no-browser" not in sys.argv)
