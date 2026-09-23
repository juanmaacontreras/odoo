"""Command-line checks against the real Odoo (read-only; never creates anything).

  python probe.py                 -> login + field report for repair.order / stock.lot
  python probe.py 1948 1953 609   -> also dry-run lookups for those AI numbers
  python probe.py --survey        -> how many spreadsheet serials exist as lots in Odoo,
                                     and how the repair orders of the last 3 years map to AIs
  python probe.py --survey --since 2024-01-01   -> same, with another start date

Uses config.json (or the file given with --config PATH). The API key is never printed.
"""

import json
import re
import sys
from collections import Counter
from datetime import date, timedelta

import core
import server

KEY_FIELDS = {
    "repair.order": ["partner_id", "product_id", "lot_id", "product_qty", "quantity", "product_uom",
                     "schedule_date", "user_id", "tag_ids", "under_warranty", "internal_notes",
                     "description", "company_id", "picking_type_id", "location_id", "state", "name"],
    "stock.lot": ["name", "ref", "barcode", "product_id", "partner_id", "company_id", "note"],
}


def strip_html(text):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def survey(app):
    odoo = app.odoo
    n_lots = odoo.execute("stock.lot", "search_count", [[]])
    n_rep = odoo.execute("repair.order", "search_count", [[]])
    n_rep_lot = odoo.execute("repair.order", "search_count", [[("lot_id", "!=", False)]])
    print("\n== Survey (read-only) ==")
    print("stock.lot records: %d" % n_lots)
    print("repair.order records: %d, with lot: %d, without lot: %d" % (n_rep, n_rep_lot, n_rep - n_rep_lot))

    lots = odoo.search_read("stock.lot", [], ["name", "ref", "product_id"])
    counts = {"ai_on_lot": 0, "exact": 0, "listed": 0, "partial": 0, "none": 0}
    examples = {k: [] for k in counts}
    for ai, rows in sorted(app.index.by_ai.items()):
        serial = next((r["serial"] for r in rows if not core.is_junk_serial(r["serial"])), None)
        by_ref = [l for l in lots if ai in core.lot_ais(l)]
        best, best_lot = None, None
        if serial:
            for l in lots:
                q = core.serial_match(serial, l["name"])
                if q and (best is None or ["exact", "listed", "partial"].index(q) < ["exact", "listed", "partial"].index(best)):
                    best, best_lot = q, l
        if by_ref:
            key, lot = "ai_on_lot", by_ref[0]
        elif best:
            key, lot = best, best_lot
        elif serial:
            key, lot = "none", None
        else:
            continue
        counts[key] += 1
        if len(examples[key]) < 6:
            examples[key].append((ai, serial, lot))
    print("spreadsheet AIs resolved to a lot: AI on lot ref: %(ai_on_lot)d, same serial: %(exact)d, "
          "serial listed in a multi-serial lot: %(listed)d, only partial (verify): %(partial)d, "
          "not in Odoo: %(none)d" % counts)
    for key, rows in examples.items():
        for ai, serial, lot in rows:
            print("  [%-9s] AI %-5s serial %-24r -> %s" % (key, ai, serial, (
                "lot %r ref %r (%s)" % (lot["name"], lot.get("ref"), lot["product_id"][1] if lot["product_id"] else "")
            ) if lot else "-"))
    refs = [l for l in lots if l.get("ref")]
    print("lots with 'Internal Reference' (ref) set: %d%s" % (
        len(refs), "  e.g. " + ", ".join(repr(l["ref"]) for l in refs[:8]) if refs else ""))

    print("\n15 most recent repair orders:")
    reps = odoo.search_read("repair.order", [], ["name", "create_date", "product_id", "lot_id", "partner_id",
                                                 "internal_notes", "state"], limit=15, order="id desc")
    for r in reps:
        print("  %-10s %s  %-9s product=%r lot=%r customer=%r notes=%r" % (
            r["name"], (r.get("create_date") or "")[:10], r.get("state"),
            r["product_id"][1] if r.get("product_id") else None,
            r["lot_id"][1] if r.get("lot_id") else None,
            r["partner_id"][1] if r.get("partner_id") else None,
            strip_html(r.get("internal_notes"))[:80]))


