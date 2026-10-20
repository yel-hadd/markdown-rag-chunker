"""Pure chunking logic, independent of the Apify runtime.

All external effects (HTTP fetches, key-value store reads, format
conversion) are injected as callables, so every function here is
unit-testable without mocks or network access.
"""

import functools
import hashlib
import io
import inspect
import json
import math
import posixpath
from typing import Any, AsyncIterable, Awaitable, Callable
from urllib.parse import urlsplit

import tiktoken
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from .models import MarkdownChunk, MarkdownSplitterInput

#: Recursive split boundaries, coarse to fine: paragraphs, lines, CJK and
#: Latin sentence ends, then words and characters.
#: Sentence ends are zero-width lookbehinds so the punctuation stays on the
#: preceding chunk; spaces start the next piece (" word"), which tokenizers
#: count as one token, so merged sizes match the real token count.
_SEPARATORS = ["\n\n", "\n", "(?<=[。！？；])", "(?<=[.!?] )", " ", ""]

#: Length (hex characters) of the short hashes exposed in output records.
_SHORT_HASH_LENGTH = 16


def _short_hash(payload: bytes) -> str:
    """First 16 hex characters of the SHA-256 of ``payload``."""
    return hashlib.sha256(payload).hexdigest()[:_SHORT_HASH_LENGTH]


def document_hash(markdown_text: str) -> str:
    """Stable fingerprint of a resolved document, pre-splitting.

    Exposed in the run Output so users can detect changes between runs
    and skip re-chunking (and re-paying) when nothing changed.
    """
    return _short_hash(markdown_text.encode("utf-8"))


def document_source(params: MarkdownSplitterInput) -> str | None:
    """Stable identity of the document, exposed as each chunk's ``source``.

    ``source_id`` wins; otherwise the URL, or ``kvs://<store>/<key>`` so the
    same key in two stores stays distinct. Pasted text without ``source_id``
    has no identity (``None``).
    """
    if params.source_id:
        return params.source_id
    if params.input_mode != "file":
        return None
    source = (params.markdown_file or "").strip()
    if source.startswith("kvs://"):
        return f"kvs://{params.kvs_store}/{source.removeprefix('kvs://')}"
    return source


def cache_record_key(params: MarkdownSplitterInput, doc_hash: str) -> str:
    """Key of this source's change-detection record in the cache store.

    Documents with an identity (URL, ``kvs://``, ``source_id``) are keyed by
    it, so an edited document overwrites its previous state; pasted text
    without ``source_id`` is keyed by its hash (a hit means "already chunked").
    """
    source = document_source(params) or f"hash:{doc_hash}"
    identity = json.dumps([params.cache_namespace, source])
    return "state-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


#: Retries for transient download failures, and the longest wait honoured.
_MAX_RETRIES = 2
_MAX_RETRY_WAIT_SECONDS = 5.0


def retry_delay(status: int | None, retry_after: str | None, attempt: int) -> float | None:
    """Seconds to wait before retrying a download, or ``None`` to give up.

    Transport errors (``status`` None), rate limiting (429) and server
    errors (5xx) are transient. Waits back off exponentially (1 s, 2 s) or
    follow a numeric ``Retry-After``, capped so a slow server cannot burn
    the run's time and compute.
    """
    transient = status is None or status == 429 or 500 <= status < 600
    if attempt >= _MAX_RETRIES or not transient:
        return None
    wait = 2.0**attempt
    try:
        requested = float(retry_after) if retry_after else None
    except ValueError:
        requested = None
    if requested is not None and math.isfinite(requested) and requested >= 0:
        wait = requested
    return min(wait, _MAX_RETRY_WAIT_SECONDS)


def http_error_message(status: int, url: str) -> str:
    """Actionable message for a failed download."""
    if status in (401, 403, 429):
        return (
            f"The source returned HTTP {status} (blocked or rate-limited): {url}. "
            "Retry with use_apify_proxy: true or your own proxy_url."
        )
    return f"The source returned HTTP {status}: {url}. Check that the URL is public."


def validation_error_message(error: Any) -> str:
    """One-line summary of a pydantic ValidationError, one clause per field."""
    parts = []
    for item in error.errors():
        message = str(item.get("msg", "")).removeprefix("Value error, ")
        field = ".".join(str(part) for part in item.get("loc", ()))
        parts.append(f"{field}: {message}" if field else message)
    return "Invalid input: " + "; ".join(parts)


