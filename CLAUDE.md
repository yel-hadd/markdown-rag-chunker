# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An Apify Actor ("Markdown RAG Chunker", Store slug `codepoetry/markdown-rag-chunker`) written in Python 3.13. It takes Markdown text or a remote/KVS document, converts it to Markdown with `markitdown`, splits it with LangChain text splitters, and pushes the chunks to the default dataset. `README.md` is the public Store listing, so edits to it are user-facing marketing copy.

## Commands

```bash
pip install -r requirements.txt                      # deps (no venv is checked in)
python -m unittest discover -s tests                 # all tests (stdlib unittest, no pytest)
python -m unittest tests.test_models_and_runtime.FormatChunksTests.test_chunk_id_is_deterministic   # single test
apify run                                            # run locally; input from storage/key_value_stores/default/INPUT.json
python -m src                                        # same entry point as the Docker CMD
apify push                                           # build and deploy to Apify
```

Run the tests from the repo root, because they import `src.*` as a package.

## Architecture

- `src/main.py` holds the Actor runtime and the only code that touches Apify or the network. It validates input, defines the I/O closures (`fetch_remote` via httpx with a 30s timeout, and `load_kvs` via `Actor.get_value`), runs `MarkdownHeaderTextSplitter`, then optionally re-splits **only** documents longer than `max_chunk_chars` with `RecursiveCharacterTextSplitter` (overlap = 10% of the cap). It enforces `max_chunks` after splitting. Any exception goes to `Actor.fail`.
- `src/processing.py` holds pure logic that can be tested without Apify. `resolve_markdown_text` takes injected `fetch_remote`, `load_kvs` and `convert_remote_file` callables, and they can be sync or async (`_maybe_await`). Tests rely on this injection instead of mocking network calls. `format_chunks` computes `chunk_id` (the first 16 hex characters of sha256 over sorted-key JSON of content + metadata) and `token_count = ceil(chars/4)`.
- `src/models.py` holds the Pydantic models. `MarkdownSplitterInput` contains the real validation rules (mode-dependent required fields, the `max_input_chars` check in text mode, and header format). File mode checks `max_input_chars` later, in `resolve_markdown_text`, after conversion.
- Supported file-mode sources are `http(s)://` URLs, which are converted by markitdown using the response `Content-Type`, and `kvs://KEY`, which must be a plain string and is **not** run through markitdown.

## Keep these in sync

- Input fields: `src/models.py` ↔ `.actor/INPUT_SCHEMA.json`. Defaults deliberately differ: the schema defaults `input_mode` to `file`, while the model defaults it to `text`.
- Output shape: `src/models.py` (`MarkdownChunk`, `ProcessingMetrics`) ↔ `.actor/dataset_schema.json` ↔ `.actor/output_schema.json` ↔ the README output table.
- Limits and memory: `.actor/actor.json` and the `max_*` defaults ↔ `docs/pricing-and-hardening.md`.

## Billing

The Actor uses pay-per-event with only Apify's synthetic events (`apify-actor-start` and `apify-default-dataset-item`). The code never calls `Actor.charge()`, and `docs/pricing-and-hardening.md` says not to add custom events or charge events whose names start with `apify-`. Revenue therefore depends on the number of `Actor.push_data()` items. Each run currently pushes **one** dataset item that contains the whole `chunks` array, so changing how data is pushed changes billing.
