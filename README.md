# Equipment intake: ALFA AI number → Odoo repair order

Local tool for Alfa Instrumentos. Type or scan an ALFA AI number (e.g. `1948`),
check the unit, manufacturer serial and customer pulled from Odoo, fix what's
wrong on screen, and create the repair order (`repair.order`, `RMA/xxxxx`) in Odoo.

It is a small Python server on `127.0.0.1` that serves an HTML page and talks to
Odoo 17 over external JSON-RPC (`/jsonrpc`) with your user and API key. The only
dependency is `xlrd` (reads the legacy `equipos.xls`).

## Safety rules (enforced in code)

* **Dry-run by default.** Every lookup is real, but the final `create` is withheld
  and the exact payload is shown. Live mode only when `"live_mode": true` in
  `config.json`. The page then shows a red banner, and you must type the AI number
  in a confirmation dialog.
* **Never creates customers, products or lots.** Every relational value must be an
  existing integer id. `OdooClient` refuses any `create/write/unlink` on
  `res.partner`, `product.*` and `stock.lot`, and rejects x2many commands other
  than `(6, 0, ids)`. Each id is checked against Odoo before sending. Archived
  records count as missing.
* **Only existing fields are sent.** Field names come from `fields_get` on this
  install (see `REPAIR_FIELD_CANDIDATES` in `core.py`). Unknown fields are
  rejected, and read-only fields are dropped with a warning.
* Live-mode extras: the create is refused if the lot already has an open repair
  (unless you tick "create anyway"). It is also refused if the same lot got a
  repair in the last 2 minutes, which stops double clicks.
* The product always comes from the lot in Odoo, never from the browser.
* The API key is never printed, logged or returned by the API. `config.json`,
  `intake_log.jsonl` and spreadsheets are git-ignored.
* The server only binds to 127.0.0.1. It checks the `Host` header and requires a
  per-run token on every API call, so other web pages can't drive it.

## Setup (Windows)

1. Python 3.9+ (`py -3 --version`).
2. Copy `config.example.json` → `config.json` and set `username`. Then paste
   your API key into `api_key`. Create the key in Odoo under
   *Preferences → Account Security → New API Key*.
3. Put `equipos.xls` next to the scripts, or set `spreadsheet` to its full path.
4. Double-click `run.bat`. It installs `xlrd` for your user if it's missing, then
   opens http://127.0.0.1:8765/.

## Command-line checks (read-only)

```
py -3 probe.py                      # login + fields_get report for repair.order / stock.lot
py -3 probe.py 1948 1953 609 447    # plus dry-run lookups and payloads for those AIs
py -3 selftest.py                   # offline tests against mock_odoo.py
```

## Lookup logic

1. AI → spreadsheet row. `1948`, `AI1948` and `01948` are all accepted. The
   spreadsheet is only used to get the manufacturer serial (column E). Its
   customer column is shown for reference only.
2. Look for the AI stored on the lot (`stock.lot` fields in `lot_ai_fields`,
   default `ref` / `barcode`, only if they exist).
3. Look for the exact serial in these forms: as written, without spaces, and
   without dots, dashes and slashes.
4. Search `ilike` on the longest run of 6 or more digits.
5. If several lots match, you pick one. If none match, the page says so. Lots
   are never created.
6. Customer: taken from the lot's `partner_id` if that field exists and is set.
   Otherwise it's the customer of the most recent repair order for the lot.
   Previous repairs are listed.

Warnings: the serial is shared with another AI, the serial is junk (`----`,
`NO TIENE`…), an H5K5 serial doesn't end in the AI, the AI appears more than
once, or the lot has an open repair.

## Files

| File | What |
|---|---|
| `core.py` | Spreadsheet index, serial normalisation, Odoo client + create guard, lookup |
| `server.py` | Stdlib HTTP server: `/api/status`, `/api/lookup`, `/api/partners`, `/api/create`, `/api/diagnose`, `/api/reload` |
| `ui.py` | The HTML page |
| `probe.py` | Read-only CLI checks against the real Odoo |
| `mock_odoo.py`, `selftest.py` | Offline mock of Odoo 17 and the test suite |
| `run.bat` | Windows launcher |
