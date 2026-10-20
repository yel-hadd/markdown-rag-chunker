**Markdown RAG Chunker** splits PDFs, web pages, Word, PowerPoint, Excel and EPUB files into **header-aware chunks for RAG**. Each chunk stays inside one section of the document, carries its header path as metadata, and comes with a stable ID and a token count, ready to embed into Pinecone, Qdrant, Weaviate or pgvector.

[Try it in Apify Console](https://console.apify.com/actors/84h4GucI0gbo1SrN7?fpr=use-apify). The form is prefilled with a Wikipedia article, so your first run is one click.

**Open source (MIT):** [github.com/yel-hadd/markdown-rag-chunker](https://github.com/yel-hadd/markdown-rag-chunker). Issues and pull requests welcome.

## What does Markdown RAG Chunker do?

- **Converts 12 formats to Markdown** with Microsoft's [markitdown](https://github.com/microsoft/markitdown): PDF, HTML, DOCX, PPTX, XLSX, XLS, EPUB, CSV, JSON, XML, Markdown and plain text. The server's content type and charset are passed to the converter, so CSV becomes a table and Latin-1 pages keep their accents.
- **Splits on the document's own headings** (`#` to `######`). A chunk about authentication keeps `{"Header 1": "API Reference", "Header 2": "Authentication"}` in its `metadata`.
- **Caps oversized sections at about 400 tokens** (1,600 characters by default). Long sections are re-split on paragraph, line and word boundaries with 10% overlap; sections under the cap stay whole.
- **Gives every chunk a deterministic `chunk_id`**, so re-runs produce the same IDs and vector-store upserts stay idempotent. Repeated identical sections get distinct IDs, so no chunk you paid for is overwritten.
- **Estimates `token_count`** (about 4 characters per token) for embedding budgets.
- **Downloads like a real browser** with [curl_cffi](https://github.com/lexiforest/curl_cffi) and a genuine Chrome TLS fingerprint, with optional Apify Proxy or your own proxy. Files up to 25 MB.
- **Skips unchanged documents** on scheduled runs: an unchanged file is not even re-converted, and the run costs only the $0.001 start fee.

## Why header-aware chunking is the right strategy for RAG

Split on the document's headings first, then cap only the sections that are too long. LangChain's documentation recommends this two-step pattern for Markdown, and retrieval benchmarks reward its result: chunks of a few hundred tokens that follow the author's structure.

- **Structure keeps meaning together.** Pinecone's [chunking strategies guide](https://www.pinecone.io/learn/chunking-strategies/) (Schwaber-Cohen and Patel, 2025) says splitting Markdown by its headings gives "more semantically coherent chunks", while fixed-size chunking can "ignore critical structure within documents".
- **A few hundred tokens retrieves well.** In Chroma's [chunking evaluation](https://www.trychroma.com/research/evaluating-chunking) (Smith and Troynikov, July 2024), recursive splitting at 400 tokens reached 89.5% recall with `text-embedding-3-large`. The common 800-token chunks with 400-token overlap scored the lowest precision and IoU of every configuration tested.
- **Overlap is not free.** The same study found that removing overlap from 400-token chunks slightly raised recall (89.5% vs 88.1%), because overlap repeats text. This Actor uses a small 10% overlap, and only inside sections that need re-splitting.
- **The header path travels with the chunk.** `metadata` holds every parent heading, so you can filter by section or prepend the path before embedding.
- **It is the documented LangChain pattern.** [LangChain's Markdown splitter docs](https://docs.langchain.com/oss/python/integrations/splitters/markdown_header_metadata_splitter): "Within each markdown group we can then apply any text splitter we want, such as RecursiveCharacterTextSplitter, which allows for further control of the chunk size."

| | Fixed-size windows | Recursive only | Header-aware + size cap (this Actor) |
| --- | --- | --- | --- |
| Respects section boundaries | No | Partly (paragraphs) | Yes |
| Header context per chunk | No | No | Yes, in `metadata` |
| Predictable chunk size | Yes | Yes | Yes, capped at ~400 tokens by default |
| Short sections kept whole | No | No | Yes |

## How to chunk a document for RAG

1. Paste a document URL into **Document URL**, or paste Markdown into **Markdown text**.
2. Keep the defaults for most documents: split on `#`, `##`, `###` and cap chunks at 1,600 characters.
3. If the site blocks direct downloads, turn on **Use Apify Proxy**.
4. Press **Start**. Each chunk is one dataset item, and run totals appear in the run's **Output**.
5. For scheduled pipelines, turn on **Skip if unchanged**.

Then embed each item's `content` and store `chunk_id` and `metadata` next to the vector.

## How much does RAG chunking cost?

This Actor uses [pay-per-event pricing](https://docs.apify.com/platform/actors/publishing/monetize/pay-per-event). You pay for the chunks you get, at the same rate for every input format, plus a small fee per run.

- **$0.80 per 1,000 chunks** on the FREE and BRONZE plans (lower on higher plans, see the [Pricing](https://apify.com/codepoetry/markdown-rag-chunker/pricing?fpr=use-apify) tab)
- **$0.001 per run**, charged once for each run of up to 1 GB memory
- Compute, storage and proxy traffic are included in these prices
- A run skipped by **Skip if unchanged** pushes no chunks and costs only the $0.001 run fee
- Run metrics in the Output record are free
- You can set a maximum charge per run in Apify; the Actor stops delivering chunks at that limit and reports it in the Output

| Input | Chunks (approx.) | Cost (approx.) |
| --- | --- | --- |
| 1-page Markdown snippet | 3 | $0.0034 |
| 10-page HTML article | 15 | $0.013 |
| 40-page PDF | 60 | $0.049 |
| 200-page technical manual | 300 | $0.241 |

## Input

```json
{
  "markdown_file": "https://en.wikipedia.org/wiki/Giant_panda",
  "headers_to_split_on": ["#", "##", "###"],
  "max_chunk_chars": 1600,
  "skip_if_unchanged": false
}
```

Send `markdown_file` (a URL) or `markdown_text` (Markdown). The Actor detects which one you used; set `input_mode` to `file` or `text` only if you send both. Every field is described under the [Input](https://apify.com/codepoetry/markdown-rag-chunker/input-schema?fpr=use-apify) tab.

## Output

Each chunk is one dataset item:

```json
{
  "content": "Giant pandas live in a few mountain ranges in central China...",
  "metadata": { "Header 1": "Giant panda", "Header 2": "Distribution and habitat" },
  "chunk_id": "f8b6be2adf7f6dbf",
  "char_count": 1342,
  "token_count": 336
}
```

Run totals are saved as the run's named **Output**:

```json
{
  "input_mode": "file",
  "input_file_type": "text/html; charset=utf-8",
  "input_chars": 255710,
  "chunk_count": 22,
  "elapsed_ms": 2140,
  "document_hash": "ee68cce8b9dd2138",
  "unchanged": false,
  "charge_limit_reached": false
}
```

| Field | Where | Description |
| --- | --- | --- |
| `content` | dataset item | Chunk text for your embedding model |
| `metadata` | dataset item | Header path (`Header 1`, `Header 2`, …) for context-aware retrieval |
| `chunk_id` | dataset item | Stable 16-character ID for idempotent upserts and deduplication |
| `char_count` | dataset item | Character length of `content` |
| `token_count` | dataset item | Approximate token count (about 4 characters per token) |
| `input_file_type` | Output | Source MIME type (for example `application/pdf`) |
| `chunk_count` | Output | Chunks delivered in this run |
| `document_hash` | Output | Fingerprint of the converted document; compare across runs to detect changes |
| `unchanged` / `cached_chunk_count` | Output | Set when Skip if unchanged skipped the run |
| `charge_limit_reached` | Output | `true` if your maximum charge per run stopped delivery early |

## Use the API from Python, JavaScript or curl

**Python**

```python
from apify_client import ApifyClient

client = ApifyClient("YOUR_APIFY_TOKEN")
run = client.actor("codepoetry/markdown-rag-chunker").call(run_input={
    "markdown_file": "https://example.com/docs/handbook.pdf",
    "headers_to_split_on": ["#", "##", "###"],
})
for item in client.dataset(run["defaultDatasetId"]).iterate_items():
    print(item["chunk_id"], item["metadata"], item["token_count"])
```

**JavaScript**

```javascript
import { ApifyClient } from 'apify-client';

const client = new ApifyClient({ token: 'YOUR_APIFY_TOKEN' });
const run = await client.actor('codepoetry/markdown-rag-chunker').call({
    markdown_file: 'https://example.com/docs/handbook.pdf',
    headers_to_split_on: ['#', '##', '###'],
});
const { items } = await client.dataset(run.defaultDatasetId).listItems();
```

**curl**

```bash
curl -X POST "https://api.apify.com/v2/acts/codepoetry~markdown-rag-chunker/run-sync-get-dataset-items?token=YOUR_APIFY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"markdown_text":"# Title\nSome content"}'
```

Ready-to-paste code for every client, including [MCP](https://apify.com/codepoetry/markdown-rag-chunker/api/mcp?fpr=use-apify) for AI agents, is on the [API](https://apify.com/codepoetry/markdown-rag-chunker/api?fpr=use-apify) tab.

## Supported input formats

| Category | Formats |
| --- | --- |
| Web pages | HTML (paste any page URL) |
| Markdown and text | `.md`, `.txt` |
| PDF | `.pdf` (text-based; scanned PDFs need OCR first) |
| Office | `.docx`, `.pptx`, `.xlsx`, `.xls` |
| Data | `.csv`, `.json`, `.xml` |
| Books | `.epub` |

Files are downloaded from an `http(s)://` URL, up to 25 MB. To read a text record from an [Apify key-value store](https://docs.apify.com/platform/storage/key-value-store), use `kvs://KEY` and select the store in the **Key-value store** field; the run gets read-only access to that store.

## Proxies

Downloads go direct by default. In file mode you can also use:

- **Use Apify Proxy** (datacenter), for sites that block direct requests or rate-limit you
- **Custom proxy URL**, your own `http://user:pass@host:8080`, `https://host:8080` or `socks5://host:1080`, stored encrypted

Proxy traffic is included in the price, and the two options are mutually exclusive.

## Markdown RAG Chunker vs Firecrawl and fixed-size splitters

| | Markdown RAG Chunker | Firecrawl | Fixed-size splitters |
| --- | --- | --- | --- |
| Pricing | $0.80 per 1,000 chunks + $0.001 per run | Monthly subscription + credits | Free (you run it) |
| Formats | PDF, HTML, DOCX, PPTX, XLSX, EPUB, CSV, JSON, XML, MD | Web pages, PDF | Whatever you wire up |
| Splitting | Header-aware with a size cap; header path in metadata | Markdown output, chunking left to you | Fixed windows that ignore structure |
| Stable IDs for vector upserts | Built in | No | You build them |
| JS-rendered sites and site crawls | No (pair with [Website Content Crawler](https://apify.com/apify/website-content-crawler?fpr=use-apify)) | Yes | No |
| Runs on | Apify (scheduling, API, MCP, storage included) | Firecrawl platform or self-hosted | Your infrastructure |

If you need JavaScript rendering and full-site crawls in one product, Firecrawl fits. If you already have documents or Markdown and want deterministic, header-aware chunks with stable IDs, or you want to chunk [Website Content Crawler](https://apify.com/apify/website-content-crawler?fpr=use-apify) output on Apify, use this Actor.

## Use cases

- **RAG over documentation**: split product docs and changelogs into retrievable sections
- **Knowledge bases**: load internal PDFs and Word documents into a vector store with stable IDs
- **Support search**: chunk help-center articles for semantic search
- **AI agents**: give agents large reference documents in budget-sized pieces, also through MCP
- **Crawler post-processing**: run after Website Content Crawler to turn crawled pages into chunks

## Related Actors

- [Website Content Crawler](https://apify.com/apify/website-content-crawler?fpr=use-apify): crawl whole sites to Markdown, then chunk the output here
- [RAG Web Browser](https://apify.com/apify/rag-web-browser?fpr=use-apify): search the web and fetch pages as Markdown for LLMs

## FAQ

### What chunk size should I use for RAG?

Start with the default 1,600 characters (about 400 tokens). Chroma's 2024 evaluation found recursive chunks of 200 to 400 tokens consistently strong, with smaller chunks more precise and larger ones better at recall. Go lower (800 to 1,000 characters) for short factual lookups and higher for long-form answers. Sections shorter than the cap are never split.

### How do I chunk a PDF for a vector database?

Paste the PDF URL into **Document URL** and run the Actor. The PDF is converted to Markdown, split by its headings, and each chunk gets a deterministic `chunk_id` to use as the upsert key in [Pinecone](https://www.pinecone.io/), [Qdrant](https://qdrant.tech/), [Weaviate](https://weaviate.io/) or [pgvector](https://github.com/pgvector/pgvector). Text-based PDFs work directly; scanned PDFs need OCR first.

### How do I convert a web page to Markdown chunks?

Paste the page URL into **Document URL**. The page is downloaded with a real Chrome TLS fingerprint, converted from HTML to Markdown, and split by its headings.

### Why are some chunks smaller than the cap?

The cap is a maximum. A section shorter than the cap stays whole so it is never cut mid-thought; only longer sections are re-split.

### Does it work with LangChain or LlamaIndex?

Yes. Splitting uses [LangChain's text splitters](https://docs.langchain.com/oss/python/integrations/splitters/markdown_header_metadata_splitter), and each item maps onto a LangChain `Document` (`content` as `page_content`, plus `metadata`) or a LlamaIndex [`Node`](https://docs.llamaindex.ai/en/stable/module_guides/loading/documents_and_nodes/). Apify also has [LlamaIndex](https://docs.apify.com/platform/integrations/llama) and LangChain integrations.

### Why is my dataset empty after a run?

If **Skip if unchanged** is on and the document and chunking settings match the previous run, nothing is pushed and the Output shows `unchanged: true` with the earlier `chunk_count`. That run costs only the $0.001 start fee. Turn the option off to push the chunks again.

### What happens when I reach my maximum charge per run?

The Actor delivers chunks up to your limit, then stops. The Output shows `charge_limit_reached: true` and the number of chunks delivered, and the run is not cached, so the next run processes the document again.

### Can I chunk a document stored in Apify?

Yes. Set **Document URL** to `kvs://KEY` and pick the store in **Key-value store**. The record must be plain text or Markdown.

### How accurate is `token_count`?

It is an estimate (about 4 characters per token) for budgeting. For exact counts, run your model's tokenizer on `content`.

## Support

Report bugs or request features in the [Issues](https://apify.com/codepoetry/markdown-rag-chunker/issues?fpr=use-apify) tab or on [GitHub](https://github.com/yel-hadd/markdown-rag-chunker/issues).

## What's new

### v1.3 (October 2026)

- **Better default chunks**: oversized sections are capped at 1,600 characters (about 400 tokens) unless you set another size; send `null` to turn the cap off via the API
- **Input mode detected automatically** from `markdown_text` or `markdown_file`, so API calls with only Markdown text just work
- **More accurate conversion**: the server's content type and charset reach the converter (CSV becomes a table, Latin-1 pages keep their accents), and the page URL enables markitdown's Wikipedia-aware converter
- **Faster change detection**: an unchanged remote file is no longer re-converted
- **Files up to 25 MB**, streamed with a hard size limit instead of a text-based estimate that rejected image-heavy PDFs
- **Apify storage input fixed**: `kvs://KEY` now reads from a store you select
- **Unique IDs for repeated sections** and honest Output when your maximum charge per run stops delivery
- Simple, all-inclusive pricing: $0.80 per 1,000 chunks plus $0.001 per run

### v1.2

- **Change detection**: every run returns a `document_hash`, and **Skip if unchanged** skips re-pushing unchanged documents. Changed chunking settings always re-process
- Prefilled input form for faster setup in Apify Console

### v1.1

- One dataset item per chunk, so billing matches the per-chunk price exactly
- Run metrics moved to the run's named **Output**
- Chrome-fingerprinted downloads with [curl_cffi](https://github.com/lexiforest/curl_cffi), plus Apify Proxy or your own proxy

### v1.0

- Initial release: multi-format conversion, header-aware splitting, deterministic chunk IDs, token estimates
