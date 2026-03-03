"""PromptEngine - 统一 Prompt 渲染引擎（Phase 1.2 MVP）。

核心流程：
1. ContextViewBuilder: ContextPack → RenderedBlocks（+ llm_exposure 控制）
2. LayoutPolicy: Blocks → prefix/middle/suffix 分组
3. BudgetController: 应用 per_item_max + max_chars 截断
4. TemplateRenderer: 渲染 system/user 模板
5. MessageComposer: 组装 OpenAI messages

输出：
- messages: List[Dict] - OpenAI 格式
- manifest: RenderManifest - 可观测清单
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from ..context import (
    ContextCatalog,
    ContextPresetsCollection,
    PromptProfile,
    load_catalog,
    load_presets,
    load_profile,
)
from ..context.types import ContextPack
from ..types import TaskPlan
from .budget import BudgetController
from .composer import MessageComposer
from .layout import LayoutPolicy
from .manifest import RenderManifest
from .templates import TemplateRenderer
from .view import ContextViewBuilder


class PromptEngine:
    """统一 Prompt 渲染引擎。
    
    将 ContextPack + TaskPlan + PromptProfile 渲染为 LLM messages 和可观测 manifest。
    """
    
    def __init__(
        self,
        catalog: Optional[ContextCatalog] = None,
        presets: Optional[ContextPresetsCollection] = None,
    ):
        """初始化 PromptEngine。
        
        Args:
            catalog: Context catalog（如果不提供，会从默认路径加载）
            presets: Context presets（如果不提供，会从默认路径加载）
        """
        # 加载 catalog 和 presets（如果未提供）
        if catalog is None:
            try:
                catalog = load_catalog("config/context_catalog.yaml")
                logger.debug("PromptEngine loaded catalog from default path")
            except Exception as e:
                logger.error(f"Failed to load catalog: {e}")
                raise
        
        if presets is None:
            try:
                presets = load_presets("config/context_presets.yaml")
                logger.debug("PromptEngine loaded presets from default path")
            except Exception as e:
                logger.warning(f"Failed to load presets (non-blocking): {e}")
        
        self.catalog = catalog
        self.presets = presets
        
        # 初始化各个组件
        self.view_builder = ContextViewBuilder(catalog, presets)
        self.layout_policy = LayoutPolicy()
        self.budget_controller = BudgetController()
        self.template_renderer = TemplateRenderer()
        self.message_composer = MessageComposer()
    
    def render(
        self,
        profile_id: str,
        plan: TaskPlan,
        ctx: ContextPack,
        profile: Optional[PromptProfile] = None,
    ) -> Tuple[List[Dict[str, Any]], RenderManifest]:
        """渲染 Prompt。
        
        Args:
            profile_id: Prompt profile ID
            plan: Task plan
            ctx: Context pack
            profile: 可选，直接提供 profile 对象（用于测试）
        
        Returns:
            (messages, manifest)
        """
        # 1. 加载 profile（如果未提供）
        if profile is None:
            profile = self._load_profile(profile_id)
        
        # 2. 初始化 manifest
        manifest = RenderManifest(profile_id=profile_id)
        
        logger.debug(f"PromptEngine rendering profile '{profile_id}'")
        
        try:
            # 3. 构建 view（从 ContextPack 提取 items）
            blocks = self.view_builder.build_view(profile, plan, ctx, manifest)
            logger.debug(f"ContextViewBuilder produced {len(blocks)} blocks")
            
            # 4. 应用布局（prefix/middle/suffix 分组）
            layout_result = self.layout_policy.apply_layout(blocks, manifest)
            
            # 5. 应用预算控制（截断）
            layout_result = self.budget_controller.apply_budget(profile, layout_result, manifest)
            
            # 6. 渲染模板
            system_text, user_text = self.template_renderer.render_templates(
                profile, layout_result, manifest
            )
            
            # 7. 组装 messages
            messages = self.message_composer.compose_messages(system_text, user_text, manifest)
            
            logger.info(f"PromptEngine completed: {manifest.summary()}")
            
            return messages, manifest
        
        except Exception as e:
            logger.error(f"PromptEngine render failed: {e}", exc_info=True)
            manifest.meta["error"] = str(e)
            
            # Fail-open: 返回最小 messages
            fallback_messages = self._get_fallback_messages(ctx)
            return fallback_messages, manifest
    
    def _load_profile(self, profile_id: str) -> PromptProfile:
        """加载 profile（支持文件路径或 ID）。"""
        # 尝试作为文件路径加载
        if "/" in profile_id or "\\" in profile_id or profile_id.endswith(".yaml"):
            try:
                return load_profile(profile_id)
            except Exception as e:
                logger.warning(f"Failed to load profile from path '{profile_id}': {e}")
        
        # 尝试从标准目录加载
        profile_path = f"config/agent/prompt_profiles/{profile_id}.yaml"
        try:
            return load_profile(profile_path)
        except Exception as e:
            logger.error(f"Failed to load profile '{profile_id}': {e}")
            raise
    
    def _get_fallback_messages(self, ctx: ContextPack) -> List[Dict[str, Any]]:
        """获取降级 messages（fail-open）。"""
        # 尝试从 current_input 提取
        current_input_slot = ctx.slots.get("current_input")
        user_text = "User input not available"
        
        if current_input_slot and current_input_slot.status == "ok":
            value = current_input_slot.value
            if isinstance(value, dict) and "text" in value:
                user_text = value["text"]
            elif isinstance(value, str):
                user_text = value
        
        return [{"role": "user", "content": user_text}]
