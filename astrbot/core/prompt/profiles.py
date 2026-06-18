"""
Prompt Profile - 按运行时职责定义 ContextPack 的内容边界。

三个内置 Profile：
  ROUTER_PROMPT_PROFILE         — 只含输入摘要，用于路由判断
  PERSONA_PROMPT_PROFILE        — 含 persona + interaction memory，无完整历史和工具
  CORE_EXECUTION_PROMPT_PROFILE — 含工具/技能/MCP/知识库，无 persona 和完整历史
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PromptRuntimePurpose(str, Enum):
    ROUTER = "router"
    PERSONA_REPLY = "persona_reply"
    CORE_EXECUTION = "core_execution"


@dataclass(frozen=True, slots=True)
class PromptProfile:
    """
    描述某个运行时角色允许/禁止哪些 ContextPack 槽位。

    allowed_slots: 非空时为白名单，只保留其中的槽。
    blocked_slots: 黑名单，始终过滤掉，优先级低于白名单。
    """

    purpose: PromptRuntimePurpose
    allowed_slots: frozenset[str] = frozenset()
    blocked_slots: frozenset[str] = frozenset()


# Router 只看输入内容，绝对不含人格/记忆/工具
ROUTER_PROMPT_PROFILE = PromptProfile(
    purpose=PromptRuntimePurpose.ROUTER,
    allowed_slots=frozenset(
        {
            "input.text",
            "input.quoted_text",
            "input.images",
            "input.files",
            "input.image_captions",
            "input.quoted_images",
            "input.quoted_image_captions",
        }
    ),
)

# Persona 含人格 + interaction memory + 输入，无完整对话历史和工具
PERSONA_PROMPT_PROFILE = PromptProfile(
    purpose=PromptRuntimePurpose.PERSONA_REPLY,
    blocked_slots=frozenset(
        {
            "conversation.history",
            "capability.tools_schema",
            "capability.plugin_tools_schema",
            "capability.skills_prompt",
            "system.tool_call_instruction",
        }
    ),
)

# Core 含工具/技能/MCP/知识库，无人格设定和完整对话历史
CORE_EXECUTION_PROMPT_PROFILE = PromptProfile(
    purpose=PromptRuntimePurpose.CORE_EXECUTION,
    blocked_slots=frozenset(
        {
            "persona.prompt",
            "persona.segments",
            "persona.begin_dialogs",
            "memory.persona_state",
            "conversation.history",
        }
    ),
)
