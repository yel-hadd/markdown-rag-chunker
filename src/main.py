"""Apify Actor runtime: input validation, splitting, and dataset output.

This is the only module that touches Apify or the network; everything else
lives in pure, injectable functions (see ``processing.py`` and ``models.py``).
"""

import hashlib
import time

from apify import Actor
from curl_cffi.requests import AsyncSession

from .models import MarkdownSplitterInput, ProcessingMetrics
from .processing import (
    document_hash,
    format_chunks,
    resolve_markdown_text,
    split_config_fingerprint,
    split_documents,
)

#: Key holding the change-detection state inside each cache store.
_CACHE_RECORD_KEY = "state"


def _cache_store_name(params: MarkdownSplitterInput, doc_hash: str) -> str:
    """Name of the per-source cache store for change detection.

    File-mode sources are keyed by their URL/`kvs://` string so that an
    edited document overwrites the previous state; text mode is keyed by
    the document hash itself so identical text always finds its cache.
    """
    source = (
        params.markdown_file.strip()
        if params.input_mode == "file" and params.markdown_file
        else f"hash:{doc_hash}"
    )
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]
    return f"markdown-rag-chunker-cache-{digest}"


async def main():
    async with Actor:
        try:
            started_at = time.perf_counter()
            input_data = await Actor.get_input() or {}
            params = MarkdownSplitterInput(**input_data)

            # Resolve the proxy for remote fetches: either the user's own
            # (free) or Apify Proxy (billed to the user as platform usage).
            fetch_proxy: str | None = None
            if params.input_mode == "file":
                if params.proxy_url:
                    fetch_proxy = params.proxy_url
                elif params.use_apify_proxy:
                    proxy_configuration = await Actor.create_proxy_configuration(
                        actor_proxy_input={"useApifyProxy": True}
                    )
                    fetch_proxy = (
                        await proxy_configuration.new_url()
                        if proxy_configuration
                        else None
                    )

            async def fetch_remote(url: str) -> tuple[bytes, str]:
                # curl_cffi impersonates a real Chrome TLS/HTTP2 fingerprint
                # (including header order), so plain HTTP clients blocked by
                # anti-bot filters (e.g. Wikipedia 403s python-httpx) succeed.
                timeout_seconds = 30
                async with AsyncSession(
                    impersonate="chrome", timeout=timeout_seconds, proxy=fetch_proxy
                ) as session:
                    response = await session.get(url)
                response.raise_for_status()
                try:
                    content_length = int(response.headers.get("content-length", ""))
                except ValueError:
                    content_length = None
                if content_length is not None:
                    # Fail fast on oversized downloads: 4 bytes/char is a safe
                    # upper bound for UTF-8 input before conversion.
                    max_bytes = params.max_input_chars * 4
                    if content_length > max_bytes:
                        raise ValueError(
                            f"Remote file is {content_length} bytes, which exceeds the "
                            f"max_input_chars budget ({params.max_input_chars} chars "
                            f"~ {max_bytes} bytes)."
                        )
                content_type = response.headers.get("content-type", "application/octet-stream")
                return response.content, content_type

            async def load_kvs(key: str) -> str:
                value = await Actor.get_value(key)
                if value is None:
                    raise ValueError(f"No key-value store record found for '{key}'.")
                if not isinstance(value, str):
                    raise ValueError(
                        f"Key-value store record '{key}' must be a plain string."
                    )
                return value

            markdown_text, input_file_type = await resolve_markdown_text(
                params=params,
                fetch_remote=fetch_remote,
                load_kvs=load_kvs,
            )

            doc_hash = document_hash(markdown_text)
            config_fingerprint = split_config_fingerprint(params)

            # Change detection: with skip_if_unchanged, a cached state whose
            # document hash and split settings both match skips splitting and
            # pushing entirely — the run then costs only the start event.
            # Cache failures never fail the run; they just re-process.
            cached_state: dict | None = None
            cache_store = None
            if params.skip_if_unchanged:
                try:
                    cache_store = await Actor.open_key_value_store(
                        name=_cache_store_name(params, doc_hash)
                    )
                    cached_state = await cache_store.get_value(_CACHE_RECORD_KEY)
                except Exception as exc:
                    Actor.log.warning(f"Cache read skipped: {exc}")

            if (
                isinstance(cached_state, dict)
                and cached_state.get("document_hash") == doc_hash
                and cached_state.get("split_config") == config_fingerprint
            ):
                metrics = ProcessingMetrics(
                    input_mode=params.input_mode,
                    input_file_type=input_file_type,
                    input_chars=len(markdown_text),
                    chunk_count=0,
                    elapsed_ms=int((time.perf_counter() - started_at) * 1000),
                    document_hash=doc_hash,
                    unchanged=True,
                    cached_chunk_count=cached_state.get("chunk_count"),
                )
                await Actor.set_value("OUTPUT", metrics.model_dump(exclude_none=True))
                Actor.log.info(
                    f"Document unchanged (document_hash={doc_hash}); skipped "
                    "chunking. No dataset items were pushed or billed."
                )
                return

            # Split Markdown (header-aware first pass, cap-aware second
            # pass, max_chunks enforced) and format one record per chunk.
            chunks = format_chunks(split_documents(params, markdown_text))

            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            metrics = ProcessingMetrics(
                input_mode=params.input_mode,
                input_file_type=input_file_type,
                input_chars=len(markdown_text),
                chunk_count=len(chunks),
                elapsed_ms=elapsed_ms,
                document_hash=doc_hash,
            )

            # One dataset item per chunk: users pay per chunk (matches the
            # published pay-per-event pricing). Metrics go to the named
            # OUTPUT record in the default key-value store (the Python SDK's
            # equivalent of set_output), which is not billed as a dataset item.
            # exclude_none keeps the record free of null placeholders.
            await Actor.set_value("OUTPUT", metrics.model_dump(exclude_none=True))
            if chunks:
                await Actor.push_data([chunk.model_dump() for chunk in chunks])

            if cache_store is not None:
                try:
                    await cache_store.set_value(
                        _CACHE_RECORD_KEY,
                        {
                            "document_hash": doc_hash,
                            "split_config": config_fingerprint,
                            "chunk_count": len(chunks),
                        },
                    )
                except Exception as exc:
                    Actor.log.warning(f"Cache write skipped: {exc}")

        except Exception as e:
            error_msg = f"Actor failed: {str(e)}"
            Actor.log.error(error_msg)
            await Actor.fail(exception=e, status_message=error_msg)
