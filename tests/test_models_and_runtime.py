"""Comprehensive tests for models and processing helpers."""

import json
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from pydantic import ValidationError

from src.models import MarkdownChunk, MarkdownSplitterInput, ProcessingMetrics
from src.processing import (
    convert_to_markdown,
    document_hash,
    format_chunks,
    resolve_markdown_text,
    split_config_fingerprint,
    split_documents,
)


# ---------------------------------------------------------------------------
# MarkdownSplitterInput – validation
# ---------------------------------------------------------------------------


class InputModelTextModeTests(unittest.TestCase):
    """Tests for text-mode validation."""

    def test_valid_text_mode(self) -> None:
        obj = MarkdownSplitterInput(input_mode="text", markdown_text="# Hello")
        self.assertEqual(obj.input_mode, "text")
        self.assertEqual(obj.markdown_text, "# Hello")

    def test_text_mode_requires_markdown_text_missing(self) -> None:
        with self.assertRaises(ValidationError):
            MarkdownSplitterInput(input_mode="text", markdown_text=None)

    def test_text_mode_rejects_whitespace_only(self) -> None:
        with self.assertRaises(ValidationError):
            MarkdownSplitterInput(input_mode="text", markdown_text="   ")

    def test_text_mode_default_input_mode(self) -> None:
        obj = MarkdownSplitterInput(markdown_text="# Hello")
        self.assertEqual(obj.input_mode, "text")

    def test_text_mode_respects_max_input_chars(self) -> None:
        with self.assertRaises(ValidationError):
            MarkdownSplitterInput(markdown_text="x" * 11, max_input_chars=10)

    def test_text_mode_accepts_exactly_max_input_chars(self) -> None:
        obj = MarkdownSplitterInput(markdown_text="x" * 10, max_input_chars=10)
        self.assertEqual(len(obj.markdown_text), 10)  # type: ignore[arg-type]


class InputModelFileModeTests(unittest.TestCase):
    """Tests for file-mode validation."""

    def test_valid_file_mode(self) -> None:
        obj = MarkdownSplitterInput(
            input_mode="file",
            markdown_file="https://example.com/doc.md",
        )
        self.assertEqual(obj.input_mode, "file")
        self.assertEqual(obj.markdown_file, "https://example.com/doc.md")

    def test_file_mode_requires_markdown_file_missing(self) -> None:
        with self.assertRaises(ValidationError):
            MarkdownSplitterInput(input_mode="file", markdown_file=None)

    def test_file_mode_rejects_whitespace_only_url(self) -> None:
        with self.assertRaises(ValidationError):
            MarkdownSplitterInput(input_mode="file", markdown_file="   ")


class InputModelFieldBoundsTests(unittest.TestCase):
    """Tests for numeric field boundaries."""

    def test_max_chunk_chars_accepts_positive(self) -> None:
        obj = MarkdownSplitterInput(markdown_text="# H", max_chunk_chars=1800)
        self.assertEqual(obj.max_chunk_chars, 1800)

    def test_max_chunk_chars_rejects_zero(self) -> None:
        with self.assertRaises(ValidationError):
            MarkdownSplitterInput(markdown_text="# H", max_chunk_chars=0)

    def test_max_chunk_chars_rejects_negative(self) -> None:
        with self.assertRaises(ValidationError):
            MarkdownSplitterInput(markdown_text="# H", max_chunk_chars=-1)

    def test_max_chunk_chars_accepts_max_boundary(self) -> None:
        obj = MarkdownSplitterInput(markdown_text="# H", max_chunk_chars=100_000)
        self.assertEqual(obj.max_chunk_chars, 100_000)

    def test_max_chunk_chars_rejects_above_max(self) -> None:
        with self.assertRaises(ValidationError):
            MarkdownSplitterInput(markdown_text="# H", max_chunk_chars=100_001)

    def test_max_chunks_default(self) -> None:
        obj = MarkdownSplitterInput(markdown_text="# H")
        self.assertEqual(obj.max_chunks, 5_000)

    def test_max_chunks_rejects_zero(self) -> None:
        with self.assertRaises(ValidationError):
            MarkdownSplitterInput(markdown_text="# H", max_chunks=0)

    def test_max_input_chars_rejects_zero(self) -> None:
        with self.assertRaises(ValidationError):
            MarkdownSplitterInput(markdown_text="# H", max_input_chars=0)

    def test_max_input_chars_rejects_above_ceiling(self) -> None:
        with self.assertRaises(ValidationError):
            MarkdownSplitterInput(markdown_text="# H", max_input_chars=5_000_001)

    def test_max_input_chars_accepts_ceiling(self) -> None:
        obj = MarkdownSplitterInput(markdown_text="# H", max_input_chars=5_000_000)
        self.assertEqual(obj.max_input_chars, 5_000_000)

    def test_max_chunks_accepts_max_boundary(self) -> None:
        obj = MarkdownSplitterInput(markdown_text="# H", max_chunks=50_000)
        self.assertEqual(obj.max_chunks, 50_000)

    def test_max_chunks_rejects_above_max(self) -> None:
        with self.assertRaises(ValidationError):
            MarkdownSplitterInput(markdown_text="# H", max_chunks=50_001)

    def test_invalid_input_mode_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            MarkdownSplitterInput(input_mode="telepathy", markdown_text="# H")  # type: ignore[arg-type]


