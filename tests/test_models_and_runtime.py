"""Comprehensive tests for models and processing helpers."""

import json
import unittest

import tiktoken
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from pydantic import ValidationError

from src.models import MarkdownChunk, MarkdownSplitterInput, ProcessingMetrics
from src.processing import (
    cache_hit,
    cache_record_key,
    convert_to_markdown,
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


class InputModeInferenceTests(unittest.TestCase):
    """input_mode is inferred when omitted, so API callers who send only
    markdown_text or only markdown_file get exactly what they sent."""

    def test_infers_file_when_only_markdown_file_given(self) -> None:
        obj = MarkdownSplitterInput(markdown_file="https://example.com/a.pdf")
        self.assertEqual(obj.input_mode, "file")

    def test_infers_text_when_only_markdown_text_given(self) -> None:
        obj = MarkdownSplitterInput(markdown_text="# H")
        self.assertEqual(obj.input_mode, "text")

    def test_blank_markdown_file_counts_as_absent(self) -> None:
        obj = MarkdownSplitterInput(markdown_text="# H", markdown_file="   ")
        self.assertEqual(obj.input_mode, "text")

    def test_both_sources_without_mode_is_rejected(self) -> None:
        # Guessing here could chunk (and bill) the wrong document.
        with self.assertRaisesRegex(ValidationError, "input_mode"):
            MarkdownSplitterInput(
                markdown_text="# H", markdown_file="https://example.com/a.pdf"
            )

    def test_explicit_mode_wins_when_both_sources_given(self) -> None:
        obj = MarkdownSplitterInput(
            input_mode="text",
            markdown_text="# H",
            markdown_file="https://example.com/a.pdf",
        )
        self.assertEqual(obj.input_mode, "text")

    def test_no_source_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "markdown_text or markdown_file"):
            MarkdownSplitterInput()


class InputModelKvsStoreTests(unittest.TestCase):
    def test_kvs_source_requires_kvs_store(self) -> None:
        with self.assertRaisesRegex(ValidationError, "kvs_store"):
            MarkdownSplitterInput(markdown_file="kvs://MY_KEY")

    def test_kvs_source_with_store_is_valid(self) -> None:
        obj = MarkdownSplitterInput(markdown_file="kvs://MY_KEY", kvs_store="abc123")
        self.assertEqual(obj.kvs_store, "abc123")

    def test_kvs_store_not_required_for_urls(self) -> None:
        obj = MarkdownSplitterInput(markdown_file="https://example.com/a.md")
        self.assertIsNone(obj.kvs_store)


class InputModelFieldBoundsTests(unittest.TestCase):
    """Tests for numeric field boundaries."""

    def test_max_chunk_chars_accepts_positive(self) -> None:
        obj = MarkdownSplitterInput(markdown_text="# H", max_chunk_chars=1800)
        self.assertEqual(obj.max_chunk_chars, 1800)

    def test_chunk_size_defaults_to_512_tokens(self) -> None:
        # 200-512 tokens is the best-supported range for modern embedders
        # (Chroma 2024, NVIDIA 2025); 512 is Voyage's contextual default.
        obj = MarkdownSplitterInput(markdown_text="# H")
        self.assertEqual(obj.max_chunk_tokens, 512)
        self.assertIsNone(obj.max_chunk_chars)
        self.assertEqual(obj.tokenizer, "cl100k_base")

    def test_max_chunk_tokens_bounds(self) -> None:
        for bad in (49, 8193):
            with self.assertRaises(ValidationError, msg=bad):
                MarkdownSplitterInput(markdown_text="# H", max_chunk_tokens=bad)
        for good in (50, 8192, None):
            obj = MarkdownSplitterInput(markdown_text="# H", max_chunk_tokens=good)
            self.assertEqual(obj.max_chunk_tokens, good)

    def test_tokenizer_choices(self) -> None:
        obj = MarkdownSplitterInput(markdown_text="# H", tokenizer="o200k_base")
        self.assertEqual(obj.tokenizer, "o200k_base")
        with self.assertRaises(ValidationError):
            MarkdownSplitterInput(markdown_text="# H", tokenizer="gpt2")

    def test_max_chunk_chars_null_disables_cap(self) -> None:
        obj = MarkdownSplitterInput(markdown_text="# H", max_chunk_chars=None)
        self.assertIsNone(obj.max_chunk_chars)

    def test_max_chunk_chars_rejects_below_100(self) -> None:
        # Tiny caps only multiply billed chunks and objects in memory.
        with self.assertRaises(ValidationError):
            MarkdownSplitterInput(markdown_text="# H", max_chunk_chars=99)

    def test_max_chunk_chars_accepts_100(self) -> None:
        obj = MarkdownSplitterInput(markdown_text="# H", max_chunk_chars=100)
        self.assertEqual(obj.max_chunk_chars, 100)

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

    def test_default_headers_are_three_levels(self) -> None:
        # Section-level chunks; matches the Console prefill and the README.
        obj = MarkdownSplitterInput(markdown_text="# H")
        self.assertEqual(obj.headers_to_split_on, ["#", "##", "###"])

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

    def test_empty_proxy_url_means_no_proxy(self) -> None:
        for blank in ("", "   "):
            obj = MarkdownSplitterInput(
                markdown_file="https://example.com/a.pdf", proxy_url=blank
            )
            self.assertIsNone(obj.proxy_url)

    def test_empty_proxy_url_does_not_conflict_with_apify_proxy(self) -> None:
        obj = MarkdownSplitterInput(
            markdown_file="https://example.com/a.pdf", proxy_url="", use_apify_proxy=True
        )
        self.assertTrue(obj.use_apify_proxy)

    def test_invalid_proxy_error_does_not_leak_credentials(self) -> None:
        # Validation errors reach run logs and the status message.
        with self.assertRaises(ValidationError) as ctx:
            MarkdownSplitterInput(
                markdown_file="https://example.com/a.pdf",
                proxy_url="ftp://user:s3cret@proxy.example.com:21",
            )
        self.assertNotIn("s3cret", str(ctx.exception))

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


