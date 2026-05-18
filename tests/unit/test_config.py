"""Tests for config module."""

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from astrbot.core.config.astrbot_config import AstrBotConfig, RateLimitStrategy
from astrbot.core.config.default import (
    CONFIG_METADATA_3,
    DEFAULT_CONFIG,
    DEFAULT_VALUE_MAP,
)
from astrbot.core.config.i18n_utils import ConfigMetadataI18n
from astrbot.dashboard.routes.config import ConfigRoute


class AwaitableJsonRequest:
    def __init__(self, payload: dict):
        self._payload = payload

    @property
    async def json(self):
        return self._payload


@pytest.fixture
def temp_config_path(tmp_path):
    """Create a temporary config path."""
    return str(tmp_path / "test_config.json")


@pytest.fixture
def minimal_default_config():
    """Create a minimal default config for testing."""
    return {
        "config_version": 2,
        "platform_settings": {
            "unique_session": False,
            "rate_limit": {
                "time": 60,
                "count": 30,
                "strategy": "stall",
            },
        },
        "provider_settings": {
            "enable": True,
            "default_provider_id": "",
        },
    }


class TestRateLimitStrategy:
    """Tests for RateLimitStrategy enum."""

    def test_stall_value(self):
        """Test stall enum value."""
        assert RateLimitStrategy.STALL.value == "stall"

    def test_discard_value(self):
        """Test discard enum value."""
        assert RateLimitStrategy.DISCARD.value == "discard"


class TestAstrBotConfigLoad:
    """Tests for AstrBotConfig loading and initialization."""

    def test_init_creates_file_if_not_exists(
        self, temp_config_path, minimal_default_config
    ):
        """Test that config file is created when it doesn't exist."""
        assert not os.path.exists(temp_config_path)

        config = AstrBotConfig(
            config_path=temp_config_path, default_config=minimal_default_config
        )

        assert os.path.exists(temp_config_path)
        assert config.config_version == 2
        assert config.platform_settings["unique_session"] is False

    def test_init_loads_existing_file(self, temp_config_path, minimal_default_config):
        """Test that existing config file is loaded."""
        existing_config = {
            "config_version": 2,
            "platform_settings": {"unique_session": True},
            "provider_settings": {"enable": False},
        }
        with open(temp_config_path, "w", encoding="utf-8-sig") as f:
            json.dump(existing_config, f)

        config = AstrBotConfig(
            config_path=temp_config_path, default_config=minimal_default_config
        )

        assert config.platform_settings["unique_session"] is True
        assert config.provider_settings["enable"] is False

    def test_first_deploy_flag(self, temp_config_path, minimal_default_config):
        """Test first_deploy flag is set for new config."""
        config = AstrBotConfig(
            config_path=temp_config_path, default_config=minimal_default_config
        )

        assert hasattr(config, "first_deploy")
        assert config.first_deploy is True

    def test_init_with_schema(self, temp_config_path):
        """Test initialization with schema."""
        schema = {
            "test_field": {
                "type": "string",
                "default": "test_value",
            },
            "nested": {
                "type": "object",
                "items": {
                    "enabled": {"type": "bool"},
                    "count": {"type": "int"},
                },
            },
        }

        config = AstrBotConfig(config_path=temp_config_path, schema=schema)

        assert config.test_field == "test_value"
        assert config.nested["enabled"] is False
        assert config.nested["count"] == 0

    def test_dot_notation_access(self, temp_config_path, minimal_default_config):
        """Test accessing config values using dot notation."""
        config = AstrBotConfig(
            config_path=temp_config_path, default_config=minimal_default_config
        )

        assert config.platform_settings is not None
        assert config.non_existent_field is None

    def test_setattr_updates_config(self, temp_config_path, minimal_default_config):
        """Test that setting attributes updates config."""
        config = AstrBotConfig(
            config_path=temp_config_path, default_config=minimal_default_config
        )

        config.new_field = "new_value"

        assert config.new_field == "new_value"

    def test_delattr_removes_field(self, temp_config_path, minimal_default_config):
        """Test that deleting attributes removes them."""
        config = AstrBotConfig(
            config_path=temp_config_path, default_config=minimal_default_config
        )
        config.temp_field = "temp"

        del config.temp_field

        # Accessing a deleted field returns None due to __getattr__
        assert config.temp_field is None
        # But the field is removed from the dict
        assert "temp_field" not in config

    def test_delattr_saves_config(self, temp_config_path, minimal_default_config):
        """Test that deleting attributes saves config to file."""
        config = AstrBotConfig(
            config_path=temp_config_path, default_config=minimal_default_config
        )
        config.temp_field = "temp"
        del config.temp_field

        with open(temp_config_path, encoding="utf-8-sig") as f:
            loaded_config = json.load(f)

        assert "temp_field" not in loaded_config

    def test_check_exist(self, temp_config_path, minimal_default_config):
        """Test check_exist method."""
        config = AstrBotConfig(
            config_path=temp_config_path, default_config=minimal_default_config
        )

        assert config.check_exist() is True

        # Create a path that definitely doesn't exist
        import pathlib

        temp_dir = pathlib.Path(temp_config_path).parent
        non_existent_path = str(temp_dir / "non_existent_config.json")

        # Check that the file doesn't exist before creating config
        assert not os.path.exists(non_existent_path)

        # Create config which will auto-create the file
        config2 = AstrBotConfig(
            config_path=non_existent_path, default_config=minimal_default_config
        )

        # Now it exists
        assert config2.check_exist() is True
        assert os.path.exists(non_existent_path)


