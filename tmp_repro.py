import io, os, json, tempfile
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from pathlib import Path

def _font(size: int = 22):
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()

def _render_text_image(lines):
    canvas = Image.new("RGB", (900, 400), "white")
    draw = ImageDraw.Draw(canvas)
    font = _font()
    y = 30
    for line in lines:
        draw.text((40, y), line, fill="black", font=font)
        y += 42
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()

# Configure temp DB and originals dir
import app.config as app_config
import app.db as app_db

tmpdir = tempfile.mkdtemp(prefix="boletas-test-")
db_path = os.path.join(tmpdir, "test.db")
app_config.settings.database_url = f"sqlite:///{db_path}"
app_config.settings.originals_dir = Path(tmpdir) / "originals"
engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
app_db.engine = engine
app_db.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

from app.db import init_db
init_db()

from app.auth.session import require_admin_api, require_admin_web
from app.main import app
app.dependency_overrides[require_admin_api] = lambda: "test-admin@example.com"
app.dependency_overrides[require_admin_web] = lambda: "test-admin@example.com"

c = TestClient(app)

r = c.post("/api/folio-batches", json={"label": "test-folios", "mode": "imported", "folios": ["B-8001"]})
print("folio-batch:", r.status_code)
rb = c.post("/api/batches", json={"label": "test-batch"})
print("batch:", rb.status_code)
batch_id = rb.json()["id"]

boleta_png = _render_text_image([
    "Folio: B-8001",
    "Fecha: 20/01/2026",
    "Centro de Explotacion: Mina San Jose",
    "Destino: Planta Norte",
    "Datos del chofer del camion: Juan Perez",
])
slip_png = _render_text_image([
    "Folio: B-8001",
    "Fecha: 20/01/2026",
    "Peso de Entrada: 500 kg",
    "Peso de Salida: 9500 kg",
])

u1 = c.post(f"/api/batches/{batch_id}/upload", files=[("files", ("boleta.png", boleta_png, "image/png"))])
print("upload1:", u1.json())

u2 = c.post(f"/api/batches/{batch_id}/upload", files=[("cfe_slip_files", ("slip.png", slip_png, "image/png"))])
body = u2.json()
print(json.dumps(body, indent=2))
rec_id = body["processed"][0]["record_id"]
rec = c.get(f"/api/records/{rec_id}")
print(json.dumps(rec.json(), indent=2))
