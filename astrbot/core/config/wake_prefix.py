"""Canonical derivation for the LLM chat wake prefix."""

from collections.abc import Mapping


def resolve_provider_wake_prefix(runtime_config: Mapping[str, object]) -> str:
    """Return the provider-specific suffix after any bot wake prefix.

    A configured ``provider_settings.wake_prefix`` may include one of the
    profile's bot-level wake prefixes (for example, ``/chat``).  The provider
    and prompt layers both operate on the remaining suffix (``chat``).
    """

    provider_settings = runtime_config.get("provider_settings", {})
    raw_prefix = (
        provider_settings.get("wake_prefix", "")
        if isinstance(provider_settings, Mapping)
        else ""
    )
    provider_prefix = raw_prefix if isinstance(raw_prefix, str) else ""
    wake_prefixes = runtime_config.get("wake_prefix", [])
    if isinstance(wake_prefixes, str):
        wake_prefixes = [wake_prefixes]
    if not isinstance(wake_prefixes, list):
        return provider_prefix
    for bot_prefix in wake_prefixes:
        if isinstance(bot_prefix, str) and provider_prefix.startswith(bot_prefix):
            return provider_prefix[len(bot_prefix) :]
    return provider_prefix
