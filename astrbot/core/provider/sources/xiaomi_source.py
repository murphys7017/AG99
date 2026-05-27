from astrbot import logger
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial

from ..register import register_provider_adapter

XIAOMI_MODELS = [
    "mimo-v2.5-pro",
    "mimo-v2.5",
    "mimo-v2-pro",
    "mimo-v2-omni",
    "mimo-v2-flash",
]


@register_provider_adapter(
    "xiaomi_chat_completion",
    "Xiaomi API 提供商适配器 (OpenAI 兼容)",
    prompt_renderer_family="openai",
)
class ProviderXiaomi(ProviderOpenAIOfficial):
    """Xiaomi provider using the OpenAI-compatible MiMo API."""

    def __init__(
        self,
        provider_config,
        provider_settings,
    ) -> None:
        if not provider_config.get("api_base"):
            provider_config["api_base"] = "https://api.xiaomimimo.com/v1"

        super().__init__(
            provider_config,
            provider_settings,
        )

        configured_model = provider_config.get("model", "mimo-v2.5")
        self.set_model(configured_model)

        logger.debug(f"Xiaomi provider initialized with model: {self.get_model()}")

    async def get_models(self) -> list[str]:
        try:
            models = await super().get_models()
            if models:
                return models
        except Exception as e:
            logger.debug(f"Failed to fetch models from Xiaomi API: {e}")

        return XIAOMI_MODELS.copy()
