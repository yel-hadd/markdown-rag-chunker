# Markdown RAG Chunker — Apify Actor image.
# Pinned base for reproducible builds; the Actor entry point is `python -m src`.
FROM python:3.13

WORKDIR /usr/src/app

# Install dependencies first so Docker caches them between code-only pushes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./

CMD ["python", "-m", "src"]