class InputModelHeaderValidatorTests(unittest.TestCase):
    """Tests for headers_to_split_on validation."""

    def test_valid_single_level(self) -> None:
        obj = MarkdownSplitterInput(markdown_text="# H", headers_to_split_on=["#"])
        self.assertEqual(obj.headers_to_split_on, ["#"])

    def test_valid_all_six_levels(self) -> None:
        obj = MarkdownSplitterInput(
            markdown_text="# H",
            headers_to_split_on=["#", "##", "###", "####", "#####", "######"],
        )
        self.assertEqual(len(obj.headers_to_split_on), 6)

    def test_rejects_more_than_six_levels(self) -> None:
        with self.assertRaises(ValidationError):
            MarkdownSplitterInput(
                markdown_text="# H",
                headers_to_split_on=["#", "##", "###", "####", "#####", "######", "#######"],
            )

    def test_rejects_invalid_header_format(self) -> None:
        with self.assertRaises(ValidationError):
            MarkdownSplitterInput(markdown_text="# H", headers_to_split_on=["h1"])

    def test_rejects_header_with_text(self) -> None:
        with self.assertRaises(ValidationError):
            MarkdownSplitterInput(markdown_text="# H", headers_to_split_on=["# Title"])

    def test_default_headers(self) -> None:
        obj = MarkdownSplitterInput(markdown_text="# H")
        self.assertEqual(obj.headers_to_split_on, ["#", "##", "###", "####", "#####", "######"])

    def test_headers_are_normalised(self) -> None:
        obj = MarkdownSplitterInput(
            markdown_text="# H", headers_to_split_on=["  ##  ", " # "]
        )
        self.assertEqual(obj.headers_to_split_on, ["##", "#"])

    def test_rejects_seven_hashes(self) -> None:
        # '#######' is not a Markdown header; accepting it would silently
        # never match anything in the splitter.
        with self.assertRaises(ValidationError):
            MarkdownSplitterInput(markdown_text="# H", headers_to_split_on=["#######"])

    def test_empty_header_list_allowed(self) -> None:
        # Pins intent: [] means "no header splitting" — the whole document
        # stays one chunk (max_chunk_chars still applies).
        obj = MarkdownSplitterInput(markdown_text="# H", headers_to_split_on=[])
        self.assertEqual(obj.headers_to_split_on, [])


class InputModelStripHeadersTests(unittest.TestCase):
    def test_strip_headers_defaults_true(self) -> None:
        obj = MarkdownSplitterInput(markdown_text="# H")
        self.assertTrue(obj.strip_headers)

    def test_strip_headers_can_be_false(self) -> None:
        obj = MarkdownSplitterInput(markdown_text="# H", strip_headers=False)
        self.assertFalse(obj.strip_headers)


class InputModelProxyTests(unittest.TestCase):
    """Tests for proxy selection in file mode."""

    def test_use_apify_proxy_defaults_false(self) -> None:
        obj = MarkdownSplitterInput(markdown_text="# H")
        self.assertFalse(obj.use_apify_proxy)

    def test_proxy_url_defaults_none(self) -> None:
        obj = MarkdownSplitterInput(markdown_text="# H")
        self.assertIsNone(obj.proxy_url)

    def test_proxy_url_accepts_http(self) -> None:
        obj = MarkdownSplitterInput(
            input_mode="file",
            markdown_file="https://example.com/doc.pdf",
            proxy_url="http://proxy.example.com:8080",
        )
        self.assertEqual(obj.proxy_url, "http://proxy.example.com:8080")

    def test_proxy_url_accepts_https_and_socks5(self) -> None:
        for url in ("https://user:pass@proxy.example.com:8080", "socks5://127.0.0.1:1080"):
            obj = MarkdownSplitterInput(
                input_mode="file", markdown_file="https://example.com/doc.pdf", proxy_url=url
            )
            self.assertEqual(obj.proxy_url, url)

    def test_proxy_url_rejects_unsupported_scheme(self) -> None:
        with self.assertRaises(ValidationError):
            MarkdownSplitterInput(
                input_mode="file",
                markdown_file="https://example.com/doc.pdf",
                proxy_url="ftp://proxy.example.com:21",
            )

    def test_proxy_url_rejects_garbage(self) -> None:
        with self.assertRaises(ValidationError):
            MarkdownSplitterInput(
                input_mode="file",
                markdown_file="https://example.com/doc.pdf",
                proxy_url="not a url",
            )

    def test_both_proxy_sources_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            MarkdownSplitterInput(
                input_mode="file",
                markdown_file="https://example.com/doc.pdf",
                use_apify_proxy=True,
                proxy_url="http://proxy.example.com:8080",
            )

    def test_apify_proxy_alone_is_valid(self) -> None:
        obj = MarkdownSplitterInput(
            input_mode="file",
            markdown_file="https://example.com/doc.pdf",
            use_apify_proxy=True,
        )
        self.assertTrue(obj.use_apify_proxy)

    def test_proxy_url_normalises_uppercase_scheme(self) -> None:
        # URL schemes are case-insensitive (RFC 3986); only the scheme is
        # lowercased so userinfo and hosts keep their case.
        obj = MarkdownSplitterInput(
            input_mode="file",
            markdown_file="https://example.com/doc.pdf",
            proxy_url="HTTP://proxy.example.com:8080",
        )
        self.assertEqual(obj.proxy_url, "http://proxy.example.com:8080")

    def test_proxy_url_returned_stripped(self) -> None:
        obj = MarkdownSplitterInput(
            input_mode="file",
            markdown_file="https://example.com/doc.pdf",
            proxy_url="  http://proxy.example.com:8080  ",
        )
        self.assertEqual(obj.proxy_url, "http://proxy.example.com:8080")

    def test_proxy_url_accepted_in_text_mode(self) -> None:
        # Pin: proxy settings only affect file-mode fetches; text mode
        # accepts them without error (there is nothing to fetch).
        obj = MarkdownSplitterInput(
            markdown_text="# H", proxy_url="http://proxy.example.com:8080"
        )
        self.assertEqual(obj.proxy_url, "http://proxy.example.com:8080")

    def test_both_proxy_sources_error_names_the_fields(self) -> None:
        with self.assertRaisesRegex(ValidationError, "use_apify_proxy or proxy_url"):
            MarkdownSplitterInput(
                input_mode="file",
                markdown_file="https://example.com/doc.pdf",
                use_apify_proxy=True,
                proxy_url="http://proxy.example.com:8080",
            )

    def test_text_mode_required_error_is_actionable(self) -> None:
        with self.assertRaisesRegex(ValidationError, "markdown_text is required"):
            MarkdownSplitterInput(input_mode="text", markdown_text=None)

    def test_file_mode_required_error_is_actionable(self) -> None:
        with self.assertRaisesRegex(ValidationError, "markdown_file is required"):
            MarkdownSplitterInput(input_mode="file", markdown_file=None)


