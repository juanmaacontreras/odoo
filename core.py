"""Core logic: spreadsheet index (AI -> manufacturer serial) and Odoo JSON-RPC client.

Safety rules enforced here:
  * Never create res.partner, product or stock.lot records. The only write this
    module can perform is repair.order.create, and only through
    OdooClient.create_repair_order, which validates every value first.
  * Every relational value must be an existing integer id (checked against Odoo).
  * Only fields that exist on this install's repair.order are sent.
  * Dry-run by default: every read happens, only the final create is withheld.
"""

import html
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Spreadsheet
# ---------------------------------------------------------------------------

JUNK_SERIAL_RE = re.compile(r"^\s*(no\s*tiene.*|s/?n|n/?a|sin\s*serie.*|-+|\.+|\?+|x+)\s*$", re.I)
AI_RE = re.compile(r"^\s*(?:AI)?\s*[-#:]?\s*(\d{1,7})\s*$", re.I)


def parse_ai(text):
    """Accept '1948', 'AI1948', 'ai 1948', '01948'. Returns int or None."""
    if text is None:
        return None
    m = AI_RE.match(str(text))
    if not m:
        return None
    value = int(m.group(1))
    return value if value > 0 else None


def cell_to_text(value):
    """Spreadsheet cell -> clean string (numbers without the trailing .0)."""
    if value is None:
        return ""
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return str(value).strip()


def is_junk_serial(serial):
    """Placeholders like '----', 'NO TIENE', 'BOMBA', 'AIRE': no digit at all."""
    s = (serial or "").strip()
    if not s or JUNK_SERIAL_RE.match(s):
        return True
    return not any(ch.isdigit() for ch in s)


def serial_key(serial):
    """Normalised key used to detect the same serial written differently."""
    return re.sub(r"[^0-9A-Z]", "", (serial or "").upper())


def serial_variants(serial):
    """Exact-match candidates: as-is, no spaces, no dots/dashes/slashes."""
    s = (serial or "").strip()
    out = []
    for v in (
        s,
        re.sub(r"\s+", "", s),
        re.sub(r"[\s.\-–—_/]+", "", s),
        # drop a trailing suffix like '- A6' / '– A6'
        re.sub(r"\s*[-–—]\s*[A-Za-z]\w{0,3}$", "", s),
    ):
        if v and v not in out:
            out.append(v)
    return out


SERIAL_DATE_RE = re.compile(r"\s+\d{2}\.\d{2}\s*$")  # Sewerin 'MM.YY' suffix: '104 15 005048 06.21'


def strip_serial_date(serial):
    return SERIAL_DATE_RE.sub("", (serial or "").strip())


def serial_core(serial):
    return serial_key(strip_serial_date(serial))


def lot_pieces(lot_name):
    """A lot name may list several serials: '009 03 000922 05.05 , 009 03 000938 05.05'."""
    return [p.strip() for p in re.split(r"[,;]| / ", lot_name or "") if p.strip()]


SERIAL_MATCH_LABELS = {
    "exact": "same serial as spreadsheet (%s)",
    "listed": "spreadsheet serial %s listed in this lot",
    "partial": "possible: shares digits with spreadsheet serial %s",
}


def serial_match(sheet_serial, lot_name):
    """'exact' | 'listed' | 'partial' | None, ignoring spacing, punctuation and the MM.YY suffix."""
    want = serial_core(sheet_serial)
    if not want:
        return None
    pieces = lot_pieces(lot_name)
    cores = [serial_core(p) for p in pieces]
    if len(pieces) == 1 and (cores[0] == want or serial_key(pieces[0]) == serial_key(sheet_serial)):
        return "exact"
    if want in cores:
        return "listed"
    run = longest_digit_run(strip_serial_date(sheet_serial))
    if run and any(run in group for p in pieces for group in re.findall(r"\d+", p)):
        return "partial"
    return None


def search_needles(serial):
    """ilike needles that survive spacing differences: the longest digit run, plus the last
    6 digits (the unit number in Sewerin 'NNN NN NNNNNN' serials)."""
    base = strip_serial_date(serial)
    needles = []
    run = longest_digit_run(base)
    if run:
        needles.append(run)
    digits = re.sub(r"\D", "", base)
    if len(digits) >= 9 and digits[-6:] not in needles:
        needles.append(digits[-6:])
    return needles


