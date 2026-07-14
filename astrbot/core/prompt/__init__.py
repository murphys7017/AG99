"""Structured prompt collection, target projection, tree building, and rendering."""

from .builder import (
    PromptContextBuilder,
    merge_context_packs,
)
from .collectors import (
    ConversationHistoryCollector,
    ExplicitContextCollector,
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
    PromptContextConflictError,
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
    PROMPT_APPLY_RESULT_EXTRA_KEY,
    PROMPT_RENDER_RESULT_EXTRA_KEY,
    PROMPT_SHADOW_APPLY_RESULT_EXTRA_KEY,
    PROMPT_SHADOW_DIFF_EXTRA_KEY,
    PROMPT_SHADOW_PROVIDER_REQUEST_EXTRA_KEY,
    AnthropicPromptRenderer,
    BasePromptRenderer,
    PromptApplyResult,
    PromptBuilder,
    PromptNode,
    PromptRenderEngine,
    PromptTreeBuilder,
    ProviderRequestAdapter,
    RenderResult,
    SerializedRenderValue,
    apply_render_result_to_request,
)
from .targets import PromptTarget, project_context_pack

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
    "PromptContextBuilder",
    "PromptContextConflictError",
    "PromptTarget",
    # Catalog
    "CatalogItem",
    "ContextCatalog",
    "ContextCatalogLoader",
    "get_catalog",
    "project_context_pack",
    "merge_context_packs",
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
    "PROMPT_SHADOW_APPLY_RESULT_EXTRA_KEY",
    "PROMPT_SHADOW_DIFF_EXTRA_KEY",
    "PROMPT_SHADOW_PROVIDER_REQUEST_EXTRA_KEY",
    "PromptApplyResult",
    "PromptBuilder",
    "PromptRenderEngine",
    "PromptTreeBuilder",
    "PromptNode",
    "ProviderRequestAdapter",
    "RenderResult",
    "SerializedRenderValue",
    "apply_render_result_to_request",
    # Collectors
    "ConversationHistoryCollector",
    "ExplicitContextCollector",
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
    # Collection flow
    "PROMPT_CONTEXT_PACK_EXTRA_KEY",
    "collect_context_pack",
    "log_context_pack",
]