# ---------------------------------------------------------------------------
# MarkdownChunk model
# ---------------------------------------------------------------------------


class MarkdownChunkModelTests(unittest.TestCase):
    def test_valid_chunk(self) -> None:
        chunk = MarkdownChunk(
            content="hello",
            metadata={"Header 1": "Intro"},
            chunk_id="abc123",
            char_count=5,
            token_count=2,
        )
        self.assertEqual(chunk.content, "hello")
        self.assertEqual(chunk.char_count, 5)

    def test_char_count_rejects_negative(self) -> None:
        with self.assertRaises(ValidationError):
            MarkdownChunk(content="x", metadata={}, chunk_id="id", char_count=-1, token_count=0)

    def test_token_count_rejects_negative(self) -> None:
        with self.assertRaises(ValidationError):
            MarkdownChunk(content="x", metadata={}, chunk_id="id", char_count=1, token_count=-1)

    def test_empty_metadata_allowed(self) -> None:
        chunk = MarkdownChunk(content="x", metadata={}, chunk_id="id", char_count=1, token_count=1)
        self.assertEqual(chunk.metadata, {})


# ---------------------------------------------------------------------------
# ProcessingMetrics model
# ---------------------------------------------------------------------------


class ProcessingMetricsTests(unittest.TestCase):
    def test_valid_metrics(self) -> None:
        m = ProcessingMetrics(
            input_mode="text",
            input_file_type="text/markdown",
            input_chars=100,
            chunk_count=5,
            elapsed_ms=42,
            document_hash="abc123",
        )
        self.assertEqual(m.input_mode, "text")
        self.assertEqual(m.input_file_type, "text/markdown")

    def test_input_file_type_defaults(self) -> None:
        m = ProcessingMetrics(
            input_mode="text",
            input_chars=0,
            chunk_count=0,
            elapsed_ms=0,
            document_hash="a",
        )
        self.assertEqual(m.input_file_type, "text/markdown")

    def test_rejects_negative_input_chars(self) -> None:
        with self.assertRaises(ValidationError):
            ProcessingMetrics(
                input_mode="text",
                input_chars=-1,
                chunk_count=0,
                elapsed_ms=0,
            )

    def test_rejects_negative_chunk_count(self) -> None:
        with self.assertRaises(ValidationError):
            ProcessingMetrics(
                input_mode="text",
                input_chars=0,
                chunk_count=-1,
                elapsed_ms=0,
            )

    def test_rejects_negative_elapsed_ms(self) -> None:
        with self.assertRaises(ValidationError):
            ProcessingMetrics(
                input_mode="text",
                input_chars=0,
                chunk_count=0,
                elapsed_ms=-1,
                document_hash="a",
            )

    def test_document_hash_required(self) -> None:
        with self.assertRaises(ValidationError):
            ProcessingMetrics(
                input_mode="text",
                input_chars=0,
                chunk_count=0,
                elapsed_ms=0,
            )

    def test_unchanged_defaults_false_and_cached_chunk_count_optional(self) -> None:
        m = ProcessingMetrics(
            input_mode="text",
            input_chars=0,
            chunk_count=0,
            elapsed_ms=0,
            document_hash="a",
        )
        self.assertFalse(m.unchanged)
        self.assertIsNone(m.cached_chunk_count)


