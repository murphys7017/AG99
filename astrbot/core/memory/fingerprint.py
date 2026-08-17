from __future__ import annotations

import hashlib
import json


def build_short_term_fingerprint(
    short_summary: str | None,
    active_focus: str | None,
) -> str:
    payload = {
        "short_summary": (short_summary or "").strip(),
        "active_focus": (active_focus or "").strip(),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["build_short_term_fingerprint"]
