# boletas_energiamineral

First-pass internal tool for a mining company that scans **boletas**
(carbón trip tickets), classifies them automatically, calculates fletero
payment, updates inventory, and surfaces only ambiguous cases for manual
review.

Built rules-first: every decision (trip classification, tariff, inventory
direction, estimated weight, when to flag for review) is driven by editable
CSV config tables in `app/rules/`, not hardcoded logic and not ML — so an
admin can see and change *why* the system decided what it decided.

## How it works

```
Upload -> Ingestion -> OCR (Tesseract) -> Field parsing -> Classification
       -> Tariff lookup -> Inventory movement -> Exception scoring
       -> Review queue (human corrects/approves) -> CSV/JSON export
```

See `app/pipeline/orchestrator.py` for the single place all of this is
wired together.

## Setup

```bash
# System dependencies (OCR engine + PDF rasterizer)
sudo apt-get install -y tesseract-ocr tesseract-ocr-spa poppler-utils

# Python dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Create the DB schema and load the sample rule config
python scripts/init_db.py
```

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
  conditions always force manual review

After editing a CSV, reload it without restarting: `python
scripts/load_config.py` (or `POST /api/config/reload`).

## Run it

```bash
uvicorn app.main:app --reload
```

Open http://localhost:8000 — create a batch, upload boleta scans, and
correct/approve anything that lands in the review queue at
http://localhost:8000/review. Export results as CSV/JSON from the batch
page or `GET /api/exports/csv?batch_id=...` / `/api/exports/json`.

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
exception codes explaining why.

## Tests

```bash
pytest -v
```

Covers the field parser, each rule engine (classification/tariff/
inventory/exception-scoring) individually, the full pipeline end-to-end
(with a deterministic fake OCR adapter, plus a real-Tesseract smoke test),
and the upload→review→export flow through the actual HTTP API.

## Project layout

```
app/
  ingestion/   upload storage, PDF page-splitting
  ocr/         OCRAdapter interface; Tesseract impl; LLM/cloud-OCR stub
  parsing/     regex/keyword field extraction, normalization
  rules/       editable CSV config + loader (source of truth for all rules)
  engines/     classification, tariff, inventory, exception/confidence scoring
  pipeline/    orchestrator.py — wires every stage together per boleta
  review/      human correction/approval service + audit trail
  exports/     CSV/JSON export
  reporting/   batch summary aggregations
  api/         JSON REST API (FastAPI)
  web/         server-rendered review UI (Jinja2, no JS framework/CDN)
scripts/       init_db, load_config, sample-fixture generator, CLI pipeline runner
tests/         unit tests per module + end-to-end pipeline/API tests
```

## Known v1 limitations

- No login/auth — designed for a single internal admin user.
- OCR runs locally via Tesseract (offline, no API key needed). If accuracy
  on handwritten notes proves insufficient, implement
  `app/ocr/llm_fallback_adapter.py` against a cloud OCR/LLM API — it's a
  drop-in swap behind the same `OCRAdapter` interface used everywhere else.
- SQLite storage, fine for ~200 boletas/week; the schema is Postgres-portable
  if volume grows.
