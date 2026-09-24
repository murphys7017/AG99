import os
import re
import uuid
from copy import deepcopy
from dataclasses import dataclass
from typing import TypedDict, TypeVar

from astrbot.core import AstrBotConfig, logger
from astrbot.core.config.astrbot_config import ASTRBOT_CONFIG_PATH
from astrbot.core.config.default import DEFAULT_CONFIG
from astrbot.core.config.domains import (
    ConfigurationDomains,
    RuntimeResourceRegistry,
    RuntimeSelection,
)
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.umop_config_router import UmopConfigRouter
from astrbot.core.utils.astrbot_path import get_astrbot_config_path
from astrbot.core.utils.shared_preferences import SharedPreferences

_VT = TypeVar("_VT")


class ConfInfo(TypedDict):
    """Configuration information for a specific session or platform."""

    id: str  # UUID of the configuration or "default"
    name: str
    path: str  # File name to the configuration file


DEFAULT_CONFIG_CONF_INFO = ConfInfo(
    id="default",
    name="default",
    path=ASTRBOT_CONFIG_PATH,
)


class ConfigurationRouteError(ValueError):
    """A UMO route points to an unavailable configuration profile."""


@dataclass(frozen=True)
class ConfigurationSelection:
    """Explicit configuration result prepared before a turn is admitted."""

    config_id: str
    config_info: ConfInfo
    runtime_config: AstrBotConfig
    domains: ConfigurationDomains
    runtime_selection: RuntimeSelection
    used_default_route: bool


