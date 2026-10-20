"""Apify Actor runtime: input validation, splitting, and dataset output.

This is the only module that touches Apify or the network; everything else
lives in pure, injectable functions (see ``processing.py`` and ``models.py``).
"""

import asyncio
import time

from apify import Actor
from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException
from pydantic import ValidationError

from .models import MarkdownSplitterInput, ProcessingMetrics
from .processing import (
    cache_hit,
    cache_record_key,
    document_hash,
    document_source,
    format_chunks,
    http_error_message,
    read_capped,
    resolve_markdown_text,
    retry_delay,
    source_fingerprint,
    split_config_fingerprint,
    split_documents,
    validation_error_message,
)

#: Named key-value store (in the run user's account) holding one
#: change-detection record per source.
_CACHE_STORE_NAME = "markdown-rag-chunker-cache"

#: Largest remote file accepted, after decompression. Binary formats (PDF,
#: DOCX, PPTX) are much larger than their text, so this is independent of
#: max_input_chars, which is checked on the converted text.
_MAX_DOWNLOAD_BYTES = 25_000_000


async def main():
    async with Actor:
        try:
            started_at = time.perf_counter()
            input_data = await Actor.get_input() or {}
            params = MarkdownSplitterInput(**input_data)

            # Resolve the proxy for remote fetches: either the user's own
            # or Apify Proxy (platform usage, included in the event prices).
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
                # The body is streamed so oversized files fail before they
                # fill memory, with or without a Content-Length header.
                # 90 s covers large files from slow hosts (25 MB at ~300 KB/s).
                async with AsyncSession(
                    impersonate="chrome", timeout=90, proxy=fetch_proxy
                ) as session:
                    # Transport errors, rate limits (429) and server errors (5xx)
                    # are retried briefly; see processing.retry_delay.
                    attempt = 0
                    while True:
                        try:
                            response = await session.get(url, stream=True)
                        except RequestException as exc:
                            wait = retry_delay(None, None, attempt)
                            if wait is None:
                                raise ValueError(f"Download failed: {exc}") from exc
                            reason = type(exc).__name__
                        else:
                            wait = retry_delay(
                                response.status_code, response.headers.get("retry-after"), attempt
                            )
                            if wait is None:
                                break
                            await response.aclose()
                            reason = f"HTTP {response.status_code}"
                        Actor.log.warning(f"{reason} from the source; retrying in {wait:.0f} s.")
                        await asyncio.sleep(wait)
                        attempt += 1
                    try:
                        if response.status_code >= 400:
                            raise ValueError(http_error_message(response.status_code, url))
                        declared = response.headers.get("content-length", "")
                        if declared.isdigit() and int(declared) > _MAX_DOWNLOAD_BYTES:
                            raise ValueError(
                                f"Remote file is {int(declared)} bytes, larger than "
                                f"the {_MAX_DOWNLOAD_BYTES // 1_000_000} MB download limit."
                            )
                        raw = await read_capped(response.aiter_content(), _MAX_DOWNLOAD_BYTES)
                    finally:
                        await response.aclose()
                content_type = response.headers.get("content-type", "application/octet-stream")
                return raw, content_type

            async def load_kvs(key: str) -> str:
                # The store comes from the kvs_store picker, which grants this
                # limited-permission run read access to it.
                store = await Actor.open_key_value_store(id=params.kvs_store)
                value = await store.get_value(key)
                if value is None:
                    raise ValueError(f"No record '{key}' in the selected key-value store.")
                if isinstance(value, bytes):
                    try:
                        value = value.decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise ValueError(f"Record '{key}' is not UTF-8 text.") from exc
                if not isinstance(value, str):
                    raise ValueError(f"Record '{key}' must be a plain text record.")
                return value

            config_fingerprint = split_config_fingerprint(params)
            cache_store = None
            cache_key = ""
            cached_state: object = None

            async def read_cache(doc_hash: str) -> object:
                # Cache failures never fail the run; they just re-process.
                nonlocal cache_store, cache_key
                cache_key = cache_record_key(params, doc_hash)
                try:
                    cache_store = await Actor.open_key_value_store(name=_CACHE_STORE_NAME)
                    return await cache_store.get_value(cache_key)
                except Exception as exc:
                    Actor.log.warning(f"Cache read skipped: {exc}")
                    cache_store = None
                    return None

            async def finish_unchanged(
                state: dict, doc_hash: str, input_chars: int, input_file_type: str
            ) -> None:
                metrics = ProcessingMetrics(
                    input_mode=params.input_mode,
                    input_file_type=input_file_type,
                    input_chars=input_chars,
                    chunk_count=0,
                    elapsed_ms=int((time.perf_counter() - started_at) * 1000),
                    document_hash=doc_hash,
                    unchanged=True,
                    cached_chunk_count=state.get("chunk_count"),
                )
                await Actor.set_value("OUTPUT", metrics.model_dump(exclude_none=True))
                Actor.log.info(
                    f"Document unchanged (document_hash={doc_hash}); skipped "
                    "chunking. No dataset items were pushed or billed."
                )

            # Change detection (skip_if_unchanged) runs in two steps. File
            # mode first compares the raw download, so an unchanged file
            # skips conversion entirely; otherwise the converted Markdown
            # hash is compared (catches pages whose bytes vary per fetch).
            source_hash: str | None = None
            payload: tuple[bytes, str] | None = None
            source = (params.markdown_file or "").strip()
            if params.input_mode == "file" and source.startswith(("https://", "http://")):
                payload = await fetch_remote(source)
                source_hash = source_fingerprint(*payload)
                if params.skip_if_unchanged:
                    # File-mode records are keyed by source, not by hash.
                    cached_state = await read_cache("")
                    if cache_hit(cached_state, config_fingerprint, source_hash=source_hash):
                        # Not converted this run: report the cached document's stats.
                        await finish_unchanged(
                            cached_state,
                            cached_state.get("document_hash", ""),
                            cached_state.get("input_chars", 0),
                            cached_state.get("input_file_type", "application/octet-stream"),
                        )
                        return

            markdown_text, input_file_type = await resolve_markdown_text(
                params=params,
                # Reuse the download fetched above instead of fetching twice.
                fetch_remote=(lambda _url: payload) if payload is not None else fetch_remote,
                load_kvs=load_kvs,
            )
            doc_hash = document_hash(markdown_text)

            if params.skip_if_unchanged:
                if not cache_key:
                    cached_state = await read_cache(doc_hash)
                if cache_hit(cached_state, config_fingerprint, document_hash=doc_hash):
                    await finish_unchanged(
                        cached_state, doc_hash, len(markdown_text), input_file_type
                    )
                    return

            # Split Markdown (header-aware first pass, cap-aware second
            # pass, max_chunks enforced) and format one record per chunk.
            chunks = format_chunks(
                split_documents(params, markdown_text),
                source=document_source(params),
                document_hash=doc_hash,
                tokenizer=params.tokenizer,
                prepend_header_path=params.strip_headers,
            )

            # One dataset item per chunk: users pay per chunk (matches the
            # published pay-per-event pricing). When the user's maximum
            # charge per run is reached, the SDK pushes only what fits.
            pushed = len(chunks)
            limit_reached = False
            if chunks:
                result = await Actor.push_data([chunk.model_dump() for chunk in chunks])
                if result.event_charge_limit_reached and result.charged_count < len(chunks):
                    pushed, limit_reached = result.charged_count, True
                    Actor.log.warning(
                        f"Maximum charge per run reached: delivered {pushed} of "
                        f"{len(chunks)} chunks. Raise the limit to get the rest."
                    )

            # Run metrics go to the named OUTPUT record in the default
            # key-value store, which is not billed as a dataset item.
            # exclude_none keeps the record free of null placeholders.
            metrics = ProcessingMetrics(
                input_mode=params.input_mode,
                input_file_type=input_file_type,
                input_chars=len(markdown_text),
                chunk_count=pushed,
                elapsed_ms=int((time.perf_counter() - started_at) * 1000),
                document_hash=doc_hash,
                charge_limit_reached=limit_reached,
            )
            await Actor.set_value("OUTPUT", metrics.model_dump(exclude_none=True))

            # Only a complete delivery may be cached: otherwise later runs
            # would skip a document whose chunks never fully arrived.
            if cache_store is not None and not limit_reached:
                try:
                    await cache_store.set_value(
                        cache_key,
                        {
                            "document_hash": doc_hash,
                            "source_hash": source_hash,
                            "split_config": config_fingerprint,
                            "chunk_count": pushed,
                            "input_chars": len(markdown_text),
                            "input_file_type": input_file_type,
                        },
                    )
                except Exception as exc:
                    Actor.log.warning(f"Cache write skipped: {exc}")

        except Exception as e:
            error_msg = (
                validation_error_message(e) if isinstance(e, ValidationError) else str(e)
            )
            Actor.log.error(error_msg)
            # Pipelines read OUTPUT; give them a structured failure too (unbilled).
            try:
                await Actor.set_value("OUTPUT", {"failed": True, "error": error_msg})
            except Exception as exc:
                Actor.log.warning(f"Could not write OUTPUT: {exc}")
            await Actor.fail(exception=e, status_message=error_msg)