def sheet_checks(app):
    idx = app.index
    print("\n== Spreadsheet checks ==")
    multi = {ai: rows for ai, rows in idx.by_ai.items() if len(rows) > 1}
    print("AIs in more than one row: %d" % len(multi))
    for ai, rows in sorted(multi.items())[:15]:
        print("  AI %-5s rows %s  raw column A: %s" % (
            ai, [r["row"] for r in rows], [(r["ai_raw"], r["ai_raw_type"]) for r in rows]))
    print("rows whose column A is not an AI number: %d" % len(idx.unparsed))
    for r in idx.unparsed[:15]:
        print("  row %-5s raw %r (%s) serial %r" % (r["row"], r["ai_raw"], r["ai_raw_type"], r["serial"]))
    odd = [r for rs in idx.by_ai.values() for r in rs
           if r["ai_raw_type"] != "float" and not re.fullmatch(r"\s*\d+\s*", str(r["ai_raw"]))]
    print("rows with an AI written as text in an unusual way: %d" % len(odd))
    for r in odd[:15]:
        print("  row %-5s raw %r -> read as AI %s" % (r["row"], r["ai_raw"], r["ai"]))


def recent_survey(app, since):
    odoo, idx = app.odoo, app.index
    reps = odoo.search_read("repair.order", [("create_date", ">=", since)],
                            ["name", "create_date", "product_id", "lot_id", "partner_id"], order="id")
    print("\n== Repair orders created since %s: %d ==" % (since, len(reps)))
    if not reps:
        return
    lot_ids = sorted({r["lot_id"][0] for r in reps if r.get("lot_id")})
    lots = {l["id"]: l for l in odoo.search_read("stock.lot", [("id", "in", lot_ids)], ["name", "ref"])}
    # spreadsheet serial core -> AIs
    by_core = {}
    for ai, rows in idx.by_ai.items():
        for r in rows:
            if not core.is_junk_serial(r["serial"]):
                by_core.setdefault(core.serial_core(r["serial"]), set()).add(ai)

    kinds, unlinked_products, examples = Counter(), Counter(), {}
    ref_vs_serial = []
    for r in reps:
        lot = lots.get(r["lot_id"][0]) if r.get("lot_id") else None
        if not lot:
            kind = "no lot"
        else:
            ref_list = [a for a in core.lot_ais(lot) if a in idx.by_ai]
            serial_ais = set()
            for piece in core.lot_pieces(lot["name"]):
                serial_ais |= by_core.get(core.serial_core(piece), set())
            if ref_list and serial_ais and not set(ref_list) & serial_ais:
                ref_vs_serial.append((r["name"], lot["name"], lot.get("ref"), sorted(serial_ais)))
            if ref_list:
                kind = "AI on lot ref"
            elif serial_ais:
                kind = "lot serial found in spreadsheet"
            elif core.lot_ais(lot):
                kind = "lot ref names an AI missing from spreadsheet"
            else:
                kind = "lot not linked to any AI"
                unlinked_products[r["product_id"][1] if r.get("product_id") else "?"] += 1
        kinds[kind] += 1
        examples.setdefault(kind, []).append(r)
    no_customer = sum(1 for r in reps if not r.get("partner_id"))
    for kind, n in kinds.most_common():
        print("  %-45s %5d  (%.0f%%)" % (kind, n, 100.0 * n / len(reps)))
    print("  repair orders without customer: %d" % no_customer)
    linked = kinds["AI on lot ref"] + kinds["lot serial found in spreadsheet"]
    print("=> the tool would find the unit from its AI for %d of %d recent repairs (%.0f%%)."
          % (linked, len(reps), 100.0 * linked / len(reps)))
    print("\nProducts of recent repairs whose lot is not linked to any AI (top 15):")
    for prod, n in unlinked_products.most_common(15):
        print("  %4d  %s" % (n, prod))
    for kind in ("lot not linked to any AI", "lot ref names an AI missing from spreadsheet", "no lot"):
        for r in examples.get(kind, [])[-5:]:
            lot = lots.get(r["lot_id"][0]) if r.get("lot_id") else None
            print("  [%s] %s %s lot=%r ref=%r product=%r" % (
                kind, r["name"], r["create_date"][:10], lot["name"] if lot else None,
                lot.get("ref") if lot else None, r["product_id"][1] if r.get("product_id") else None))
    if ref_vs_serial:
        print("\nLots where the AI in 'ref' disagrees with the AI the spreadsheet gives for its serial: %d"
              % len(ref_vs_serial))
        for name, lot_name, ref, ais in ref_vs_serial[:10]:
            print("  %s lot %r ref %r, spreadsheet serial belongs to AI %s" % (name, lot_name, ref, ais))


