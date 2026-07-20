from __future__ import annotations

from dataclasses import asdict
from typing import Any

from astrbot.core.assets import AssetRef, create_asset_ref
from astrbot.core.provider.entities import ProviderRequest

from .turn_state import get_interaction_turn_state


def build_canonical_user_message(event) -> dict[str, Any]:
    """Build the visible-dialogue user message from collected input facts."""
    text = (event.message_str or "").strip()
    pack = _resolve_context_pack(event)
    assets = _collect_assets(pack)
    if not assets:
        assets = _collect_provider_request_assets(event)

    content_parts: list[dict[str, Any]] = []
    if text:
        content_parts.append({"type": "text", "text": text})
    for asset in assets:
        content_parts.append({"type": "text", "text": _asset_history_marker(asset)})

    if not content_parts:
        content_parts.append({"type": "text", "text": "[non-text input]"})

    content: str | list[dict[str, Any]]
    if len(content_parts) == 1 and text and not assets:
        content = text
    else:
        content = content_parts
    message: dict[str, Any] = {"role": "user", "content": content}
    if assets:
        message["_astrbot_assets"] = [asdict(asset) for asset in assets]
    return message


def _resolve_context_pack(event):
    turn_state = get_interaction_turn_state(event)
    material = getattr(turn_state, "context_material", None)
    return getattr(material, "prompt_context_pack", None)


def _collect_assets(pack) -> list[AssetRef]:
    slots = getattr(pack, "slots", None)
    if not isinstance(slots, dict):
        return []
    captions = _caption_map(slots)
    assets: list[AssetRef] = []
    seen: set[tuple[str, str]] = set()
    for slot_name in ("input.images", "input.quoted_images"):
        slot = slots.get(slot_name)
        records = getattr(slot, "value", None)
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            source_ref = str(record.get("ref", "") or "").strip()
            _append_asset(
                assets,
                seen,
                kind="image",
                source_ref=source_ref,
                source=str(record.get("source", "message") or "message"),
                content_sha256=record.get("sha256") or record.get("content_sha256"),
                caption=captions.get(source_ref),
            )
    file_slot = slots.get("input.files")
    file_records = getattr(file_slot, "value", None)
    if isinstance(file_records, list):
        for record in file_records:
            if not isinstance(record, dict):
                continue
            source_ref = str(
                record.get("file", "") or record.get("url", "") or ""
            ).strip()
            name = str(record.get("name", "") or "").strip() or None
            _append_asset(
                assets,
                seen,
                kind="file",
                source_ref=source_ref or (name or ""),
                source=str(record.get("source", "message") or "message"),
                content_sha256=record.get("sha256") or record.get("content_sha256"),
                name=name,
            )
    return assets


def _caption_map(slots: dict[str, Any]) -> dict[str, str]:
    captions: dict[str, str] = {}
    for slot_name in ("input.image_captions", "input.quoted_image_captions"):
        slot = slots.get(slot_name)
        records = getattr(slot, "value", None)
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            source_ref = str(record.get("ref", "") or "").strip()
            caption = str(record.get("caption", "") or "").strip()
            if source_ref and caption:
                captions[source_ref] = caption
    return captions


def _collect_provider_request_assets(event) -> list[AssetRef]:
    request = event.get_extra("provider_request")
    if not isinstance(request, ProviderRequest):
        return []
    assets: list[AssetRef] = []
    seen: set[tuple[str, str]] = set()
    for source_ref in request.image_urls or []:
        _append_asset(
            assets,
            seen,
            kind="image",
            source_ref=str(source_ref),
            source="provider_request",
        )
    return assets


def _append_asset(
    assets: list[AssetRef],
    seen: set[tuple[str, str]],
    *,
    kind: str,
    source_ref: str,
    source: str,
    content_sha256: str | None = None,
    caption: str | None = None,
    name: str | None = None,
) -> None:
    if not source_ref:
        return
    key = (kind, source_ref)
    if key in seen:
        return
    seen.add(key)
    assets.append(
        create_asset_ref(
            kind=kind,
            source_ref=source_ref,
            source=source,
            content_sha256=content_sha256,
            caption=caption,
            name=name,
        )
    )


def _asset_history_marker(asset: AssetRef) -> str:
    if asset.kind == "image":
        return f"[image: {asset.caption}]" if asset.caption else "[image]"
    return f"[file: {asset.name or asset.reference_id}]"


__all__ = ["AssetRef", "build_canonical_user_message"]