class InputModelSkipUnchangedTests(unittest.TestCase):
    def test_skip_if_unchanged_defaults_false(self) -> None:
        obj = MarkdownSplitterInput(markdown_text="# H")
        self.assertFalse(obj.skip_if_unchanged)


class DocumentHashTests(unittest.TestCase):
    def test_same_text_same_hash(self) -> None:
        self.assertEqual(document_hash("# Hello"), document_hash("# Hello"))

    def test_different_text_different_hash(self) -> None:
        self.assertNotEqual(document_hash("# Hello"), document_hash("# World"))

    def test_hash_is_16_hex_chars(self) -> None:
        h = document_hash("text")
        self.assertEqual(len(h), 16)
        int(h, 16)  # must be valid hex

    def test_empty_text_allowed(self) -> None:
        self.assertEqual(len(document_hash("")), 16)


class SplitConfigFingerprintTests(unittest.TestCase):
    def _params(self, **kw) -> MarkdownSplitterInput:
        base = dict(markdown_text="# H")
        base.update(kw)
        return MarkdownSplitterInput(**base)

    def test_same_config_same_fingerprint(self) -> None:
        self.assertEqual(
            split_config_fingerprint(self._params()),
            split_config_fingerprint(self._params()),
        )

    def test_headers_change_fingerprint(self) -> None:
        self.assertNotEqual(
            split_config_fingerprint(self._params()),
            split_config_fingerprint(self._params(headers_to_split_on=["#"])),
        )

    def test_max_chunk_chars_change_fingerprint(self) -> None:
        self.assertNotEqual(
            split_config_fingerprint(self._params()),
            split_config_fingerprint(self._params(max_chunk_chars=1800)),
        )

    def test_strip_headers_change_fingerprint(self) -> None:
        self.assertNotEqual(
            split_config_fingerprint(self._params()),
            split_config_fingerprint(self._params(strip_headers=False)),
        )

    def test_input_text_does_not_change_fingerprint(self) -> None:
        self.assertEqual(
            split_config_fingerprint(self._params(markdown_text="# A")),
            split_config_fingerprint(self._params(markdown_text="# B")),
        )

    def test_limits_and_flags_do_not_change_fingerprint(self) -> None:
        # max_chunks, max_input_chars and proxy/skip flags do not affect
        # splitting, so changing them must not invalidate the cache.
        self.assertEqual(
            split_config_fingerprint(self._params()),
            split_config_fingerprint(
                self._params(
                    max_chunks=99,
                    max_input_chars=42,
                    skip_if_unchanged=True,
                    use_apify_proxy=True,
                )
            ),
        )


# ---------------------------------------------------------------------------
# format_chunks
# ---------------------------------------------------------------------------


class FormatChunksTests(unittest.TestCase):
    class _Doc:
        def __init__(self, page_content: str, metadata: dict) -> None:
            self.page_content = page_content
            self.metadata = metadata

    def test_basic_chunk_metrics(self) -> None:
        chunks = format_chunks([self._Doc("abc", {"Header 1": "H1"})])
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].char_count, 3)
        self.assertEqual(chunks[0].token_count, 1)  # ceil(3/4)

    def test_token_count_formula(self) -> None:
        # 4 chars → ceil(4/4) = 1
        chunks = format_chunks([self._Doc("abcd", {})])
        self.assertEqual(chunks[0].token_count, 1)
        # 5 chars → ceil(5/4) = 2
        chunks = format_chunks([self._Doc("abcde", {})])
        self.assertEqual(chunks[0].token_count, 2)

    def test_empty_content_produces_zero_counts(self) -> None:
        chunks = format_chunks([self._Doc("", {})])
        self.assertEqual(chunks[0].char_count, 0)
        self.assertEqual(chunks[0].token_count, 0)

    def test_chunk_id_is_nonempty_string(self) -> None:
        chunks = format_chunks([self._Doc("hello", {})])
        self.assertIsInstance(chunks[0].chunk_id, str)
        self.assertTrue(len(chunks[0].chunk_id) > 0)

    def test_chunk_id_is_16_lowercase_hex_chars(self) -> None:
        chunks = format_chunks([self._Doc("hello", {"Header 1": "World"})])
        self.assertRegex(chunks[0].chunk_id, r"^[0-9a-f]{16}$")

    def test_chunk_id_independent_of_metadata_key_order(self) -> None:
        a = format_chunks([self._Doc("same", {"Header 1": "A", "Header 2": "B"})])[0].chunk_id
        b = format_chunks([self._Doc("same", {"Header 2": "B", "Header 1": "A"})])[0].chunk_id
        self.assertEqual(a, b)

    def test_chunk_id_deterministic_for_unicode_content(self) -> None:
        a = format_chunks([self._Doc("héllo ☃ слайд", {})])[0].chunk_id
        b = format_chunks([self._Doc("héllo ☃ слайд", {})])[0].chunk_id
        self.assertEqual(a, b)

    def test_chunk_id_is_deterministic(self) -> None:
        doc = self._Doc("hello", {"Header 1": "World"})
        id1 = format_chunks([doc])[0].chunk_id
        id2 = format_chunks([doc])[0].chunk_id
        self.assertEqual(id1, id2)

    def test_chunk_id_differs_for_different_content(self) -> None:
        id1 = format_chunks([self._Doc("foo", {})])[0].chunk_id
        id2 = format_chunks([self._Doc("bar", {})])[0].chunk_id
        self.assertNotEqual(id1, id2)

    def test_chunk_id_differs_for_different_metadata(self) -> None:
        id1 = format_chunks([self._Doc("same", {"Header 1": "A"})])[0].chunk_id
        id2 = format_chunks([self._Doc("same", {"Header 1": "B"})])[0].chunk_id
        self.assertNotEqual(id1, id2)

    def test_empty_list_returns_empty(self) -> None:
        self.assertEqual(format_chunks([]), [])

    def test_multiple_chunks_all_have_ids(self) -> None:
        docs = [self._Doc(f"content {i}", {"Header 1": f"H{i}"}) for i in range(5)]
        chunks = format_chunks(docs)
        ids = {c.chunk_id for c in chunks}
        self.assertEqual(len(ids), 5, "All chunk IDs must be unique")

    def test_metadata_preserved(self) -> None:
        chunks = format_chunks([self._Doc("text", {"Header 1": "Intro", "Header 2": "Sub"})])
        self.assertEqual(chunks[0].metadata, {"Header 1": "Intro", "Header 2": "Sub"})

    def test_none_page_content_treated_as_empty(self) -> None:
        doc = self._Doc.__new__(self._Doc)
        doc.page_content = None  # type: ignore[assignment]
        doc.metadata = {}
        chunks = format_chunks([doc])
        self.assertEqual(chunks[0].content, "")
        self.assertEqual(chunks[0].char_count, 0)


