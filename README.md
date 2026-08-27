# boletas_energiamineral

Internal tool for a mining company that manages **boletas** (carbón trip
tickets) end to end: generate pre-numbered, QR-coded boleta batches for the
print vendor, scan the completed boletas back in at the delivery point (OCR
+ QR), classify each trip and calculate fletero payment, update inventory,
and surface only ambiguous cases for manual review. The whole app is
behind a single admin login (Supabase Auth).

Built rules-first: every decision (trip classification, tariff, inventory
direction, estimated weight, when to flag for review) is driven by editable
CSV config tables in `app/rules/`, not hardcoded logic and not ML — so an
admin can see and change *why* the system decided what it decided.

## How it works

```
Point A (loading, 100% paper):
  Admin generates a folio batch -> print-ready PDF w/ QR codes -> vendor prints

Point B (delivery):
  Scan/photo of the filled-in boleta -> QR decode (folio) + OCR (everything
  else) -> field parsing -> folio registry check -> classification -> tariff
  lookup -> inventory movement -> exception scoring -> review queue (human
  corrects/approves) -> CSV/JSON export / fletero ledger
```

See `app/pipeline/orchestrator.py` for the single place the Point-B side is
wired together, and `app/qr/batch_pdf.py` for the boleta template design.

## Setup

```bash
# System dependencies (OCR engine, PDF rasterizer, QR decoder)
sudo apt-get install -y tesseract-ocr tesseract-ocr-spa poppler-utils libzbar0

# Python dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Create the DB schema and load the sample rule config
python scripts/init_db.py
```

## Auth

Every route (including the API) requires the single admin login, except
`/login`, `/logout`, `/api/health`, and the Twilio WhatsApp webhook
(`POST /webhooks/twilio/whatsapp`, authenticated by `X-Twilio-Signature`).
There is no signup route — create the one admin account against Supabase Auth:

```bash
python scripts/create_admin_user.py --email admin@example.com --password 'change-me'
```

This needs `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` set (see
`.env.example`). Local dev without Supabase configured yet will show a
clear "not configured" message on the login page instead of crashing.

## Configure your real routes, tariffs, and weights

The CSVs under `app/rules/` ship with **placeholder sample data** so the
app runs out of the box. Before using this for real payments/inventory,
replace them with the client's actual data:

- `app/rules/route_config.csv` — which origin→destination pairs mean what
  trip type, and whether that trip increases (`inbound`), decreases
  (`outbound`), or doesn't affect (`none`) inventory
- `app/rules/tariff_config.csv` — what each trip type/distance band pays
- `app/rules/weight_estimation_config.csv` — estimated weight to use when a
  boleta's weight field is missing
- `app/rules/exception_thresholds.csv` — confidence thresholds and which
  conditions always force manual review (includes `unknown_folio`,
  `folio_already_used`, and `volumen_mismatch_pct` for the folio/QR flow)

After editing a CSV, reload it without restarting: `python
scripts/load_config.py` (or `POST /api/config/reload`).

## Generating a folio batch for the print vendor

1. Log in, go to **Lotes de Folios** → create a batch:
   - **Secuencial**: prefix + starting number + count (e.g. `B-` / `3201` / `200`)
   - **Lista pegada**: paste an explicit folio list (e.g. to keep using an
     existing numbering convention) — one per line
2. Download the print-ready PDF (`app/qr/batch_pdf.py` — one boleta page
   per folio, QR + folio + the same field labels the OCR parser looks for)
   and send it to the vendor, or download the CSV if they have their own
   QR rendering pipeline.
3. When a completed boleta is scanned back in, its QR resolves the folio
   with full confidence and gets checked against this registry — a folio
   nobody issued (`unknown_folio`) or already scanned once
   (`folio_already_used`) is flagged for review instead of silently
   accepted.

## Run it

```bash
uvicorn app.main:app --reload
```

Open http://localhost:8000 — create a batch, upload boleta scans, and
correct/approve anything that lands in the review queue at
http://localhost:8000/review. Export results as CSV/JSON from the batch
page or `GET /api/exports/csv?batch_id=...` / `/api/exports/json`.

## Upload boletas from WhatsApp

