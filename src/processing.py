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

from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from .models import MarkdownChunk, MarkdownSplitterInput

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


def cache_record_key(params: MarkdownSplitterInput, doc_hash: str) -> str:
    """Key of this source's change-detection record in the cache store.

    File-mode sources are keyed by their URL/``kvs://`` string so an edited
    document overwrites its previous state; text mode has no source name,
    so it is keyed by the document hash (a hit means "already chunked").
    """
    source = (
        (params.markdown_file or "").strip()
        if params.input_mode == "file"
        else f"hash:{doc_hash}"
    )
    return "state-" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]


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
            },
            sort_keys=True,
        ).encode("utf-8")
    )


def split_documents(params: MarkdownSplitterInput, markdown_text: str) -> list[Any]:
    """Split resolved Markdown into LangChain documents per the input settings.

    Maps each header level to its ``Header N`` metadata key, drops sections
    whose content ends up empty (a header with no body must not become a
    billed zero-character chunk), re-splits **only** oversized sections when
    ``max_chunk_chars`` is set (10% overlap), and enforces ``max_chunks``.
    """
    headers = [(h, f"Header {h.count('#')}") for h in params.headers_to_split_on]
    documents = MarkdownHeaderTextSplitter(
        headers_to_split_on=headers, strip_headers=params.strip_headers
    ).split_text(markdown_text)
    documents = [d for d in documents if (d.page_content or "").strip()]

    if params.max_chunk_chars:
        cap = params.max_chunk_chars
        # Lower bound on the final count: an oversized section needs at least
        # ceil(len / cap) pieces. Failing here avoids building a huge list of
        # documents (and their metadata copies) just to reject it.
        minimum = sum(
            max(1, math.ceil(len(d.page_content or "") / cap)) for d in documents
        )
        if minimum > params.max_chunks:
            raise ValueError(
                f"Chunk count would be at least {minimum}, which exceeds "
                f"max_chunks ({params.max_chunks})."
            )
        overlap = min(cap // 10, cap - 1)
        resizer = RecursiveCharacterTextSplitter(
            chunk_size=cap,
            chunk_overlap=overlap,
            separators=["\n\n", "\n", " ", ""],
        )
        resized: list[Any] = []
        for document in documents:
            if len(document.page_content or "") <= cap:
                resized.append(document)
            else:
                resized.extend(resizer.split_documents([document]))
        documents = resized

    if len(documents) > params.max_chunks:
        raise ValueError(
            f"Chunk count {len(documents)} exceeds max_chunks ({params.max_chunks})."
        )
    return documents


def format_chunks(documents: list[Any]) -> list[MarkdownChunk]:
    """Convert LangChain documents into ``MarkdownChunk`` output records.

    Each chunk gets a deterministic ``chunk_id`` — the first 16 hex
    characters of the SHA-256 over the sorted-key JSON of its content and
    metadata — so re-running the same input yields the same IDs and
    downstream upserts stay idempotent. Repeats of an identical chunk also
    hash their occurrence number, so every pushed chunk has a unique ID.
    """
    chunks: list[MarkdownChunk] = []
    seen: dict[str, int] = {}
    for doc in documents:
        content = doc.page_content or ""
        metadata = doc.metadata or {}
        payload: dict[str, Any] = {"content": content, "metadata": metadata}
        identity = json.dumps(payload, ensure_ascii=True, sort_keys=True)
        occurrence = seen.get(identity, 0)
        seen[identity] = occurrence + 1
        if occurrence:
            payload["occurrence"] = occurrence
        hash_input = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        chunk_id = hashlib.sha256(hash_input).hexdigest()[:16]
        char_count = len(content)
        token_count = math.ceil(char_count / 4) if char_count else 0
        chunks.append(
            MarkdownChunk(
                content=content,
                metadata=metadata,
                chunk_id=chunk_id,
                char_count=char_count,
                token_count=token_count,
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
        raise ValueError("Converted file content is empty.")
    return markdown_text
