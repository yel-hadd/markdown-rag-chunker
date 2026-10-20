# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An open-source Apify Actor ("Markdown RAG Chunker", Store slug `codepoetry/markdown-rag-chunker`) written in Python 3.13. It converts documents to Markdown with `markitdown`, splits them with LangChain text splitters, and pushes one dataset item per chunk. `README.md` is the public Store listing — it is user-facing marketing copy, so keep its claims accurate and in sync with actual behavior. Follow TDD: write the failing test first for any behavior in `src/models.py` or `src/processing.py`, keep code and comments minimal and high-quality.

## Commands

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt   # deps (no venv is checked in; the system Python has an unrelated pydantic/crawlee conflict)
python -m unittest discover -s tests                 # all tests (stdlib unittest, no pytest)
python -m unittest tests.test_models_and_runtime.FormatChunksTests.test_chunk_id_is_deterministic   # single test
apify run                                            # run locally; input from storage/key_value_stores/default/INPUT.json
python -m src                                        # same entry point as the Dockerfile CMD
apify push                                           # build and deploy to Apify (authenticated as codepoetry)
apify call 84h4GucI0gbo1SrN7 -i '<json input>' -o   # verify a deployed build end to end
```

Run the tests from the repo root, because they import `src.*` as a package. `src/main.py` cannot be unit-tested locally (it imports `apify`), which is why all logic lives in `processing.py`/`models.py` and network wiring is verified with real `apify call` runs after each push.

## Architecture

- `src/main.py` — the Actor runtime and the only module touching Apify or the network. It parses input into `MarkdownSplitterInput`, resolves the fetch proxy (user's own `proxy_url` wins, else `use_apify_proxy` via `Actor.create_proxy_configuration`, only in file mode), then defines the I/O closures: `fetch_remote` (curl_cffi `AsyncSession` with `impersonate="chrome"` and a 30s timeout — a real Chrome TLS/HTTP2 fingerprint; plain HTTP clients get 403'd by sites like Wikipedia) with a `Content-Length` fail-fast guard (`max_input_chars * 4` bytes), and `load_kvs` (`Actor.get_value`, plain strings only, not run through markitdown). For `http(s)` sources it downloads once up front (reusing the payload for conversion), resolves the document, applies change detection, splits via `processing.split_documents`, writes run metrics to the `OUTPUT` record via `Actor.set_value`, and pushes one dataset item per chunk. Any exception goes to `Actor.fail`.
- `src/processing.py` — pure logic, testable without Apify. `resolve_markdown_text` takes injected `fetch_remote`, `load_kvs`, `convert_remote_file` callables (sync or async; `_maybe_await`). `split_documents` maps header levels to `Header N` metadata keys, drops sections with empty content (they would become billed zero-char chunks), re-splits **only** oversized sections when `max_chunk_chars` is set (overlap = 10% of the cap), and enforces `max_chunks`. `format_chunks` computes `chunk_id` (first 16 hex chars of sha256 over sorted-key JSON of content + metadata) and `token_count = ceil(chars/4)`. `document_hash` fingerprints the resolved document and `source_fingerprint` the raw download (bytes + content type); `cache_hit` decides whether a cached state matches; `split_config_fingerprint` hashes every input that affects splitting (headers, strip_headers, max_chunk_chars) so the change-detection cache never reports a stale hit. Tests inject fakes instead of mocking network calls.
- Change detection (`skip_if_unchanged`): a named KVS per source (`markdown-rag-chunker-cache-<sha>`, file mode keyed by source string, text mode by document hash) holds `{document_hash, source_hash, split_config, chunk_count, input_chars, input_file_type}` under the `state` key. File mode checks `source_hash` before conversion (an unchanged download skips markitdown entirely), then everything falls back to `document_hash` after conversion (dynamic pages whose bytes vary per fetch). On a hit the run pushes nothing and exits with `unchanged: true` in OUTPUT. Stores are created inside the run, so they belong to the run's user — never the creator. Cache read/write failures are logged and ignored, never fatal.
- `src/models.py` — Pydantic models holding all validation: mode-dependent required fields, `max_input_chars` (checked in text mode at validation, in file mode after conversion), header format (1–6 `#` characters, entries normalised so `' ## '` and `'##'` fingerprint identically), `proxy_url` scheme check (case-insensitive `http://`/`https://`/`socks5://`; only the scheme is lowercased), and proxy mutual exclusion. `MarkdownChunk` shapes dataset items; `ProcessingMetrics` shapes the `OUTPUT` record.
- `Dockerfile` — explicit, pinned to `python:3.13`, CMD `python -m src`. (Without it the CLI silently generates one.) `.dockerignore` keeps `.git`, local `storage/`, tests and docs out of the image.

## Keep these in sync

- Input fields: `src/models.py` ↔ `.actor/INPUT_SCHEMA.json`. Defaults deliberately differ: the schema defaults `input_mode` to `file`, while the model defaults it to `text`.
- Output shape: `src/models.py` (`MarkdownChunk` per dataset item, `ProcessingMetrics` for the `OUTPUT` record) ↔ `.actor/dataset_schema.json` (one chunk per item) ↔ `.actor/output_schema.json` (templated links only — every property needs a `template`, e.g. `{{links.apiDefaultDatasetUrl}}/items`; the platform rejects anything else at build time) ↔ the README output table.
- Limits and memory: `.actor/actor.json` and the `max_*` defaults ↔ `docs/pricing-and-hardening.md`.
- Feature claims: README ↔ actual behavior. No misleading claims — pricing examples, proxy billing, and format support must match the code.

## Billing

Pay-per-event with only Apify's synthetic events: `apify-actor-start` and `apify-default-dataset-item`. The code never calls `Actor.charge()`; `docs/pricing-and-hardening.md` says never to add custom events or charge event names starting with `apify-`. Published prices: `apify-actor-start` $0.001 (all tiers) and `apify-default-dataset-item` $0.0008/$0.0007/$0.0006 per chunk, i.e. $0.80/1k chunks + $0.001/run. The creator pays **all** platform usage of users' runs (compute, storage, Apify Proxy) out of the 80% revenue share, so the start fee exists to make zero-chunk runs (skip hits, failed inputs) profitable; measured costs and the margin check live in `docs/pricing-and-hardening.md`. Revenue is the number of `Actor.push_data()` items (one per chunk) plus one start event per run. The `OUTPUT` record is written via `Actor.set_value` and is **not** billed. Changing how data is pushed, adding run time, or adding storage writes changes margins.

## Gotchas learned the hard way

- The platform validates `.actor/output_schema.json` at build time: every property must have a `template` (link-style outputs only).
- In crawlee ≥1.x, `ProxyConfiguration.new_url()` is async — `await` it.
- Pinning an old `apify` SDK broke every CLI-started run (`meta.origin: 'CLI'` not in its pydantic enum); keep `apify` reasonably current.
