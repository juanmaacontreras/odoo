"""Tiny in-memory Odoo 17 JSON-RPC imitation for offline testing (selftest.py).

Implements only what this tool uses: common.version/authenticate and
object.execute_kw with fields_get, search_read, search, search_count, read and
create. Records every create so tests can assert nothing else was created.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API_KEY = "test-key"


def F(string, type_, relation=None, readonly=False, required=False, selection=None):
    d = {"string": string, "type": type_, "readonly": readonly, "required": required}
    if relation:
        d["relation"] = relation
    if selection:
        d["selection"] = selection
    return d


FIELDS = {
    "repair.order": {
        "id": F("ID", "integer", readonly=True),
        "name": F("Repair Reference", "char", required=True),
        "picking_type_id": F("Operation Type", "many2one", "stock.picking.type", required=True),
        "location_id": F("Location", "many2one", "stock.location", required=True),
        "partner_id": F("Customer", "many2one", "res.partner"),
        "product_id": F("Product to Repair", "many2one", "product.product"),
        "lot_id": F("Lot/Serial", "many2one", "stock.lot", readonly=True),  # computed+stored in Odoo 17
        "location_dest_id": F("Destination Location", "many2one", "stock.location", required=True),
        "parts_location_id": F("Parts Location", "many2one", "stock.location", required=True),
        "recycle_location_id": F("Recycle Location", "many2one", "stock.location", required=True),
        "product_qty": F("Product Quantity", "float"),
        "product_uom": F("Product Unit of Measure", "many2one", "uom.uom"),
        "schedule_date": F("Scheduled Date", "datetime", required=True),
        "user_id": F("Responsible", "many2one", "res.users"),
        "company_id": F("Company", "many2one", "res.company", required=True),
        "tag_ids": F("Tags", "many2many", "repair.tags"),
        "under_warranty": F("Under Warranty", "boolean"),
        "internal_notes": F("Internal Notes", "html"),
        "state": F("Status", "selection", readonly=True,
                   selection=[["draft", "New"], ["confirmed", "Confirmed"], ["under_repair", "Under Repair"],
                              ["done", "Repaired"], ["cancel", "Cancelled"]]),
        "create_date": F("Created on", "datetime", readonly=True),
    },
    "stock.lot": {
        "id": F("ID", "integer", readonly=True),
        "name": F("Lot/Serial Number", "char"),
        "ref": F("Internal Reference", "char"),
        "product_id": F("Product", "many2one", "product.product"),
        "company_id": F("Company", "many2one", "res.company"),
        "create_date": F("Created on", "datetime", readonly=True),
    },
    "res.partner": {k: F(k, t, r) for k, t, r in [
        ("id", "integer", None), ("name", "char", None), ("display_name", "char", None),
        ("parent_id", "many2one", "res.partner"), ("is_company", "boolean", None),
        ("vat", "char", None), ("city", "char", None), ("active", "boolean", None)]},
    "product.product": {k: F(k, t, r) for k, t, r in [
        ("id", "integer", None), ("display_name", "char", None), ("default_code", "char", None),
        ("uom_id", "many2one", "uom.uom")]},
    "res.users": {k: F(k, t, r) for k, t, r in [
        ("id", "integer", None), ("name", "char", None), ("company_id", "many2one", "res.company")]},
    "repair.tags": {"id": F("ID", "integer"), "name": F("Name", "char")},
    "uom.uom": {"id": F("ID", "integer"), "name": F("Name", "char")},
    "res.company": {"id": F("ID", "integer"), "name": F("Name", "char")},
}

# what Odoo would default on repair.order (schedule_date has no default on purpose)
DEFAULTS = {"repair.order": {"name": "New", "company_id": 1, "picking_type_id": 1, "location_id": 8}}


def seed():
    partners = [
        {"id": 10, "name": "Remotti S.A.", "parent_id": False, "is_company": True, "vat": "30-11111111-1", "city": "CABA"},
        {"id": 11, "name": "Daniel Biglio", "parent_id": [10, "Remotti S.A."], "is_company": False, "vat": False, "city": "CABA"},
        {"id": 20, "name": "AySA", "parent_id": False, "is_company": True, "vat": "30-22222222-2", "city": "CABA"},
        {"id": 21, "name": "AySA DRN", "parent_id": [20, "AySA"], "is_company": False, "vat": False, "city": "CABA"},
        {"id": 30, "name": "Old Customer", "parent_id": False, "is_company": True, "vat": False, "city": "", "active": False},
    ]
    for p in partners:
        p.setdefault("active", True)
        p["display_name"] = ("%s, %s" % (p["parent_id"][1], p["name"])) if p["parent_id"] else p["name"]
    products = [
        {"id": 5, "display_name": "[CP4] CheckPoint 4 O2/CO2 (O) (CP4)", "default_code": "CP4", "uom_id": [1, "Units"]},
        {"id": 6, "display_name": "[H5K5] H5K5 Leak Tester", "default_code": "H5K5", "uom_id": [1, "Units"]},
        {"id": 7, "display_name": "[AQUAPHON] Aquaphon A200", "default_code": "AQUAPHON", "uom_id": [1, "Units"]},
    ]
    lots = [
        {"id": 100, "name": "11262443", "ref": False, "product_id": [5, products[0]["display_name"]]},
        {"id": 101, "name": "62181114", "ref": "AI1114", "product_id": [6, products[1]["display_name"]]},
        {"id": 102, "name": "01102004429", "ref": False, "product_id": [7, products[2]["display_name"]]},
        {"id": 103, "name": "SN-01102004429-B", "ref": False, "product_id": [7, products[2]["display_name"]]},
        {"id": 104, "name": "A6 553311", "ref": False, "product_id": [7, products[2]["display_name"]]},
        {"id": 105, "name": "A6553311", "ref": False, "product_id": [7, products[2]["display_name"]]},
        {"id": 106, "name": "104 15 005048   06.21", "ref": "AI00001", "product_id": [7, products[2]["display_name"]]},
        {"id": 107, "name": "009 03 000922 05.05  , 009 03 000938 05.05", "ref": "AI00115 , AI00116",
         "product_id": [7, products[2]["display_name"]]},
        {"id": 108, "name": "03002000529 , 03102000334", "ref": False, "product_id": [7, products[2]["display_name"]]},
    ]
    for l in lots:
        l["company_id"] = [1, "Alfa Instrumentos SRL"]
        l["create_date"] = "2024-01-01 12:00:00"
    repairs = [
        {"id": 3221, "name": "RMA/03221", "partner_id": [11, "Remotti S.A., Daniel Biglio"],
         "product_id": [5, products[0]["display_name"]], "lot_id": [100, "11262443"], "state": "done",
         "schedule_date": "2025-03-01 13:00:00", "create_date": "2025-03-01 12:00:00", "user_id": [2, "Juanma"],
         "under_warranty": False},
        {"id": 3300, "name": "RMA/03300", "partner_id": [21, "AySA, AySA DRN"],
         "product_id": [7, products[2]["display_name"]], "lot_id": [102, "01102004429"], "state": "confirmed",
         "schedule_date": "2026-08-01 13:00:00", "create_date": "2026-08-01 12:00:00", "user_id": [2, "Juanma"],
         "under_warranty": False},
    ]
    return {
        "res.partner": partners, "product.product": products, "stock.lot": lots, "repair.order": repairs,
        "res.users": [{"id": 2, "name": "Juanma", "company_id": [1, "Alfa Instrumentos SRL"]}],
        "repair.tags": [{"id": 1, "name": "Garantia"}, {"id": 2, "name": "Calibracion"}],
        "uom.uom": [{"id": 1, "name": "Units"}],
        "res.company": [{"id": 1, "name": "Alfa Instrumentos SRL"}],
    }


def _get(rec, path, db):
    if "." in path:  # parent_id.name style
        head, rest = path.split(".", 1)
        val = rec.get(head)
        if not val:
            return None
        rel_model = {"parent_id": "res.partner"}.get(head)
        target = next((r for r in db.get(rel_model, []) if r["id"] == val[0]), None)
        return _get(target, rest, db) if target else None
    val = rec.get(path)
    if isinstance(val, list) and len(val) == 2 and isinstance(val[0], int):
        return val[0]
    return val


def _leaf(rec, leaf, db):
    field, op, value = leaf
    v = _get(rec, field, db)
    if op == "=":
        return v == value or (value is False and v in (None, False))
    if op == "!=":
        return v != value
    if op == "in":
        return v in value
    if op == "not in":
        return v not in value
    if op in (">=", "<=", ">", "<"):
        if v in (None, False):
            return False
        return {">=": v >= value, "<=": v <= value, ">": v > value, "<": v < value}[op]
    if op == "ilike":
        return v not in (None, False) and str(value).lower() in str(v).lower()
    raise ValueError("unsupported operator %s" % op)


def match(rec, domain, db):
    stack = []
    for tok in reversed(domain):
        if tok == "|":
            a, b = stack.pop(), stack.pop()
            stack.append(a or b)
        elif tok == "&":
            a, b = stack.pop(), stack.pop()
            stack.append(a and b)
        elif tok == "!":
            stack.append(not stack.pop())
        else:
            stack.append(_leaf(rec, tok, db))
    return all(stack)


class MockOdoo:
    def __init__(self):
        self.db = seed()
        self.creates = []  # (model, vals)
        self.calls = []    # (model, method)
        self.lock = threading.Lock()
        self.httpd = None

    def records(self, model, domain, include_archived=False):
        recs = self.db.get(model, [])
        if not include_archived:
            recs = [r for r in recs if r.get("active", True)]
        return [r for r in recs if match(r, domain, self.db)]

    def execute(self, model, method, args, kw):
        self.calls.append((model, method))
        if model not in FIELDS:
            raise ValueError("Object %s doesn't exist" % model)
        if method == "fields_get":
            return FIELDS[model]
        if method == "onchange":
            return self.onchange(model, args[1], args[3])
        if method == "default_get":
            defaults = DEFAULTS.get(model, {})
            return {f: defaults[f] for f in args[0] if f in defaults}
        if method in ("search_read", "search", "search_count"):
            recs = self.records(model, args[0] if args else [])
            if kw.get("order", "").startswith("create_date desc"):
                recs = sorted(recs, key=lambda r: (r.get("create_date") or "", r["id"]), reverse=True)
            elif kw.get("order", "").startswith("id desc"):
                recs = sorted(recs, key=lambda r: r["id"], reverse=True)
            if kw.get("limit"):
                recs = recs[:kw["limit"]]
            if method == "search_count":
                return len(recs)
            if method == "search":
                return [r["id"] for r in recs]
            fields = kw.get("fields") or list(FIELDS[model])
            for f in fields:
                if f not in FIELDS[model]:
                    raise ValueError("Invalid field %r on model %r" % (f, model))
            return [dict({"id": r["id"]}, **{f: r.get(f, False) for f in fields}) for r in recs]
        if method == "read":
            ids = args[0]
            fields = (kw.get("fields") or list(FIELDS[model]))
            return [dict({"id": r["id"]}, **{f: r.get(f, False) for f in fields})
                    for r in self.db[model] if r["id"] in ids]
        if method == "create":
            vals = args[0]
            with self.lock:
                self.creates.append((model, vals))
                if model != "repair.order":
                    raise ValueError("mock: unexpected create on %s" % model)
                for k in vals:
                    if k not in FIELDS[model]:
                        raise ValueError("Invalid field %r on model %r" % (k, model))
                new_id = max(r["id"] for r in self.db[model]) + 1
                rec = {"id": new_id, "name": "RMA/%05d" % new_id, "state": "draft",
                       "create_date": "2026-09-23 12:00:00"}
                for k, v in vals.items():
                    if FIELDS[model][k]["type"] == "many2one":
                        rel = self.db[FIELDS[model][k]["relation"]]
                        r = next(x for x in rel if x["id"] == v)
                        v = [v, r.get("display_name") or r.get("name")]
                    rec[k] = v
                self.db[model].append(rec)
                return new_id
        raise ValueError("mock: method %s not supported" % method)

    def onchange(self, model, values, spec):
        """Odoo 17 first-call onchange: defaults + values + computes, nothing saved."""
        rec = dict(DEFAULTS.get(model, {}))
        rec.update(values)
        if model == "repair.order":
            if rec.get("picking_type_id"):  # locations computed from the operation type
                for f in ("location_id", "location_dest_id", "parts_location_id", "recycle_location_id"):
                    rec.setdefault(f, 8)
            lot = next((l for l in self.db["stock.lot"] if l["id"] == rec.get("lot_id")), None)
            if lot and lot["product_id"][0] != rec.get("product_id"):
                rec["lot_id"] = False  # Odoo clears a lot that belongs to another product
        out = {}
        for f in spec:
            v = rec.get(f, False)
            meta = FIELDS[model].get(f, {})
            if meta.get("type") == "many2one" and v:
                rel = self.db.get(meta["relation"], [])
                r = next((x for x in rel if x["id"] == v), {})
                v = {"id": v, "display_name": r.get("display_name") or r.get("name") or str(v)}
            out[f] = v
        return {"value": out}

    def handle(self, payload):
        p = payload["params"]
        service, method, args = p["service"], p["method"], p["args"]
        if service == "common" and method == "version":
            return {"server_version": "17.0", "server_serie": "17.0"}
        if service == "common" and method == "authenticate":
            db, login, key, _ = args
            return 2 if (db == "alfa-v1" and login == "juanma" and key == API_KEY) else False
        if service == "object" and method == "execute_kw":
            db, uid, key, model, meth = args[:5]
            if key != API_KEY or uid != 2:
                raise PermissionError("Access Denied")
            return self.execute(model, meth, args[5] if len(args) > 5 else [], args[6] if len(args) > 6 else {})
        raise ValueError("unsupported call %s.%s" % (service, method))

    def start(self, port=0):
        mock = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                try:
                    out = {"jsonrpc": "2.0", "id": body.get("id"), "result": mock.handle(body)}
                except Exception as e:
                    out = {"jsonrpc": "2.0", "id": body.get("id"),
                           "error": {"code": 200, "message": "Odoo Server Error",
                                     "data": {"name": type(e).__name__, "message": str(e)}}}
                data = json.dumps(out).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()


if __name__ == "__main__":
    m = MockOdoo()
    print("Mock Odoo on", m.start(8069), "db=alfa-v1 user=juanma key=%s" % API_KEY)
    threading.Event().wait()
