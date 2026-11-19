"""Bounded deterministic normalization for untrusted text-source records."""

from __future__ import annotations

import json
import codecs
import unicodedata
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import ConfigDict, Field, JsonValue, field_validator, model_validator

from pids.contracts import Contract, content_sha256

MAX_RECORD_BYTES = 256 * 1024
MAX_METADATA_BYTES = 16 * 1024


class IncomingRecord(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    owner_id: int = Field(gt=0, strict=True)
    source_id: UUID
    external_id: str | None = Field(default=None, max_length=512)
    text: str | None = Field(default=None, max_length=MAX_RECORD_BYTES)
    payload_bytes: bytes | None = Field(default=None, exclude=True)
    content_type: str = Field(default="text/plain", min_length=1, max_length=128)
    character_encoding: str | None = Field(default="utf-8", max_length=80)
    retention_expires_at: datetime | None = None
    record_type: str = Field(default="text", min_length=1, max_length=64)
    occurred_at: datetime | None = None
    source_timezone: str | None = Field(default=None, max_length=128)
    source_version: str | None = Field(default=None, max_length=256)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("payload_bytes", mode="before")
    @classmethod
    def payload_must_be_bytes(cls, value: bytes | None) -> bytes | None:
        if value is not None and not isinstance(value, bytes):
            raise TypeError("payload_bytes must be bytes")
        if value is not None and len(value) > MAX_RECORD_BYTES:
            raise ValueError("record exceeds the payload byte limit")
        return value

    @field_validator("text")
    @classmethod
    def bounded_utf8_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "\x00" in value:
            raise ValueError("text contains a null character")
        if len(value.encode("utf-8")) > MAX_RECORD_BYTES:
            raise ValueError("record exceeds the UTF-8 byte limit")
        return value

    @field_validator("character_encoding")
    @classmethod
    def supported_encoding(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return codecs.lookup(value).name
        except LookupError as exc:
            raise ValueError("character_encoding must be supported") from exc

    @field_validator("content_type")
    @classmethod
    def safe_content_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        media_type = normalized.split(";", 1)[0].strip()
        if "/" not in media_type or any(ord(char) < 32 or ord(char) == 127 for char in normalized):
            raise ValueError("content_type must be a media type")
        return normalized

    @field_validator("retention_expires_at")
    @classmethod
    def retention_timestamp_is_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("retention_expires_at must include a timezone")
        return value.astimezone(timezone.utc) if value is not None else None

    @field_validator("external_id")
    @classmethod
    def valid_external_id(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("external_id must not be empty")
        return unicodedata.normalize("NFC", value.strip()) if value is not None else None

    @field_validator("occurred_at")
    @classmethod
    def aware_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("occurred_at must include a timezone")
        return value.astimezone(timezone.utc) if value is not None else None

    @field_validator("metadata")
    @classmethod
    def bounded_metadata(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        canonical_json(value)
        return value

    @model_validator(mode="after")
    def payload_and_text_match(self) -> IncomingRecord:
        if self.payload_bytes is None:
            if self.text is None:
                raise ValueError("text or payload_bytes is required")
            return self
        encoding = self.character_encoding or "utf-8"
        try:
            decoded = self.payload_bytes.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError) as exc:
            raise ValueError("payload cannot be decoded using character_encoding") from exc
        if len(self.payload_bytes) > MAX_RECORD_BYTES:
            raise ValueError("record exceeds the payload byte limit")
        if self.text is not None and self.text != decoded:
            raise ValueError("text must match the decoded original payload")
        if "\x00" in decoded:
            raise ValueError("text contains a null character")
        return self

    @property
    def decoded_text(self) -> str:
        if self.payload_bytes is not None:
            return self.payload_bytes.decode(self.character_encoding or "utf-8", errors="strict")
        if self.text is None:
            raise ValueError("text or payload_bytes is required")
        return self.text

    @property
    def original_payload(self) -> bytes:
        if self.payload_bytes is not None:
            return self.payload_bytes
        return self.decoded_text.encode(self.character_encoding or "utf-8")


class NormalizedContent(Contract):
    text: str
    metadata: dict[str, JsonValue]
    normalized_sha256: str


def canonical_json(value: Any) -> str:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("metadata must contain finite JSON values") from exc
    if len(encoded.encode("utf-8")) > MAX_METADATA_BYTES:
        raise ValueError("metadata exceeds the byte limit")
    return encoded


def normalize_text(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("text content must be a string")
    if "\x00" in value:
        raise ValueError("text contains a null character")
    value.encode("utf-8")
    value = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(" ".join(line.split()) for line in value.split("\n")).strip()


def normalize_record(record: IncomingRecord) -> NormalizedContent:
    text = normalize_text(record.decoded_text)
    metadata = json.loads(canonical_json(record.metadata))
    digest = content_sha256(text.encode("utf-8"))
    return NormalizedContent(text=text, metadata=metadata, normalized_sha256=digest)
