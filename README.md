# boletas_energiamineral

Internal tool for a mining company that manages **boletas** (carbón trip
tickets) end to end: generate pre-numbered, QR-coded boleta batches for the
print vendor, scan the completed boletas back in at the delivery point (OCR
+ QR), classify each trip and calculate fletero payment, update inventory,
and surface only ambiguous cases for manual review. The whole app is
behind a single admin login (Clerk).

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

## Auth (Clerk)

All routes (including the API) require a Clerk session, except:

- `/api/health` (public)
- `/login` and `/logout` (UI entry/exit)

Setup:

1) In Clerk Dashboard, create an application and copy:

- Publishable key → `CLERK_PUBLISHABLE_KEY`
- Secret key → `CLERK_SECRET_KEY`
- (Optional) JWT public key (PEM) → `CLERK_JWT_KEY` for networkless verification
- (Optional) Allowed origins → `CLERK_AUTHORIZED_PARTIES` (comma-separated)

2) Export keys in your environment (never commit real secrets):

```bash
export CLERK_PUBLISHABLE_KEY=pk_test_...
export CLERK_SECRET_KEY=sk_test_...
# optional:
# export CLERK_JWT_KEY='-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----\n'
# export CLERK_AUTHORIZED_PARTIES='http://localhost:8000,https://boletas.example.com'
```

3) Run the server (SQLite strongly recommended locally; see AGENTS.md for the override):

```bash
DATABASE_URL="sqlite:///./data/boletas.db" \
.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

If Clerk is not configured in a production-like environment, protected routes
fail closed (401/redirect) and `/login` shows a clear “Clerk no está configurado”
message. In tests, endpoints continue to use FastAPI dependency overrides.

In production you MUST set a strong `SESSION_SECRET_KEY` environment variable
(e.g. `openssl rand -hex 32`). The app will refuse to start with the insecure
placeholder when running in a production-like environment. Optionally set
`ADMIN_EMAILS` (comma‑separated) to restrict admin access to a known allowlist.

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

## OCR backends and OpenAI (handwriting)

This app supports two OCR engines:

- Tesseract (offline): best for printed text, no API key required
- OpenAI vision (online): best on handwriting/phone photos

Backend selection is controlled by `OCR_BACKEND`:

- `tesseract`: use only local Tesseract
- `openai`: use only OpenAI vision (primary)
- `auto` (default): Tesseract first; if confidence is low OR key fields look missing,
  escalate to OpenAI when an API key is set. Fallback is safe; no key means it behaves like plain Tesseract.

Environment variables (see `.env.example`):

```bash
# Enable OpenAI OCR
export OPENAI_API_KEY="sk-..."        # required for OCR_BACKEND=openai|auto
export OPENAI_OCR_MODEL="gpt-4o-mini" # any vision-capable chat model
export OCR_BACKEND="auto"             # or "openai" to force the OpenAI path
```

Local run examples:

```bash
# SQLite override strongly recommended locally (see AGENTS.md)
DATABASE_URL="sqlite:///./data/boletas.db" \
  OPENAI_API_KEY="sk-..." OCR_BACKEND="auto" \
  .venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Railway/hosted: set `OPENAI_API_KEY`, `OPENAI_OCR_MODEL` (optional), and `OCR_BACKEND`
as environment variables in the deployment settings. Leave unset to run Tesseract-only.

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
  auth/        Clerk-based auth gate (backend session verification, require_admin_*)
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
  api/         JSON REST API (FastAPI)
  web/         server-rendered UI (Jinja2, no JS framework/CDN; Clerk JS for auth UI)
scripts/       init_db, load_config, create_admin_user, sample-fixture generator, CLI pipeline runner
tests/         unit tests per module + end-to-end pipeline/API tests
```

## Known v1 limitations

- Tesseract accuracy on difficult handwriting remains limited; use `OCR_BACKEND=openai`
  or the default `auto` mode (Tesseract + selective OpenAI escalation) for better results.
- Single admin role — no separate permission levels. Auth just gates the
  whole app; there's no per-user audit trail beyond the free-text
  `edited_by` field already captured on corrections.
- SQLite works for local dev with zero setup; production should point
  `DATABASE_URL` at Supabase Postgres (see `.env.example`) — add `?sslmode=require`
  for managed Postgres — the schema is
  already Postgres-compatible, no code changes needed.
