# Markdown RAG Chunker

**Markdown RAG Chunker** turns PDFs, web pages, Word, Excel, PowerPoint, and Markdown files into clean, **header-aware chunks** ready for embeddings and vector databases. Built for AI engineers shipping RAG pipelines who want predictable splits, stable IDs, and token estimates without writing chunking code. [Run the Actor in Apify Console](https://console.apify.com/actors/84h4GucI0gbo1SrN7?addFromActorId=84h4GucI0gbo1SrN7).

## What does Markdown RAG Chunker do?

- **Multi-format input**: convert PDF, HTML, DOCX, XLSX, PPTX, CSV, JSON, XML, EPUB, and plain text to Markdown before chunking
- **Header-aware splitting**: split by `#` through `######` so each chunk keeps its parent section in metadata
- **Token-aware sizing**: optional `max_chunk_chars` re-splits only oversized sections, so retrieval stays focused
- **Deterministic chunk IDs**: every chunk has a stable `chunk_id` for idempotent upserts and deduplication in [Pinecone](https://www.pinecone.io/), [Qdrant](https://qdrant.tech/), [Weaviate](https://weaviate.io/), [pgvector](https://github.com/pgvector/pgvector), and others
- **Token count estimates**: `token_count` per chunk for embedding budget planning
- **Run telemetry**: `metrics` payload with `input_file_type`, `chunk_count`, and `elapsed_ms` for monitoring
- **Two input modes**: paste Markdown directly, or pass an HTTPS URL or `kvs://KEY` for any supported file
- **Pay only when you use it**: pay-per-event pricing — no monthly rental

Behind the scenes the Actor uses Microsoft's [markitdown](https://github.com/microsoft/markitdown) for format conversion and [LangChain's text splitters](https://python.langchain.com/docs/how_to/markdown_header_metadata_splitter/) (`MarkdownHeaderTextSplitter` plus `RecursiveCharacterTextSplitter`) for chunking. You get production-grade defaults without managing the dependencies yourself.

## Supported input formats

| Category | Formats |
| --- | --- |
| Markdown and text | `.md`, `.txt` |
| Web | HTML pages, single web page URLs |
| PDF | `.pdf` |
| Office | `.docx`, `.xlsx`, `.xls`, `.pptx` |
| Data | `.csv`, `.json`, `.xml` |
| Books | `.epub` |

Provide any of these as an HTTPS URL or as an [Apify Key-Value Store](https://docs.apify.com/platform/storage/key-value-store) record (`kvs://KEY`). MIME type is detected automatically from the response and reported back in `metrics.input_file_type`.

## How to use Markdown RAG Chunker

1. Open the Actor and choose `input_mode`. Pick `text` to paste Markdown directly, or `file` to load any supported document from a URL or `kvs://KEY`.
2. Set `headers_to_split_on`. Most RAG pipelines work best with `["#", "##", "###"]` — that gives you section-level chunks while preserving page-level context in metadata.
3. Optionally set `max_chunk_chars` (for example, `1800` characters or about 450 tokens) to cap oversized sections. Only chunks above the cap are re-split, so well-sized sections are preserved untouched.
4. Run the Actor and read results from the dataset. Pipe each item's `content` into your embedding model and store the `chunk_id` and `metadata` alongside the vector.

The full input form is documented under the [Input](https://apify.com/codepoetry/markdown-rag-chunker/input-schema) tab. Run output schema and field types live under the [Output](https://apify.com/codepoetry/markdown-rag-chunker/output-schema) tab.

## Output format

```json
{
  "chunks": [
    {
      "content": "Install the SDK with pip install ...",
      "metadata": { "Header 1": "Guide", "Header 2": "Install" },
      "chunk_id": "f8b6be2adf7f6dbf",
      "char_count": 124,
      "token_count": 31
    }
  ],
  "metrics": {
    "input_mode": "file",
    "input_file_type": "application/pdf",
    "input_chars": 4281,
    "chunk_count": 12,
    "elapsed_ms": 184
  }
}
```

| Field | Description |
| --- | --- |
| `content` | Chunk text to send to your embedding model |
| `metadata` | Header hierarchy (`Header 1`, `Header 2`, ...) for context-aware retrieval |
| `chunk_id` | Stable 16-char ID for idempotent upserts and deduplication |
| `char_count` | Character length of `content` |
| `token_count` | Approximate token count (~1 token per 4 characters) |
| `metrics.input_file_type` | Detected source MIME type (for example, `application/pdf`) |
| `metrics.chunk_count` | Total chunks produced in the run |
| `metrics.elapsed_ms` | End-to-end processing time |

## How much does document chunking cost?

This Actor uses [pay-per-event pricing](https://docs.apify.com/platform/actors/publishing/monetize/pay-per-event) — one simple unit, no surprises. **You pay per chunk** produced, regardless of input format.

- **$0.80 per 1,000 chunks** (FREE / BRONZE tier; lower on higher tiers)
- No monthly fee — if you don't run it, you pay nothing
- Same price whether you send Markdown text, a PDF URL, a Word doc, or an HTML page

**Example scenarios:**

| Input | Chunks produced | Approx. cost |
| --- | --- | --- |
| 1-page Markdown snippet | 3 | $0.0024 |
| 10-page HTML article | 15 | $0.012 |
| 40-page PDF | 60 | $0.048 |
| 200-page technical manual | 300 | $0.24 |

Exact unit prices per subscription tier are on the [Pricing](https://apify.com/codepoetry/markdown-rag-chunker/pricing) tab.

## Use cases

- **RAG over documentation**: split long product docs and changelogs into retrievable sections
- **Knowledge bases**: ingest internal PDFs and Word docs into a vector store with stable IDs
- **Customer support search**: chunk help center articles for semantic search
- **AI agents**: feed large reference documents to agents in budget-friendly slices
- **Crawler post-processing**: chain after [Website Content Crawler](https://apify.com/apify/website-content-crawler) to convert crawled pages into RAG-ready chunks

## FAQ

### How do I chunk a PDF for a vector database?

Set `input_mode` to `file`, paste the PDF URL into `markdown_file`, and run the Actor. The PDF is converted to Markdown via [markitdown](https://github.com/microsoft/markitdown), split by header hierarchy, and each chunk gets a deterministic `chunk_id` you can use as the upsert key in [Pinecone](https://www.pinecone.io/), [Qdrant](https://qdrant.tech/), [Weaviate](https://weaviate.io/), or [pgvector](https://github.com/pgvector/pgvector).

### What is the difference between header-aware chunking and fixed-size chunking?

Fixed-size chunking cuts text every N characters or tokens, often slicing through paragraphs and losing structural context. Header-aware chunking splits on Markdown headings, so each chunk maps to a logical section and the parent header chain is kept in `metadata`. This gives retrieval models real context — a chunk about "Authentication" still knows it lives under the "API Reference" section.

### Can I chain Markdown RAG Chunker with a web crawler?

Yes. A common pipeline is **[Website Content Crawler](https://apify.com/apify/website-content-crawler) → Markdown RAG Chunker → embeddings → vector DB**. The crawler produces clean Markdown for each page, and this Actor splits that Markdown into RAG-ready chunks with stable IDs.

### Does it work with LangChain or LlamaIndex?

Yes. The chunking is built on [LangChain text splitters](https://python.langchain.com/docs/how_to/markdown_header_metadata_splitter/), and the output (`content` + `metadata`) maps cleanly onto a LangChain [`Document`](https://python.langchain.com/api_reference/core/documents/langchain_core.documents.base.Document.html) or LlamaIndex [`Node`](https://docs.llamaindex.ai/en/stable/module_guides/loading/documents_and_nodes/). You can also use Apify's [LangChain](https://python.langchain.com/docs/integrations/providers/apify/) and [LlamaIndex](https://docs.apify.com/platform/integrations/llama) integrations directly inside both frameworks.

### How accurate is `token_count`?

`token_count` is a fast estimate (~1 token per 4 characters) intended for budget planning and guardrails. For exact token counts, run your model's tokenizer over `content` after retrieval.

### Where do I find API examples?

Use the [API](https://apify.com/codepoetry/markdown-rag-chunker/api), [Python](https://apify.com/codepoetry/markdown-rag-chunker/api/python), [JavaScript](https://apify.com/codepoetry/markdown-rag-chunker/api/javascript), [CLI](https://apify.com/codepoetry/markdown-rag-chunker/api/cli), [OpenAPI](https://apify.com/codepoetry/markdown-rag-chunker/api/openapi), and [MCP](https://apify.com/codepoetry/markdown-rag-chunker/api/mcp) tabs on this Actor's page — they include ready-to-paste code with the correct Actor ID and input shape for every supported client.
