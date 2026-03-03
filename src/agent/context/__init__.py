"""Context module exports."""

from .builder import ContextBuilder, RecentObsContextBuilder, SlotContextBuilder
from .types import ContextPack, ContextSlot, ProviderResult, SlotSpec
from .catalog import ContextCatalog, ContextItem, load_catalog
from .profile import (
    PromptProfile,
    load_profile,
    load_profiles,
    validate_profiles,
    resolve_profile_items,
)
from .presets import ContextPreset, ContextPresetsCollection, load_presets

__all__ = [
    "ContextBuilder",
    "RecentObsContextBuilder",
    "SlotContextBuilder",
    "ContextPack",
    "ContextSlot",
    "ProviderResult",
    "SlotSpec",
    # Catalog & Profile
    "ContextCatalog",
    "ContextItem",
    "load_catalog",
    "PromptProfile",
    "load_profile",
    "load_profiles",
    "validate_profiles",
    "resolve_profile_items",
    # Presets
    "ContextPreset",
    "ContextPresetsCollection",
    "load_presets",
]
