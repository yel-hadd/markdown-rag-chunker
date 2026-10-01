import hashlib
import io
import inspect
import json
import math
from typing import Any, Awaitable, Callable

from .models import MarkdownChunk, MarkdownSplitterInput


def format_chunks(documents: list[Any]) -> list[MarkdownChunk]:
    chunks: list[MarkdownChunk] = []
    for doc in documents:
        content = doc.page_content or ""
        metadata = doc.metadata or {}
        hash_input = json.dumps(
            {"content": content, "metadata": metadata},
            ensure_ascii=True,
            sort_keys=True,
        ).encode("utf-8")
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
    if inspect.isawaitable(value):
        return await value
    return value


async def resolve_markdown_text(
    params: MarkdownSplitterInput,
    fetch_remote: Callable[[str], Awaitable[tuple[bytes, str]] | tuple[bytes, str]],
    load_kvs: Callable[[str], Awaitable[str] | str],
    convert_remote_file: Callable[[bytes, str], str] | None = None,
) -> tuple[str, str]:
    converter = convert_remote_file or convert_to_markdown
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
        markdown_text = converter(bytes(raw_bytes), input_file_type)
    elif source.startswith("kvs://"):
        key = source.removeprefix("kvs://")
        if not key:
            raise ValueError("kvs:// source must include a key name.")
        markdown_text = await _maybe_await(load_kvs(key))
    else:
        raise ValueError(
            "Unsupported markdown_file source. Use https://... or kvs://KEY."
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


def convert_to_markdown(raw_bytes: bytes, content_type: str) -> str:
    try:
        from markitdown import MarkItDown
    except ImportError as exc:  # pragma: no cover - dependency-level failure
        raise RuntimeError(
            "markitdown is required for URL file conversion. "
            "Install dependencies from requirements.txt."
        ) from exc

    converter = MarkItDown(enable_plugins=False)
    result = converter.convert_stream(
        io.BytesIO(raw_bytes), mimetype=content_type or "application/octet-stream"
    )
    markdown_text = result.text_content if result else None
    if markdown_text is None or not markdown_text.strip():
        raise ValueError("Converted file content is empty.")
    return markdown_text