async def read_capped(parts: AsyncIterable[bytes], limit: int) -> bytes:
    """Join streamed byte parts, failing as soon as ``limit`` is exceeded.

    Keeps memory bounded for downloads without (or with a lying)
    Content-Length header, and for compressed bodies that expand.
    """
    buffer = bytearray()
    async for part in parts:
        buffer += part
        if len(buffer) > limit:
            raise ValueError(
                f"Remote file is larger than {limit // 1_000_000} MB, the download limit."
            )
    return bytes(buffer)


def source_fingerprint(raw_bytes: bytes, content_type: str) -> str:
    """Fingerprint of a fetched file before conversion.

    Lets change detection skip the (expensive) conversion when the raw
    download is byte-identical. The content type is included because it
    steers conversion (format and charset); JSON encoding keeps the two
    fields unambiguous.
    """
    header = json.dumps(content_type).encode("utf-8")
    return _short_hash(header + b"\n" + raw_bytes)


def cache_hit(
    state: Any,
    split_config: str,
    *,
    document_hash: str | None = None,
    source_hash: str | None = None,
) -> bool:
    """Whether a cached change-detection state matches the current run.

    Split settings must match, plus either the raw source fingerprint
    (checked before conversion) or the converted document hash.
    """
    if not isinstance(state, dict) or state.get("split_config") != split_config:
        return False
    return any(
        value is not None and state.get(key) == value
        for key, value in (("source_hash", source_hash), ("document_hash", document_hash))
    )


def split_config_fingerprint(params: MarkdownSplitterInput) -> str:
    """Fingerprint of every input that affects how text is split.

    Guards the change-detection cache: if the user changes splitting
    settings, chunks differ even for an unchanged document, so the cache
    must not report a hit. Inputs that do not affect splitting (the text
    itself, limits, proxy settings) are deliberately excluded.
    """
    return _short_hash(
        json.dumps(
            {
                "headers_to_split_on": params.headers_to_split_on,
                "strip_headers": params.strip_headers,
                "max_chunk_chars": params.max_chunk_chars,
                # The token cap is ignored when a character cap is set.
                "max_chunk_tokens": None if params.max_chunk_chars else params.max_chunk_tokens,
                "tokenizer": params.tokenizer,
            },
            sort_keys=True,
        ).encode("utf-8")
    )


@functools.cache
def _encoding(name: str) -> tiktoken.Encoding:
    """Load a tiktoken encoding once (files are baked into the image)."""
    return tiktoken.get_encoding(name)


def count_tokens(text: str, tokenizer: str) -> int:
    """Exact token count of ``text`` for a tiktoken encoding name."""
    return len(_encoding(tokenizer).encode(text, disallowed_special=())) if text else 0


#: Section and chunk counts, shared by split_documents and format_chunks so
#: an unsplit section is tokenized once (the splitter's many small pieces
#: use the uncached count_tokens).
_count_tokens_cached = functools.lru_cache(maxsize=16_384)(count_tokens)


