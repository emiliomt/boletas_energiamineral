# AGENTS.md

## Cursor Cloud specific instructions

This is a Python 3.12 / FastAPI app (`boletas_energiamineral`) that OCRs mining
boleta scans, classifies them, computes fletero payment + inventory movement via
editable CSV rules in `app/rules/`, and routes ambiguous cases to a review queue.
Standard setup/run/test commands live in `README.md`; only the non-obvious
cloud caveats are captured here.

### Python env
- Python deps are installed into a project-local virtualenv at `.venv` (the
  startup update script creates it and runs `pip install -r requirements.txt`).
  Run everything with `.venv/bin/python` / `.venv/bin/uvicorn` / `.venv/bin/pytest`.
- System deps `tesseract-ocr`, `tesseract-ocr-spa`, and `poppler-utils` are
  required for OCR/PDF rasterization and are baked into the VM snapshot (not the
  update script). If they are ever missing:
  `sudo apt-get install -y tesseract-ocr tesseract-ocr-spa poppler-utils`.

### IMPORTANT: DATABASE_URL must be overridden to SQLite
- The VM injects a `DATABASE_URL` secret pointing at a **Postgres** database that
  belongs to an unrelated project. This app is designed to run zero-setup on
  **SQLite** and does not depend on `psycopg2`, so the injected value makes the
  app (and `pytest`, and `scripts/init_db.py`) crash at import with
  `ModuleNotFoundError: No module named 'psycopg2'`.
- Because pydantic-settings prioritizes OS env vars over the `.env` file, you
  cannot fix this by writing `.env`. Override it per-command instead, e.g.:
  ```bash
  DATABASE_URL="sqlite:///./data/boletas.db" .venv/bin/python scripts/init_db.py
  DATABASE_URL="sqlite:///./data/boletas.db" .venv/bin/python -m pytest -v
  DATABASE_URL="sqlite:///./data/boletas.db" .venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
  ```
  (The test suite itself always uses in-memory SQLite, but importing `app.db`
  eagerly builds the engine from `DATABASE_URL`, so the override is still needed.)

### Run / test / lint
- Dev server: the uvicorn command above serves the JSON API + server-rendered
  review UI at `http://localhost:8000` (dashboard `/`, review queue `/review`,
  health `/api/health`). `--reload` hot-reloading works for `app/` source edits.
- Tests: `pytest` (33 tests, includes a real-Tesseract OCR smoke test). No test
  DB setup is needed beyond the `DATABASE_URL` override.
- There is **no configured linter** (no ruff/flake8/pre-commit); the README only
  documents `pytest`. `python -m compileall app scripts` works as a syntax check.

### Notes
- `scripts/init_db.py` creates the SQLite schema and loads the CSV rule config;
  it is idempotent. The SQLite file (`data/boletas.db`) and uploaded scans under
  `data/originals/` are gitignored runtime data.
