"""Typed configuration domains used during the configuration migration.

The persisted configuration format is intentionally unchanged in this first
step.  These projections make the intended ownership explicit so consumers can
move away from reading the complete configuration tree or ``default`` profile
directly.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .astrbot_config import AstrBotConfig

BUILTIN_WEBCHAT_ADAPTER_BINDING_ID = "webchat"


def _freeze_value(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_value(item) for key, item in deepcopy(value).items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in deepcopy(value))
    if isinstance(value, set):
        return frozenset(_freeze_value(item) for item in deepcopy(value))
    return value


def _freeze_mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        return MappingProxyType({})
    return _freeze_value(value)


@dataclass(frozen=True)
class ModelProviderRegistry:
    """Provider resources available to profiles.

    This object contains provider definitions only.  Selection of a provider
    for chat, planning, expression, TTS, or STT belongs to a bot profile.
    """

    sources: tuple[Mapping[str, Any], ...]
    definitions: tuple[Mapping[str, Any], ...]

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> ModelProviderRegistry:
        sources = config.get("provider_sources", [])
        definitions = config.get("provider", [])
        return cls(
            sources=tuple(
                _freeze_mapping(item) for item in sources if isinstance(item, dict)
            ),
            definitions=tuple(
                _freeze_mapping(item)
                for item in definitions
                if isinstance(item, dict)
            ),
        )

    @classmethod
    def from_configs(
        cls, configs: Mapping[str, Mapping[str, Any]]
    ) -> ModelProviderRegistry:
        sources: dict[str, Mapping[str, Any]] = {}
        definitions: dict[str, Mapping[str, Any]] = {}
        anonymous_sources: set[str] = set()

        for config_id, config in configs.items():
            local = cls.from_config(dict(config))
            for source in local.sources:
                key = str(source.get("id", "")).strip()
                if not key:
                    key = repr(source)
                    if key in anonymous_sources:
                        continue
                    anonymous_sources.add(key)
                _merge_resource_entry(sources, key, source, "provider source", config_id)
            for definition in local.definitions:
                provider_id = str(definition.get("id", "")).strip()
                if not provider_id:
                    raise ValueError(
                        f"provider definition in config {config_id!r} has no id"
                    )
                _merge_resource_entry(
                    definitions,
                    provider_id,
                    definition,
                    "provider",
                    config_id,
                )

        return cls(
            sources=tuple(sources.values()),
            definitions=tuple(definitions.values()),
        )

    @property
    def provider_ids(self) -> frozenset[str]:
        return frozenset(
            str(item.get("id"))
            for item in self.definitions
            if item.get("id") is not None
        )


@dataclass(frozen=True)
class BotProfileConfig:
    """Behavior and policy configuration for one routed bot profile."""

    config_id: str
    model_policy: Mapping[str, Any]
    interaction_policy: Mapping[str, Any]
    prompt_policy: Mapping[str, Any]
    memory_policy: Mapping[str, Any]
    plugin_policy: Mapping[str, Any]
    output_policy: Mapping[str, Any]
    executor_policy: Mapping[str, Any]
    adapter_binding_ids: tuple[str, ...]

    @classmethod
    def from_config(cls, config_id: str, config: dict[str, Any]) -> BotProfileConfig:
        provider_settings = config.get("provider_settings", {})
        interaction = config.get("interaction_middleware", {})
        tts_settings = config.get("provider_tts_settings", {})
        stt_settings = config.get("provider_stt_settings", {})
        platform_entries = config.get("platform", [])

        # These are profile policies, not provider definitions.  Keep their
        # canonical role names independent from the legacy storage keys.
        model_policy = {
            "chat_provider_id": provider_settings.get("default_provider_id", ""),
            "expression_provider_id": interaction.get("expression_provider_id", ""),
            "planner_provider_id": interaction.get("planner_provider_id", ""),
            "image_caption_provider_id": provider_settings.get(
                "default_image_caption_provider_id", ""
            ),
            "context_compression_provider_id": provider_settings.get(
                "llm_compress_provider_id", ""
            ),
            "stt_provider_id": stt_settings.get("provider_id", ""),
            "tts_provider_id": tts_settings.get("provider_id", ""),
            "fallback_chat_models": deepcopy(
                provider_settings.get("fallback_chat_models", [])
            ),
            "provider_pool": deepcopy(provider_settings.get("provider_pool", ["*"])),
        }

        return cls(
            config_id=config_id,
            model_policy=_freeze_mapping(model_policy),
            interaction_policy=_freeze_mapping(interaction),
            prompt_policy=_freeze_mapping(
                {
                    "persona": provider_settings.get("default_personality", "default"),
                    "prompt_prefix": provider_settings.get("prompt_prefix", ""),
                    "context_limit_reached_strategy": provider_settings.get(
                        "context_limit_reached_strategy"
                    ),
                    "max_context_length": provider_settings.get("max_context_length"),
                }
            ),
            memory_policy=_freeze_mapping(config.get("memory", {})),
            plugin_policy=_freeze_mapping(
                {
                    "plugin_set": config.get("plugin_set", ["*"]),
                    "capability_targets": interaction.get(
                        "plugin_capability_targets", {}
                    ),
                }
            ),
            output_policy=_freeze_mapping(
                {
                    "streaming_response": provider_settings.get(
                        "streaming_response", False
                    ),
                    "segmented_reply": config.get("platform_settings", {}).get(
                        "segmented_reply", {}
                    ),
                    "tts_enabled": bool(tts_settings.get("enable", False)),
                    "tts_dual_output": bool(tts_settings.get("dual_output", False)),
                    "tts_trigger_probability": tts_settings.get(
                        "trigger_probability", 1.0
                    ),
                    "tts_use_file_service": bool(
                        tts_settings.get("use_file_service", False)
                    ),
                }
            ),
            executor_policy=_freeze_mapping(
                {
                    "runner_type": provider_settings.get("agent_runner_type", "local"),
                    "runner_provider_ids": {
                        key.removesuffix("_agent_runner_provider_id"): value
                        for key, value in provider_settings.items()
                        if key.endswith("_agent_runner_provider_id")
                    },
                }
            ),
            adapter_binding_ids=tuple(
                dict.fromkeys(
                    [
                        *(
                            str(item.get("id"))
                            for item in platform_entries
                            if isinstance(item, dict) and item.get("id")
                        ),
                        BUILTIN_WEBCHAT_ADAPTER_BINDING_ID,
                    ]
                )
            ),
        )


@dataclass(frozen=True)
class AdapterBinding:
    """A platform adapter instance referenced by a bot profile."""

    binding_id: str
    adapter_type: str
    settings: Mapping[str, Any]
    owner_config_ids: tuple[str, ...] = ()

    @classmethod
    def from_config_entry(
        cls, entry: dict[str, Any], owner_config_id: str = ""
    ) -> AdapterBinding:
        binding_id = str(entry.get("id", "")).strip()
        adapter_type = str(entry.get("type", "")).strip()
        if not binding_id or not adapter_type:
            raise ValueError("adapter binding requires non-empty id and type")
        return cls(
            binding_id=binding_id,
            adapter_type=adapter_type,
            settings=_freeze_mapping(entry),
            owner_config_ids=(owner_config_id,) if owner_config_id else (),
        )


@dataclass(frozen=True)
class AdapterRegistry:
    """All platform adapter bindings visible to the process."""

    bindings: tuple[AdapterBinding, ...]

    @classmethod
    def from_configs(
        cls, configs: Mapping[str, Mapping[str, Any]]
    ) -> AdapterRegistry:
        bindings: dict[str, AdapterBinding] = {}
        for config_id, config in configs.items():
            for entry in config.get("platform", []):
                if not isinstance(entry, dict):
                    continue
                binding = AdapterBinding.from_config_entry(entry, config_id)
                existing = bindings.get(binding.binding_id)
                if existing is None:
                    bindings[binding.binding_id] = binding
                elif (
                    existing.adapter_type != binding.adapter_type
                    or existing.settings != binding.settings
                ):
                    raise ValueError(
                        f"conflicting adapter binding {binding.binding_id!r} "
                        f"between existing configuration and {config_id!r}"
                    )
                else:
                    bindings[binding.binding_id] = AdapterBinding(
                        binding_id=existing.binding_id,
                        adapter_type=existing.adapter_type,
                        settings=existing.settings,
                        owner_config_ids=tuple(
                            dict.fromkeys(
                                existing.owner_config_ids + binding.owner_config_ids
                            )
                        ),
                    )
        if BUILTIN_WEBCHAT_ADAPTER_BINDING_ID not in bindings:
            bindings[BUILTIN_WEBCHAT_ADAPTER_BINDING_ID] = AdapterBinding(
                binding_id=BUILTIN_WEBCHAT_ADAPTER_BINDING_ID,
                adapter_type="webchat",
                settings=MappingProxyType({}),
            )
        return cls(bindings=tuple(bindings.values()))

    @property
    def binding_ids(self) -> frozenset[str]:
        return frozenset(item.binding_id for item in self.bindings)


@dataclass(frozen=True)
class RuntimeSelection:
    """Validated profile/adapter choice used as the input to a turn snapshot."""

    profile_id: str
    adapter_binding_id: str
    provider_references: Mapping[str, str]


def _merge_resource_entry(
    target: dict[str, Mapping[str, Any]],
    key: str,
    value: Mapping[str, Any],
    kind: str,
    config_id: str,
) -> None:
    existing = target.get(key)
    if existing is None:
        target[key] = value
        return
    if existing != value:
        raise ValueError(
            f"conflicting {kind} {key!r} between existing configuration "
            f"and {config_id!r}"
        )


@dataclass(frozen=True)
class RuntimeResourceRegistry:
    """Process-wide resources merged from all loaded configuration profiles."""

    providers: ModelProviderRegistry
    adapters: AdapterRegistry

    @classmethod
    def from_configs(
        cls, configs: Mapping[str, Mapping[str, Any]]
    ) -> RuntimeResourceRegistry:
        return cls(
            providers=ModelProviderRegistry.from_configs(configs),
            adapters=AdapterRegistry.from_configs(configs),
        )


@dataclass(frozen=True)
class ConfigurationDomains:
    """Non-mutating domain projection of one persisted configuration."""

    config_id: str
    providers: ModelProviderRegistry
    profile: BotProfileConfig
    adapter_bindings: tuple[AdapterBinding, ...]

    @classmethod
    def from_config(
        cls,
        config_id: str,
        config: AstrBotConfig | dict[str, Any],
    ) -> ConfigurationDomains:
        raw_config = dict(config)
        return cls(
            config_id=config_id,
            providers=ModelProviderRegistry.from_config(raw_config),
            profile=BotProfileConfig.from_config(config_id, raw_config),
            adapter_bindings=tuple(
                AdapterBinding.from_config_entry(item)
                for item in raw_config.get("platform", [])
                if isinstance(item, dict)
            ),
        )

    def validate_references(
        self, resources: RuntimeResourceRegistry | None = None
    ) -> None:
        binding_ids = (
            resources.adapters.binding_ids
            if resources is not None
            else {item.binding_id for item in self.adapter_bindings}
        )
        if resources is None and len(binding_ids) != len(self.adapter_bindings):
            raise ValueError("duplicate adapter binding id")

        missing_bindings = set(self.profile.adapter_binding_ids) - binding_ids
        if missing_bindings:
            raise ValueError(
                "profile references unknown adapter bindings: "
                + ", ".join(sorted(missing_bindings))
            )

        provider_refs = {
            key: value
            for key, value in self.profile.model_policy.items()
            if key.endswith("_provider_id") and isinstance(value, str) and value
        }
        provider_ids = (
            resources.providers.provider_ids
            if resources is not None
            else self.providers.provider_ids
        )
        missing_providers = set(provider_refs.values()) - provider_ids
        if missing_providers:
            raise ValueError(
                "profile references unknown providers: "
                + ", ".join(sorted(missing_providers))
            )

    def select(
        self,
        adapter_binding_id: str,
        resources: RuntimeResourceRegistry | None = None,
    ) -> RuntimeSelection:
        self.validate_references(resources)
        if adapter_binding_id not in self.profile.adapter_binding_ids:
            raise ValueError(
                f"adapter binding {adapter_binding_id!r} is not bound to profile "
                f"{self.profile.config_id!r}"
            )
        provider_references = {
            key.removesuffix("_provider_id"): value
            for key, value in self.profile.model_policy.items()
            if key.endswith("_provider_id") and isinstance(value, str) and value
        }
        return RuntimeSelection(
            profile_id=self.profile.config_id,
            adapter_binding_id=adapter_binding_id,
            provider_references=_freeze_mapping(provider_references),
        )


__all__ = [
    "AdapterBinding",
    "AdapterRegistry",
    "BotProfileConfig",
    "BUILTIN_WEBCHAT_ADAPTER_BINDING_ID",
    "ConfigurationDomains",
    "ModelProviderRegistry",
    "RuntimeResourceRegistry",
    "RuntimeSelection",
]
