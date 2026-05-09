"""Tests for prompt context catalog loading."""

from pathlib import Path

import pytest

from astrbot.core.prompt import context_catalog as context_catalog_module
from astrbot.core.prompt.context_catalog import ContextCatalogLoader, get_catalog


def test_context_catalog_loader_builds_indexes_from_valid_yaml(tmp_path: Path):
    catalog_path = tmp_path / "context_catalog.yaml"
    catalog_path.write_text(
        """
version: "1.2"
contexts:
  - id: persona.prompt
    category: persona
    slots: [persona]
    required: true
    multiple: false
    lifecycle: static
    notes: base persona
    meta:
      parser: legacy_prompt_v1
  - id: knowledge.snippets
    category: tools
    slots: [rag_context]
    required: false
    multiple: true
    lifecycle: dynamic
""".strip(),
        encoding="utf-8",
    )

    catalog = ContextCatalogLoader.load(catalog_path)

    assert catalog.version == "1.2"
    assert catalog.has("persona.prompt") is True
    assert catalog.get("persona.prompt") is not None
    assert catalog.get("persona.prompt").meta == {"parser": "legacy_prompt_v1"}
    assert [item.id for item in catalog.list_by_category("persona")] == [
        "persona.prompt"
    ]
    assert [item.id for item in catalog.list_required()] == ["persona.prompt"]
    assert [item.id for item in catalog.list_allows_multiple()] == [
        "knowledge.snippets"
    ]


def test_default_catalog_declares_interaction_memory_slot():
    catalog = ContextCatalogLoader.load(
        Path("data/config/prompt/context_catalog.yaml"),
        strict=True,
    )

    item = catalog.get("memory.interaction")
    assert item is not None
    assert item.category == "memory"
    assert item.slots == ["history"]


def test_default_catalog_declares_dynamic_context_extension_slot():
    catalog = ContextCatalogLoader.load(
        Path("data/config/prompt/context_catalog.yaml"),
        strict=True,
    )

    item = catalog.get("extension.context")
    assert item is not None
    assert item.category == "extension"
    assert item.slots == ["user_input"]


def test_context_catalog_loader_skips_invalid_items_fail_open(tmp_path: Path):
    catalog_path = tmp_path / "context_catalog.yaml"
    catalog_path.write_text(
        """
version: "1.0"
contexts:
  - id: session.datetime
    category: session
    slots: [system]
    required: false
    multiple: false
    lifecycle: session
  - id: broken.item
    category: invalid
    slots: [system]
    required: false
    multiple: false
    lifecycle: static
""".strip(),
        encoding="utf-8",
    )

    catalog = ContextCatalogLoader.load(catalog_path)

    assert [item.id for item in catalog.contexts] == ["session.datetime"]
    assert catalog.get("broken.item") is None


def test_context_catalog_loader_returns_empty_catalog_when_file_missing(tmp_path: Path):
    missing_path = tmp_path / "missing_context_catalog.yaml"

    catalog = ContextCatalogLoader.load(missing_path)

    assert catalog.version == "0.1"
    assert catalog.contexts == []


def test_context_catalog_loader_raises_when_file_missing_in_strict_mode(tmp_path: Path):
    missing_path = tmp_path / "missing_context_catalog.yaml"

    with pytest.raises(FileNotFoundError, match="Context catalog not found"):
        ContextCatalogLoader.load(missing_path, strict=True)


def test_context_catalog_loader_raises_on_invalid_yaml_in_strict_mode(tmp_path: Path):
    catalog_path = tmp_path / "context_catalog.yaml"
    catalog_path.write_text("contexts: [", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Failed to load catalog yaml"):
        ContextCatalogLoader.load(catalog_path, strict=True)


def test_context_catalog_loader_raises_on_invalid_root_in_strict_mode(tmp_path: Path):
    catalog_path = tmp_path / "context_catalog.yaml"
    catalog_path.write_text("- not-a-dict", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Catalog root must be a dict"):
        ContextCatalogLoader.load(catalog_path, strict=True)


def test_context_catalog_loader_raises_on_invalid_contexts_type_in_strict_mode(
    tmp_path: Path,
):
    catalog_path = tmp_path / "context_catalog.yaml"
    catalog_path.write_text(
        """
version: "1.0"
contexts: {}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Catalog 'contexts' must be a list"):
        ContextCatalogLoader.load(catalog_path, strict=True)


def test_context_catalog_loader_raises_on_invalid_item_in_strict_mode(tmp_path: Path):
    catalog_path = tmp_path / "context_catalog.yaml"
    catalog_path.write_text(
        """
version: "1.0"
contexts:
  - id: broken.item
    category: invalid
    slots: [system]
    required: false
    multiple: false
    lifecycle: static
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Failed to parse catalog item 0"):
        ContextCatalogLoader.load(catalog_path, strict=True)


def test_get_catalog_uses_cache_until_force_reload(tmp_path: Path, monkeypatch):
    catalog_path = tmp_path / "context_catalog.yaml"
    catalog_path.write_text(
        """
version: "1.0"
contexts:
  - id: input.text
    category: input
    slots: [user_input]
    required: false
    multiple: false
    lifecycle: ephemeral
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(context_catalog_module, "_catalog_cache", {})

    first = get_catalog(catalog_path, force_reload=True)

    catalog_path.write_text(
        """
version: "2.0"
contexts: []
""".strip(),
        encoding="utf-8",
    )

    cached = get_catalog(catalog_path)
    reloaded = get_catalog(catalog_path, force_reload=True)

    assert first is cached
    assert cached.version == "1.0"
    assert reloaded.version == "2.0"
    assert reloaded.contexts == []


def test_get_catalog_separates_strict_and_non_strict_cache(tmp_path: Path, monkeypatch):
    catalog_path = tmp_path / "context_catalog.yaml"
    catalog_path.write_text(
        """
version: "1.0"
contexts:
  - id: input.text
    category: input
    slots: [user_input]
    required: false
    multiple: false
    lifecycle: ephemeral
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(context_catalog_module, "_catalog_cache", {})

    non_strict = get_catalog(catalog_path, force_reload=True, strict=False)
    strict = get_catalog(catalog_path, force_reload=True, strict=True)
    cached_non_strict = get_catalog(catalog_path, strict=False)
    cached_strict = get_catalog(catalog_path, strict=True)

    assert non_strict is cached_non_strict
    assert strict is cached_strict
    assert strict is not non_strict
