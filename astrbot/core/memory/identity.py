from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from astrbot.core.platform.astr_message_event import AstrMessageEvent

from .config import MemoryConfig, ensure_identity_mappings_file, get_memory_config
from .store import MemoryStore
from .types import MemoryIdentity, MemoryIdentityBinding


def build_platform_user_key(platform_id: str, sender_user_id: str) -> str:
    return f"{platform_id}:{sender_user_id}"


class MemoryIdentityMappingService:
    def __init__(
        self,
        store: MemoryStore,
        *,
        config: MemoryConfig | None = None,
    ) -> None:
        self.store = store
        self.config = config or store.config or get_memory_config()
        self.mappings_path = Path(self.config.identity.mappings_path)

    async def resolve_canonical_user_id(self, platform_user_key: str) -> str | None:
        binding = await self.store.get_identity_mapping(platform_user_key)
        if binding is None:
            return None
        return binding.canonical_user_id

    async def reload_from_yaml(self) -> int:
        if not self.config.identity.enabled:
            return await self.store.sync_identity_mappings([])
        bindings = self.load_bindings()
        return await self.store.sync_identity_mappings(bindings)

    def load_bindings(self) -> list[MemoryIdentityBinding]:
        if self.config.identity.bindings is not None:
            return self._parse_bindings_payload(
                {"bindings": self.config.identity.bindings}
            )
        return self.load_bindings_from_yaml()

    def load_bindings_from_yaml(self) -> list[MemoryIdentityBinding]:
        payload = self._load_yaml_payload()
        return self._parse_bindings_payload(payload)

    def validate_yaml(self) -> list[MemoryIdentityBinding]:
        return self.load_bindings_from_yaml()

    def write_bindings_to_yaml(
        self,
        bindings: list[MemoryIdentityBinding],
    ) -> None:
        serialized_bindings = [
            {
                "platform_id": binding.platform_id,
                "sender_user_id": binding.sender_user_id,
                "canonical_user_id": binding.canonical_user_id,
                **(
                    {"nickname_hint": binding.nickname_hint}
                    if binding.nickname_hint
                    else {}
                ),
            }
            for binding in sorted(
                bindings,
                key=lambda item: (
                    item.platform_id,
                    item.sender_user_id,
                    item.canonical_user_id,
                    item.platform_user_key,
                ),
            )
        ]
        self.mappings_path.parent.mkdir(parents=True, exist_ok=True)
        self.mappings_path.write_text(
            yaml.safe_dump(
                {"bindings": serialized_bindings},
                allow_unicode=False,
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    def upsert_binding_in_yaml(
        self,
        platform_id: str,
        sender_user_id: str,
        canonical_user_id: str,
        nickname_hint: str | None = None,
    ) -> MemoryIdentityBinding:
        if self.config.identity.bindings is not None:
            raise RuntimeError(
                "memory identity mappings are configured in Web config; "
                "edit memory.identity.bindings instead of the YAML file"
            )
        binding = self._build_binding(
            platform_id=platform_id,
            sender_user_id=sender_user_id,
            canonical_user_id=canonical_user_id,
            nickname_hint=nickname_hint,
        )
        bindings = self.load_bindings_from_yaml()
        updated = False
        for index, existing in enumerate(bindings):
            if existing.platform_user_key != binding.platform_user_key:
                continue
            bindings[index] = binding
            updated = True
            break
        if not updated:
            bindings.append(binding)
        self.write_bindings_to_yaml(bindings)
        return binding

    def remove_binding_from_yaml(self, platform_user_key: str) -> bool:
        if self.config.identity.bindings is not None:
            raise RuntimeError(
                "memory identity mappings are configured in Web config; "
                "edit memory.identity.bindings instead of the YAML file"
            )
        bindings = self.load_bindings_from_yaml()
        filtered = [
            binding
            for binding in bindings
            if binding.platform_user_key != platform_user_key
        ]
        if len(filtered) == len(bindings):
            return False
        self.write_bindings_to_yaml(filtered)
        return True

    async def bind_platform_user(
        self,
        platform_id: str,
        sender_user_id: str,
        canonical_user_id: str,
        nickname_hint: str | None = None,
    ) -> MemoryIdentityBinding:
        binding = self.upsert_binding_in_yaml(
            platform_id,
            sender_user_id,
            canonical_user_id,
            nickname_hint=nickname_hint,
        )
        await self.reload_from_yaml()
        loaded = await self.store.get_identity_mapping(binding.platform_user_key)
        if loaded is None:
            raise RuntimeError(
                "memory identity mapping reload finished but binding was not persisted"
            )
        return loaded

    async def unbind_platform_user(self, platform_user_key: str) -> bool:
        removed = self.remove_binding_from_yaml(platform_user_key)
        if not removed:
            return False
        await self.reload_from_yaml()
        return True

    async def list_bindings_for_canonical_user(
        self,
        canonical_user_id: str,
    ) -> list[MemoryIdentityBinding]:
        return await self.store.list_identity_mappings_for_canonical_user(
            canonical_user_id
        )

    def _load_yaml_payload(self) -> dict[str, Any]:
        ensure_identity_mappings_file(self.mappings_path)
        loaded = yaml.safe_load(self.mappings_path.read_text(encoding="utf-8"))
        if loaded is None:
            return {"bindings": []}
        if not isinstance(loaded, dict):
            raise ValueError("memory identity mappings must be a mapping object")
        return loaded

    def _parse_bindings_payload(
        self,
        payload: dict[str, Any],
    ) -> list[MemoryIdentityBinding]:
        raw_bindings = payload.get("bindings")
        if not isinstance(raw_bindings, list):
            raise ValueError("memory identity mappings field `bindings` must be a list")

        bindings: list[MemoryIdentityBinding] = []
        seen_keys: set[str] = set()
        for index, raw_binding in enumerate(raw_bindings):
            if not isinstance(raw_binding, dict):
                raise ValueError(
                    f"memory identity mapping entry #{index} must be an object"
                )
            platform_id = self._required_string(
                raw_binding.get("platform_id"),
                f"bindings[{index}].platform_id",
            )
            sender_user_id = self._required_string(
                raw_binding.get("sender_user_id"),
                f"bindings[{index}].sender_user_id",
            )
            canonical_user_id = self._required_string(
                raw_binding.get("canonical_user_id"),
                f"bindings[{index}].canonical_user_id",
            )
            nickname_hint = self._optional_string(raw_binding.get("nickname_hint"))
            binding = self._build_binding(
                platform_id=platform_id,
                sender_user_id=sender_user_id,
                canonical_user_id=canonical_user_id,
                nickname_hint=nickname_hint,
            )
            if binding.platform_user_key in seen_keys:
                raise ValueError(
                    "duplicate memory identity mapping for platform_user_key "
                    f"`{binding.platform_user_key}`"
                )
            seen_keys.add(binding.platform_user_key)
            bindings.append(binding)
        return bindings

    @staticmethod
    def _required_string(value: Any, field_name: str) -> str:
        if isinstance(value, str) and value.strip():
            return value.strip()
        raise ValueError(
            f"memory identity mapping missing required field `{field_name}`"
        )

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @staticmethod
    def _build_binding(
        *,
        platform_id: str,
        sender_user_id: str,
        canonical_user_id: str,
        nickname_hint: str | None,
    ) -> MemoryIdentityBinding:
        now = datetime.now(UTC)
        return MemoryIdentityBinding(
            mapping_id=str(uuid.uuid4()),
            platform_id=platform_id.strip(),
            sender_user_id=sender_user_id.strip(),
            platform_user_key=build_platform_user_key(
                platform_id.strip(),
                sender_user_id.strip(),
            ),
            canonical_user_id=canonical_user_id.strip(),
            nickname_hint=nickname_hint.strip() if nickname_hint else None,
            created_at=now,
            updated_at=now,
        )


class MemoryIdentityResolver:
    def __init__(self, mapping_service: MemoryIdentityMappingService) -> None:
        self.mapping_service = mapping_service

    async def resolve_from_event(self, event: AstrMessageEvent) -> MemoryIdentity:
        umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
        if not umo:
            raise ValueError(
                "memory identity resolver requires event.unified_msg_origin"
            )

        platform_id = self._normalize_optional_string(
            self._safe_call(event, "get_platform_id")
        )
        sender_user_id = self._normalize_optional_string(
            self._safe_call(event, "get_sender_id")
        )
        sender_nickname = self._resolve_sender_nickname(event)

        platform_user_key: str | None = None
        canonical_user_id: str | None = None
        if platform_id and sender_user_id:
            platform_user_key = build_platform_user_key(platform_id, sender_user_id)
            canonical_user_id = await self.mapping_service.resolve_canonical_user_id(
                platform_user_key
            )

        return MemoryIdentity(
            umo=umo,
            platform_id=platform_id,
            sender_user_id=sender_user_id,
            sender_nickname=sender_nickname,
            platform_user_key=platform_user_key,
            canonical_user_id=canonical_user_id,
        )

    @staticmethod
    def _safe_call(event: AstrMessageEvent, method_name: str) -> Any:
        method = getattr(event, method_name, None)
        if callable(method):
            return method()
        return None

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    def _resolve_sender_nickname(self, event: AstrMessageEvent) -> str | None:
        direct_name = self._normalize_optional_string(
            self._safe_call(event, "get_sender_name")
        )
        if direct_name:
            return direct_name

        message_obj = getattr(event, "message_obj", None)
        sender = getattr(message_obj, "sender", None)
        nickname = getattr(sender, "nickname", None)
        return self._normalize_optional_string(nickname)
