"""Agent 配置注册表（Phase 0 骨架）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import yaml


class AgentConfigRegistry:
    """读取并提供 Agent 配置访问接口。"""

    def __init__(self, config_path: str = "config/agent/agent.yaml") -> None:
        self.config_path = Path(config_path)
        self._legacy_config_path = Path("configs/agent/agent.yaml")
        self._cache: Optional[Dict[str, Any]] = None

    def load(self, force_reload: bool = False) -> Dict[str, Any]:
        """加载配置；本阶段支持缺省硬编码配置。"""
        if self._cache is not None and not force_reload:
            return self._cache

        config = self._default_config()
        config_file = self.config_path
        if not config_file.exists() and self._legacy_config_path.exists():
            # 兼容旧路径，避免迁移期间加载失败。
            config_file = self._legacy_config_path

        if config_file.exists():
            try:
                loaded = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
                if isinstance(loaded, dict):
                    config = self._merge(config, loaded)
            except Exception:
                # Phase 0 fail-open：配置错误不阻断 Agent 启动
                pass

        self.validate(config)
        self._cache = config
        return config

    def validate(self, config: Dict[str, Any]) -> None:
        """校验配置结构（Phase 0 最小校验）。"""
        if not isinstance(config, dict):
            raise ValueError("agent config must be a dict")
        if not isinstance(config.get("pool_selector"), dict):
            raise ValueError("agent pool selector config must be a dict")
        if not isinstance(config.get("pools"), dict):
            raise ValueError("agent pools config must be a dict")

    def get_pool_selector_config(self, selector_id: Optional[str] = None) -> Dict[str, Any]:
        """获取 PoolSelector 配置。"""
        cfg = self.load()
        source = cfg.get("pool_selector", {}) if isinstance(cfg.get("pool_selector"), dict) else {}
        default_id = source.get("default", "rule")
        selector_key = selector_id or default_id
        items = source.get("items", {})
        if isinstance(items, dict) and isinstance(items.get(selector_key), dict):
            merged = dict(items.get(selector_key, {}))
            file_cfg = self._load_pool_selector_file(selector_key, merged)
            if isinstance(file_cfg, dict):
                merged = self._merge(file_cfg, merged)
            merged.setdefault("id", selector_key)
            return merged
        return {"id": selector_key, "kind": selector_key}

    def get_pool_config(self, pool_id: Optional[str] = None) -> Dict[str, Any]:
        """获取 pool 配置。"""
        cfg = self.load()
        pools_cfg = dict(cfg.get("pools", {}))
        default_id = pools_cfg.get("default", "chat")
        pid = pool_id or default_id
        items = pools_cfg.get("items", {})
        if isinstance(items, dict) and isinstance(items.get(pid), dict):
            merged = dict(items.get(pid, {}))
            merged.setdefault("id", pid)
            return merged
        return {"id": pid, "kind": pid}

    @staticmethod
    def _default_config() -> Dict[str, Any]:
        return {
            "version": "0.1-phase0",
            "pool_selector": {
                "default": "default",
                "items": {
                    "default": {
                        "kind": "hybrid",
                        "config_file": "config/agent/pool_selector/default.yaml",
                    },
                    "rule": {"kind": "rule"},
                    "hybrid": {"kind": "hybrid"},
                    "llm": {"kind": "llm"},
                },
            },
            "pools": {
                "default": "chat",
                "items": {
                    "chat": {"kind": "chat"},
                    "code": {"kind": "code_stub"},
                    "plan": {"kind": "plan_stub"},
                    "creative": {"kind": "creative_stub"},
                },
            },
        }

    @staticmethod
    def _merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(out.get(key), dict):
                out[key] = AgentConfigRegistry._merge(dict(out[key]), value)
            else:
                out[key] = value
        return out

    def _load_pool_selector_file(self, selector_id: str, item_cfg: Dict[str, Any]) -> Dict[str, Any]:
        config_file = item_cfg.get("config_file")
        candidates: list[Path] = []
        if isinstance(config_file, str) and config_file.strip():
            candidates.append(Path(config_file.strip()))
        candidates.append(Path(f"config/agent/pool_selector/{selector_id}.yaml"))

        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                loaded = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
                if isinstance(loaded, dict):
                    return loaded
            except Exception:
                continue
        return {}
