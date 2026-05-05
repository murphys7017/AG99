from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from astrbot.core.star.star_handler import star_map

from .contributors import coerce_priority


@dataclass(slots=True)
class ContributorRegistration:
    contributor: Any
    plugin_id: str
    definition_module_path: str
    owner_module_path: str | None
    seq: int


def normalize_plugin_owner_module(module_path: str | None) -> str | None:
    if not isinstance(module_path, str) or not module_path:
        return None
    parts = module_path.split(".")
    for index, part in enumerate(parts):
        if part in {"builtin_stars", "plugins"} and index + 1 < len(parts):
            return ".".join(parts[: index + 2] + ["main"])
    return module_path


def is_registration_active(registration: ContributorRegistration) -> bool:
    for candidate in (
        registration.owner_module_path,
        registration.definition_module_path,
    ):
        if not candidate:
            continue
        plugin = star_map.get(candidate)
        if plugin is not None:
            return bool(plugin.activated)
    return True


def matches_module_prefix(
    registration: ContributorRegistration,
    module_prefix: str,
) -> bool:
    for candidate in (
        registration.definition_module_path,
        registration.owner_module_path,
    ):
        if not candidate:
            continue
        if candidate == module_prefix or candidate.startswith(f"{module_prefix}."):
            return True
    return False


def sort_registrations(registrations: list[ContributorRegistration]) -> list[Any]:
    active = [
        registration
        for registration in registrations
        if is_registration_active(registration)
    ]
    active.sort(
        key=lambda registration: (
            coerce_priority(getattr(registration.contributor, "priority", 100)),
            registration.seq,
        )
    )
    return [registration.contributor for registration in active]