# ---------------------------------------------------------------------------
# resolve_markdown_text
# ---------------------------------------------------------------------------


class ResolveMarkdownTextTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_mode_returns_text_and_mime(self) -> None:
        params = MarkdownSplitterInput(markdown_text="# Heading")
        text, mime = await resolve_markdown_text(
            params=params,
            fetch_remote=lambda _: (b"", "text/plain"),
            load_kvs=lambda _: "# kvs",
        )
        self.assertEqual(text, "# Heading")
        self.assertEqual(mime, "text/markdown")

    async def test_file_mode_https_calls_converter(self) -> None:
        params = MarkdownSplitterInput(
            input_mode="file", markdown_file="https://example.com/doc.html"
        )
        text, mime = await resolve_markdown_text(
            params=params,
            fetch_remote=lambda _: (b"<h1>Title</h1>", "text/html"),
            load_kvs=lambda _: "",
            convert_remote_file=lambda _raw, _ct: "# Title",
        )
        self.assertEqual(text, "# Title")
        self.assertEqual(mime, "text/html")

    async def test_file_mode_http_scheme_also_works(self) -> None:
        params = MarkdownSplitterInput(
            input_mode="file", markdown_file="http://example.com/doc.md"
        )
        text, mime = await resolve_markdown_text(
            params=params,
            fetch_remote=lambda _: (b"# fetched", "text/markdown"),
            load_kvs=lambda _: "",
            convert_remote_file=lambda _raw, _ct: "# fetched",
        )
        self.assertEqual(text, "# fetched")

    async def test_file_mode_kvs_returns_value(self) -> None:
        params = MarkdownSplitterInput(
            input_mode="file", markdown_file="kvs://MY_KEY"
        )
        text, mime = await resolve_markdown_text(
            params=params,
            fetch_remote=lambda _: (b"", ""),
            load_kvs=lambda key: f"# from {key}",
        )
        self.assertEqual(text, "# from MY_KEY")
        self.assertEqual(mime, "text/markdown")

    async def test_file_mode_kvs_strips_prefix(self) -> None:
        received_keys: list[str] = []

        params = MarkdownSplitterInput(
            input_mode="file", markdown_file="kvs://SOME_KEY"
        )
        await resolve_markdown_text(
            params=params,
            fetch_remote=lambda _: (b"", ""),
            load_kvs=lambda key: received_keys.append(key) or "# content",  # type: ignore[func-returns-value]
        )
        self.assertEqual(received_keys, ["SOME_KEY"])

    async def test_file_mode_rejects_empty_kvs_key(self) -> None:
        params = MarkdownSplitterInput(
            input_mode="file", markdown_file="kvs://"
        )
        with self.assertRaises(ValueError, msg="kvs:// with no key should raise"):
            await resolve_markdown_text(
                params=params,
                fetch_remote=lambda _: (b"", ""),
                load_kvs=lambda _: "# content",
            )

    async def test_file_mode_rejects_unsupported_scheme(self) -> None:
        params = MarkdownSplitterInput(
            input_mode="file", markdown_file="ftp://example.com/file.md"
        )
        with self.assertRaises(ValueError):
            await resolve_markdown_text(
                params=params,
                fetch_remote=lambda _: (b"", ""),
                load_kvs=lambda _: "# content",
            )

    async def test_content_type_normalised_to_lowercase(self) -> None:
        params = MarkdownSplitterInput(
            input_mode="file", markdown_file="https://example.com/doc.pdf"
        )
        _, mime = await resolve_markdown_text(
            params=params,
            fetch_remote=lambda _: (b"%PDF-1.4", "Application/PDF"),
            load_kvs=lambda _: "",
            convert_remote_file=lambda _raw, _ct: "# pdf content",
        )
        self.assertEqual(mime, "application/pdf")

    async def test_empty_content_type_falls_back_to_octet_stream(self) -> None:
        params = MarkdownSplitterInput(
            input_mode="file", markdown_file="https://example.com/doc"
        )
        _, mime = await resolve_markdown_text(
            params=params,
            fetch_remote=lambda _: (b"data", ""),
            load_kvs=lambda _: "",
            convert_remote_file=lambda _raw, _ct: "# content",
        )
        self.assertEqual(mime, "application/octet-stream")

    async def test_converted_content_exceeding_max_input_chars_raises(self) -> None:
        params = MarkdownSplitterInput(
            input_mode="file",
            markdown_file="https://example.com/doc.md",
            max_input_chars=5,
        )
        with self.assertRaises(ValueError, msg="should raise when result exceeds limit"):
            await resolve_markdown_text(
                params=params,
                fetch_remote=lambda _: (b"data", "text/markdown"),
                load_kvs=lambda _: "",
                convert_remote_file=lambda _raw, _ct: "# content that is too long",
            )

    async def test_whitespace_only_resolved_content_raises(self) -> None:
        params = MarkdownSplitterInput(
            input_mode="file", markdown_file="https://example.com/empty.md"
        )
        with self.assertRaises(ValueError):
            await resolve_markdown_text(
                params=params,
                fetch_remote=lambda _: (b"   ", "text/markdown"),
                load_kvs=lambda _: "",
                convert_remote_file=lambda _raw, _ct: "   ",
            )

    async def test_converter_receives_correct_arguments(self) -> None:
        received: list[tuple] = []

        params = MarkdownSplitterInput(
            input_mode="file", markdown_file="https://example.com/doc.pdf"
        )
        await resolve_markdown_text(
            params=params,
            fetch_remote=lambda _: (b"%PDF", "application/pdf"),
            load_kvs=lambda _: "",
            convert_remote_file=lambda raw, ct: received.append((raw, ct)) or "# result",  # type: ignore[func-returns-value]
        )
        self.assertEqual(received, [(b"%PDF", "application/pdf")])

    async def test_async_fetch_remote_is_awaited(self) -> None:
        async def async_fetch(_url: str) -> tuple[bytes, str]:
            return b"# async content", "text/markdown"

        params = MarkdownSplitterInput(
            input_mode="file", markdown_file="https://example.com/doc.md"
        )
        text, _ = await resolve_markdown_text(
            params=params,
            fetch_remote=async_fetch,
            load_kvs=lambda _: "",
            convert_remote_file=lambda raw, _ct: raw.decode(),
        )
        self.assertEqual(text, "# async content")

    async def test_async_load_kvs_is_awaited(self) -> None:
        async def async_load(_key: str) -> str:
            return "# async kvs"

        params = MarkdownSplitterInput(
            input_mode="file", markdown_file="kvs://MY_KEY"
        )
        text, _ = await resolve_markdown_text(
            params=params,
            fetch_remote=lambda _: (b"", ""),
            load_kvs=async_load,
        )
        self.assertEqual(text, "# async kvs")

    async def test_fetch_remote_non_tuple_raises(self) -> None:
        params = MarkdownSplitterInput(
            input_mode="file", markdown_file="https://example.com/doc.md"
        )
        with self.assertRaisesRegex(ValueError, "tuple"):
            await resolve_markdown_text(
                params=params,
                fetch_remote=lambda _: b"raw bytes only",  # type: ignore[return-value]
                load_kvs=lambda _: "",
            )

    async def test_fetch_remote_wrong_arity_raises(self) -> None:
        params = MarkdownSplitterInput(
            input_mode="file", markdown_file="https://example.com/doc.md"
        )
        with self.assertRaisesRegex(ValueError, "tuple"):
            await resolve_markdown_text(
                params=params,
                fetch_remote=lambda _: (b"x", "text/plain", "extra"),  # type: ignore[return-value]
                load_kvs=lambda _: "",
            )

    async def test_fetch_remote_non_bytes_payload_raises(self) -> None:
        params = MarkdownSplitterInput(
            input_mode="file", markdown_file="https://example.com/doc.md"
        )
        with self.assertRaisesRegex(ValueError, "bytes"):
            await resolve_markdown_text(
                params=params,
                fetch_remote=lambda _: ("a string", "text/plain"),  # type: ignore[return-value]
                load_kvs=lambda _: "",
            )

    async def test_fetch_remote_non_string_content_type_raises(self) -> None:
        params = MarkdownSplitterInput(
            input_mode="file", markdown_file="https://example.com/doc.md"
        )
        with self.assertRaisesRegex(ValueError, "content type"):
            await resolve_markdown_text(
                params=params,
                fetch_remote=lambda _: (b"x", 123),  # type: ignore[return-value]
                load_kvs=lambda _: "",
            )

    async def test_load_kvs_non_string_result_raises(self) -> None:
        params = MarkdownSplitterInput(
            input_mode="file", markdown_file="kvs://MY_KEY"
        )
        with self.assertRaisesRegex(ValueError, "UTF-8 string"):
            await resolve_markdown_text(
                params=params,
                fetch_remote=lambda _: (b"", ""),
                load_kvs=lambda _: 42,  # type: ignore[return-value]
            )

    async def test_converted_content_exactly_at_max_input_chars_passes(self) -> None:
        params = MarkdownSplitterInput(
            input_mode="file",
            markdown_file="https://example.com/doc.md",
            max_input_chars=9,
        )
        text, _ = await resolve_markdown_text(
            params=params,
            fetch_remote=lambda _: (b"data", "text/markdown"),
            load_kvs=lambda _: "",
            convert_remote_file=lambda _raw, _ct: "# content",  # exactly 9 chars
        )
        self.assertEqual(text, "# content")

    async def test_file_mode_strips_surrounding_whitespace_from_source(self) -> None:
        received_urls: list[str] = []
        params = MarkdownSplitterInput(
            input_mode="file", markdown_file="  https://example.com/a.md  "
        )
        await resolve_markdown_text(
            params=params,
            fetch_remote=lambda url: received_urls.append(url) or (b"# x", "text/markdown"),  # type: ignore[func-returns-value]
            load_kvs=lambda _: "",
        )
        self.assertEqual(received_urls, ["https://example.com/a.md"])


