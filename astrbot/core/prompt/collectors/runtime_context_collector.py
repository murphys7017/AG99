from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from ..context_types import ContextSlot
from ..interfaces.context_collector_inferface import ContextCollectorInterface


class RuntimeContextCollector(ContextCollectorInterface):
    """Collect a read-only runtime fact projection for background policy."""

    def __init__(
        self,
        *,
        personal_state: Mapping[str, Any],
        observation_batch: Mapping[str, Any],
        observation_features: Mapping[str, Any],
        session_datetime: Mapping[str, Any],
        session_info: Mapping[str, Any],
    ) -> None:
        self._values = {
            "runtime.personal_state": dict(personal_state),
            "runtime.observation_batch": dict(observation_batch),
            "runtime.observation_features": dict(observation_features),
            "session.datetime": dict(session_datetime),
            "session.user_info": dict(session_info),
        }

    async def collect(
        self,
        event,
        plugin_context,
        config,
        provider_request=None,
    ) -> list[ContextSlot]:
        del event, plugin_context, config, provider_request
        return [
            ContextSlot(
                name=name,
                value=deepcopy(value),
                category="runtime" if name.startswith("runtime.") else "session",
                source="personal_runtime",
                render_mode="structured",
                meta={"targets": ["personal_policy"], "scope": "ephemeral"},
            )
            for name, value in self._values.items()
        ]


__all__ = ["RuntimeContextCollector"]