class TestConfigValidation:
    """Tests for config validation and integrity checking."""

    def test_insert_missing_config_items(
        self, temp_config_path, minimal_default_config
    ):
        """Test that missing config items are inserted with default values."""
        existing_config = {"config_version": 2}
        with open(temp_config_path, "w", encoding="utf-8-sig") as f:
            json.dump(existing_config, f)

        config = AstrBotConfig(
            config_path=temp_config_path, default_config=minimal_default_config
        )

        assert "platform_settings" in config
        assert "provider_settings" in config

    def test_replace_none_with_default(self, temp_config_path, minimal_default_config):
        """Test that None values are replaced with defaults."""
        existing_config = {
            "config_version": 2,
            "platform_settings": None,
            "provider_settings": None,
        }
        with open(temp_config_path, "w", encoding="utf-8-sig") as f:
            json.dump(existing_config, f)

        AstrBotConfig(
            config_path=temp_config_path, default_config=minimal_default_config
        )

        # Reload to verify the values were replaced
        config2 = AstrBotConfig(
            config_path=temp_config_path, default_config=minimal_default_config
        )

        assert config2.platform_settings is not None
        assert config2.provider_settings is not None

    def test_reorder_config_keys(self, temp_config_path, minimal_default_config):
        """Test that config keys are reordered to match default."""
        existing_config = {
            "provider_settings": {"enable": True},
            "config_version": 2,
            "platform_settings": {"unique_session": False},
        }
        with open(temp_config_path, "w", encoding="utf-8-sig") as f:
            json.dump(existing_config, f)

        AstrBotConfig(
            config_path=temp_config_path, default_config=minimal_default_config
        )

        with open(temp_config_path, encoding="utf-8-sig") as f:
            loaded_config = json.load(f)

        keys = list(loaded_config.keys())
        assert keys[0] == "config_version"
        assert keys[1] == "platform_settings"
        assert keys[2] == "provider_settings"

    def test_preserve_unknown_config_keys(
        self, temp_config_path, minimal_default_config
    ):
        """Unknown keys are preserved so new config can survive older defaults."""
        existing_config = {
            "config_version": 2,
            "platform_settings": {},
            "unknown_key": "should_be_preserved",
        }
        with open(temp_config_path, "w", encoding="utf-8-sig") as f:
            json.dump(existing_config, f)

        config = AstrBotConfig(
            config_path=temp_config_path, default_config=minimal_default_config
        )

        assert config["unknown_key"] == "should_be_preserved"

    def test_nested_config_validation(self, temp_config_path):
        """Test validation of nested config structures."""
        default_config = {
            "nested": {
                "level1": {
                    "level2": {
                        "value": 42,
                    },
                },
            },
        }

        existing_config = {
            "nested": {
                "level1": {},  # Missing level2
            },
        }
        with open(temp_config_path, "w", encoding="utf-8-sig") as f:
            json.dump(existing_config, f)

        config = AstrBotConfig(
            config_path=temp_config_path, default_config=default_config
        )

        assert "level2" in config.nested["level1"]
        assert config.nested["level1"]["level2"]["value"] == 42

    def test_integrity_log_does_not_include_inserted_secret_value(
        self, temp_config_path, monkeypatch
    ):
        """Default values may contain secrets and should not be logged."""
        from astrbot.core.config import astrbot_config

        existing_config = {}
        default_config = {"api_key": "secret-value"}
        messages = []
        with open(temp_config_path, "w", encoding="utf-8-sig") as f:
            json.dump(existing_config, f)

        monkeypatch.setattr(astrbot_config.logger, "info", messages.append)

        AstrBotConfig(config_path=temp_config_path, default_config=default_config)

        assert messages
        assert all("secret-value" not in message for message in messages)
        assert all("api_key" not in message for message in messages)
        assert any("配置项不存在" in message for message in messages)