# ---------------------------------------------------------------------------
# split_documents
# ---------------------------------------------------------------------------


class SplitDocumentsTests(unittest.TestCase):
    """Splitting behaviour, exercised through the real LangChain splitters.

    These tests pin the runtime contract: header→metadata mapping, the
    second pass that only re-splits oversized sections, empty-section
    filtering (zero-char chunks would be billed), and max_chunks limits.
    """

    def _params(self, **kw) -> MarkdownSplitterInput:
        base: dict = dict(markdown_text="# placeholder")
        base.update(kw)
        return MarkdownSplitterInput(**base)

    def test_splits_on_headers_and_keeps_header_chain(self) -> None:
        text = "# Guide\nintro\n## Install\nrun pip\n### Windows\nuse cmd"
        docs = split_documents(
            self._params(headers_to_split_on=["#", "##", "###"]), text
        )
        metas = [d.metadata for d in docs]
        self.assertIn({"Header 1": "Guide"}, metas)
        self.assertIn({"Header 1": "Guide", "Header 2": "Install"}, metas)
        self.assertIn(
            {"Header 1": "Guide", "Header 2": "Install", "Header 3": "Windows"}, metas
        )

    def test_sections_under_cap_pass_through_untouched(self) -> None:
        text = "# Short\nabc"
        docs = split_documents(
            self._params(headers_to_split_on=["#"], max_chunk_chars=100), text
        )
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].page_content, "abc")

    def test_only_oversized_sections_are_resplit(self) -> None:
        long_body = "word " * 80  # 400 chars
        text = f"# Short\nabc\n\n# Long\n{long_body}"
        docs = split_documents(
            self._params(headers_to_split_on=["#"], max_chunk_chars=200), text
        )
        short = [d for d in docs if d.metadata.get("Header 1") == "Short"]
        long_ = [d for d in docs if d.metadata.get("Header 1") == "Long"]
        self.assertEqual(len(short), 1)
        self.assertEqual(short[0].page_content, "abc")
        self.assertGreater(len(long_), 1)
        self.assertTrue(all(len(d.page_content) <= 200 for d in long_))

    def test_empty_sections_are_dropped(self) -> None:
        # A header with no body would otherwise become a billed zero-char chunk.
        text = "# Empty\n\n# Full\nreal content"
        docs = split_documents(self._params(headers_to_split_on=["#"]), text)
        contents = [d.page_content for d in docs]
        self.assertNotIn("", contents)
        self.assertEqual(contents, ["real content"])

    def test_no_headers_returns_whole_document(self) -> None:
        text = "# looks like a header\nbut no splitting requested\n\nmore"
        docs = split_documents(self._params(headers_to_split_on=[]), text)
        self.assertEqual(len(docs), 1)
        # The splitter may normalise trailing whitespace, so compare loosely.
        self.assertEqual(docs[0].page_content.split(), text.split())

    def test_strip_headers_false_keeps_headers_in_content(self) -> None:
        text = "# Title\nbody"
        docs = split_documents(
            self._params(headers_to_split_on=["#"], strip_headers=False), text
        )
        self.assertIn("# Title", docs[0].page_content)

    def test_chunk_count_equal_to_max_chunks_passes(self) -> None:
        text = "# A\none\n\n# B\ntwo"
        docs = split_documents(
            self._params(headers_to_split_on=["#"], max_chunks=2), text
        )
        self.assertEqual(len(docs), 2)

    def test_chunk_count_exceeding_max_chunks_raises(self) -> None:
        text = "# A\none\n\n# B\ntwo"
        with self.assertRaisesRegex(ValueError, "max_chunks"):
            split_documents(self._params(headers_to_split_on=["#"], max_chunks=1), text)


