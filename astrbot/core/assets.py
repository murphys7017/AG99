from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AssetRef:
    """Safe metadata identity for media referenced by a dialogue turn."""

    reference_id: str
    identity_kind: str
    kind: str
    source: str
    resolvable: bool = False
    caption: str | None = None
    name: str | None = None


def create_asset_ref(
    *,
    kind: str,
    source_ref: str,
    source: str,
    content_sha256: str | None = None,
    caption: str | None = None,
    name: str | None = None,
) -> AssetRef:
    """Create metadata without persisting temporary paths, URLs, or inline media."""

    digest = _normalize_sha256(content_sha256)
    if digest is not None:
        reference_id = f"content-sha256:{digest}"
        identity_kind = "content_sha256"
    else:
        source_digest = hashlib.sha256(f"{kind}\0{source_ref}".encode()).hexdigest()
        reference_id = f"source-sha256:{source_digest}"
        identity_kind = "source_reference"
    return AssetRef(
        reference_id=reference_id,
        identity_kind=identity_kind,
        kind=kind,
        source=source,
        caption=caption,
        name=name,
    )


def _normalize_sha256(value: str | None) -> str | None:
    digest = str(value or "").strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        return None
    return digest


__all__ = ["AssetRef", "create_asset_ref"]