class ProcessingMetricsChargeLimitTests(unittest.TestCase):
    def test_charge_limit_reached_defaults_false(self) -> None:
        m = ProcessingMetrics(
            input_mode="text", input_chars=1, chunk_count=1, elapsed_ms=0, document_hash="a"
        )
        self.assertFalse(m.charge_limit_reached)

    def test_charge_limit_reached_can_be_reported(self) -> None:
        m = ProcessingMetrics(
            input_mode="text",
            input_chars=1,
            chunk_count=125,
            elapsed_ms=0,
            document_hash="a",
            charge_limit_reached=True,
        )
        self.assertTrue(m.charge_limit_reached)


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


class SourceFingerprintTests(unittest.TestCase):
    def test_same_bytes_and_type_same_fingerprint(self) -> None:
        self.assertEqual(
            source_fingerprint(b"%PDF-1.4", "application/pdf"),
            source_fingerprint(b"%PDF-1.4", "application/pdf"),
        )

    def test_different_bytes_different_fingerprint(self) -> None:
        self.assertNotEqual(
            source_fingerprint(b"a", "text/html"), source_fingerprint(b"b", "text/html")
        )

    def test_content_type_changes_fingerprint(self) -> None:
        # Conversion depends on the MIME type, so identical bytes served
        # under a different type must not reuse the cached result.
        self.assertNotEqual(
            source_fingerprint(b"x", "text/html"), source_fingerprint(b"x", "text/plain")
        )

    def test_type_and_bytes_cannot_collide_by_concatenation(self) -> None:
        self.assertNotEqual(
            source_fingerprint(b"b", "text/a"), source_fingerprint(b"ab", "text/")
        )

    def test_is_16_lowercase_hex_chars(self) -> None:
        self.assertRegex(source_fingerprint(b"x", "text/html"), r"^[0-9a-f]{16}$")


class CacheRecordKeyTests(unittest.TestCase):
    _KEY_RE = r"^state-[0-9a-f]{24}$"  # valid KVS key characters, fixed length

    def test_file_mode_keyed_by_source(self) -> None:
        a = MarkdownSplitterInput(markdown_file="https://example.com/a.pdf")
        b = MarkdownSplitterInput(markdown_file="https://example.com/b.pdf")
        self.assertRegex(cache_record_key(a, "h1"), self._KEY_RE)
        self.assertNotEqual(cache_record_key(a, "h1"), cache_record_key(b, "h1"))
        # An edited document keeps the same record, so it is overwritten.
        self.assertEqual(cache_record_key(a, "h1"), cache_record_key(a, "h2"))

    def test_file_mode_ignores_surrounding_whitespace(self) -> None:
        a = MarkdownSplitterInput(markdown_file="https://example.com/a.pdf")
        b = MarkdownSplitterInput(markdown_file="  https://example.com/a.pdf ")
        self.assertEqual(cache_record_key(a, "h"), cache_record_key(b, "h"))

    def test_text_mode_keyed_by_document_hash(self) -> None:
        p = MarkdownSplitterInput(markdown_text="# H")
        self.assertRegex(cache_record_key(p, "h1"), self._KEY_RE)
        self.assertNotEqual(cache_record_key(p, "h1"), cache_record_key(p, "h2"))


