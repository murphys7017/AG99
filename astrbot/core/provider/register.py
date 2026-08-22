from astrbot.core import logger

from .entities import PromptRendererFamily, ProviderMetaData, ProviderType
from .func_tool_manager import FuncCall

provider_registry: list[ProviderMetaData] = []
"""维护了通过装饰器注册的 Provider"""
provider_cls_map: dict[str, ProviderMetaData] = {}
"""维护了 Provider 类型名称和 ProviderMetadata 的映射"""

llm_tools = FuncCall()


def register_provider_adapter(
    provider_type_name: str,
    desc: str,
    provider_type: ProviderType = ProviderType.CHAT_COMPLETION,
    default_config_tmpl: dict | None = None,
    provider_display_name: str | None = None,
    prompt_renderer_family: PromptRendererFamily = "base",
):
    """用于注册平台适配器的带参装饰器"""
    allowed_renderer_families = {"base", "openai", "anthropic", "minimax"}
    if prompt_renderer_family not in allowed_renderer_families:
        raise ValueError(
            "Invalid prompt_renderer_family "
            f"{prompt_renderer_family!r}; expected one of "
            f"{sorted(allowed_renderer_families)}"
        )

    def decorator(cls):
        if provider_type_name in provider_cls_map:
            raise ValueError(
                f"检测到大模型提供商适配器 {provider_type_name} 已经注册，可能发生了大模型提供商适配器类型命名冲突。",
            )

        # 添加必备选项
        if default_config_tmpl:
            if "type" not in default_config_tmpl:
                default_config_tmpl["type"] = provider_type_name
            if "enable" not in default_config_tmpl:
                default_config_tmpl["enable"] = False
            if "id" not in default_config_tmpl:
                default_config_tmpl["id"] = provider_type_name

        pm = ProviderMetaData(
            id="default",  # will be replaced when instantiated
            model=None,
            type=provider_type_name,
            desc=desc,
            provider_type=provider_type,
            cls_type=cls,
            default_config_tmpl=default_config_tmpl,
            provider_display_name=provider_display_name,
            prompt_renderer_family=prompt_renderer_family,
        )
        provider_registry.append(pm)
        provider_cls_map[provider_type_name] = pm
        logger.debug(f"服务提供商 Provider {provider_type_name} 已注册")
        return cls

    return decorator
