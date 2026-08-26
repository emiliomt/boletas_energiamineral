# AGENTS.md

## Cursor Cloud specific instructions

This is a Python 3.12 / FastAPI app (`boletas_energiamineral`) that OCRs mining
boleta scans, decodes folio QRs, classifies them, computes fletero payment +
inventory movement via editable CSV rules in `app/rules/`, routes ambiguous
cases to a review queue, and exports CSV/JSON/Excel. Standard setup/run/test
commands live in `README.md`; only the non-obvious cloud caveats are captured
here.

The actively **deployed** branch is `claude/boleta-processing-carbon-gfmzeg`
(auto-deploys to Railway). Base new work on that branch, not `main` (which only
contains the README).

### Python env
- Python deps are installed into a project-local virtualenv at `.venv` (the
  startup update script creates it and runs `pip install -r requirements.txt`).
  Run everything with `.venv/bin/python` / `.venv/bin/uvicorn` / `.venv/bin/pytest`.
- Required system deps (baked into the VM snapshot, NOT the update script):
  `tesseract-ocr`, `tesseract-ocr-spa`, `poppler-utils` (OCR + PDF rasterize) and
  `libzbar0` (QR decoding via `pyzbar`). If ever missing:
  `sudo apt-get install -y tesseract-ocr tesseract-ocr-spa poppler-utils libzbar0`.

### IMPORTANT: DATABASE_URL — override to SQLite for local dev
- The VM injects a `DATABASE_URL` secret pointing at a **Postgres** database that
  belongs to an unrelated project. This app is designed to run zero-setup on
  **SQLite**. `psycopg2-binary` is a dependency, so it no longer crashes at
  import, but leaving the injected value makes the app/tests talk to that
  unrelated Postgres DB.
- Because pydantic-settings prioritizes OS env vars over the `.env` file, you
  cannot fix this by writing `.env`. Override it per-command instead, e.g.:
  ```bash
  DATABASE_URL="sqlite:///./data/boletas.db" .venv/bin/python scripts/init_db.py
  DATABASE_URL="sqlite:///./data/boletas.db" .venv/bin/python -m pytest -q
  DATABASE_URL="sqlite:///./data/boletas.db" .venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
  ```
  (The `pytest` `db_session` fixture always uses in-memory SQLite, but importing
  `app.db` eagerly builds the engine from `DATABASE_URL`, and API/e2e tests spin
  up the app which calls `init_db()`, so the override is still needed.)

### IMPORTANT: Auth gate (Supabase) blocks the UI
- Every route except `/login`, `/logout`, and `/api/health` now requires a
  single admin session (see `app/main.py`). `/` returns `303 -> /login?next=/`
  when unauthenticated.
- Login verifies credentials against Supabase Auth. Local dev therefore needs
  `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` set, plus an
  admin created out-of-band via `python scripts/create_admin_user.py` (needs the
  service-role key). Without those, `/login` shows a "Supabase not configured"
  error and the UI cannot be exercised end-to-end.
- These Supabase secrets are NOT currently present in this VM's env (the injected
  secrets are for a different Next.js/Clerk/Twilio project). The full test suite
  still passes without them because `test_auth.py` stubs credential verification.

### Run / test / lint
- Dev server: the uvicorn command above serves the JSON API + server-rendered UI
  at `http://localhost:8000` (health `/api/health`; everything else behind login).
  `--reload` hot-reloading works for `app/` source edits.
- Tests: `pytest` (68 tests, includes a real-Tesseract OCR smoke test and QR
  roundtrip/integration tests). Needs the `DATABASE_URL` override; no other setup.
- There is **no configured linter** (no ruff/flake8/pre-commit); the README only
  documents `pytest`. `python -m compileall app scripts` works as a syntax check.

### OCR backend
- `OCR_BACKEND` selects the OCR engine (`app/ocr/factory.py`): `tesseract`
  (offline), `openai` (vision model, best on handwriting/photos), or `auto`
  (default: Tesseract, escalating to OpenAI only when `OPENAI_API_KEY` is set
  AND Tesseract confidence is low). With no key, `auto` behaves like Tesseract,
  so behavior is unchanged out of the box.
- `OPENAI_API_KEY` IS present in this VM's env, so `auto`/`openai` make real
  OpenAI calls (cost + ~5-10s latency per boleta). The test suite never calls
  the API — `tests/test_openai_ocr.py` monkeypatches the client.

### Notes
- `scripts/init_db.py` creates the SQLite schema and loads the CSV rule config;
  it is idempotent, and also backfills newly added `folio_batches` columns via
  `ALTER TABLE` (see `app/db._ensure_columns`). The SQLite file
  (`data/boletas.db`) and uploaded scans under `data/originals/` are gitignored.
- A `Dockerfile` exists for Railway/PaaS deployment (installs the same system
  deps); it is not used by the Cloud Agent dev environment.

## Frontend (Jinja2 + vanilla CSS)

The UI is server-rendered FastAPI/Jinja2 with `app/web/static/style.css` and a
small optional `app/web/static/app.js`. Do **not** introduce React, Vue,
Svelte, Tailwind, shadcn/ui, a Node build, or CSS-in-JS. Catalogs such as
Beautiful UI, shadcn, beUI, Rare UI, and transitions.dev are visual
**references only** — translate ideas into semantic HTML and local CSS.

Preserve routes, form `action`s, input `name`s, `action` values, Jinja
variables, Spanish copy, and backend contracts. Prefer tokens and shared
classes in `style.css` over inline styles. Keep JavaScript dependency-free and
progressive: forms and links must work without it.

Verify keyboard access, visible `:focus-visible`, mobile reflow (~375/768/1440),
and `@media (prefers-reduced-motion: reduce)`. Status must not rely on color
alone. After UI changes run:

```bash
DATABASE_URL="sqlite:///./data/boletas.db" .venv/bin/python -m pytest -q
```
