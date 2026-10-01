import time

import httpx
from apify import Actor
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from .models import MarkdownSplitterInput, MarkdownSplitterOutput, ProcessingMetrics
from .processing import format_chunks, resolve_markdown_text


async def main():
    async with Actor:
        try:
            started_at = time.perf_counter()
            input_data = await Actor.get_input() or {}
            params = MarkdownSplitterInput(**input_data)

            async def fetch_remote(url: str) -> tuple[bytes, str]:
                timeout_seconds = 30
                async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                    response = await client.get(url)
                response.raise_for_status()
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

            await Actor.push_data(
                MarkdownSplitterOutput(
                    chunks=chunks,
                    metrics=metrics,
                    error=None,
                ).model_dump(exclude_none=True)
            )

        except Exception as e:
            error_msg = f"Actor failed: {str(e)}"
            Actor.log.error(error_msg)
            await Actor.fail(exception=e, status_message=error_msg)