def ref_ais(ref, allow_plain=True):
    """AI numbers named in a lot reference: 'AI01948', 's/n: AI01631', 'AI00115 , AI00116', '1948'.
    allow_plain=False for lot names, where a bare number is a serial, not an AI."""
    ref = (ref or "").strip()
    if not ref:
        return []
    numbers = [int(n) for n in re.findall(r"(?i)AI\s*[-#:]?\s*(\d{1,7})\b", ref)]
    if allow_plain and re.fullmatch(r"0*\d{1,5}", ref):
        numbers.append(int(ref))
    return numbers


def ai_in_ref(ai, ref, allow_plain=True):
    return ai in ref_ais(ref, allow_plain)


def lot_ais(lot):
    """AIs a lot names in its ref or (as 'AI01948') in its name."""
    return sorted(set(ref_ais(lot.get("ref"))) | set(ref_ais(lot.get("name"), allow_plain=False)))


def longest_digit_run(serial, minimum=6):
    runs = re.findall(r"\d+", serial or "")
    if not runs:
        return None
    best = max(runs, key=len)
    return best if len(best) >= minimum else None


class EquipmentIndex:
    """Maps AI number -> spreadsheet rows. Used ONLY for AI -> manufacturer serial."""

    COLUMNS = ("ai", "status", "date", "model", "serial", "version", "customer")

    def __init__(self, rows, source="(memory)"):
        self.source = source
        self.by_ai = {}
        self.by_serial = {}
        self.unparsed = []  # rows with something in column A that is not an AI number
        for row in rows:
            ai = row.get("ai")
            if not ai:
                if str(row.get("ai_raw") or "").strip():
                    self.unparsed.append(row)
                continue
            self.by_ai.setdefault(ai, []).append(row)
            if not is_junk_serial(row.get("serial")):
                self.by_serial.setdefault(serial_key(row["serial"]), set()).add(ai)

    @classmethod
    def from_xls(cls, path):
        import xlrd  # only dependency

        book = xlrd.open_workbook(path)
        sheet = book.sheet_by_index(0)
        rows = []
        for r in range(1, sheet.nrows):  # row 0 is the header
            vals = sheet.row_values(r)
            vals += [""] * (7 - len(vals))
            ai = parse_ai(cell_to_text(vals[0]))
            annulled = bool(re.search(r"(?i)anulad", str(vals[0])))
            if ai is None and annulled:  # e.g. '  ANULADO    1811'
                nums = re.findall(r"\d+", str(vals[0]))
                ai = int(nums[-1]) if nums else None
            date = vals[2]
            if isinstance(date, float) and date > 0:
                try:
                    date = xlrd.xldate_as_datetime(date, book.datemode).date().isoformat()
                except Exception:
                    date = cell_to_text(date)
            else:
                date = cell_to_text(date)
            rows.append({
                "ai": ai,
                "ai_raw": vals[0] if isinstance(vals[0], str) else cell_to_text(vals[0]) if vals[0] != "" else "",
                "ai_raw_type": type(vals[0]).__name__,
                "annulled": annulled,
                "row": r + 1,
                "status": cell_to_text(vals[1]),
                "date": date,
                "model": cell_to_text(vals[3]),
                "serial": cell_to_text(vals[4]),
                "version": cell_to_text(vals[5]),
                "customer": cell_to_text(vals[6]),
            })
        return cls(rows, source=path)

    def stats(self):
        rows = [r for rs in self.by_ai.values() for r in rs]
        return {
            "source": self.source,
            "ais": len(self.by_ai),
            "with_serial": sum(1 for r in rows if not is_junk_serial(r["serial"])),
            "shared_serials": sum(1 for s in self.by_serial.values() if len(s) > 1),
            "ais_in_several_rows": sum(1 for rs in self.by_ai.values() if len(rs) > 1),
            "unparsed_ai_cells": len(self.unparsed),
        }

    def lookup(self, ai):
        """Returns {'rows': [...], 'serial': str|None, 'warnings': [...]}."""
        rows = self.by_ai.get(ai, [])
        warnings = []
        if len(rows) > 1:
            warnings.append("AI %d appears in %d spreadsheet rows (%s)." % (
                ai, len(rows), ", ".join(str(r["row"]) for r in rows)))
        for r in rows:
            if r.get("annulled"):
                warnings.append("Spreadsheet row %s marks AI %d as ANULADO: %r." % (r["row"], ai, r["ai_raw"]))
        serials = []
        for r in rows:
            if is_junk_serial(r["serial"]):
                if r["serial"]:
                    warnings.append("Spreadsheet serial for AI %d is not a serial: %r." % (ai, r["serial"]))
                continue
            if r["serial"] not in serials:
                serials.append(r["serial"])
            others = sorted(self.by_serial.get(serial_key(r["serial"]), set()) - {ai})
            if others:
                warnings.append("Serial %s is also listed for AI %s in the spreadsheet." % (
                    r["serial"], ", ".join(map(str, others))))
            if "H5K5" in r["model"].upper():
                digits = re.sub(r"\D", "", r["serial"])
                if digits[-4:] != str(ai).zfill(4)[-4:]:
                    warnings.append("H5K5 unit: serial %s does not end in the AI (%04d)." % (r["serial"], ai))
        if len(serials) > 1:
            warnings.append("Rows for AI %d disagree on the serial: %s." % (ai, ", ".join(serials)))
        return {"rows": rows, "serial": serials[0] if serials else None,
                "serials": serials, "warnings": warnings}


