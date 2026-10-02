"""Shared limits for complete document uploads; unrelated to model context."""

DOCUMENT_MAX_BYTES = 64 * 1024 * 1024
DOCUMENT_MAX_BASE64_CHARS = ((DOCUMENT_MAX_BYTES + 2) // 3) * 4
DOCUMENT_LIMIT_LABEL = "64 MiB"
