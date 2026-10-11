"""Pydantic models defining the Actor's input contract and output records.

All validation lives here so that invalid input fails fast — before any
compute is spent — which keeps runs cheap and error messages actionable.
"""

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

#: Proxy URL schemes accepted for user-supplied proxies in ``proxy_url``.
_PROXY_SCHEMES = ("http://", "https://", "socks5://")


class MarkdownSplitterInput(BaseModel):
    input_mode: Literal["text", "file"] = Field(
        "text", description="Input mode: direct markdown text or file reference"
    )
    markdown_text: Optional[str] = Field(
        None, description="Markdown content to split (text mode)"
    )
    markdown_file: Optional[str] = Field(
        None,
        description=(
            "Source in file mode. Supported: HTTP(S) URL for markdown, HTML, PDF, "
            "Word, Excel, PowerPoint, CSV, JSON, XML, EPUB, and more; or kvs://KEY."
        ),
    )
    max_input_chars: int = Field(
        500_000, ge=1, le=5_000_000, description="Maximum accepted input size in chars"
    )
    max_chunks: int = Field(
        5_000, ge=1, le=50_000, description="Maximum number of chunks produced"
    )
    max_chunk_chars: Optional[int] = Field(
        None,
        ge=1,
        le=100_000,
        description="Optional second-pass cap for chunk size in characters",
    )
    headers_to_split_on: list[str] = Field(
        default=["#", "##", "###", "####", "#####", "######"],
        description="Header levels to split on (e.g., ['#', '##'])",
    )
    strip_headers: bool = Field(True, description="Remove headers from chunks")
    use_apify_proxy: bool = Field(
        False,
        description=(
            "Route the remote fetch through Apify Proxy (datacenter, auto group). "
            "Billed by Apify as standard platform usage; off by default."
        ),
    )
    proxy_url: Optional[str] = Field(
        None,
        description=(
            "Bring your own proxy for the remote fetch, e.g. "
            "http://user:pass@host:8080 or socks5://host:1080. Free: no Apify "
            "proxy charges. Mutually exclusive with use_apify_proxy."
        ),
    )

    @field_validator("headers_to_split_on")
    @classmethod
    def validate_headers(cls, v: list[str]) -> list[str]:
        if len(v) > 6:
            raise ValueError("You can provide at most 6 header levels.")
        for h in v:
            stripped = h.strip()
            if (
                not stripped.startswith("#")
                or not stripped.replace("#", "").strip() == ""
            ):
                raise ValueError(f"Invalid header format: {h}. Use '#', '##', etc.")
        return v

    @field_validator("proxy_url")
    @classmethod
    def validate_proxy_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        stripped = v.strip()
        if not stripped.startswith(_PROXY_SCHEMES) or not re.match(
            r"^[a-z0-9]+://[\w.\-:@\[\]]+", stripped
        ):
            raise ValueError(
                f"Invalid proxy_url: {v}. Use http://, https:// or socks5:// "
                "with a host, e.g. http://user:pass@host:8080."
            )
        return stripped

    @model_validator(mode="after")
    def validate_input_source(self) -> "MarkdownSplitterInput":
        if self.use_apify_proxy and self.proxy_url is not None:
            raise ValueError(
                "Set either use_apify_proxy or proxy_url, not both."
            )
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
    """Run summary returned as the Actor's named output (not billed)."""

    input_mode: Literal["text", "file"] = Field(..., description="Input mode used")
    input_file_type: str = Field(
        "text/markdown",
        description="Detected file MIME type or text/markdown for direct input.",
    )
    input_chars: int = Field(..., ge=0, description="Input size in characters")
    chunk_count: int = Field(..., ge=0, description="Produced chunk count")
    elapsed_ms: int = Field(..., ge=0, description="Processing time in milliseconds")
