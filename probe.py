"""Command-line checks against the real Odoo (read-only; never creates anything).

  python probe.py                 -> login + field report for repair.order / stock.lot
  python probe.py 1948 1953 609   -> also dry-run lookups for those AI numbers
  python probe.py --survey        -> how many spreadsheet serials exist as lots in Odoo,
                                     and how recent repair orders were filled in

Uses config.json (or the file given with --config PATH). The API key is never printed.
"""

import json
import re
import sys

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
        by_ref = [l for l in lots if core.ai_in_ref(ai, l.get("ref"))]
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


def main(argv):
    cfg_path = server.DEFAULT_CONFIG
    if "--config" in argv:
        i = argv.index("--config")
        cfg_path = argv[i + 1]
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
        survey(app)

    for raw in argv:
        print("\n" + "=" * 70 + "\nAI %s" % raw)
        r = core.lookup(app.index, app.odoo, raw, cfg["lot_ai_fields"])
        if not r["ok"]:
            print("  ", r["error"])
            continue
        for row in r["sheet"]["rows"]:
            print("  spreadsheet row %s: model=%r serial=%r customer=%r" % (
                row["row"], row["model"], row["serial"], row["customer"]))
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
            except core.GuardError as ex:
                print("  guard refused:", ex)
        elif r["lots"]:
            print("  (no dry-run payload: pick lot / customer in the UI)")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (core.OdooError, core.GuardError) as e:
        sys.exit("ERROR: %s" % e)