Point a Twilio WhatsApp sender (sandbox or production) at this app so the
crew can photograph boletas on their phone and dump them into a scanning
batch without opening the web UI.

1. Set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `PUBLIC_BASE_URL`
   (the public `https://…` origin, no trailing slash — required so Twilio
   signature checks work behind Railway/any proxy). Optional:
   `TWILIO_WHATSAPP_FROM` (display only) and `WHATSAPP_ALLOWED_SENDERS`
   (comma-separated E.164 numbers; empty means any sender can upload).
2. In Twilio Console → Messaging → WhatsApp sandbox (or the production
   sender) set **When a message comes in** to
   `https://<PUBLIC_BASE_URL>/webhooks/twilio/whatsapp` with **HTTP POST**.
3. Join the sandbox if that's what you're using, then send photos. The
   first photo opens a lote de escaneo named `WhatsApp …`; later photos
   from the same number keep going into that lote until they text `fin`.
4. Operators can text `ayuda` for the command list (`lote nuevo`,
   `tipo entrada`, `productor …`, `cfe`, …). The webhook only downloads
   and stores the file; OCR runs after Twilio gets its TwiML reply so
   we stay inside the 15-second webhook timeout.

The **WhatsApp** page in the admin menu shows whether Twilio is
configured, the webhook URL, the allowlist, and recent conversations.

## Try it with the bundled sample boletas

No real scans yet? Generate 3 synthetic sample boletas and run the full
pipeline over them from the command line:

```bash
python scripts/generate_sample_fixtures.py
python scripts/run_pipeline_on_folder.py \
  --input tests/fixtures/boletas --output /tmp/boletas_run
cat /tmp/boletas_run/results.csv
```

Two are auto-processed straight through; the third (deliberately garbled,
simulating a poor/illegible scan) is flagged `needs_review` with specific
exception codes explaining why. (These fixtures pre-date the folio
registry and don't carry a QR, so they're pre-seeded folios in the test
suite — see `tests/test_pipeline_qr_integration.py` for the QR-carrying
version of this same flow.)

## Tests

```bash
pytest -v
```

Covers the field parser, each rule engine (classification/tariff/
inventory/exception-scoring/folio-registry), QR generate↔decode round-trip,
folio batch generation (both modes), auth (unauthenticated requests
blocked, credentials verified against a mocked Supabase), the full pipeline
end-to-end (deterministic fake-OCR + a real-Tesseract smoke test, both with
and without an embedded QR), and the upload→review→export flow through the
actual HTTP API.

## Project layout

```
app/
  auth/        Supabase Auth credential check + session (login/logout, require_admin_*)
  ingestion/   upload storage, PDF page-splitting
  ocr/         OCRAdapter interface; Tesseract impl; LLM/cloud-OCR stub; QR decoder
  qr/          QR generation + the print-ready boleta-batch PDF template
  parsing/     regex/keyword field extraction, normalization
  rules/       editable CSV config + loader (source of truth for all rules)
  engines/     classification, tariff, inventory, folio registry, exception/confidence scoring
  pipeline/    orchestrator.py — wires every stage together per boleta
  review/      human correction/approval service + audit trail
  exports/     CSV/JSON export
  reporting/   batch summary aggregations
  whatsapp/    Twilio WhatsApp inbound webhook (signature + media ingest)
  api/         JSON REST API (FastAPI)
  web/         server-rendered UI (Jinja2, no JS framework/CDN)
scripts/       init_db, load_config, create_admin_user, sample-fixture generator, CLI pipeline runner
tests/         unit tests per module + end-to-end pipeline/API tests
```

## Known v1 limitations

- OCR runs locally via Tesseract (offline, no API key needed). If accuracy
  on handwritten notes proves insufficient, implement
  `app/ocr/llm_fallback_adapter.py` against a cloud OCR/LLM API — it's a
  drop-in swap behind the same `OCRAdapter` interface used everywhere else.
- Single admin role — no separate permission levels. Auth just gates the
  whole app; there's no per-user audit trail beyond the free-text
  `edited_by` field already captured on corrections.
- SQLite works for local dev with zero setup; production should point
  `DATABASE_URL` at Supabase Postgres (see `.env.example`) — the schema is
  already Postgres-compatible, no code changes needed.
