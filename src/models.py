"""Pydantic models defining the Actor's input contract and output records.

All validation lives here so that invalid input fails fast — before any
compute is spent — which keeps runs cheap and error messages actionable.
"""

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Proxy URL schemes accepted for user-supplied proxies in ``proxy_url``.
_PROXY_SCHEMES = ("http", "https", "socks5")


class MarkdownSplitterInput(BaseModel):
    # Errors reach run logs: keep proxy credentials and huge texts out.
    model_config = ConfigDict(hide_input_in_errors=True)

    input_mode: Optional[Literal["text", "file"]] = Field(
        None,
        description=(
            "Input mode: direct markdown text or file reference. Inferred from "
            "whichever of markdown_text / markdown_file is set when omitted."
        ),
    )
    markdown_text: Optional[str] = Field(
        None, description="Markdown content to split (text mode)"
    )
    markdown_file: Optional[str] = Field(
        None,
        description=(
            "Source in file mode. Supported: HTTP(S) URL for markdown, HTML, PDF, "
            "Word, Excel, PowerPoint, CSV, JSON, XML, EPUB, and more; or kvs://KEY "
            "to read a record from the store selected in kvs_store."
        ),
    )
    kvs_store: Optional[str] = Field(
        None,
        description=(
            "Key-value store (ID) that kvs://KEY sources read from. Selected via "
            "the Console store picker, which grants the run read access."
        ),
    )
    max_input_chars: int = Field(
        500_000, ge=1, le=5_000_000, description="Maximum accepted input size in chars"
    )
    max_chunks: int = Field(
        5_000, ge=1, le=50_000, description="Maximum number of chunks produced"
    )
    max_chunk_chars: Optional[int] = Field(
        1600,
        ge=100,
        le=100_000,
        description=(
            "Second-pass cap for chunk size in characters (default ~400 tokens); "
            "only oversized sections are re-split. null disables the cap."
        ),
    )
    headers_to_split_on: list[str] = Field(
        default=["#", "##", "###", "####", "#####", "######"],
        description="Header levels to split on (e.g., ['#', '##'])",
    )
    strip_headers: bool = Field(True, description="Remove headers from chunks")
    skip_if_unchanged: bool = Field(
        False,
        description=(
            "Skip re-chunking when the source document and split settings are "
            "unchanged since the last run: nothing is pushed to the dataset and "
            "the run costs only the start event. The Output reports "
            "unchanged=true with the cached chunk count. First runs always "
            "process."
        ),
    )
    use_apify_proxy: bool = Field(
        False,
        description=(
            "Route the remote fetch through Apify Proxy (datacenter, auto group). "
            "Included in the price; off by default."
        ),
    )
    proxy_url: Optional[str] = Field(
        None,
        description=(
            "Bring your own proxy for the remote fetch, e.g. "
            "http://user:pass@host:8080 or socks5://host:1080. Mutually "
            "exclusive with use_apify_proxy."
        ),
    )

    @field_validator("headers_to_split_on")
    @classmethod
    def validate_headers(cls, v: list[str]) -> list[str]:
        if len(v) > 6:
            raise ValueError("You can provide at most 6 header levels.")
        # Markdown headers are 1–6 '#' characters; anything else would never
        # match in the splitter. Entries are normalised (stripped) so that
        # ' ## ' and '##' fingerprint identically for change detection.
        normalised = [h.strip() for h in v]
        for raw, h in zip(v, normalised):
            if not re.fullmatch(r"#{1,6}", h):
                raise ValueError(f"Invalid header format: {raw}. Use '#', '##', etc.")
        return normalised

    @field_validator("proxy_url")
    @classmethod
    def validate_proxy_url(cls, v: Optional[str]) -> Optional[str]:
        stripped = (v or "").strip()
        if not stripped:
            return None
        scheme, has_sep, rest = stripped.partition("://")
        if (
            not has_sep
            or scheme.lower() not in _PROXY_SCHEMES
            or not re.match(r"^[\w.\-:@\[\]]+", rest)
        ):
            # Never echo the value: it may carry proxy credentials.
            raise ValueError(
                "Invalid proxy_url. Use http://, https:// or socks5:// "
                "with a host, e.g. http://user:pass@host:8080."
            )
        # Schemes are case-insensitive (RFC 3986); only the scheme is
        # lowercased so userinfo, host and path keep their original case.
        return f"{scheme.lower()}://{rest}"

    @model_validator(mode="after")
    def validate_input_source(self) -> "MarkdownSplitterInput":
        if self.use_apify_proxy and self.proxy_url is not None:
            raise ValueError(
                "Set either use_apify_proxy or proxy_url, not both."
            )
        if self.input_mode is None:
            has_text = bool(self.markdown_text and self.markdown_text.strip())
            has_file = bool(self.markdown_file and self.markdown_file.strip())
            if has_text and has_file:
                raise ValueError(
                    "Both markdown_text and markdown_file are set: choose one with "
                    "input_mode ('text' or 'file')."
                )
            if not (has_text or has_file):
                raise ValueError("Provide markdown_text or markdown_file.")
            self.input_mode = "file" if has_file else "text"
        if self.input_mode == "text":
            if self.markdown_text is None or not self.markdown_text.strip():
                raise ValueError("markdown_text is required when input_mode is 'text'.")
            if len(self.markdown_text) > self.max_input_chars:
                raise ValueError(
                    f"markdown_text exceeds max_input_chars ({self.max_input_chars})."
                )
        else:
            if self.markdown_file is None or not self.markdown_file.strip():
                raise ValueError(
                    "markdown_file is required when input_mode is 'file'."
                )
            if self.markdown_file.strip().startswith("kvs://") and not self.kvs_store:
                raise ValueError(
                    "kvs:// sources need kvs_store: select the key-value store "
                    "that holds the record."
                )
        return self


class MarkdownChunk(BaseModel):
    content: str = Field(..., description="Chunk content")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="LangChain markdown metadata"
    )
    chunk_id: str = Field(..., description="Deterministic chunk identifier")
    char_count: int = Field(..., ge=0, description="Number of characters in content")
    token_count: int = Field(..., ge=0, description="Estimated token count in content")


class ProcessingMetrics(BaseModel):
    """Run summary written to the OUTPUT record (never billed)."""

    input_mode: Literal["text", "file"] = Field(..., description="Input mode used")
    input_file_type: str = Field(
        "text/markdown",
        description="Detected file MIME type or text/markdown for direct input.",
    )
    input_chars: int = Field(..., ge=0, description="Input size in characters")
    chunk_count: int = Field(..., ge=0, description="Chunks pushed in this run")
    elapsed_ms: int = Field(..., ge=0, description="Processing time in milliseconds")
    document_hash: str = Field(
        ...,
        description=(
            "Fingerprint of the resolved document (pre-splitting). Compare "
            "between runs to detect content changes client-side."
        ),
    )
    unchanged: bool = Field(
        False,
        description=(
            "True when skip_if_unchanged detected no change since the last "
            "run; no chunks were pushed and none were billed."
        ),
    )
    cached_chunk_count: Optional[int] = Field(
        None,
        ge=0,
        description="Chunk count from the previous run, present when unchanged=true.",
    )
    charge_limit_reached: bool = Field(
        False,
        description=(
            "True when the run's maximum charge stopped the push early; "
            "chunk_count is then the number of chunks actually delivered."
        ),
    )