# ---------------------------------------------------------------------------
# Schema ↔ model congruence
# ---------------------------------------------------------------------------


class SchemaCongruenceTests(unittest.TestCase):
    """Pin the deliberate contract between INPUT_SCHEMA.json and the model.

    The input_mode default intentionally diverges (schema: ``file`` for the
    Console form, model: ``text`` for API callers); everything else must
    stay in sync so the form and the validator never disagree.
    """

    @classmethod
    def setUpClass(cls) -> None:
        path = Path(__file__).resolve().parent.parent / ".actor" / "INPUT_SCHEMA.json"
        cls.schema = json.loads(path.read_text())

    def test_input_mode_default_diverges_deliberately(self) -> None:
        self.assertEqual(self.schema["properties"]["input_mode"]["default"], "file")
        self.assertEqual(
            MarkdownSplitterInput(markdown_text="# H").input_mode, "text"
        )

    def test_numeric_limits_match_model(self) -> None:
        props = self.schema["properties"]
        cases = {
            "max_input_chars": {"default": 500_000, "minimum": 1, "maximum": 5_000_000},
            "max_chunks": {"default": 5_000, "minimum": 1, "maximum": 50_000},
            "max_chunk_chars": {"minimum": 1, "maximum": 100_000},
        }
        for field, expected in cases.items():
            for key, value in expected.items():
                self.assertEqual(
                    props[field].get(key), value, f"{field}.{key} drifted from the model"
                )

    def test_every_model_field_is_represented(self) -> None:
        schema_fields = set(self.schema["properties"])
        model_fields = set(MarkdownSplitterInput.model_fields)
        self.assertEqual(schema_fields, model_fields)


