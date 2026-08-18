# Explicit build so Railway (or any Docker host) doesn't have to guess how
# to run this: Python's own dependency detection has no way to know we also
# need the tesseract/poppler/zbar system binaries for OCR, PDF splitting,
# and QR decoding.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-spa \
    poppler-utils \
    libzbar0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway (and most PaaS hosts) inject PORT at runtime; default to 8000 for
# local `docker run`. Shell form so ${PORT} actually expands.
ENV PORT=8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
