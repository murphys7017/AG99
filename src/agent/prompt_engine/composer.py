"""MessageComposer - LLM Messages 组装器。

将渲染后的 system/user text 组装为 OpenAI 格式的 messages。
"""

from __future__ import annotations

from typing import Any, Dict, List

from loguru import logger

from .manifest import RenderManifest


class MessageComposer:
    """LLM Messages 组装器。"""
    
    def compose_messages(
        self,
        system_text: str,
        user_text: str,
        manifest: RenderManifest,
    ) -> List[Dict[str, Any]]:
        """组装 messages（OpenAI 格式）。
        
        Args:
            system_text: System prompt
            user_text: User prompt
            manifest: 记录输出长度
        
        Returns:
            List of messages: [{"role": "system", "content": ...}, {"role": "user", "content": ...}]
        """
        messages: List[Dict[str, Any]] = []
        
        # 1. 添加 system message（如果非空）
        if system_text and system_text.strip():
            messages.append({
                "role": "system",
                "content": system_text
            })
            manifest.output_lengths["system_chars"] = len(system_text)
        else:
            manifest.output_lengths["system_chars"] = 0
        
        # 2. 添加 user message（必须有）
        if not user_text or not user_text.strip():
            logger.warning("User text is empty, using fallback")
            user_text = "No user input available"
        
        messages.append({
            "role": "user",
            "content": user_text
        })
        manifest.output_lengths["user_chars"] = len(user_text)
        
        # 3. 计算总长度
        total_chars = sum(manifest.output_lengths.values())
        manifest.output_lengths["total_chars"] = total_chars
        
        logger.debug(
            f"MessageComposer: system={manifest.output_lengths['system_chars']} chars, "
            f"user={manifest.output_lengths['user_chars']} chars, "
            f"total={total_chars} chars"
        )
        
        return messages