class TestConfigHotReload:
    """Tests for config hot reload functionality."""

    def test_save_config(self, temp_config_path, minimal_default_config):
        """Test saving config to file."""
        config = AstrBotConfig(
            config_path=temp_config_path, default_config=minimal_default_config
        )
        config.new_field = "new_value"
        config.save_config()

        with open(temp_config_path, encoding="utf-8-sig") as f:
            loaded_config = json.load(f)

        assert loaded_config["new_field"] == "new_value"

    def test_save_config_with_replace(self, temp_config_path, minimal_default_config):
        """Test saving config with replacement."""
        config = AstrBotConfig(
            config_path=temp_config_path, default_config=minimal_default_config
        )

        replacement_config = {
            "replaced": True,
            "extra_field": "value",
        }
        config.save_config(replace_config=replacement_config)

        with open(temp_config_path, encoding="utf-8-sig") as f:
            loaded_config = json.load(f)

        # The replacement config is merged with existing config
        assert loaded_config["replaced"] is True
        assert loaded_config["extra_field"] == "value"
        # Original fields are preserved because update merges
        assert "platform_settings" in loaded_config

    def test_modification_persists_after_reload(
        self, temp_config_path, minimal_default_config
    ):
        """Test that modifications persist after reloading."""
        config1 = AstrBotConfig(
            config_path=temp_config_path, default_config=minimal_default_config
        )
        config1.platform_settings["unique_session"] = True
        config1.save_config()

        config2 = AstrBotConfig(
            config_path=temp_config_path, default_config=minimal_default_config
        )

        assert config2.platform_settings["unique_session"] is True