# ---------------------------------------------------------------------------
# convert_to_markdown
# ---------------------------------------------------------------------------


class ConvertToMarkdownTests(unittest.TestCase):
    def _fake_markitdown_module(self, text_content: str | None) -> ModuleType:
        """Build a fake markitdown module whose converter returns given text_content."""

        class _Result:
            pass

        _Result.text_content = text_content  # type: ignore[attr-defined]

        class _Converter:
            def __init__(self) -> None:
                self.last_mimetype: str | None = None

            def convert_stream(self, _stream, mimetype=None):
                self.last_mimetype = mimetype
                return _Result()

        _instance = _Converter()

        fake = ModuleType("markitdown")
        fake.MarkItDown = lambda enable_plugins=False: _instance  # type: ignore[attr-defined]
        fake._instance = _instance  # type: ignore[attr-defined]
        return fake

    def test_returns_text_content(self) -> None:
        fake = self._fake_markitdown_module("# Title\n\nBody")
        with patch.dict("sys.modules", {"markitdown": fake}):
            result = convert_to_markdown(b"<h1>Title</h1><p>Body</p>", "text/html")
        self.assertEqual(result, "# Title\n\nBody")

    def test_passes_mimetype_to_converter(self) -> None:
        fake = self._fake_markitdown_module("# ok")
        with patch.dict("sys.modules", {"markitdown": fake}):
            convert_to_markdown(b"data", "application/pdf")
        self.assertEqual(fake._instance.last_mimetype, "application/pdf")  # type: ignore[attr-defined]

    def test_empty_content_type_uses_octet_stream(self) -> None:
        fake = self._fake_markitdown_module("# ok")
        with patch.dict("sys.modules", {"markitdown": fake}):
            convert_to_markdown(b"data", "")
        self.assertEqual(fake._instance.last_mimetype, "application/octet-stream")  # type: ignore[attr-defined]

    def test_raises_when_result_is_none(self) -> None:
        fake = self._fake_markitdown_module(None)
        with patch.dict("sys.modules", {"markitdown": fake}):
            with self.assertRaises(ValueError, msg="None text_content should raise"):
                convert_to_markdown(b"data", "text/html")

    def test_raises_when_result_is_whitespace_only(self) -> None:
        fake = self._fake_markitdown_module("   \n  ")
        with patch.dict("sys.modules", {"markitdown": fake}):
            with self.assertRaises(ValueError):
                convert_to_markdown(b"data", "text/html")

    def test_passes_raw_bytes_as_stream(self) -> None:
        import io as _io

        received_streams: list[bytes] = []

        class _Converter:
            def convert_stream(self, stream, mimetype=None):
                received_streams.append(stream.read())

                class _R:
                    text_content = "# ok"

                return _R()

        fake = ModuleType("markitdown")
        fake.MarkItDown = lambda enable_plugins=False: _Converter()  # type: ignore[attr-defined]
        with patch.dict("sys.modules", {"markitdown": fake}):
            convert_to_markdown(b"\x25\x50\x44\x46", "application/pdf")

        self.assertEqual(received_streams, [b"\x25\x50\x44\x46"])


if __name__ == "__main__":
    unittest.main()