def split_documents(params: MarkdownSplitterInput, markdown_text: str) -> list[Any]:
    """Split resolved Markdown into LangChain documents per the input settings.

    Maps each header level to its ``Header N`` metadata key, drops sections
    whose content ends up empty (a header with no body must not become a
    billed zero-character chunk), re-splits **only** sections over the size
    cap (``max_chunk_chars`` if set, else ``max_chunk_tokens``; 10% overlap),
    and enforces ``max_chunks``.
    """
    headers = [(h, f"Header {h.count('#')}") for h in params.headers_to_split_on]
    documents = MarkdownHeaderTextSplitter(
        headers_to_split_on=headers, strip_headers=params.strip_headers
    ).split_text(markdown_text)
    documents = [d for d in documents if (d.page_content or "").strip()]

    if params.max_chunk_chars:
        cap: int | None = params.max_chunk_chars
        measure: Callable[[str], int] = len
    else:
        cap = params.max_chunk_tokens
        measure = functools.partial(count_tokens, tokenizer=params.tokenizer)

    if cap:
        size_of = (
            measure
            if params.max_chunk_chars
            else functools.partial(_count_tokens_cached, tokenizer=params.tokenizer)
        )
        sizes = [size_of(d.page_content or "") for d in documents]
        # Lower bound on the final count: an oversized section needs at least
        # ceil(size / cap) pieces. Failing here avoids building a huge list of
        # documents (and their metadata copies) just to reject it.
        minimum = sum(max(1, math.ceil(size / cap)) for size in sizes)
        if minimum > params.max_chunks:
            raise ValueError(
                f"Chunk count would be at least {minimum}, which exceeds "
                f"max_chunks ({params.max_chunks})."
            )
        def resplit(document: Any, target: int) -> list[Any]:
            resizer = RecursiveCharacterTextSplitter(
                chunk_size=target,
                chunk_overlap=min(target // 10, target - 1),
                separators=_SEPARATORS,
                is_separator_regex=True,
                length_function=measure,
            )
            pieces: list[Any] = []
            for piece in resizer.split_documents([document]):
                # Pieces are sized separately; joined text can tokenize a few
                # tokens longer, so re-split the rare piece that overshoots.
                excess = measure(piece.page_content) - cap
                pieces.extend(resplit(piece, max(10, target - excess - 8)) if excess > 0 and target > 10 else [piece])
            return pieces

        resized: list[Any] = []
        for document, size in zip(documents, sizes):
            resized.extend([document] if size <= cap else resplit(document, cap))
        documents = resized

    if len(documents) > params.max_chunks:
        raise ValueError(
            f"Chunk count {len(documents)} exceeds max_chunks ({params.max_chunks})."
        )
    return documents


def header_path(metadata: dict[str, Any]) -> str:
    """Readable section path, e.g. ``"Guide > Install > Windows"``.

    Ordered by header level (``Header 1``, ``Header 2``, …), not by dict
    order; empty when the chunk sits above the first header.
    """
    levels = sorted(
        (int(key.removeprefix("Header ")), str(value))
        for key, value in metadata.items()
        if key.startswith("Header ") and key.removeprefix("Header ").isdigit()
    )
    return " > ".join(value for _, value in levels)


def format_chunks(
    documents: list[Any],
    source: str | None = None,
    document_hash: str = "",
    tokenizer: str = "cl100k_base",
    prepend_header_path: bool = True,
) -> list[MarkdownChunk]:
    """Convert LangChain documents into ``MarkdownChunk`` output records.

    Each chunk gets a deterministic ``chunk_id``: the first 16 hex
    characters of the SHA-256 over the sorted-key JSON of its scope, content
    and metadata. The scope is the source (URL / ``kvs://KEY``), or the
    document hash for pasted text, so identical sections in different
    documents never share an ID, while untouched sections keep theirs when
    another part of the same file changes. Repeats of an identical chunk
    within a document also hash their occurrence number. ``token_count`` is
    an exact count with ``tokenizer``. ``embedding_text`` prepends the
    header path unless ``prepend_header_path`` is off (headers kept in
    ``content``, so the path would repeat them).
    """
    scope = source or f"text:{document_hash}"
    chunks: list[MarkdownChunk] = []
    seen: dict[str, int] = {}
    for index, doc in enumerate(documents):
        content = doc.page_content or ""
        metadata = doc.metadata or {}
        payload: dict[str, Any] = {"scope": scope, "content": content, "metadata": metadata}
        identity = json.dumps(payload, ensure_ascii=True, sort_keys=True)
        occurrence = seen.get(identity, 0)
        seen[identity] = occurrence + 1
        if occurrence:
            payload["occurrence"] = occurrence
        hash_input = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        path = header_path(metadata)
        chunks.append(
            MarkdownChunk(
                content=content,
                metadata=metadata,
                header_path=path,
                embedding_text=f"{path}\n\n{content}" if path and prepend_header_path else content,
                chunk_id=hashlib.sha256(hash_input).hexdigest()[:16],
                chunk_index=index,
                chunk_total=len(documents),
                source=source,
                document_hash=document_hash,
                char_count=len(content),
                token_count=_count_tokens_cached(content, tokenizer),
            )
        )
    return chunks


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` if it is awaitable, otherwise return it unchanged.

    Lets callers inject either sync or async callables for I/O.
    """
    if inspect.isawaitable(value):
        return await value
    return value


async def resolve_markdown_text(
    params: MarkdownSplitterInput,
    fetch_remote: Callable[[str], Awaitable[tuple[bytes, str]] | tuple[bytes, str]],
    load_kvs: Callable[[str], Awaitable[str] | str],
    convert_remote_file: Callable[[bytes, str], str] | None = None,
) -> tuple[str, str]:
    """Resolve raw input into ``(markdown_text, input_file_type)``.

    Text mode returns the input as-is. File mode accepts an ``http(s)://``
    URL (fetched and converted to Markdown from the response MIME type) or
    a ``kvs://KEY`` record (plain string, passed through unconverted).
    Raises ``ValueError`` on unsupported schemes, empty results, or inputs
    over ``max_input_chars``.
    """
    if params.input_mode == "text":
        if params.markdown_text is None:
            raise ValueError("markdown_text is required in text mode.")
        return params.markdown_text, "text/markdown"

    if params.markdown_file is None:
        raise ValueError("markdown_file is required in file mode.")

    source = params.markdown_file.strip()
    markdown_text: Any
    input_file_type = "text/markdown"
    if source.startswith(("https://", "http://")):
        remote_payload = await _maybe_await(fetch_remote(source))
        if not isinstance(remote_payload, tuple) or len(remote_payload) != 2:
            raise ValueError(
                "Remote fetch must return a (raw_bytes, content_type) tuple."
            )
        raw_bytes, content_type = remote_payload
        if not isinstance(raw_bytes, (bytes, bytearray)):
            raise ValueError("Remote file payload must be bytes.")
        if not isinstance(content_type, str):
            raise ValueError("Remote file content type must be a string.")
        input_file_type = content_type.strip().lower() or "application/octet-stream"
        converter = convert_remote_file or functools.partial(convert_to_markdown, url=source)
        markdown_text = converter(bytes(raw_bytes), input_file_type)
    elif source.startswith("kvs://"):
        key = source.removeprefix("kvs://")
        if not key:
            raise ValueError("kvs:// source must include a key name.")
        markdown_text = await _maybe_await(load_kvs(key))
    else:
        raise ValueError(
            "Unsupported markdown_file source. Use http(s)://... or kvs://KEY."
        )

    if not isinstance(markdown_text, str):
        raise ValueError("Resolved markdown file must be a UTF-8 string.")
    if not markdown_text.strip():
        raise ValueError("Resolved markdown file content is empty.")
    if len(markdown_text) > params.max_input_chars:
        raise ValueError(
            f"Input exceeds max_input_chars ({params.max_input_chars}) in file mode."
        )
    return markdown_text, input_file_type


def convert_to_markdown(raw_bytes: bytes, content_type: str, url: str | None = None) -> str:
    """Convert a downloaded file to Markdown with markitdown.

    The declared MIME type, charset, URL and file extension are passed as
    hints (markitdown still sniffs the bytes); ``application/octet-stream``
    is treated as unknown because servers send it for any binary file.
    """
    try:
        from markitdown import MarkItDown, StreamInfo
    except ImportError as exc:  # pragma: no cover - dependency-level failure
        raise RuntimeError(
            "markitdown is required for URL file conversion. "
            "Install dependencies from requirements.txt."
        ) from exc

    mimetype, _, params = content_type.partition(";")
    mimetype = mimetype.strip().lower()
    charset = None
    for param in params.split(";"):
        name, _, value = param.partition("=")
        if name.strip().lower() == "charset" and value.strip():
            charset = value.strip().strip('"')
    extension = posixpath.splitext(urlsplit(url).path)[1].lower() if url else ""

    converter = MarkItDown(enable_plugins=False)
    result = converter.convert_stream(
        io.BytesIO(raw_bytes),
        stream_info=StreamInfo(
            mimetype=None if mimetype in ("", "application/octet-stream") else mimetype,
            charset=charset,
            extension=extension or None,
            url=url,
        ),
    )
    markdown_text = result.text_content if result else None
    if markdown_text is None or not markdown_text.strip():
        raise ValueError(
            "Converted file content is empty. Scanned PDFs and images contain no "
            "text layer; run OCR first."
        )
    return markdown_text
