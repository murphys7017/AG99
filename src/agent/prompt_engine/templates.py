"""TemplateRenderer - 模板渲染器（Jinja2）。

支持内联模板和外部文件引用，提供 fail-open 容错。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from jinja2 import Environment, Template, TemplateSyntaxError, UndefinedError
from loguru import logger

from ..context import PromptProfile
from .layout import LayoutResult
from .manifest import RenderManifest


class TemplateRenderer:
    """模板渲染器（Jinja2）。"""
    
    def __init__(self):
        self.env = Environment(
            autoescape=False,  # Prompt 不需要 HTML 转义
            trim_blocks=True,
            lstrip_blocks=True,
        )
    
    def render_templates(
        self,
        profile: PromptProfile,
        layout_result: LayoutResult,
        manifest: RenderManifest,
    ) -> tuple[str, str]:
        """渲染 system 和 user 模板。
        
        Args:
            profile: Prompt profile
            layout_result: Layout result with blocks
            manifest: Manifest to record errors
        
        Returns:
            (system_text, user_text)
        """
        # 1. 构建模板变量
        variables = self._build_variables(layout_result)
        
        # 2. 渲染 system template
        system_text = self._render_single_template(
            template_id="system",
            template_source=profile.templates.system_template,
            variables=variables,
            manifest=manifest,
        )
        
        # 3. 渲染 user template
        user_text = self._render_single_template(
            template_id="user",
            template_source=profile.templates.user_template,
            variables=variables,
            manifest=manifest,
        )
        
        # 4. 记录到 manifest
        manifest.template_ids["system"] = "inline"
        manifest.template_ids["user"] = "inline"
        
        return system_text, user_text
    
    def _build_variables(self, layout_result: LayoutResult) -> Dict[str, Any]:
        """构建模板变量。
        
        策略：
        - 每个 block 以 item_id 为 key 暴露（例如 current_input.text）
        - 支持点号访问（例如 {{ current_input.text }}）
        """
        variables: Dict[str, Any] = {}
        
        for block in layout_result.all_blocks():
            # 直接用 item_id 作为变量名（支持点号）
            # 例如：current_input.text -> variables["current_input.text"]
            variables[block.item_id] = block.value
            
            # 也尝试构建嵌套字典（例如 current_input.text -> variables["current_input"]["text"]）
            if "." in block.item_id:
                parts = block.item_id.split(".")
                if len(parts) == 2:
                    parent, child = parts
                    if parent not in variables:
                        variables[parent] = {}
                    if isinstance(variables[parent], dict):
                        variables[parent][child] = block.value
        
        return variables
    
    def _render_single_template(
        self,
        template_id: str,
        template_source: str,
        variables: Dict[str, Any],
        manifest: RenderManifest,
    ) -> str:
        """渲染单个模板（fail-open）。
        
        Args:
            template_id: 模板标识（system/user）
            template_source: 模板源码
            variables: 变量字典
            manifest: 记录错误
        
        Returns:
            渲染后的文本（失败时返回空字符串或降级内容）
        """
        if not template_source or not template_source.strip():
            logger.debug(f"Template '{template_id}' is empty, skipping")
            return ""
        
        try:
            # 支持 file:// 引用（可选扩展）
            if template_source.strip().startswith("file://"):
                template_source = self._load_from_file(template_source.strip()[7:])
            
            # 渲染
            template = self.env.from_string(template_source)
            result = template.render(**variables)
            return result.strip()
        
        except TemplateSyntaxError as e:
            error_msg = f"Template syntax error in '{template_id}': {e}"
            logger.error(error_msg)
            manifest.template_errors.append(error_msg)
            return self._get_fallback(template_id, variables)
        
        except UndefinedError as e:
            error_msg = f"Undefined variable in template '{template_id}': {e}"
            logger.warning(error_msg)
            manifest.template_errors.append(error_msg)
            # 对于 undefined 变量，尝试继续（Jinja2 会用空字符串替换）
            return self._get_fallback(template_id, variables)
        
        except Exception as e:
            error_msg = f"Template rendering error in '{template_id}': {e}"
            logger.error(error_msg)
            manifest.template_errors.append(error_msg)
            return self._get_fallback(template_id, variables)
    
    def _load_from_file(self, file_path: str) -> str:
        """从文件加载模板（可选扩展）。"""
        try:
            path = Path(file_path)
            if not path.exists():
                raise FileNotFoundError(f"Template file not found: {file_path}")
            return path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to load template file '{file_path}': {e}")
            raise
    
    def _get_fallback(self, template_id: str, variables: Dict[str, Any]) -> str:
        """获取降级内容（fail-open）。
        
        对于 user template，至少包含 current_input.text。
        对于 system template，返回空。
        """
        if template_id == "user":
            # 尝试提取 current_input.text
            current_input = variables.get("current_input.text")
            if current_input:
                return str(current_input)
            
            # 尝试嵌套访问
            if "current_input" in variables and isinstance(variables["current_input"], dict):
                text = variables["current_input"].get("text")
                if text:
                    return str(text)
            
            # 最后的降级
            return "User input not available"
        
        return ""
