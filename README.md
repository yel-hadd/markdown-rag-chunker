**Markdown RAG Chunker** converts PDFs, webpages, Word, Excel, PowerPoint, EPUB, and Markdown into clean, **header-aware chunks** for embeddings, vector databases, and LLM pipelines — with deterministic IDs, token estimates, and pay-per-chunk pricing. No subscription, no chunking code to maintain. Built for AI engineers shipping RAG pipelines who need predictable, repeatable splits.

[Run it now in Apify Console](https://console.apify.com/actors/84h4GucI0gbo1SrN7?fpr=use-apify) — paste Markdown or drop a document URL and press Start.

**Open source (MIT)**: [github.com/yel-hadd/markdown-rag-chunker](https://github.com/yel-hadd/markdown-rag-chunker) — issues and pull requests welcome.

## What does Markdown RAG Chunker do?

- **Converts 10+ formats to Markdown first**: PDF, HTML, DOCX, XLSX, PPTX, CSV, JSON, XML, EPUB, and plain text — via Microsoft's [markitdown](https://github.com/microsoft/markitdown)
- **Splits by header structure, not fixed windows**: sections break at `#`–`######` and each chunk carries its parent header chain in `metadata`, so a chunk about "Authentication" still knows it lives under "API Reference"
- **Keeps well-sized sections intact**: optional `max_chunk_chars` re-splits *only* oversized sections (with 10% overlap); sections already under the cap pass through untouched
- **Gives every chunk a deterministic `chunk_id`**: the same input always produces the same IDs, so upserts into [Pinecone](https://www.pinecone.io/), [Qdrant](https://qdrant.tech/), [Weaviate](https://weaviate.io/), [pgvector](https://github.com/pgvector/pgvector), or any vector store are idempotent and deduplicable
- **Estimates tokens per chunk** (`token_count`, ~1 token per 4 characters) for embedding budget planning
- **Fetches like a real browser**: remote downloads use [curl_cffi](https://github.com/lexiforest/curl_cffi) with a genuine Chrome TLS/HTTP2 fingerprint, so sites that block plain HTTP clients (Wikipedia 403s `python-httpx`) still work — with optional proxy support on top
- **Skips unchanged documents**: every run returns a `document_hash`, and `skip_if_unchanged` makes recurring runs of the same document cost a fraction of a cent instead of the full per-chunk price
- **Costs nothing when idle**: pay-per-event pricing, $0.80 per 1,000 chunks on the FREE plan

## Supported input formats

| Category | Formats |
| --- | --- |
| Webpages | HTML — paste any webpage URL and it's converted to Markdown |
| Markdown and text | `.md`, `.txt` |
| PDF | `.pdf` |
| Office | `.docx`, `.xlsx`, `.xls`, `.pptx` |
| Data | `.csv`, `.json`, `.xml` |
| Books | `.epub` |

Provide files as an `http(s)://` URL or as an [Apify Key-Value Store](https://docs.apify.com/platform/storage/key-value-store) record (`kvs://KEY`). MIME type is detected automatically and reported as `input_file_type`.

## How to chunk a document for RAG

1. Open the Actor and pick **Input Mode**: `text` to paste Markdown directly, or `file` to load a document from a URL or `kvs://KEY`.
2. Set **Headers to Split On**. Most RAG pipelines work best with `["#", "##", "###"]` — section-level chunks with page context preserved in metadata.
3. Optionally set **Max Chunk Chars** (for example `1800` ≈ 450 tokens) to cap oversized sections.
4. If the source site blocks bots, enable **Use Apify Proxy** or enter your own **Proxy URL** (free).
5. Press **Start**. Each chunk lands in the default dataset as its own item; run totals appear in the run's **Output**.

Then pipe each item's `content` into your embedding model and store `chunk_id` and `metadata` next to the vector. For scheduled or recurring pipelines, turn on **Skip if Unchanged** — re-runs of an unchanged document push nothing and cost a fraction of a cent. The full input form is documented under the [Input](https://apify.com/codepoetry/markdown-rag-chunker/input-schema?fpr=use-apify) tab; field types live under [Output](https://apify.com/codepoetry/markdown-rag-chunker/output-schema?fpr=use-apify).

## Quick start with the API

**Python**

```python
from apify_client import ApifyClient

client = ApifyClient("YOUR_APIFY_TOKEN")
run = client.actor("codepoetry/markdown-rag-chunker").call(run_input={
    "input_mode": "file",
    "markdown_file": "https://example.com/docs/handbook.pdf",
    "headers_to_split_on": ["#", "##", "###"],
    "max_chunk_chars": 1800,
})
for item in client.dataset(run["defaultDatasetId"]).iterate_items():
    print(item["chunk_id"], item["metadata"], item["token_count"])
```

**JavaScript**

```javascript
import { ApifyClient } from 'apify-client';

const client = new ApifyClient({ token: 'YOUR_APIFY_TOKEN' });
const run = await client.actor('codepoetry/markdown-rag-chunker').call({
    input_mode: 'file',
    markdown_file: 'https://example.com/docs/handbook.pdf',
    headers_to_split_on: ['#', '##', '###'],
});
const { items } = await client.dataset(run.defaultDatasetId).list_items();
```

**curl**

```bash
curl -X POST "https://api.apify.com/v2/acts/codepoetry~markdown-rag-chunker/run-sync-get-dataset-items?fpr=use-apify&token=YOUR_APIFY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"input_mode":"text","markdown_text":"# Title\nSome content"}'
```

## Output format

Each chunk is one dataset item:

```json
{
  "content": "Install the SDK with pip install ...",
  "metadata": { "Header 1": "Guide", "Header 2": "Install" },
  "chunk_id": "f8b6be2adf7f6dbf",
  "char_count": 124,
  "token_count": 31
}
```

Run-level metrics come back as the run's named **Output** (never billed as dataset items):

```json
{
  "input_mode": "file",
  "input_file_type": "application/pdf",
  "input_chars": 4281,
  "chunk_count": 12,
  "elapsed_ms": 184,
  "document_hash": "3f2a9c1e8b7d4a60",
  "unchanged": false
}
```

| Field | Where | Description |
| --- | --- | --- |
| `content` | dataset item | Chunk text for your embedding model |
| `metadata` | dataset item | Header hierarchy (`Header 1`, `Header 2`, …) for context-aware retrieval |
| `chunk_id` | dataset item | Stable 16-char ID for idempotent upserts and deduplication |
| `char_count` | dataset item | Character length of `content` |
| `token_count` | dataset item | Approximate token count (~1 token per 4 characters) |
| `input_file_type` | Output | Detected source MIME type (for example `application/pdf`) |
| `chunk_count` | Output | Chunks pushed in this run |
| `document_hash` | Output | Fingerprint of the source document — compare across runs to detect changes |
| `unchanged` / `cached_chunk_count` | Output | Set when Skip if Unchanged skipped the run |
| `elapsed_ms` | Output | End-to-end processing time |

## How much does RAG chunking cost?

This Actor uses [pay-per-event pricing](https://docs.apify.com/platform/actors/publishing/monetize/pay-per-event): **you pay per chunk produced**, at the same rate for every input format.

- **$0.80 per 1,000 chunks** on the FREE and BRONZE plans (lower on higher plans — see the [Pricing](https://apify.com/codepoetry/markdown-rag-chunker/pricing?fpr=use-apify) tab)
- Each run also incurs Apify's standard actor-start event (a fraction of a cent)
- **Re-runs of unchanged documents can cost ~nothing**: enable **Skip if Unchanged** and a run that detects no change pushes no chunks and is not billed for any
- Run metrics are returned as the named Output and are **not** billed
- No monthly fee — unused months cost nothing

| Input | Chunks produced | Approx. cost |
| --- | --- | --- |
| 1-page Markdown snippet | 3 | $0.0024 |
| 10-page HTML article | 15 | $0.012 |
| 40-page PDF | 60 | $0.048 |
| 200-page technical manual | 300 | $0.24 |

### Proxies and fair billing

Fetches go direct by default (free). In `file` mode you can opt into either:

- **`proxy_url` — bring your own proxy, free**: `http://user:pass@host:8080`, `https://host:8080`, or `socks5://host:1080`. No Apify proxy charges; you pay only the per-chunk price.
- **`use_apify_proxy` — Apify Proxy (datacenter)**: for sites that block datacenter IPs or rate-limit you. Billed by Apify as standard platform usage on your plan — the same rate as any other Actor's traffic, no markup from us.

The two options are mutually exclusive; neither changes chunk pricing.

## Markdown RAG Chunker vs Firecrawl and plain text splitters

| | Markdown RAG Chunker | Firecrawl | Fixed-size splitters |
| --- | --- | --- | --- |
| Pricing | Pay per chunk ($0.80/1k), no subscription | Monthly subscription + credits | Free (DIY) |
| Formats | PDF, HTML, DOCX, XLSX, PPTX, EPUB, CSV, JSON, XML, MD | Web pages, PDF (scrape-centric) | Whatever you wire up |
| Splitting | Header-aware + token-aware cap, header chain in metadata | Markdown + chunk endpoints | Fixed char/token windows, no structure |
| Stable IDs for vector upserts | Built in | No | DIY |
| JS-rendered sites / site crawling | No (pair with [Website Content Crawler](https://apify.com/apify/website-content-crawler?fpr=use-apify)) | Yes | No |
| Runs on | Apify (scheduling, API, MCP, storage included) | Firecrawl platform / self-host | Your infra |

If you need JS rendering and full-site crawls in one product, Firecrawl is a good fit. If you already have documents or Markdown and need cheap, deterministic, header-aware chunking with stable IDs — or you want to chunk [Website Content Crawler](https://apify.com/apify/website-content-crawler?fpr=use-apify) output on Apify — this Actor does exactly that, without a subscription.

## Use cases

- **RAG over documentation**: split product docs and changelogs into retrievable sections
- **Knowledge bases**: ingest internal PDFs and Word docs into a vector store with stable IDs
- **Customer support search**: chunk help-center articles for semantic search
- **AI agents**: serve large reference documents to agents in budget-sized slices
- **Crawler post-processing**: chain after [Website Content Crawler](https://apify.com/apify/website-content-crawler?fpr=use-apify) to turn crawled pages into RAG-ready chunks

## FAQ

### How do I convert a webpage or HTML to Markdown chunks?

Set `input_mode` to `file` and paste the page URL into `markdown_file`. The webpage is fetched with a real Chrome TLS fingerprint (so most bot blocks don't apply), converted from HTML to Markdown, and split by header structure — each chunk keeps its section path in `metadata`.

### How do I chunk a PDF for a vector database?

Set `input_mode` to `file`, paste the PDF URL into `markdown_file`, and run the Actor. The PDF is converted to Markdown with [markitdown](https://github.com/microsoft/markitdown), split by header hierarchy, and each chunk gets a deterministic `chunk_id` you can use as the upsert key in [Pinecone](https://www.pinecone.io/), [Qdrant](https://qdrant.tech/), [Weaviate](https://weaviate.io/), or [pgvector](https://github.com/pgvector/pgvector).

### Why header-aware chunking instead of fixed-size chunking?

Fixed-size splitters cut every N characters, slicing through paragraphs and dropping structural context. Header-aware splitting maps each chunk to a logical section and keeps the parent header chain in `metadata`, giving retrieval real context instead of orphaned text fragments.

### Can I chain it with a web crawler?

Yes — a common pipeline is **[Website Content Crawler](https://apify.com/apify/website-content-crawler?fpr=use-apify) → Markdown RAG Chunker → embeddings → vector DB**. The crawler produces clean Markdown per page; this Actor splits it into chunks with stable IDs.

### Does it work with LangChain or LlamaIndex?

Yes. Chunking is built on [LangChain text splitters](https://python.langchain.com/docs/how_to/markdown_header_metadata_splitter/), and each output item maps directly onto a LangChain [`Document`](https://python.langchain.com/api_reference/core/documents/langchain_core.documents.base.Document.html) or LlamaIndex [`Node`](https://docs.llamaindex.ai/en/stable/module_guides/loading/documents_and_nodes/). Apify also ships [LangChain](https://python.langchain.com/docs/integrations/providers/apify/) and [LlamaIndex](https://docs.apify.com/platform/integrations/llama) integrations.

### Why is my dataset empty after a run?

If **Skip if Unchanged** is on and the document (and split settings) haven't changed since the last run, the Actor pushes nothing and reports `unchanged: true` with the cached `chunk_count` in the run Output — that run costs a fraction of a cent instead of the per-chunk price. Turn the toggle off when you want the chunks re-pushed.

### How do I avoid paying for documents that haven't changed?

Turn on **Skip if Unchanged** (or pass `"skip_if_unchanged": true` via the API). Each run also returns a `document_hash` in the Output, so you can compare hashes client-side and only re-chunk when a document actually changed.

### How accurate is `token_count`?

It's a fast estimate (~1 token per 4 characters) for budget planning and guardrails. For exact counts, run your model's tokenizer over `content` after retrieval.

### Where do I find API examples?

The [API](https://apify.com/codepoetry/markdown-rag-chunker/api?fpr=use-apify), [Python](https://apify.com/codepoetry/markdown-rag-chunker/api/python?fpr=use-apify), [JavaScript](https://apify.com/codepoetry/markdown-rag-chunker/api/javascript?fpr=use-apify), [CLI](https://apify.com/codepoetry/markdown-rag-chunker/api/cli?fpr=use-apify), [OpenAPI](https://apify.com/codepoetry/markdown-rag-chunker/api/openapi?fpr=use-apify), and [MCP](https://apify.com/codepoetry/markdown-rag-chunker/api/mcp?fpr=use-apify) tabs on this Actor's page include ready-to-paste code with the correct Actor ID and input shape for every supported client.

## What's new

### v1.2

- **Change detection to save you money**: every run returns a `document_hash`, and the new **Skip if Unchanged** option makes re-runs of unchanged documents cost a fraction of a cent — no chunks are re-pushed or billed. Split-setting changes are detected too, so tuning `headers_to_split_on` or `max_chunk_chars` always re-processes
- Prefilled input form for faster setup in Apify Console
- Copy-paste Python, JavaScript, and curl quick-start examples in the README

### v1.1

- One dataset item per chunk (previously a single wrapped item per run) — better for dataset pagination, streaming, and API clients, and billing now matches the published per-chunk price exactly
- Run metrics moved to the run's named **Output** (`input_mode`, `input_file_type`, `input_chars`, `chunk_count`, `elapsed_ms`) — no longer billed
- Anti-blocking fetch layer: [curl_cffi](https://github.com/lexiforest/curl_cffi) with a real Chrome TLS/HTTP2 fingerprint replaces the plain HTTP client
- Bring-your-own proxy for free (`proxy_url`: `http://`, `https://`, `socks5://`), or opt into Apify Proxy (`use_apify_proxy`)
- Oversized remote files rejected up front via `Content-Length`, before any download or conversion
- Upgraded Apify SDK to 4.x, fixing CLI-started runs

### v1.0

- Initial release: multi-format conversion (PDF, HTML, DOCX, XLSX, PPTX, EPUB, …), header-aware splitting, deterministic chunk IDs, token estimates
