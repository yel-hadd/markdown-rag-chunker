"""Apify Actor runtime: input validation, splitting, and dataset output.

This is the only module that touches Apify or the network; everything else
lives in pure, injectable functions (see ``processing.py`` and ``models.py``).
"""

import time

from apify import Actor
from curl_cffi.requests import AsyncSession
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from .models import MarkdownSplitterInput, ProcessingMetrics
from .processing import format_chunks, resolve_markdown_text


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

            # Convert headers to LangChain format
            headers = []
            for h in params.headers_to_split_on:
                level = h.count("#")
                headers.append((h, f"Header {level}"))

            # Split Markdown
            splitter = MarkdownHeaderTextSplitter(
                headers_to_split_on=headers, strip_headers=params.strip_headers
            )
            documents = splitter.split_text(markdown_text)
            if params.max_chunk_chars:
                chunk_overlap = min(
                    params.max_chunk_chars // 10,
                    max(params.max_chunk_chars - 1, 0),
                )
                recursive_splitter = RecursiveCharacterTextSplitter(
                    chunk_size=params.max_chunk_chars,
                    chunk_overlap=chunk_overlap,
                    separators=["\n\n", "\n", " ", ""],
                )
                resized_documents = []
                for document in documents:
                    if len(document.page_content or "") <= params.max_chunk_chars:
                        resized_documents.append(document)
                        continue
                    resized_documents.extend(recursive_splitter.split_documents([document]))
                documents = resized_documents

            # Format output
            chunks = format_chunks(documents)
            if len(chunks) > params.max_chunks:
                raise ValueError(
                    f"Chunk count {len(chunks)} exceeds max_chunks ({params.max_chunks})."
                )

            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            metrics = ProcessingMetrics(
                input_mode=params.input_mode,
                input_file_type=input_file_type,
                input_chars=len(markdown_text),
                chunk_count=len(chunks),
                elapsed_ms=elapsed_ms,
            )

            # One dataset item per chunk: users pay per chunk (matches the
            # published pay-per-event pricing). Metrics go to the named
            # OUTPUT record in the default key-value store (the Python SDK's
            # equivalent of set_output), which is not billed as a dataset item.
            await Actor.set_value("OUTPUT", metrics.model_dump())
            if chunks:
                await Actor.push_data([chunk.model_dump() for chunk in chunks])

        except Exception as e:
            error_msg = f"Actor failed: {str(e)}"
            Actor.log.error(error_msg)
            await Actor.fail(exception=e, status_message=error_msg)
