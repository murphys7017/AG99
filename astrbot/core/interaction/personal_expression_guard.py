from __future__ import annotations

import hashlib
import unicodedata

PREVIOUS_EXPRESSION_FINGERPRINT_METADATA_KEY = (
    "previous_expression_fingerprint"
)


def fingerprint_personal_expression(text: object) -> str | None:
    normalized = unicodedata.normalize("NFKC", str(text or "")).casefold()
    normalized = "".join(
        char
        for char in normalized
        if not char.isspace()
        and not unicodedata.category(char).startswith("P")
    )
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


__all__ = [
    "PREVIOUS_EXPRESSION_FINGERPRINT_METADATA_KEY",
    "fingerprint_personal_expression",
]
