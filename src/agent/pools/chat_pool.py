"""ChatPool 最小实现（不接入 LLM）。"""

from __future__ import annotations

import os
from typing import Any, Dict

from loguru import logger

from ..types import AgentRequest, ContextPack, TaskPlan


class ChatPool:
    """Phase 0 聊天池：生成一个稳定 draft。"""

    pool_id = "chat"
    name = "chat_pool"

    async def run(self, req: AgentRequest, plan: TaskPlan, ctx: ContextPack) -> Dict[str, Any]:
        # ============================================================
        # Phase 1.2 集成点：PromptEngine 调试模式
        # 通过环境变量 DEBUG_PROMPT_ENGINE=1 启用
        # ============================================================
        if os.getenv("DEBUG_PROMPT_ENGINE") == "1":
            try:
                from ..prompt_engine import PromptEngine
                logger.info("DEBUG_PROMPT_ENGINE enabled, invoking PromptEngine")
                
                engine = PromptEngine()
                messages, manifest = engine.render("chat.single_pass", plan, ctx)
                
                logger.info(f"PromptEngine manifest: {manifest.summary()}")
                logger.debug(f"PromptEngine messages: {len(messages)} messages")
                
                # 在调试模式下，返回 manifest 摘要而不是实际 draft
                return {
                    "draft": f"[DEBUG] PromptEngine rendered {len(messages)} messages. See manifest for details.",
                    "task_type": plan.task_type,
                    "pool_id": self.pool_id,
                    "context_recent_count": len(ctx.recent_obs),
                    "prompt_engine_manifest": manifest.to_dict(),
                    "prompt_engine_messages": messages,
                }
            except Exception as e:
                logger.error(f"PromptEngine debug mode failed: {e}", exc_info=True)
                # Fail-open: 继续正常流程
        
        # ============================================================
        # Phase 0 原有逻辑（保持不变）
        # ============================================================
        current_slot = ctx.slots.get("current_input") if hasattr(ctx, "slots") else None
        slot_value = current_slot.value if current_slot else {}
        slot_text = slot_value.get("text") if isinstance(slot_value, dict) else None

        payload = getattr(req.obs, "payload", None)
        text = slot_text if isinstance(slot_text, str) else getattr(payload, "text", None)
        normalized = (text or "").strip() if isinstance(text, str) else ""

        if not normalized:
            draft = "我收到了你的消息。请再补充一点细节，我会继续处理。"
        elif plan.task_type == "code":
            draft = f"我看到了代码/报错线索：{normalized}"
        elif plan.task_type == "plan":
            draft = f"我看到了设计/方案诉求：{normalized}"
        elif plan.task_type == "creative":
            draft = f"我看到了创意方向：{normalized}"
        else:
            draft = f"我收到了你的消息：{normalized}"

        return {
            "draft": draft,
            "task_type": plan.task_type,
            "pool_id": self.pool_id,
            "context_recent_count": len(ctx.recent_obs),
        }