class DocumentSourceTests(unittest.TestCase):
    def test_url_source(self) -> None:
        p = MarkdownSplitterInput(markdown_file=" https://a.example/x.pdf ")
        self.assertEqual(document_source(p), "https://a.example/x.pdf")

    def test_kvs_source_includes_the_store(self) -> None:
        a = MarkdownSplitterInput(markdown_file="kvs://manual", kvs_store="store-a")
        b = MarkdownSplitterInput(markdown_file="kvs://manual", kvs_store="store-b")
        self.assertEqual(document_source(a), "kvs://store-a/manual")
        self.assertNotEqual(document_source(a), document_source(b))

    def test_pasted_text_has_no_source(self) -> None:
        self.assertIsNone(document_source(MarkdownSplitterInput(markdown_text="# H")))

    def test_source_id_overrides(self) -> None:
        for p in (
            MarkdownSplitterInput(markdown_text="# H", source_id="crawl:page-1"),
            MarkdownSplitterInput(markdown_file="https://a.example/x.pdf", source_id="crawl:page-1"),
        ):
            self.assertEqual(document_source(p), "crawl:page-1")


class SourceIdTests(unittest.TestCase):
    def test_blank_source_id_is_none(self) -> None:
        self.assertIsNone(MarkdownSplitterInput(markdown_text="# H", source_id="  ").source_id)

    def test_text_with_source_id_keeps_ids_of_untouched_sections(self) -> None:
        # Without a stable identity, one typo anywhere changed every ID.
        def ids(text: str) -> dict[str, str]:
            p = MarkdownSplitterInput(markdown_text=text, source_id="doc-1")
            docs = split_documents(p, text)
            return {
                c.header_path: c.chunk_id
                for c in format_chunks(docs, source=document_source(p), document_hash=document_hash(text))
            }
        before = ids("# A\nalpha\n# B\nbeta")
        after = ids("# A\nalpha\n# B\nbeta (typo fixed)")
        self.assertEqual(before["A"], after["A"])
        self.assertNotEqual(before["B"], after["B"])

    def test_text_cache_key_follows_source_id(self) -> None:
        a = MarkdownSplitterInput(markdown_text="# v1", source_id="doc-1")
        b = MarkdownSplitterInput(markdown_text="# v2", source_id="doc-1")
        self.assertEqual(cache_record_key(a, "h1"), cache_record_key(b, "h2"))


class RetryDelayTests(unittest.TestCase):
    def test_transport_errors_are_retried(self) -> None:
        self.assertEqual(retry_delay(None, None, attempt=0), 1.0)
        self.assertIsNone(retry_delay(None, None, attempt=2))

    def test_non_finite_or_negative_retry_after_falls_back_to_backoff(self) -> None:
        for bad in ("nan", "inf", "-5"):
            self.assertEqual(retry_delay(503, bad, attempt=0), 1.0, bad)

    def test_success_and_client_errors_are_not_retried(self) -> None:
        for status in (200, 301, 400, 403, 404, 410):
            self.assertIsNone(retry_delay(status, None, attempt=0), status)

    def test_rate_limit_and_server_errors_are_retried_with_backoff(self) -> None:
        for status in (429, 500, 502, 503, 504):
            self.assertEqual(retry_delay(status, None, attempt=0), 1.0, status)
        self.assertEqual(retry_delay(503, None, attempt=1), 2.0)

    def test_gives_up_after_max_attempts(self) -> None:
        self.assertIsNone(retry_delay(503, None, attempt=2))

    def test_honours_retry_after_seconds_within_cap(self) -> None:
        self.assertEqual(retry_delay(429, "3", attempt=0), 3.0)

    def test_caps_long_retry_after(self) -> None:
        # Waiting minutes would burn the user's run time and our compute.
        self.assertEqual(retry_delay(429, "120", attempt=0), 5.0)

    def test_ignores_unparseable_retry_after(self) -> None:
        self.assertEqual(retry_delay(429, "Wed, 21 Oct 2026 07:28:00 GMT", attempt=0), 1.0)


class ReadCappedTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    async def _parts(*parts: bytes):
        for part in parts:
            yield part

    async def test_joins_parts_under_limit(self) -> None:
        self.assertEqual(await read_capped(self._parts(b"ab", b"cd"), limit=10), b"abcd")

    async def test_exactly_at_limit_passes(self) -> None:
        self.assertEqual(await read_capped(self._parts(b"ab", b"cd"), limit=4), b"abcd")

    async def test_over_limit_raises_without_reading_everything(self) -> None:
        consumed: list[bytes] = []

        async def parts():
            for part in (b"abc", b"def", b"ghi"):
                consumed.append(part)
                yield part

        with self.assertRaisesRegex(ValueError, "larger than"):
            await read_capped(parts(), limit=5)
        self.assertEqual(consumed, [b"abc", b"def"])


class CacheHitTests(unittest.TestCase):
    _STATE = {"document_hash": "d1", "source_hash": "s1", "split_config": "c1"}

    def test_document_hash_match_is_a_hit(self) -> None:
        self.assertTrue(cache_hit(self._STATE, "c1", document_hash="d1"))

    def test_source_hash_match_is_a_hit(self) -> None:
        self.assertTrue(cache_hit(self._STATE, "c1", source_hash="s1"))

    def test_split_config_mismatch_is_a_miss(self) -> None:
        self.assertFalse(cache_hit(self._STATE, "c2", document_hash="d1"))
        self.assertFalse(cache_hit(self._STATE, "c2", source_hash="s1"))

    def test_hash_mismatch_is_a_miss(self) -> None:
        self.assertFalse(cache_hit(self._STATE, "c1", document_hash="other"))
        self.assertFalse(cache_hit(self._STATE, "c1", source_hash="other"))

    def test_no_hash_given_is_a_miss(self) -> None:
        self.assertFalse(cache_hit(self._STATE, "c1"))

    def test_state_without_source_hash_never_matches_source(self) -> None:
        # States written before source hashing existed lack the key; None
        # must not compare equal to a missing value.
        legacy = {"document_hash": "d1", "split_config": "c1"}
        self.assertFalse(cache_hit(legacy, "c1", source_hash=None))
        self.assertFalse(cache_hit(legacy, "c1", source_hash="s1"))

    def test_non_dict_state_is_a_miss(self) -> None:
        for state in (None, "garbage", 42, ["d1"]):
            self.assertFalse(cache_hit(state, "c1", document_hash="d1"))


class InputModelDefaultsTests(unittest.TestCase):
    def test_max_input_chars_default_fits_a_long_manual(self) -> None:
        # A 300-page PDF converts to ~600k-1M characters.
        self.assertEqual(MarkdownSplitterInput(markdown_text="# H").max_input_chars, 2_000_000)

    def test_cache_namespace_defaults_empty(self) -> None:
        self.assertEqual(MarkdownSplitterInput(markdown_text="# H").cache_namespace, "")


class CacheNamespaceTests(unittest.TestCase):
    def test_namespace_separates_pipelines(self) -> None:
        dev = MarkdownSplitterInput(markdown_file="https://a.example/x.pdf", cache_namespace="dev")
        prod = MarkdownSplitterInput(markdown_file="https://a.example/x.pdf", cache_namespace="prod")
        self.assertNotEqual(cache_record_key(dev, ""), cache_record_key(prod, ""))

    def test_kvs_store_is_part_of_the_key(self) -> None:
        a = MarkdownSplitterInput(markdown_file="kvs://doc", kvs_store="store-a")
        b = MarkdownSplitterInput(markdown_file="kvs://doc", kvs_store="store-b")
        self.assertNotEqual(cache_record_key(a, ""), cache_record_key(b, ""))


