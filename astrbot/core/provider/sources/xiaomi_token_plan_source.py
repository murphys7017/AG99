from astrbot import logger
from astrbot.core.provider.sources.anthropic_source import ProviderAnthropic

from ..register import register_provider_adapter

XIAOMI_TOKEN_PLAN_MODELS = [
    "mimo-v2.5-pro",
    "mimo-v2.5",
    "mimo-v2-pro",
    "mimo-v2-omni",
    "mimo-v2-flash",
]


@register_provider_adapter(
    "xiaomi_token_plan",
    "Xiaomi Token Plan 提供商适配器",
    prompt_renderer_family="anthropic",
)
class ProviderXiaomiTokenPlan(ProviderAnthropic):
    """Xiaomi Token Plan provider using the Anthropic-compatible API."""

    def __init__(
        self,
        provider_config,
        provider_settings,
    ) -> None:
        provider_config["api_base"] = "https://token-plan-cn.xiaomimimo.com/anthropic"

        key = provider_config.get("key", "")
        actual_key = key[0] if isinstance(key, list) and key else key
        if actual_key:
            provider_config.setdefault("custom_headers", {})["Authorization"] = (
                f"Bearer {actual_key}"
            )

        super().__init__(
            provider_config,
            provider_settings,
        )

        configured_model = provider_config.get("model", "mimo-v2.5")
        if configured_model not in XIAOMI_TOKEN_PLAN_MODELS:
            logger.warning(
                f"Configured model {configured_model!r} is not in the known "
                f"Token Plan model list "
                f"({', '.join(XIAOMI_TOKEN_PLAN_MODELS)}). "
                f"The model may still work if your plan supports it. "
                f"If you encounter errors, please check your plan's "
                f"model availability."
            )

        self.set_model(configured_model)

    async def get_models(self) -> list[str]:
        return XIAOMI_TOKEN_PLAN_MODELS.copy()
