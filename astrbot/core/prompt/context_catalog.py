"""
Context Catalog - 声明式上下文定义加载器（第一阶段）

本模块提供：
- CatalogItem: 单个上下文项的定义
- ContextCatalog: 所有 catalog 项的容器
- ContextCatalogLoader: YAML 配置加载器
- get_catalog(): 全局单例访问器
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from astrbot import logger

from .context_types import (
    CategoryType,
    LifecycleType,
    SlotName,
)

# ========== Catalog 数据模型 ==========


@dataclass
class CatalogItem:
    """
    Catalog 中单个上下文项的定义。

    这是声明式规范，不是运行时数据。
    """

    id: str  # 唯一标识（如 "persona.prompt"）
    category: CategoryType  # 语义类别
    slots: list[SlotName]  # 可以填充哪些布局槽
    required: bool  # 此上下文是否必需
    multiple: bool  # 是否允许多个实例
    lifecycle: LifecycleType  # 生命周期类型

    # 可选字段
    notes: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContextCatalog:
    """
    所有上下文项定义的容器。
    """

    version: str
    contexts: list[CatalogItem] = field(default_factory=list)

    # 用于快速查找的索引
    _id_index: dict[str, CatalogItem] = field(
        default_factory=dict, init=False, repr=False
    )
    _category_index: dict[str, list[CatalogItem]] = field(
        default_factory=dict, init=False, repr=False
    )

    def __post_init__(self) -> None:
        """构建查找索引。"""
        self._id_index = {item.id: item for item in self.contexts}

        # 构建类别索引
        self._category_index = {}
        for item in self.contexts:
            if item.category not in self._category_index:
                self._category_index[item.category] = []
            self._category_index[item.category].append(item)

    def get(self, item_id: str) -> CatalogItem | None:
        """按 ID 获取 item。"""
        return self._id_index.get(item_id)

    def has(self, item_id: str) -> bool:
        """检查 item 是否存在。"""
        return item_id in self._id_index

    def list_by_category(self, category: CategoryType) -> list[CatalogItem]:
        """列出某个类别中的所有 items。"""
        return self._category_index.get(category, [])

    def list_required(self) -> list[CatalogItem]:
        """列出所有必需的 items。"""
        return [item for item in self.contexts if item.required]

    def list_allows_multiple(self) -> list[CatalogItem]:
        """列出所有允许多个实例的 items。"""
        return [item for item in self.contexts if item.multiple]


# ========== 加载器 ==========


class ContextCatalogLoader:
    """
    从 YAML 配置加载 ContextCatalog。
    """

    # 合法的枚举值
    VALID_CATEGORIES: set[str] = {
        "system",
        "persona",
        "memory",
        "input",
        "rag",
        "tools",
        "session",
        "extension",
    }

    VALID_LIFECYCLES: set[str] = {
        "static",
        "session",
        "rolling",
        "ephemeral",
        "dynamic",
    }

    VALID_SLOTS: set[str] = {
        "system",
        "persona",
        "history",
        "user_input",
        "rag_context",
        "tools",
    }

    @classmethod
    def load(
        cls,
        path: str | Path = "data/config/prompt/context_catalog.yaml",
        *,
        strict: bool = False,
    ) -> ContextCatalog:
        """
        从 YAML 文件加载 catalog。

        如果文件不存在或加载失败，返回空 catalog（fail-open 策略）。
        """
        path = Path(path)

        if not path.exists():
            if strict:
                raise FileNotFoundError(f"Context catalog not found at {path}")
            logger.warning(f"Context catalog not found at {path}, using empty catalog")
            return ContextCatalog(version="0.1", contexts=[])

        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            if strict:
                raise RuntimeError(f"Failed to load catalog yaml from {path}") from exc
            logger.error(f"Failed to load catalog yaml: {exc}, using empty catalog")
            return ContextCatalog(version="0.1", contexts=[])

        if not isinstance(data, dict):
            if strict:
                raise RuntimeError("Catalog root must be a dict")
            logger.warning("Catalog root must be a dict, using empty catalog")
            return ContextCatalog(version="0.1", contexts=[])

        version = str(data.get("version", "0.1"))
        contexts_raw = data.get("contexts", [])

        if not isinstance(contexts_raw, list):
            if strict:
                raise RuntimeError("Catalog 'contexts' must be a list")
            logger.warning("Catalog 'contexts' must be a list, using empty catalog")
            return ContextCatalog(version=version, contexts=[])

        contexts: list[CatalogItem] = []
        for idx, item_data in enumerate(contexts_raw):
            try:
                item = cls._parse_item(item_data)
                contexts.append(item)
            except Exception as exc:
                if strict:
                    raise RuntimeError(
                        f"Failed to parse catalog item {idx}: {exc}"
                    ) from exc
                logger.warning(f"Failed to parse catalog item {idx}: {exc}, skipping")

        catalog = ContextCatalog(version=version, contexts=contexts)
        cls._validate(catalog)

        logger.info(f"Loaded context catalog: {len(contexts)} items, version={version}")
        return catalog

    @classmethod
    def _parse_item(cls, data: dict[str, Any]) -> CatalogItem:
        """从 dict 解析单个 catalog item。"""
        # 必需字段
        item_id = data.get("id")
        if not item_id or not isinstance(item_id, str):
            raise ValueError("Item 'id' is required and must be a string")

        category = data.get("category")
        if category not in cls.VALID_CATEGORIES:
            raise ValueError(f"Invalid category '{category}' for item '{item_id}'")

        slots = data.get("slots")
        if not isinstance(slots, list) or not slots:
            raise ValueError(f"Item '{item_id}' 'slots' must be a non-empty list")

        # 验证每个 slot
        for slot in slots:
            if slot not in cls.VALID_SLOTS:
                raise ValueError(f"Invalid slot '{slot}' for item '{item_id}'")

        required = bool(data.get("required", False))
        multiple = bool(data.get("multiple", False))

        lifecycle = data.get("lifecycle")
        if lifecycle not in cls.VALID_LIFECYCLES:
            raise ValueError(f"Invalid lifecycle '{lifecycle}' for item '{item_id}'")

        # 可选字段
        notes = str(data.get("notes", ""))
        meta = data.get("meta", {})
        if not isinstance(meta, dict):
            meta = {}

        return CatalogItem(
            id=item_id,
            category=category,
            slots=slots,
            required=required,
            multiple=multiple,
            lifecycle=lifecycle,
            notes=notes,
            meta=meta,
        )

    @classmethod
    def _validate(cls, catalog: ContextCatalog) -> None:
        """
        验证 catalog（仅打警告日志，不失败）。
        """
        # 检查 ID 唯一性
        id_set: set[str] = set()
        for item in catalog.contexts:
            if item.id in id_set:
                logger.warning(f"Duplicate catalog item ID: {item.id}")
            id_set.add(item.id)

        # 检查必需项
        required_ids = [item.id for item in catalog.list_required()]
        if required_ids:
            logger.debug(f"Required context items: {required_ids}")


# ========== 全局单例 ==========

_catalog_cache: dict[tuple[str, bool], ContextCatalog] = {}


def get_catalog(
    path: str | Path | None = None,
    force_reload: bool = False,
    *,
    strict: bool = False,
) -> ContextCatalog:
    """
    获取全局 ContextCatalog 单例。

    Args:
        path: 可选的加载路径（仅在第一次加载或 force_reload 时使用）
        force_reload: 强制从磁盘重新加载
    """
    global _catalog_cache

    resolved_path = str(
        Path(path or "data/config/prompt/context_catalog.yaml").resolve()
    )
    cache_key = (resolved_path, strict)

    if force_reload or cache_key not in _catalog_cache:
        _catalog_cache[cache_key] = ContextCatalogLoader.load(
            resolved_path,
            strict=strict,
        )

    return _catalog_cache[cache_key]