def main(argv):
    cfg_path = server.DEFAULT_CONFIG
    if "--config" in argv:
        i = argv.index("--config")
        cfg_path = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    since = (date.today() - timedelta(days=3 * 365)).isoformat()
    if "--since" in argv:
        i = argv.index("--since")
        since = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    do_survey = "--survey" in argv
    argv = [a for a in argv if a != "--survey"]
    cfg = server.load_config(cfg_path)
    print("Odoo: %s  db: %s  user: %s  api key: %s  mode: %s" % (
        cfg["odoo_url"], cfg["database"], cfg["username"] or "(missing)",
        "set" if cfg["api_key"] else "MISSING", "LIVE" if cfg["live_mode"] else "dry-run"))
    app = server.App(cfg)
    print("Spreadsheet:", app.index_error or app.index.stats())

    uid = app.odoo.login()
    print("Login OK: uid=%s, server %s" % (uid, app.odoo.server_version))

    for model, wanted in KEY_FIELDS.items():
        meta = app.odoo.fields(model)
        print("\n== %s: %d fields ==" % (model, len(meta)))
        for f in wanted:
            m = meta.get(f)
            print("  %-18s %s" % (f, "-- not present --" if not m else "%s%s%s  %r" % (
                m["type"], " -> " + m["relation"] if m.get("relation") else "",
                " REQUIRED" if m.get("required") else "", m.get("string"))))
        req = sorted(k for k, v in meta.items() if v.get("required"))
        print("  required fields:", ", ".join(req))
    print("\nField map used for repair.order:", json.dumps(app.odoo.repair_field_map(), indent=2))

    if do_survey:
        sheet_checks(app)
        survey(app)
        recent_survey(app, since)

    for raw in argv:
        print("\n" + "=" * 70 + "\nAI %s" % raw)
        r = core.lookup(app.index, app.odoo, raw, cfg["lot_ai_fields"])
        if not r["ok"]:
            print("  ", r["error"])
            continue
        for row in r["sheet"]["rows"]:
            print("  spreadsheet row %s (column A raw %r): model=%r serial=%r customer=%r" % (
                row["row"], row.get("ai_raw"), row["model"], row["serial"], row["customer"]))
        for w in r["warnings"]:
            print("  WARNING:", w)
        for s in r["steps"]:
            print("  step:", s)
        print("  match:", r["match"])
        for e in r["lots"]:
            lot = e["lot"]
            print("  lot id=%s name=%r ref=%r match=%s product=%r customer=%r (%s) repairs=%s open=%s" % (
                lot["id"], lot["name"], lot.get("ref"), e["match"], e["product"]["display_name"] if e["product"] else lot.get("product_id"),
                e["customer"]["name"] if e["customer"] else None, e["customer_source"],
                [x["name"] for x in e["repairs"]], e["open_repairs"]))
        if len(r["lots"]) == 1 and r["lots"][0]["customer"]:
            e = r["lots"][0]
            try:
                res = app.do_create({"ai": str(r["ai"]), "lot_id": e["lot"]["id"],
                                     "partner_id": e["customer"]["id"], "preview": True})
                print("  DRY-RUN payload for repair.order.create:")
                print("   ", json.dumps(res["payload"], ensure_ascii=False, indent=2).replace("\n", "\n    "))
                for w in res["warnings"]:
                    print("  payload warning:", w)
                print("  Odoo would fill in:", json.dumps(res.get("odoo_would_fill"), ensure_ascii=False))
            except core.GuardError as ex:
                print("  guard refused:", ex)
        elif r["lots"]:
            print("  (no dry-run payload: pick lot / customer in the UI)")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (core.OdooError, core.GuardError) as e:
        sys.exit("ERROR: %s" % e)