class TestConfigSchemaToDefault:
    """Tests for schema to default config conversion."""

    def test_convert_schema_with_defaults(self, temp_config_path):
        """Test converting schema with explicit defaults."""
        schema = {
            "string_field": {"type": "string", "default": "custom"},
            "int_field": {"type": "int", "default": 100},
            "bool_field": {"type": "bool", "default": True},
        }

        config = AstrBotConfig(config_path=temp_config_path, schema=schema)

        assert config.string_field == "custom"
        assert config.int_field == 100
        assert config.bool_field is True

    def test_convert_schema_without_defaults(self, temp_config_path):
        """Test converting schema using default value map."""
        schema = {
            "string_field": {"type": "string"},
            "int_field": {"type": "int"},
            "bool_field": {"type": "bool"},
        }

        config = AstrBotConfig(config_path=temp_config_path, schema=schema)

        assert config.string_field == DEFAULT_VALUE_MAP["string"]
        assert config.int_field == DEFAULT_VALUE_MAP["int"]
        assert config.bool_field == DEFAULT_VALUE_MAP["bool"]

    def test_unsupported_schema_type_raises_error(self, temp_config_path):
        """Test that unsupported schema types raise error."""
        schema = {
            "field": {"type": "unsupported_type"},
        }

        with pytest.raises(TypeError, match="不受支持的配置类型"):
            AstrBotConfig(config_path=temp_config_path, schema=schema)

    def test_template_list_type(self, temp_config_path):
        """Test template_list schema type."""
        schema = {
            "templates": {"type": "template_list", "default": []},
        }

        config = AstrBotConfig(config_path=temp_config_path, schema=schema)

        assert config.templates == []

    def test_nested_object_schema(self, temp_config_path):
        """Test nested object schema conversion."""
        schema = {
            "nested": {
                "type": "object",
                "items": {
                    "field1": {"type": "string"},
                    "field2": {"type": "int"},
                },
            },
        }

        config = AstrBotConfig(config_path=temp_config_path, schema=schema)

        assert config.nested["field1"] == ""
        assert config.nested["field2"] == 0


