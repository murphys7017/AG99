import httpx

from astrbot import logger
from astrbot.core.provider.sources.anthropic_source import ProviderAnthropic

from ..register import register_provider_adapter

MINIMAX_TOKEN_PLAN_MODELS = [
    "MiniMax-M3",
    "MiniMax-M2.7",
    "MiniMax-M2.7-highspeed",
    "MiniMax-M2.5",
    "MiniMax-M2.5-highspeed",
    "MiniMax-M2.1",
    "MiniMax-M2.1-highspeed",
    "MiniMax-M2",
]


@register_provider_adapter(
    "minimax_token_plan",
    "MiniMax Token Plan Provider Adapter",
    prompt_renderer_family="minimax",
)
class ProviderMiniMaxTokenPlan(ProviderAnthropic):
    """MiniMax Token Plan provider.

    The model list is fetched from MiniMax when possible and falls back to a
    local list when the endpoint or API key is unavailable.
    """

    def __init__(
        self,
        provider_config,
        provider_settings,
    ) -> None:
        # Keep api_base fixed; Token Plan users do not need to configure it.
        provider_config["api_base"] = "https://api.minimaxi.com/anthropic"
        # MiniMax Token Plan requires the Authorization: Bearer <token> header.
        key = provider_config.get("key", "")
        actual_key = key[0] if isinstance(key, list) else key
        provider_config.setdefault("custom_headers", {})["Authorization"] = (
            f"Bearer {actual_key}"
        )

        super().__init__(
            provider_config,
            provider_settings,
        )

        configured_model = provider_config.get("model", "MiniMax-M3")
        self.set_model(configured_model)

    async def get_models(self) -> list[str]:
        key = self.chosen_api_key
        if not key:
            logger.warning("No API key configured for MiniMax Token Plan.")
            return MINIMAX_TOKEN_PLAN_MODELS.copy()
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://api.minimaxi.com/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                models = [
                    item["id"]
                    for item in data.get("data", [])
                    if isinstance(item, dict) and isinstance(item.get("id"), str)
                ]
                return models or MINIMAX_TOKEN_PLAN_MODELS.copy()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch MiniMax Token Plan model list: %s", exc)
            return MINIMAX_TOKEN_PLAN_MODELS.copy()
