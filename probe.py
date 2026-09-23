"""Command-line checks against the real Odoo (read-only; never creates anything).

  python probe.py                 -> login + field report for repair.order / stock.lot
  python probe.py 1948 1953 609   -> also dry-run lookups for those AI numbers

Uses config.json (or the file given with --config PATH). The API key is never printed.
"""

import json
import sys

import core
import server

KEY_FIELDS = {
    "repair.order": ["partner_id", "product_id", "lot_id", "product_qty", "quantity", "product_uom",
                     "schedule_date", "user_id", "tag_ids", "under_warranty", "internal_notes",
                     "description", "company_id", "picking_type_id", "location_id", "state", "name"],
    "stock.lot": ["name", "ref", "barcode", "product_id", "partner_id", "company_id", "note"],
}


def main(argv):
    cfg_path = server.DEFAULT_CONFIG
    if "--config" in argv:
        i = argv.index("--config")
        cfg_path = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
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
            print("  lot id=%s name=%r product=%r customer=%r (%s) repairs=%s open=%s" % (
                lot["id"], lot["name"], e["product"]["display_name"] if e["product"] else lot.get("product_id"),
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