class TestConfigMetadataI18n:
    """Tests for i18n utils."""

    def test_get_i18n_key(self):
        """Test generating i18n key."""
        key = ConfigMetadataI18n._get_i18n_key(
            group="ai_group",
            section="general",
            field="enable",
            attr="description",
        )

        assert key == "ai_group.general.enable.description"

    def test_get_i18n_key_without_field(self):
        """Test generating i18n key without field."""
        key = ConfigMetadataI18n._get_i18n_key(
            group="ai_group",
            section="general",
            field="",
            attr="description",
        )

        assert key == "ai_group.general.description"

    def test_convert_to_i18n_keys_simple(self):
        """Test converting simple metadata to i18n keys."""
        metadata = {
            "ai_group": {
                "name": "AI Settings",
                "metadata": {
                    "general": {
                        "description": "General settings",
                        "items": {
                            "enable": {
                                "description": "Enable feature",
                                "type": "bool",
                                "default": True,
                            },
                        },
                    },
                },
            },
        }

        result = ConfigMetadataI18n.convert_to_i18n_keys(metadata)

        assert result["ai_group"]["name"] == "ai_group.name"
        assert (
            result["ai_group"]["metadata"]["general"]["description"]
            == "ai_group.general.description"
        )
        assert (
            result["ai_group"]["metadata"]["general"]["items"]["enable"]["description"]
            == "ai_group.general.enable.description"
        )

    def test_convert_to_i18n_keys_with_hint(self):
        """Test converting metadata with hint."""
        metadata = {
            "group": {
                "metadata": {
                    "section": {
                        "hint": "This is a hint",
                        "items": {
                            "field": {
                                "hint": "Field hint",
                                "type": "string",
                            },
                        },
                    },
                },
            },
        }

        result = ConfigMetadataI18n.convert_to_i18n_keys(metadata)

        assert result["group"]["metadata"]["section"]["hint"] == "group.section.hint"
        assert (
            result["group"]["metadata"]["section"]["items"]["field"]["hint"]
            == "group.section.field.hint"
        )

    def test_convert_to_i18n_keys_with_labels(self):
        """Test converting metadata with labels."""
        metadata = {
            "group": {
                "metadata": {
                    "section": {
                        "items": {
                            "field": {
                                "labels": ["Label1", "Label2"],
                                "type": "string",
                            },
                        },
                    },
                },
            },
        }

        result = ConfigMetadataI18n.convert_to_i18n_keys(metadata)

        assert (
            result["group"]["metadata"]["section"]["items"]["field"]["labels"]
            == "group.section.field.labels"
        )

    def test_convert_to_i18n_keys_nested_items(self):
        """Test converting metadata with nested items."""
        metadata = {
            "group": {
                "metadata": {
                    "section": {
                        "items": {
                            "nested": {
                                "description": "Nested field",
                                "type": "object",
                                "items": {
                                    "inner": {
                                        "description": "Inner field",
                                        "type": "string",
                                    },
                                },
                            },
                        },
                    },
                },
            },
        }

        result = ConfigMetadataI18n.convert_to_i18n_keys(metadata)

        assert (
            result["group"]["metadata"]["section"]["items"]["nested"]["description"]
            == "group.section.nested.description"
        )
        assert (
            result["group"]["metadata"]["section"]["items"]["nested"]["items"]["inner"][
                "description"
            ]
            == "group.section.nested.inner.description"
        )

    def test_convert_to_i18n_keys_preserves_non_i18n_fields(self):
        """Test that non-i18n fields are preserved."""
        metadata = {
            "group": {
                "metadata": {
                    "section": {
                        "items": {
                            "field": {
                                "description": "Field description",
                                "type": "string",
                                "other_field": "preserve this",
                            },
                        },
                    },
                },
            },
        }

        result = ConfigMetadataI18n.convert_to_i18n_keys(metadata)

        assert (
            result["group"]["metadata"]["section"]["items"]["field"]["other_field"]
            == "preserve this"
        )

    def test_convert_to_i18n_keys_with_name(self):
        """Test converting metadata with name field."""
        metadata = {
            "group": {
                "metadata": {
                    "section": {
                        "items": {
                            "field": {
                                "name": "Field Name",
                                "type": "string",
                            },
                        },
                    },
                },
            },
        }

        result = ConfigMetadataI18n.convert_to_i18n_keys(metadata)

        assert (
            result["group"]["metadata"]["section"]["items"]["field"]["name"]
            == "group.section.field.name"
        )

    def test_interaction_middleware_extension_config_metadata_is_exposed(self):
        """Extension config page requires this group to be present in metadata."""
        result = ConfigMetadataI18n.convert_to_i18n_keys(CONFIG_METADATA_3)

        group = result["interaction_middleware_group"]
        assert sorted(group["metadata"]) == [
            "decision",
            "finalizer",
            "general",
            "stream",
        ]
        assert (
            group["metadata"]["general"]["items"]["interaction_middleware.enabled"][
                "description"
            ]
            == "interaction_middleware_group.general.interaction_middleware.enabled.description"
        )
        assert (
            group["metadata"]["decision"]["items"][
                "interaction_middleware.decision_provider_id"
            ]["_special"]
            == "select_provider"
        )

    def test_interaction_middleware_defaults_match_exposed_metadata(self):
        """All exposed interaction middleware config keys should have defaults."""
        exposed_keys = {
            item_key
            for section in CONFIG_METADATA_3["interaction_middleware_group"][
                "metadata"
            ].values()
            for item_key in section["items"]
        }
        defaults = DEFAULT_CONFIG["interaction_middleware"]

        for item_key in exposed_keys:
            prefix, key = item_key.split(".", 1)
            assert prefix == "interaction_middleware"
            assert key in defaults

    def test_memory_extension_config_metadata_is_exposed(self):
        """Extension config page requires memory metadata to be present."""
        result = ConfigMetadataI18n.convert_to_i18n_keys(CONFIG_METADATA_3)

        group = result["memory_group"]
        assert sorted(group["metadata"]) == [
            "advanced",
            "general",
            "injection",
            "models",
            "schedule",
        ]
        assert (
            group["metadata"]["general"]["items"]["memory.enabled"]["description"]
            == "memory_group.general.memory.enabled.description"
        )
        assert (
            group["metadata"]["injection"]["items"]["memory.injection.long_term.top_k"][
                "description"
            ]
            == "memory_group.injection.memory.injection.long_term.top_k.description"
        )
        assert (
            group["metadata"]["models"]["items"][
                "memory.analysis.standard_provider_id"
            ]["_special"]
            == "select_provider"
        )
        assert (
            group["metadata"]["models"]["items"][
                "memory.analysis.advanced_provider_id"
            ]["_special"]
            == "select_provider"
        )
        assert (
            group["metadata"]["models"]["items"]["memory.vector_index.provider_id"][
                "_special"
            ]
            == "select_provider_embedding"
        )

    def test_memory_defaults_match_exposed_metadata(self):
        """All exposed memory config keys should exist in DEFAULT_CONFIG."""

        def get_by_selector(config: dict, selector: str):
            current = config
            for key in selector.split("."):
                assert isinstance(current, dict), selector
                assert key in current, selector
                current = current[key]
            return current

        exposed_keys = {
            item_key
            for section in CONFIG_METADATA_3["memory_group"]["metadata"].values()
            for item_key in section["items"]
        }

        for item_key in exposed_keys:
            get_by_selector(DEFAULT_CONFIG, item_key)