class ErrorMessageTests(unittest.TestCase):
    def test_blocked_suggests_proxy(self) -> None:
        for status in (401, 403, 429):
            msg = http_error_message(status, "https://a.example/x.pdf")
            self.assertIn(str(status), msg)
            self.assertIn("use_apify_proxy", msg)

    def test_not_found_names_the_url(self) -> None:
        msg = http_error_message(404, "https://a.example/x.pdf")
        self.assertIn("404", msg)
        self.assertIn("https://a.example/x.pdf", msg)
        self.assertNotIn("use_apify_proxy", msg)

    def test_validation_errors_are_one_line_per_field(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            MarkdownSplitterInput(markdown_text="# H", max_chunks=0)
        msg = validation_error_message(ctx.exception)
        self.assertIn("max_chunks", msg)
        self.assertNotIn("pydantic.dev", msg)
        self.assertNotIn("\n", msg)

    def test_model_level_errors_have_no_field_prefix(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            MarkdownSplitterInput()
        self.assertEqual(
            validation_error_message(ctx.exception),
            "Invalid input: Provide markdown_text or markdown_file.",
        )


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

    def test_char_cap_makes_token_cap_irrelevant(self) -> None:
        a = split_config_fingerprint(self._params(max_chunk_chars=2000, max_chunk_tokens=512))
        b = split_config_fingerprint(self._params(max_chunk_chars=2000, max_chunk_tokens=None))
        self.assertEqual(a, b)

    def test_token_settings_change_fingerprint(self) -> None:
        base = split_config_fingerprint(self._params())
        self.assertNotEqual(base, split_config_fingerprint(self._params(max_chunk_tokens=256)))
        self.assertNotEqual(base, split_config_fingerprint(self._params(tokenizer="o200k_base")))

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

    def test_token_count_is_real_tokenizer_count(self) -> None:
        # chars/4 undercounts CJK by 2-5x; count real tokens instead.
        text = "这是一个用于测试的中文句子。"
        for name in ("cl100k_base", "o200k_base"):
            chunk = format_chunks([self._Doc(text, {})], tokenizer=name)[0]
            self.assertEqual(chunk.token_count, len(tiktoken.get_encoding(name).encode(text)))

    def test_embedding_text_prepends_header_path(self) -> None:
        chunk = format_chunks([self._Doc("Use pip.", {"Header 1": "Guide", "Header 2": "Install"})])[0]
        self.assertEqual(chunk.embedding_text, "Guide > Install\n\nUse pip.")

    def test_embedding_text_does_not_repeat_kept_headers(self) -> None:
        # With strip_headers off, content already starts with its header.
        doc = self._Doc("## Install\nUse pip.", {"Header 1": "Guide", "Header 2": "Install"})
        chunk = format_chunks([doc], prepend_header_path=False)[0]
        self.assertEqual(chunk.embedding_text, "## Install\nUse pip.")

    def test_embedding_text_is_content_without_headers(self) -> None:
        self.assertEqual(format_chunks([self._Doc("Intro text", {})])[0].embedding_text, "Intro text")

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

    def test_identical_chunks_get_distinct_ids(self) -> None:
        # Two identical sections under the same headers are both pushed and
        # billed; equal IDs would make a keyed upsert silently drop one.
        doc = lambda: self._Doc("TODO", {"Header 1": "API", "Header 2": "Example"})  # noqa: E731
        ids = [c.chunk_id for c in format_chunks([doc(), doc(), doc()])]
        self.assertEqual(len(set(ids)), 3)

    def test_first_occurrence_keeps_its_standalone_id(self) -> None:
        alone = format_chunks([self._Doc("x", {})])[0].chunk_id
        first, _second = format_chunks([self._Doc("x", {}), self._Doc("x", {})])
        self.assertEqual(first.chunk_id, alone)

    def test_identical_chunks_in_different_sources_get_distinct_ids(self) -> None:
        # A shared "## License" section in two documents must not overwrite
        # the other document's vector when upserting by chunk_id.
        doc = self._Doc("MIT", {"Header 2": "License"})
        a = format_chunks([doc], source="https://a.example/x.pdf")[0].chunk_id
        b = format_chunks([doc], source="https://b.example/y.pdf")[0].chunk_id
        self.assertNotEqual(a, b)

    def test_text_mode_ids_are_scoped_by_document_hash(self) -> None:
        doc = self._Doc("MIT", {"Header 2": "License"})
        a = format_chunks([doc], document_hash="aaaa")[0].chunk_id
        b = format_chunks([doc], document_hash="bbbb")[0].chunk_id
        self.assertNotEqual(a, b)

    def test_ids_stable_for_same_source(self) -> None:
        doc = self._Doc("MIT", {"Header 2": "License"})
        a = format_chunks([doc], source="https://a.example/x.pdf", document_hash="h1")[0]
        b = format_chunks([doc], source="https://a.example/x.pdf", document_hash="h2")[0]
        # Unchanged sections keep their ID when another part of the file changes.
        self.assertEqual(a.chunk_id, b.chunk_id)

    def test_provenance_fields(self) -> None:
        chunks = format_chunks(
            [self._Doc("a", {"Header 1": "Guide"}), self._Doc("b", {"Header 1": "Guide"})],
            source="https://a.example/x.pdf",
            document_hash="h1",
        )
        self.assertEqual([c.chunk_index for c in chunks], [0, 1])
        self.assertEqual({c.chunk_total for c in chunks}, {2})
        self.assertEqual({c.source for c in chunks}, {"https://a.example/x.pdf"})
        self.assertEqual({c.document_hash for c in chunks}, {"h1"})

    def test_header_path_follows_header_level_not_dict_order(self) -> None:
        chunk = format_chunks(
            [self._Doc("x", {"Header 3": "Windows", "Header 1": "Guide", "Header 2": "Install"})]
        )[0]
        self.assertEqual(chunk.header_path, "Guide > Install > Windows")

    def test_header_path_empty_without_headers(self) -> None:
        self.assertEqual(format_chunks([self._Doc("x", {})])[0].header_path, "")

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
            input_mode="file", kvs_store="store", markdown_file="kvs://MY_KEY"
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
            input_mode="file", kvs_store="store", markdown_file="kvs://SOME_KEY"
        )
        await resolve_markdown_text(
            params=params,
            fetch_remote=lambda _: (b"", ""),
            load_kvs=lambda key: received_keys.append(key) or "# content",  # type: ignore[func-returns-value]
        )
        self.assertEqual(received_keys, ["SOME_KEY"])

    async def test_file_mode_rejects_empty_kvs_key(self) -> None:
        params = MarkdownSplitterInput(
            input_mode="file", kvs_store="store", markdown_file="kvs://"
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
            input_mode="file", kvs_store="store", markdown_file="kvs://MY_KEY"
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
            input_mode="file", kvs_store="store", markdown_file="kvs://MY_KEY"
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

    def test_impossible_chunk_count_fails_before_resplitting(self) -> None:
        # 100k chars at a 100-char cap need >= 1000 chunks; with
        # max_chunks=10 the run must fail before building any of them.
        text = "word " * 20_000
        with patch(
            "src.processing.RecursiveCharacterTextSplitter.split_documents",
            side_effect=AssertionError("re-split should not run"),
        ):
            with self.assertRaisesRegex(ValueError, "max_chunks"):
                split_documents(
                    self._params(headers_to_split_on=[], max_chunk_chars=100, max_chunks=10),
                    text,
                )

    def test_early_check_does_not_reject_feasible_splits(self) -> None:
        text = "word " * 100  # 500 chars at a 200 cap: 3+ chunks
        docs = split_documents(
            self._params(headers_to_split_on=[], max_chunk_chars=200, max_chunks=5), text
        )
        self.assertLessEqual(len(docs), 5)

    def test_cjk_text_splits_at_sentence_ends(self) -> None:
        # CJK has no spaces; without sentence separators the splitter would
        # cut mid-sentence at the character level.
        sentence = "这是一个用于测试的中文句子，它没有空格。"
        docs = split_documents(
            self._params(headers_to_split_on=[], max_chunk_chars=100), sentence * 20
        )
        self.assertGreater(len(docs), 1)
        for d in docs[:-1]:
            self.assertTrue(d.page_content.endswith("。"), d.page_content[-5:])

    def test_token_cap_bounds_every_chunk(self) -> None:
        enc = tiktoken.get_encoding("cl100k_base")
        for text in ("word " * 3000, "这是一个用于测试的中文句子，它没有空格。" * 300):
            docs = split_documents(self._params(headers_to_split_on=[]), text)
            self.assertGreater(len(docs), 1)
            self.assertLessEqual(max(len(enc.encode(d.page_content)) for d in docs), 512)

    def test_token_chunks_fill_close_to_the_cap(self) -> None:
        # Text without paragraph breaks splits at word level; pieces must be
        # counted in context or chunks come out far below the cap (and users
        # pay for more of them).
        enc = tiktoken.get_encoding("cl100k_base")
        words = ("retrieval augmented generation needs well sized chunks " * 600).strip()
        docs = split_documents(self._params(headers_to_split_on=[]), words)
        sizes = sorted(len(enc.encode(d.page_content)) for d in docs[:-1])
        self.assertLessEqual(sizes[-1], 512)
        self.assertGreaterEqual(sizes[len(sizes) // 2], 460)

    def test_sections_under_token_cap_stay_whole(self) -> None:
        docs = split_documents(self._params(headers_to_split_on=["#"]), "# A\nshort body")
        self.assertEqual([d.page_content for d in docs], ["short body"])

    def test_char_cap_overrides_token_cap(self) -> None:
        docs = split_documents(
            self._params(headers_to_split_on=[], max_chunk_chars=300), "word " * 600
        )
        self.assertLessEqual(max(len(d.page_content) for d in docs), 300)

    def test_no_cap_when_both_disabled(self) -> None:
        text = "word " * 3000
        docs = split_documents(
            self._params(headers_to_split_on=[], max_chunk_tokens=None), text
        )
        self.assertEqual(len(docs), 1)

    def test_token_mode_early_max_chunks_check(self) -> None:
        with patch(
            "src.processing.RecursiveCharacterTextSplitter.split_documents",
            side_effect=AssertionError("re-split should not run"),
        ):
            with self.assertRaisesRegex(ValueError, "max_chunks"):
                split_documents(
                    self._params(headers_to_split_on=[], max_chunk_tokens=50, max_chunks=10),
                    "word " * 5000,
                )

    def test_chunk_count_exceeding_max_chunks_raises(self) -> None:
        text = "# A\none\n\n# B\ntwo"
        with self.assertRaisesRegex(ValueError, "max_chunks"):
            split_documents(self._params(headers_to_split_on=["#"], max_chunks=1), text)


# ---------------------------------------------------------------------------
# Schema ↔ model congruence
# ---------------------------------------------------------------------------


class SchemaCongruenceTests(unittest.TestCase):
    """Pin the contract between INPUT_SCHEMA.json and the model.

    The platform applies schema ``default`` values to API runs, so source
    fields must only be prefilled (Console form and Apify's daily test),
    never defaulted, or a caller's own input could be ignored.
    """

    @classmethod
    def setUpClass(cls) -> None:
        path = Path(__file__).resolve().parent.parent / ".actor" / "INPUT_SCHEMA.json"
        cls.schema = json.loads(path.read_text())

    def test_source_fields_have_no_defaults(self) -> None:
        for field in ("input_mode", "markdown_text", "markdown_file"):
            self.assertNotIn("default", self.schema["properties"][field], field)

    def test_markdown_file_prefill_is_a_valid_url_input(self) -> None:
        # Apify's daily test runs the prefilled input and needs chunks.
        prefill = self.schema["properties"]["markdown_file"]["prefill"]
        obj = MarkdownSplitterInput(markdown_file=prefill)
        self.assertEqual(obj.input_mode, "file")

    def test_kvs_store_is_a_read_only_store_picker(self) -> None:
        field = self.schema["properties"]["kvs_store"]
        self.assertEqual(field["editor"], "resourcePicker")
        self.assertEqual(field["resourceType"], "keyValueStore")
        self.assertEqual(field["resourcePermissions"], ["READ"])

    def test_numeric_limits_match_model(self) -> None:
        props = self.schema["properties"]
        cases = {
            "max_input_chars": {"default": 2_000_000, "minimum": 1, "maximum": 5_000_000},
            "max_chunks": {"default": 5_000, "minimum": 1, "maximum": 50_000},
            "max_chunk_chars": {"minimum": 100, "maximum": 100_000},
            "max_chunk_tokens": {"default": 512, "minimum": 50, "maximum": 8192},
        }
        for field, expected in cases.items():
            for key, value in expected.items():
                self.assertEqual(
                    props[field].get(key), value, f"{field}.{key} drifted from the model"
                )

    def test_null_chunk_cap_passes_platform_validation(self) -> None:
        # The model accepts null to disable the cap; the platform validates
        # API input against the schema first, so the field must be nullable.
        for field in ("max_chunk_chars", "max_chunk_tokens"):
            self.assertIs(self.schema["properties"][field].get("nullable"), True, field)

    def test_tokenizer_enum_matches_model(self) -> None:
        field = self.schema["properties"]["tokenizer"]
        self.assertEqual(field["enum"], ["cl100k_base", "o200k_base"])
        self.assertEqual(field["default"], "cl100k_base")

    def test_headers_default_matches_model(self) -> None:
        self.assertEqual(
            self.schema["properties"]["headers_to_split_on"]["default"],
            MarkdownSplitterInput(markdown_text="# H").headers_to_split_on,
        )

    def test_dataset_schema_matches_chunk_model(self) -> None:
        path = Path(__file__).resolve().parent.parent / ".actor" / "dataset_schema.json"
        fields = json.loads(path.read_text())["fields"]
        self.assertEqual(set(fields["properties"]), set(MarkdownChunk.model_fields))
        self.assertEqual(set(fields["required"]), set(MarkdownChunk.model_fields))

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

        class _StreamInfo:
            def __init__(self, **fields) -> None:
                self.__dict__.update(fields)

        class _Converter:
            def __init__(self) -> None:
                self.last_info: _StreamInfo | None = None

            # Strict like markitdown 0.1.x: hints only via stream_info.
            def convert_stream(self, _stream, *, stream_info=None):
                self.last_info = stream_info
                return _Result()

        _instance = _Converter()

        fake = ModuleType("markitdown")
        fake.MarkItDown = lambda enable_plugins=False: _instance  # type: ignore[attr-defined]
        fake.StreamInfo = _StreamInfo  # type: ignore[attr-defined]
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
        self.assertEqual(fake._instance.last_info.mimetype, "application/pdf")  # type: ignore[attr-defined]

    def test_empty_content_type_leaves_mimetype_to_sniffing(self) -> None:
        fake = self._fake_markitdown_module("# ok")
        with patch.dict("sys.modules", {"markitdown": fake}):
            convert_to_markdown(b"data", "")
        self.assertIsNone(fake._instance.last_info.mimetype)  # type: ignore[attr-defined]

    def test_splits_charset_and_passes_url_and_extension(self) -> None:
        fake = self._fake_markitdown_module("# ok")
        with patch.dict("sys.modules", {"markitdown": fake}):
            convert_to_markdown(
                b"data",
                "Text/HTML; charset=ISO-8859-1",
                url="https://example.com/docs/page.HTML?x=1",
            )
        info = fake._instance.last_info  # type: ignore[attr-defined]
        self.assertEqual(info.mimetype, "text/html")
        self.assertEqual(info.charset, "ISO-8859-1")
        self.assertEqual(info.url, "https://example.com/docs/page.HTML?x=1")
        self.assertEqual(info.extension, ".html")

    def test_octet_stream_is_treated_as_unknown(self) -> None:
        # Servers send octet-stream for any binary; let markitdown sniff.
        fake = self._fake_markitdown_module("# ok")
        with patch.dict("sys.modules", {"markitdown": fake}):
            convert_to_markdown(b"data", "application/octet-stream")
        self.assertIsNone(fake._instance.last_info.mimetype)  # type: ignore[attr-defined]

    def test_real_markitdown_honours_declared_charset(self) -> None:
        html = "<h1>Menu</h1><p>café</p>".encode("latin-1")
        result = convert_to_markdown(html, "text/html; charset=ISO-8859-1")
        self.assertIn("café", result)

    def test_real_markitdown_converts_declared_csv_to_table(self) -> None:
        result = convert_to_markdown(b"name,qty\napple,3\n", "text/csv")
        self.assertIn("| name | qty |", result)

    def test_empty_result_mentions_scanned_pdfs(self) -> None:
        fake = self._fake_markitdown_module("  ")
        with patch.dict("sys.modules", {"markitdown": fake}):
            with self.assertRaisesRegex(ValueError, "OCR"):
                convert_to_markdown(b"%PDF", "application/pdf")

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
            def convert_stream(self, stream, *, stream_info=None):
                received_streams.append(stream.read())

                class _R:
                    text_content = "# ok"

                return _R()

        fake = ModuleType("markitdown")
        fake.MarkItDown = lambda enable_plugins=False: _Converter()  # type: ignore[attr-defined]
        fake.StreamInfo = lambda **fields: fields  # type: ignore[attr-defined]
        with patch.dict("sys.modules", {"markitdown": fake}):
            convert_to_markdown(b"\x25\x50\x44\x46", "application/pdf")

        self.assertEqual(received_streams, [b"\x25\x50\x44\x46"])


if __name__ == "__main__":
    unittest.main()