# ---------------------------------------------------------------------------
# Odoo JSON-RPC
# ---------------------------------------------------------------------------

class OdooError(Exception):
    pass


class GuardError(ValueError):
    """Raised when a repair.order payload fails the safety checks."""


# logical name -> candidate field names on repair.order (first existing wins)
REPAIR_FIELD_CANDIDATES = {
    "partner": ["partner_id"],
    "product": ["product_id"],
    "lot": ["lot_id"],
    "quantity": ["product_qty", "quantity", "product_uom_qty"],
    "uom": ["product_uom"],
    "schedule_date": ["schedule_date", "scheduled_date", "date_planned"],
    "user": ["user_id"],
    "tags": ["tag_ids"],
    "notes": ["internal_notes", "description", "notes", "note"],
    "under_warranty": ["under_warranty", "guarantee_limit"],
    "company": ["company_id"],
}

# Computed+stored fields that fields_get reports as read-only but that the Odoo web form
# sends on create (repair.order.lot_id in Odoo 17). They are sent and checked after the simulation.
WRITABLE_READONLY = {"lot_id"}

# Odoo 17 computes these from the operation type when the record is created.
KNOWN_COMPUTED_ON_CREATE = {"location_id", "location_dest_id", "parts_location_id", "recycle_location_id"}

WRITE_FORBIDDEN_MODELS = {"res.partner", "product.product", "product.template", "stock.lot",
                          "stock.production.lot"}


