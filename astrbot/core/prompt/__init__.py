"""
AstrBot Prompt Engine - 上下文数据层（第一阶段）

本模块提供：
- ContextCatalog: 声明式上下文定义
- ContextPack/ContextSlot: 收集到的上下文数据容器
- ContextCollector: 上下文收集器抽象基类和具体实现
-（未来：Selector 选择器、Renderer 渲染器）
"""

from .collectors import (
    ConversationHistoryCollector,
    InputCollector,
    KnowledgeCollector,
    MemoryCollector,
    PersonaCollector,
    PolicyCollector,
    SessionCollector,
    SkillsCollector,
    SubagentCollector,
    SystemCollector,
    ToolsCollector,
)
from .context_catalog import (
    CatalogItem,
    ContextCatalog,
    ContextCatalogLoader,
    get_catalog,
)
from .context_collect import (
    PROMPT_CONTEXT_PACK_EXTRA_KEY,
    collect_context_pack,
    log_context_pack,
)
from .context_types import (
    CategoryType,
    ContextPack,
    ContextSlot,
    LifecycleType,
    LLMExposureType,
    PlacementType,
    RenderModeType,
    SlotName,
)
from .extensions import (
    PROMPT_EXTENSION_MOUNTS,
    PROMPT_EXTENSION_VALUE_KINDS,
    PromptExtension,
    PromptExtensionMount,
    PromptExtensionValueKind,
)
from .input_annotations import (
    INPUT_ITEM_ANNOTATIONS_EXTRA_KEY,
    INPUT_QUOTED_TEXT_ANNOTATION_KEY,
    INPUT_TEXT_ANNOTATION_KEY,
    SUPPORTED_INPUT_ANNOTATION_FIELDS,
    build_message_annotation_key,
    build_reply_chain_annotation_key,
    copy_input_annotation_fields,
    extract_input_annotation_fields,
    normalize_input_annotations,
)
from .interfaces import ContextCollectorInterface, PromptExtensionCollectorInterface
from .persona_segments import (
    finalize_persona_segments,
    normalize_section_name,
    parse_legacy_persona_prompt,
)
from .render import (
    AnthropicPromptRenderer,
    PROMPT_APPLY_RESULT_EXTRA_KEY,
    PROMPT_RENDER_RESULT_EXTRA_KEY,
    PROMPT_SELECTED_CONTEXT_PACK_EXTRA_KEY,
    PROMPT_SELECTION_DECISION_EXTRA_KEY,
    PROMPT_SHADOW_APPLY_RESULT_EXTRA_KEY,
    PROMPT_SHADOW_DIFF_EXTRA_KEY,
    PROMPT_SHADOW_PROVIDER_REQUEST_EXTRA_KEY,
    BasePromptRenderer,
    LLMPromptContextSelector,
    PassthroughPromptSelector,
    PromptApplyResult,
    PromptBuilder,
    PromptNode,
    PromptRenderEngine,
    PromptSelectionDecision,
    PromptSelectorInterface,
    PromptSelectorSettings,
    ProviderRequestAdapter,
    RenderResult,
    RuleBasedPromptSelector,
    SerializedRenderValue,
    apply_prompt_selection,
    apply_render_result_to_request,
    build_prompt_selector,
    select_context_pack,
    select_context_pack_async,
)

__all__ = [
    # Types
    "CategoryType",
    "LifecycleType",
    "PlacementType",
    "RenderModeType",
    "LLMExposureType",
    "SlotName",
    # Input annotation helpers
    "INPUT_ITEM_ANNOTATIONS_EXTRA_KEY",
    "INPUT_QUOTED_TEXT_ANNOTATION_KEY",
    "INPUT_TEXT_ANNOTATION_KEY",
    "SUPPORTED_INPUT_ANNOTATION_FIELDS",
    "build_message_annotation_key",
    "build_reply_chain_annotation_key",
    "copy_input_annotation_fields",
    "extract_input_annotation_fields",
    "normalize_input_annotations",
    # Prompt extensions
    "PROMPT_EXTENSION_MOUNTS",
    "PROMPT_EXTENSION_VALUE_KINDS",
    "PromptExtension",
    "PromptExtensionMount",
    "PromptExtensionValueKind",
    # Data models
    "ContextSlot",
    "ContextPack",
    # Catalog
    "CatalogItem",
    "ContextCatalog",
    "ContextCatalogLoader",
    "get_catalog",
    # Persona parsing
    "normalize_section_name",
    "parse_legacy_persona_prompt",
    "finalize_persona_segments",
    # Interfaces
    "ContextCollectorInterface",
    "PromptExtensionCollectorInterface",
    "BasePromptRenderer",
    "AnthropicPromptRenderer",
    "PROMPT_APPLY_RESULT_EXTRA_KEY",
    "PROMPT_RENDER_RESULT_EXTRA_KEY",
    "PROMPT_SELECTED_CONTEXT_PACK_EXTRA_KEY",
    "PROMPT_SELECTION_DECISION_EXTRA_KEY",
    "PROMPT_SHADOW_APPLY_RESULT_EXTRA_KEY",
    "PROMPT_SHADOW_DIFF_EXTRA_KEY",
    "PROMPT_SHADOW_PROVIDER_REQUEST_EXTRA_KEY",
    "PromptApplyResult",
    "PromptBuilder",
    "PromptRenderEngine",
    "PromptNode",
    "PromptSelectionDecision",
    "PromptSelectorInterface",
    "PromptSelectorSettings",
    "ProviderRequestAdapter",
    "RenderResult",
    "RuleBasedPromptSelector",
    "SerializedRenderValue",
    "LLMPromptContextSelector",
    "apply_prompt_selection",
    "apply_render_result_to_request",
    "build_prompt_selector",
    # Collectors
    "ConversationHistoryCollector",
    "InputCollector",
    "KnowledgeCollector",
    "MemoryCollector",
    "PersonaCollector",
    "PolicyCollector",
    "SessionCollector",
    "SkillsCollector",
    "SubagentCollector",
    "SystemCollector",
    "ToolsCollector",
    # Selector
    "PassthroughPromptSelector",
    "select_context_pack",
    "select_context_pack_async",
    # Collection flow
    "PROMPT_CONTEXT_PACK_EXTRA_KEY",
    "collect_context_pack",
    "log_context_pack",
]