class AstrBotConfigManager:
    """A class to manage the system configuration of AstrBot, aka ACM"""

    def __init__(
        self,
        default_config: AstrBotConfig,
        ucr: UmopConfigRouter,
        sp: SharedPreferences,
    ) -> None:
        self.sp = sp
        self.ucr = ucr
        self.confs: dict[str, AstrBotConfig] = {}
        """uuid / "default" -> AstrBotConfig"""
        self.confs["default"] = default_config
        self.abconf_data = None
        self._load_all_configs()
        self._migrate_profile_resources_to_global_owner()

    @staticmethod
    def _build_migrated_resource_id(
        *,
        config_id: str,
        kind: str,
        resource_id: str,
        occupied: set[str],
    ) -> str:
        slug = re.sub(r"[^A-Za-z0-9_]+", "_", resource_id).strip("_")
        base = f"profile_{config_id[:8]}_{kind}_{slug or 'resource'}"
        candidate = base
        suffix = 2
        while candidate in occupied:
            candidate = f"{base}_{suffix}"
            suffix += 1
        return candidate

    @staticmethod
    def _rewrite_profile_provider_references(
        value,
        replacements: dict[str, str],
    ):
        """Rewrite only declared provider-reference fields in Profile policy."""
        if isinstance(value, dict):
            rewritten = {}
            for key, item in value.items():
                if key.endswith("_provider_id") and isinstance(item, str):
                    rewritten[key] = replacements.get(item, item)
                elif key == "fallback_chat_models" and isinstance(item, list):
                    rewritten[key] = [
                        replacements.get(entry, entry)
                        if isinstance(entry, str)
                        else entry
                        for entry in item
                    ]
                else:
                    rewritten[key] = (
                        AstrBotConfigManager._rewrite_profile_provider_references(
                            item, replacements
                        )
                    )
            return rewritten
        if isinstance(value, list):
            return [
                AstrBotConfigManager._rewrite_profile_provider_references(
                    item, replacements
                )
                for item in value
            ]
        return value

    def _migrate_profile_resources_to_global_owner(self) -> None:
        """Promote legacy resource copies to the global owner.

        Profiles historically embed process-global resources. Exact copies are
        deduplicated. A conflicting ID is namespaced for that Profile before
        every exact provider-ID reference in the Profile is rewritten. The
        migrated Profile then owns policy references and adapter binding IDs only.
        """
        global_config = self.default_conf
        global_sources = deepcopy(global_config.get("provider_sources", []))
        global_providers = deepcopy(global_config.get("provider", []))
        if not isinstance(global_sources, list) or not isinstance(global_providers, list):
            raise ValueError("global resource owner has invalid provider resources")

        source_by_id = {
            str(item.get("id", "")).strip(): item
            for item in global_sources
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        }
        provider_by_id = {
            str(item.get("id", "")).strip(): item
            for item in global_providers
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        }
        changed_profiles: list[AstrBotConfig] = []
        promoted_sources = 0
        promoted_providers = 0
        renamed_resources = 0

        for config_id, profile in self.confs.items():
            if config_id == "default":
                continue
            local_sources = profile.get("provider_sources", [])
            local_providers = profile.get("provider", [])
            local_platforms = profile.get("platform", [])
            if not isinstance(local_sources, list) or not isinstance(local_providers, list):
                raise ValueError(f"profile {config_id!r} has invalid provider resources")
            if not isinstance(local_platforms, list):
                raise ValueError(f"profile {config_id!r} has invalid adapter bindings")
            if not local_sources and not local_providers and not local_platforms:
                continue

            source_replacements: dict[str, str] = {}
            for entry in local_sources:
                if not isinstance(entry, dict):
                    continue
                source = deepcopy(entry)
                source_id = str(source.get("id", "")).strip()
                if not source_id:
                    global_sources.append(source)
                    promoted_sources += 1
                    continue
                current = source_by_id.get(source_id)
                if current is None:
                    source_by_id[source_id] = source
                    global_sources.append(source)
                    promoted_sources += 1
                    continue
                if current == source:
                    continue
                replacement = self._build_migrated_resource_id(
                    config_id=config_id,
                    kind="source",
                    resource_id=source_id,
                    occupied=set(source_by_id),
                )
                source["id"] = replacement
                source_by_id[replacement] = source
                global_sources.append(source)
                source_replacements[source_id] = replacement
                promoted_sources += 1
                renamed_resources += 1

            provider_replacements: dict[str, str] = {}
            for entry in local_providers:
                if not isinstance(entry, dict):
                    continue
                provider = deepcopy(entry)
                source_id = str(provider.get("provider_source_id", "")).strip()
                if source_id in source_replacements:
                    provider["provider_source_id"] = source_replacements[source_id]
                provider_id = str(provider.get("id", "")).strip()
                if not provider_id:
                    raise ValueError(
                        f"provider definition in profile {config_id!r} has no id"
                    )
                current = provider_by_id.get(provider_id)
                if current is None:
                    provider_by_id[provider_id] = provider
                    global_providers.append(provider)
                    promoted_providers += 1
                    continue
                if current == provider:
                    continue
                replacement = self._build_migrated_resource_id(
                    config_id=config_id,
                    kind="provider",
                    resource_id=provider_id,
                    occupied=set(provider_by_id),
                )
                provider["id"] = replacement
                provider_by_id[replacement] = provider
                global_providers.append(provider)
                provider_replacements[provider_id] = replacement
                promoted_providers += 1
                renamed_resources += 1

            migrated_profile = self._rewrite_profile_provider_references(
                dict(profile), provider_replacements
            )
            migrated_profile["provider_sources"] = []
            migrated_profile["provider"] = []
            migrated_profile["adapter_binding_ids"] = list(
                dict.fromkeys(
                    [
                        *(
                            str(entry.get("id")).strip()
                            for entry in local_platforms
                            if isinstance(entry, dict)
                            and str(entry.get("id", "")).strip()
                        ),
                        *(
                            entry.strip()
                            for entry in migrated_profile.get(
                                "adapter_binding_ids", []
                            )
                            if isinstance(entry, str) and entry.strip()
                        ),
                    ]
                )
            )
            migrated_profile["platform"] = []
            profile.clear()
            profile.update(migrated_profile)
            changed_profiles.append(profile)

        if not changed_profiles:
            return

        global_config["provider_sources"] = global_sources
        global_config["provider"] = global_providers
        global_config.save_config()
        for profile in changed_profiles:
            profile.save_config()
        logger.info(
            "Migrated Profile provider resources to global owner: profiles=%s "
            "sources=%s providers=%s renamed=%s",
            len(changed_profiles),
            promoted_sources,
            promoted_providers,
            renamed_resources,
        )

    def _get_abconf_data(self) -> dict:
        """获取所有的 abconf 数据"""
        if self.abconf_data is None:
            self.abconf_data = self.sp.get(
                "abconf_mapping",
                {},
                scope="global",
                scope_id="global",
            )
        return self.abconf_data

    def _load_all_configs(self) -> None:
        """Load all configurations from the shared preferences."""
        abconf_data = self._get_abconf_data()
        self.abconf_data = abconf_data
        for uuid_, meta in abconf_data.items():
            filename = meta["path"]
            conf_path = os.path.join(get_astrbot_config_path(), filename)
            if os.path.exists(conf_path):
                conf = AstrBotConfig(config_path=conf_path)
                self.confs[uuid_] = conf
            else:
                logger.warning(
                    f"Config file {conf_path} for UUID {uuid_} does not exist, skipping.",
                )
                continue

    def _load_conf_mapping(self, umo: str | MessageSession) -> ConfInfo:
        """获取指定 umo 的配置文件 uuid, 如果不存在则返回默认配置(返回 "default")

        Returns:
            ConfInfo: 包含配置文件的 uuid, 路径和名称等信息, 是一个 dict 类型

        """
        # uuid -> { "path": str, "name": str }
        abconf_data = self._get_abconf_data()

        if isinstance(umo, MessageSession):
            umo = str(umo)
        else:
            try:
                umo = str(MessageSession.from_str(umo))  # validate
            except Exception:
                return DEFAULT_CONFIG_CONF_INFO

        conf_id = self.ucr.get_conf_id_for_umop(umo)
        if conf_id:
            meta = abconf_data.get(conf_id)
            if meta and isinstance(meta, dict):
                # The binding now belongs to the router. Reading metadata must
                # not mutate the shared preference mapping.
                clean_meta = {key: value for key, value in meta.items() if key != "umop"}
                return ConfInfo(**clean_meta, id=conf_id)

        return DEFAULT_CONFIG_CONF_INFO

    def _save_conf_mapping(
        self,
        abconf_path: str,
        abconf_id: str,
        abconf_name: str | None = None,
    ) -> None:
        """保存配置文件的映射关系"""
        abconf_data = self.sp.get(
            "abconf_mapping",
            {},
            scope="global",
            scope_id="global",
        )
        random_word = abconf_name or uuid.uuid4().hex[:8]
        abconf_data[abconf_id] = {
            "path": abconf_path,
            "name": random_word,
        }
        self.sp.put("abconf_mapping", abconf_data, scope="global", scope_id="global")
        self.abconf_data = abconf_data

    def get_conf(self, umo: str | MessageSession | None) -> AstrBotConfig:
        """获取指定 umo 的配置文件。如果不存在，则 fallback 到默认配置文件。"""
        if not umo:
            return self.confs["default"]

        uuid_ = self._load_conf_mapping(umo)["id"]

        conf = self.confs.get(uuid_)
        if not conf:
            conf = self.confs["default"]  # default MUST exists

        return conf

    def get_resource_registry(self) -> RuntimeResourceRegistry:
        """Return the process-wide Provider/Adapter resource projection.

        This is intentionally a read-only projection and does not alter the
        existing ProviderManager or PlatformManager loading behavior yet.
        """
        return RuntimeResourceRegistry.from_configs(self.confs)

    def resolve_configuration_selection(
        self, umo: str | MessageSession
    ) -> ConfigurationSelection:
        """Resolve one UMO without silently substituting a missing profile.

        Legacy ``get_conf`` remains available while call sites migrate. New
        turn-admission code must use this method so a stale route cannot look
        like a valid default-profile selection.
        """
        if isinstance(umo, MessageSession):
            session = umo
        else:
            try:
                session = MessageSession.from_str(umo)
            except Exception as exc:
                raise ConfigurationRouteError(f"invalid UMO: {umo!r}") from exc

        normalized_umo = str(session)
        routed_config_id = self.ucr.get_conf_id_for_umop(normalized_umo)
        used_default_route = routed_config_id is None
        config_id = routed_config_id or "default"
        runtime_config = self.confs.get(config_id)
        if runtime_config is None:
            raise ConfigurationRouteError(
                f"UMO route points to unavailable configuration: "
                f"umo={normalized_umo!r} config_id={config_id!r}"
            )

        if config_id == "default":
            config_info = dict(DEFAULT_CONFIG_CONF_INFO)
        else:
            meta = self._get_abconf_data().get(config_id)
            if not isinstance(meta, dict):
                raise ConfigurationRouteError(
                    f"configuration metadata is unavailable: config_id={config_id!r}"
                )
            config_info = ConfInfo(
                **{key: value for key, value in meta.items() if key != "umop"},
                id=config_id,
            )

        domains = ConfigurationDomains.from_config(config_id, runtime_config)
        resources = self.get_resource_registry()
        runtime_selection = domains.select(session.platform_id, resources)
        return ConfigurationSelection(
            config_id=config_id,
            config_info=config_info,
            runtime_config=runtime_config,
            domains=domains,
            runtime_selection=runtime_selection,
            used_default_route=used_default_route,
        )

    @property
    def default_conf(self) -> AstrBotConfig:
        """获取默认配置文件"""
        return self.confs["default"]

    def get_conf_info(self, umo: str | MessageSession) -> ConfInfo:
        """获取指定 umo 的配置文件元数据"""
        return self._load_conf_mapping(umo)

    def get_conf_list(self) -> list[ConfInfo]:
        """获取所有配置文件的元数据列表"""
        conf_list = []
        abconf_mapping = self._get_abconf_data()
        for uuid_, meta in abconf_mapping.items():
            if not isinstance(meta, dict):
                continue
            clean_meta = {key: value for key, value in meta.items() if key != "umop"}
            conf_list.append(ConfInfo(**clean_meta, id=uuid_))
        conf_list.append(DEFAULT_CONFIG_CONF_INFO)
        return conf_list

    def create_conf(
        self,
        config: dict = DEFAULT_CONFIG,
        name: str | None = None,
    ) -> str:
        conf_uuid = str(uuid.uuid4())
        conf_file_name = f"abconf_{conf_uuid}.json"
        conf_path = os.path.join(get_astrbot_config_path(), conf_file_name)
        conf = AstrBotConfig(config_path=conf_path, default_config=config)
        conf.save_config()
        self._save_conf_mapping(conf_file_name, conf_uuid, abconf_name=name)
        self.confs[conf_uuid] = conf
        return conf_uuid

    def delete_conf(self, conf_id: str) -> bool:
        """删除指定配置文件

        Args:
            conf_id: 配置文件的 UUID

        Returns:
            bool: 删除是否成功

        Raises:
            ValueError: 如果试图删除默认配置文件

        """
        if conf_id == "default":
            raise ValueError("不能删除默认配置文件")

        # 从映射中移除
        abconf_data = self.sp.get(
            "abconf_mapping",
            {},
            scope="global",
            scope_id="global",
        )
        if conf_id not in abconf_data:
            logger.warning(f"配置文件 {conf_id} 不存在于映射中")
            return False

        # 获取配置文件路径
        conf_path = os.path.join(
            get_astrbot_config_path(),
            abconf_data[conf_id]["path"],
        )

        # 删除配置文件
        try:
            if os.path.exists(conf_path):
                os.remove(conf_path)
                logger.info(f"已删除配置文件: {conf_path}")
        except Exception as e:
            logger.error(f"删除配置文件 {conf_path} 失败: {e}")
            return False

        # 从内存中移除
        if conf_id in self.confs:
            del self.confs[conf_id]

        # 从映射中移除
        del abconf_data[conf_id]
        self.sp.put("abconf_mapping", abconf_data, scope="global", scope_id="global")
        self.abconf_data = abconf_data

        logger.info(f"成功删除配置文件 {conf_id}")
        return True

    def update_conf_info(self, conf_id: str, name: str | None = None) -> bool:
        """更新配置文件信息

        Args:
            conf_id: 配置文件的 UUID
            name: 新的配置文件名称 (可选)

        Returns:
            bool: 更新是否成功

        """
        if conf_id == "default":
            raise ValueError("不能更新默认配置文件的信息")

        abconf_data = self.sp.get(
            "abconf_mapping",
            {},
            scope="global",
            scope_id="global",
        )
        if conf_id not in abconf_data:
            logger.warning(f"配置文件 {conf_id} 不存在于映射中")
            return False

        # 更新名称
        if name is not None:
            abconf_data[conf_id]["name"] = name

        # 保存更新
        self.sp.put("abconf_mapping", abconf_data, scope="global", scope_id="global")
        self.abconf_data = abconf_data
        logger.info(f"成功更新配置文件 {conf_id} 的信息")
        return True

    def g(
        self,
        umo: str | None = None,
        key: str | None = None,
        default: _VT = None,
    ) -> _VT:
        """获取配置项。umo 为 None 时使用默认配置"""
        if umo is None:
            return self.confs["default"].get(key, default)
        conf = self.get_conf(umo)
        return conf.get(key, default)