class TestConfigRouteMemoryReload:
    @pytest.mark.asyncio
    async def test_save_resets_memory_runtime_when_memory_config_changes(self):
        route = object.__new__(ConfigRoute)
        route.acm = SimpleNamespace(
            confs={
                "default": {
                    "memory": {
                        "enabled": True,
                    },
                }
            },
            default_conf={
                "provider_sources": [],
                "provider": [],
                "platform": [],
            },
        )
        route.core_lifecycle = SimpleNamespace(
            reload_pipeline_scheduler=AsyncMock(),
        )
        new_config = {
            "memory": {
                "enabled": False,
            },
            "provider_sources": ["client-value"],
            "provider": ["client-value"],
            "platform": ["client-value"],
        }
        route._save_astrbot_configs = AsyncMock()

        with (
            patch(
                "astrbot.dashboard.routes.config.request",
                new=AwaitableJsonRequest(
                    {
                        "config": new_config,
                        "conf_id": "default",
                    }
                ),
            ),
            patch(
                "astrbot.dashboard.routes.config.reset_memory_config"
            ) as reset_memory_config,
            patch(
                "astrbot.dashboard.routes.config.shutdown_memory_service",
                new=AsyncMock(),
            ) as shutdown_memory_service,
        ):
            response = await route.post_astrbot_configs()

        assert response["status"] == "ok"
        reset_memory_config.assert_called_once()
        assert reset_memory_config.call_args.args[0] is route.acm.confs["default"]
        shutdown_memory_service.assert_awaited_once()
        assert shutdown_memory_service.await_args.args[0] is route.acm.confs["default"]
        route._save_astrbot_configs.assert_awaited_once()
        saved_config = route._save_astrbot_configs.await_args.args[0]
        assert saved_config["provider_sources"] == []
        assert saved_config["provider"] == []
        assert saved_config["platform"] == []

    @pytest.mark.asyncio
    async def test_save_does_not_reset_memory_runtime_when_memory_config_unchanged(
        self,
    ):
        memory_config = {
            "enabled": True,
        }
        route = object.__new__(ConfigRoute)
        route.acm = SimpleNamespace(
            confs={
                "default": {
                    "memory": memory_config,
                }
            },
            default_conf={
                "provider_sources": [],
                "provider": [],
                "platform": [],
            },
        )
        route.core_lifecycle = SimpleNamespace(
            reload_pipeline_scheduler=AsyncMock(),
        )
        route._save_astrbot_configs = AsyncMock()

        with (
            patch(
                "astrbot.dashboard.routes.config.request",
                new=AwaitableJsonRequest(
                    {
                        "config": {
                            "memory": dict(memory_config),
                            "provider_sources": [],
                            "provider": [],
                            "platform": [],
                        },
                        "conf_id": "default",
                    }
                ),
            ),
            patch(
                "astrbot.dashboard.routes.config.reset_memory_config"
            ) as reset_memory_config,
            patch(
                "astrbot.dashboard.routes.config.shutdown_memory_service",
                new=AsyncMock(),
            ) as shutdown_memory_service,
        ):
            response = await route.post_astrbot_configs()

        assert response["status"] == "ok"
        reset_memory_config.assert_not_called()
        shutdown_memory_service.assert_not_awaited()