class OdooClient:
    def __init__(self, url, db, username, api_key, timeout=20):
        self.url = url.rstrip("/") + "/jsonrpc"
        self.db = db
        self.username = username
        self._key = api_key  # never logged, never returned
        self.timeout = timeout
        self.uid = None
        self.server_version = None
        self._fields = {}
        self._req = 0

    def __repr__(self):
        return "<OdooClient %s db=%s user=%s>" % (self.url, self.db, self.username)

    def _rpc(self, service, method, args):
        self._req += 1
        body = json.dumps({"jsonrpc": "2.0", "method": "call", "id": self._req,
                           "params": {"service": service, "method": method, "args": args}}).encode()
        req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise OdooError("HTTP %s from Odoo" % e.code) from None
        except (urllib.error.URLError, OSError) as e:
            raise OdooError("Cannot reach Odoo at %s: %s" % (self.url, getattr(e, "reason", e))) from None
        if data.get("error"):
            err = data["error"]
            msg = (err.get("data") or {}).get("message") or err.get("message") or "Odoo error"
            raise OdooError(msg)
        return data.get("result")

    # -- session ----------------------------------------------------------
    def login(self):
        info = self._rpc("common", "version", [])
        self.server_version = info.get("server_version") if isinstance(info, dict) else None
        uid = self._rpc("common", "authenticate", [self.db, self.username, self._key, {}])
        if not uid:
            raise OdooError("Login failed: check username / API key / database name.")
        self.uid = uid
        return uid

    def execute(self, model, method, args=None, kwargs=None):
        if self.uid is None:
            self.login()
        if model in WRITE_FORBIDDEN_MODELS and method in ("create", "write", "unlink", "copy",
                                                            "name_create", "load"):
            raise GuardError("Refusing %s on %s: this tool never modifies %s." % (method, model, model))
        if method not in ("fields_get", "search_read", "search", "search_count", "read",
                          "check_access_rights", "default_get", "onchange") and not (model == "repair.order" and method == "create"):
            raise GuardError("Method %s.%s is not allowed by this tool." % (model, method))
        return self._rpc("object", "execute_kw",
                         [self.db, self.uid, self._key, model, method, args or [], kwargs or {}])

    # -- reads ------------------------------------------------------------
    def fields(self, model):
        if model not in self._fields:
            self._fields[model] = self.execute(model, "fields_get", [], {
                "attributes": ["string", "type", "relation", "required", "readonly", "selection"]})
        return self._fields[model]

    def search_read(self, model, domain, fields, limit=None, order=None):
        existing = self.fields(model)
        kw = {"fields": [f for f in fields if f in existing]}
        if limit:
            kw["limit"] = limit
        if order:
            kw["order"] = order
        return self.execute(model, "search_read", [domain], kw)

    def repair_field_map(self):
        meta = self.fields("repair.order")
        return {logical: next((c for c in cands if c in meta), None)
                for logical, cands in REPAIR_FIELD_CANDIDATES.items()}

    # -- the one write ----------------------------------------------------
    def validate_repair_vals(self, vals):
        """Static + live checks. Returns (clean_vals, warnings). Raises GuardError."""
        meta = self.fields("repair.order")
        clean, warnings, to_check = {}, [], []
        for name, value in vals.items():
            f = meta.get(name)
            if f is None:
                raise GuardError("Field %r does not exist on repair.order in this Odoo." % name)
            if f.get("readonly") and name not in WRITABLE_READONLY:
                warnings.append("Field %s is read-only in this Odoo; not sent." % name)
                continue
            ftype = f.get("type")
            if ftype == "many2one":
                if type(value) is not int or value <= 0:
                    raise GuardError("%s must be an existing integer id, got %r." % (name, value))
                to_check.append((name, f["relation"], [value]))
            elif ftype in ("many2many", "one2many"):
                ok = (isinstance(value, list) and len(value) == 1 and isinstance(value[0], (list, tuple))
                      and len(value[0]) == 3 and value[0][0] == 6 and value[0][1] == 0
                      and isinstance(value[0][2], list)
                      and all(type(i) is int and i > 0 for i in value[0][2]))
                if not ok:
                    raise GuardError("%s must be [(6, 0, [existing ids])], got %r." % (name, value))
                value = [[6, 0, list(value[0][2])]]
                if value[0][2]:
                    to_check.append((name, f["relation"], value[0][2]))
            elif ftype == "boolean":
                if not isinstance(value, bool):
                    raise GuardError("%s must be true/false." % name)
            elif ftype in ("float", "integer", "monetary"):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise GuardError("%s must be a number." % name)
            elif ftype in ("char", "text", "html"):
                if not isinstance(value, str):
                    raise GuardError("%s must be text." % name)
            elif ftype == "date":
                if not (isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", value)):
                    raise GuardError("%s must be YYYY-MM-DD." % name)
            elif ftype == "datetime":
                if not (isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", value)):
                    raise GuardError("%s must be 'YYYY-MM-DD HH:MM:SS' (UTC)." % name)
            elif ftype == "selection":
                allowed = [s[0] for s in f.get("selection") or []]
                if value not in allowed:
                    raise GuardError("%s must be one of %s." % (name, allowed))
            else:
                raise GuardError("Field %s has type %s, which this tool does not send." % (name, ftype))
            clean[name] = value
        for name, relation, ids in to_check:
            found = self.execute(relation, "search_count", [[("id", "in", ids)]])
            if found != len(set(ids)):
                raise GuardError("%s: id(s) %s not found (or archived) in %s." % (name, ids, relation))
        return clean, warnings

    def simulate(self, vals):
        """Ask Odoo to compute a NEW repair order from vals without saving it (the same
        'onchange' call the web form makes while you fill it in). Returns {field: value}."""
        meta = self.fields("repair.order")
        wanted = {k for k, v in meta.items() if v.get("required")} | set(vals) | {
            "lot_id", "product_uom", "location_id", "location_dest_id", "picking_type_id", "company_id"}
        spec = {k: {} for k in sorted(wanted) if k in meta}
        res = self.execute("repair.order", "onchange", [[], dict(vals), [], spec])
        out = {}
        for k, v in ((res or {}).get("value") or {}).items():
            if isinstance(v, dict):
                v = [v.get("id"), v.get("display_name")] if v.get("id") else False
            out[k] = v
        return out

    def check_against_odoo(self, vals):
        """Returns (would_fill, problems). Uses the simulation; falls back to default_get."""
        meta = self.fields("repair.order")
        required = sorted(k for k, v in meta.items() if v.get("required") and k not in vals)
        problems = []
        try:
            sim = self.simulate(vals)
        except (OdooError, GuardError) as e:
            sim = None
            defaults = self.execute("repair.order", "default_get", [required]) if required else {}
            missing = [k for k in required if defaults.get(k) in (None, False, "", [])
                       and k not in KNOWN_COMPUTED_ON_CREATE]
            if missing:
                problems.append("Required field(s) without value or default: %s." % ", ".join(missing))
            return {"simulation": "unavailable (%s)" % e}, problems
        missing = [k for k in required if sim.get(k) in (None, False, "", [])]
        if missing:
            problems.append("Required field(s) Odoo would leave empty: %s." % ", ".join(missing))
        for k, v in vals.items():
            if meta.get(k, {}).get("type") == "many2one":
                got = sim.get(k)
                got_id = got[0] if isinstance(got, (list, tuple)) and got else got
                if k in sim and got_id != v:
                    problems.append("Odoo would change %s from %s to %r." % (k, v, got))
        would_fill = {k: v for k, v in sim.items() if k not in vals}
        return would_fill, problems

    def create_repair_order(self, vals, dry_run=True):
        clean, warnings = self.validate_repair_vals(vals)
        would_fill, problems = self.check_against_odoo(clean)
        if problems and not dry_run:
            raise GuardError("Not created: " + " ".join(problems))
        warnings += ["Odoo would reject or alter this: " + p for p in problems]
        if dry_run:
            return {"dry_run": True, "payload": clean, "warnings": warnings, "odoo_would_fill": would_fill}
        new_id = self.execute("repair.order", "create", [clean])
        if isinstance(new_id, list):
            new_id = new_id[0]
        name = None
        try:
            name = self.execute("repair.order", "read", [[new_id]], {"fields": ["name"]})[0]["name"]
        except Exception:
            pass
        return {"dry_run": False, "payload": clean, "warnings": warnings, "odoo_would_fill": would_fill,
                "id": new_id, "name": name}


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

LOT_READ_FIELDS = ["name", "product_id", "ref", "barcode", "company_id", "partner_id", "create_date"]
REPAIR_READ_FIELDS = ["name", "partner_id", "product_id", "state", "schedule_date", "create_date",
                      "user_id", "under_warranty"]
CLOSED_REPAIR_STATES = ("done", "cancel")


def lookup(index, odoo, raw_ai, lot_ai_fields=("ref", "barcode")):
    ai = parse_ai(raw_ai)
    if ai is None:
        return {"ok": False, "error": "Not a valid AI number: %r" % raw_ai}
    sheet = index.lookup(ai) if index else {"rows": [], "serial": None, "serials": [], "warnings": []}
    result = {"ok": True, "ai": ai, "sheet": sheet, "lots": [], "match": None,
              "warnings": list(sheet["warnings"]), "steps": []}
    if not sheet["rows"]:
        result["warnings"].append("AI %d is not in the spreadsheet." % ai)

    lot_meta = odoo.fields("stock.lot")
    read_fields = [f for f in LOT_READ_FIELDS + list(lot_ai_fields) if f in lot_meta]
    lots = []

    # 1. AI stored on the lot itself (e.g. ref 'AI01948' or 'AI00115 , AI00116')
    ai_fields = [f for f in lot_ai_fields if f in lot_meta and lot_meta[f].get("type") == "char"]
    found_by = {}  # lot id -> (lot, [reasons])

    def add(lot, reason):
        found_by.setdefault(lot["id"], (lot, []))[1].append(reason)

    ref_needles = ["%05d" % ai, "AI%d" % ai, "AI %d" % ai]
    for f in ai_fields + ["name"]:
        plain = f != "name"  # a bare number in a lot name is a serial, not an AI
        needles = ref_needles
        domain = (["|"] * len(needles) + [(f, "=", str(ai))] if plain else ["|"] * (len(needles) - 1)) + [
            (f, "ilike", n) for n in needles]
        cands = odoo.search_read("stock.lot", domain, read_fields, limit=200)
        hits = [l for l in cands if ai_in_ref(ai, l.get(f), plain)]
        result["steps"].append("stock.lot.%s = %r or ilike any of %s -> %d candidates, %d with AI %d" % (
            f, str(ai), needles, len(cands), len(hits), ai))
        for lot in hits:
            add(lot, "AI on lot (%s: %s)" % (f, lot.get(f)))
    if not ai_fields:
        result["steps"].append("stock.lot has no %s field(s); skipped AI-on-lot search" % "/".join(lot_ai_fields))

    # 2. manufacturer serial: exact variants + candidates sharing the longest digit run, then scored
    for serial in sheet["serials"]:
        variants = serial_variants(serial)
        cands = {l["id"]: l for l in odoo.search_read("stock.lot", [("name", "in", variants)], read_fields,
                                                      limit=20)}
        needles = search_needles(serial)
        if needles:
            domain = ["|"] * (len(needles) - 1) + [("name", "ilike", n) for n in needles]
            for l in odoo.search_read("stock.lot", domain, read_fields, limit=50):
                cands.setdefault(l["id"], l)
        result["steps"].append("serial %r: name in %s or ilike any of %s -> %d candidates" % (
            serial, variants, needles, len(cands)))
        for lot in cands.values():
            quality = serial_match(serial, lot["name"])
            if quality:
                add(lot, SERIAL_MATCH_LABELS[quality] % serial)

    def rank(item):
        lot, reasons = item
        text = " ".join(reasons)
        return (0 if text.startswith("AI on lot") else 1,
                0 if "same serial" in text else 1 if "listed" in text else 2, lot["id"])

    ranked = sorted(found_by.values(), key=rank)
    lots = [lot for lot, _ in ranked]
    reasons = {lot["id"]: r for lot, r in ranked}
    if ranked:
        result["match"] = "; ".join(ranked[0][1])
        by_ai = [l for l, r in ranked if any(x.startswith("AI on lot") for x in r)]
        by_serial = [l for l, r in ranked if any("same serial" in x or "listed" in x for x in r)]
        if by_ai and by_serial and not set(l["id"] for l in by_ai) & set(l["id"] for l in by_serial):
            result["warnings"].append("The lot tagged with AI %d (%s) is not the lot matching the spreadsheet "
                                      "serial (%s). Check which one is right." % (
                                          ai, by_ai[0]["name"], by_serial[0]["name"]))
        for lot, r in ranked:
            others = [a for a in lot_ais(lot) if a != ai]
            if others and ai not in lot_ais(lot):
                result["warnings"].append(
                    "Lot %r matches the spreadsheet serial but Odoo tags it with AI %s, not AI %d: "
                    "one of them has a typo." % (lot["name"], ", ".join(map(str, others)), ai))
        if all(all(x.startswith("possible") for x in r) for _, r in ranked):
            result["warnings"].append("Only partial serial matches: verify the lot really is this unit.")

    if not lots:
        result["warnings"].append("No lot/serial found in Odoo for AI %d." % ai)
        return result

    # products (default_code, uom) in one read
    product_ids = sorted({l["product_id"][0] for l in lots if l.get("product_id")})
    products = {}
    if product_ids:
        for p in odoo.search_read("product.product", [("id", "in", product_ids)],
                                  ["display_name", "default_code", "uom_id"]):
            products[p["id"]] = p

    repair_meta = odoo.fields("repair.order")
    fmap = odoo.repair_field_map()
    for lot in lots:
        entry = {"lot": lot, "match": reasons.get(lot["id"], []),
                 "product": products.get(lot["product_id"][0]) if lot.get("product_id") else None,
                 "customer": None, "customer_source": None, "repairs": [], "open_repairs": []}
        if fmap["lot"]:
            order = "create_date desc, id desc" if "create_date" in repair_meta else "id desc"
            reps = odoo.search_read("repair.order", [(fmap["lot"], "=", lot["id"])], REPAIR_READ_FIELDS,
                                    limit=20, order=order)
            entry["repairs"] = reps
            entry["open_repairs"] = [r["name"] for r in reps if r.get("state") not in CLOSED_REPAIR_STATES]
        if lot.get("partner_id"):
            entry["customer"] = {"id": lot["partner_id"][0], "name": lot["partner_id"][1]}
            entry["customer_source"] = "Odoo: lot's customer field"
        else:
            for r in entry["repairs"]:
                if r.get("partner_id"):
                    entry["customer"] = {"id": r["partner_id"][0], "name": r["partner_id"][1]}
                    entry["customer_source"] = "Odoo: most recent repair %s" % r["name"]
                    break
        if len(lot_pieces(lot["name"])) > 1:
            entry["note"] = "This lot holds several serials (kit or grouped units)."
        result["lots"].append(entry)
    # within the same match quality, lots that already had repairs come first
    rank_of = {lot["id"]: rank((lot, reasons[lot["id"]]))[:2] for lot in lots}
    result["lots"].sort(key=lambda e: (rank_of[e["lot"]["id"]], -len(e["repairs"]), e["lot"]["id"]))
    if len(lots) > 1:
        result["warnings"].append("%d lots match; pick the right one." % len(lots))
    return result


def search_partners(odoo, query, limit=20):
    q = (query or "").strip()
    if len(q) < 2:
        return []
    domain = ["|", "|", ("name", "ilike", q), ("parent_id.name", "ilike", q), ("vat", "ilike", q)]
    return odoo.search_read("res.partner", domain,
                            ["display_name", "name", "parent_id", "is_company", "vat", "city"],
                            limit=limit, order="is_company desc, display_name")


def local_to_utc(value):
    """'2026-09-23T10:30' (browser local time) -> '2026-09-23 13:30:00' UTC."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.astimezone()  # system local time zone
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def build_repair_vals(odoo, form, notes_prefix=""):
    """Turn the UI form into repair.order vals using this install's field names.

    form: {partner_id, product_id, lot_id, user_id, tag_ids, schedule_date,
           under_warranty, notes, company_id}
    """
    meta = odoo.fields("repair.order")
    fmap = odoo.repair_field_map()
    vals = {}

    def put(logical, value):
        name = fmap.get(logical)
        if name and value not in (None, "", []):
            vals[name] = value

    for logical in ("partner", "product", "lot"):
        if not fmap.get(logical):
            raise GuardError("repair.order has no %s field in this Odoo." % logical)
    put("partner", form.get("partner_id"))
    put("product", form.get("product_id"))
    put("lot", form.get("lot_id"))
    for logical in ("partner", "product", "lot"):
        if fmap[logical] not in vals:
            raise GuardError("Missing %s." % logical)
    put("quantity", 1.0)
    put("user", form.get("user_id"))
    put("company", form.get("company_id"))
    if form.get("uom_id") and fmap.get("uom") and not meta[fmap["uom"]].get("readonly"):
        put("uom", form["uom_id"])
    if form.get("tag_ids"):
        put("tags", [[6, 0, list(form["tag_ids"])]])
    if not form.get("schedule_date") and fmap.get("schedule_date") and meta[fmap["schedule_date"]].get("required"):
        form = dict(form, schedule_date=datetime.now().strftime("%Y-%m-%dT%H:%M"))
    if form.get("schedule_date") and fmap.get("schedule_date"):
        if meta[fmap["schedule_date"]]["type"] == "date":
            put("schedule_date", form["schedule_date"][:10])
        else:
            put("schedule_date", local_to_utc(form["schedule_date"]))
    if fmap.get("under_warranty") and meta[fmap["under_warranty"]]["type"] == "boolean":
        vals[fmap["under_warranty"]] = bool(form.get("under_warranty"))
    notes = "\n".join(x for x in (notes_prefix, (form.get("notes") or "").strip()) if x)
    if notes and fmap.get("notes"):
        if meta[fmap["notes"]]["type"] == "html":
            notes = "".join("<p>%s</p>" % html.escape(line) for line in notes.splitlines() if line.strip())
        put("notes", notes)
    return vals
