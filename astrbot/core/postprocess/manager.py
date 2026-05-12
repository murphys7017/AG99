from __future__ import annotations

from collections import defaultdict

from astrbot.core import logger

from .types import PostProcessContext, PostProcessor, PostProcessTrigger


class PostProcessManager:
    def __init__(self) -> None:
        self._processors: list[PostProcessor] = []
        self._trigger_mapping: dict[PostProcessTrigger, list[PostProcessor]] = (
            defaultdict(list)
        )

    def register(self, processor: PostProcessor) -> bool:
        if processor in self._processors:
            logger.debug(
                "postprocess: skip duplicate processor registration name=%s",
                processor.name,
            )
            return False

        self._processors.append(processor)
        for trigger in processor.triggers:
            self._trigger_mapping[trigger].append(processor)
        return True

    def unregister(self, processor: PostProcessor) -> bool:
        if processor not in self._processors:
            return False

        self._processors.remove(processor)
        for trigger in processor.triggers:
            processors = self._trigger_mapping.get(trigger)
            if not processors:
                continue
            self._trigger_mapping[trigger] = [
                registered for registered in processors if registered is not processor
            ]
            if not self._trigger_mapping[trigger]:
                del self._trigger_mapping[trigger]
        return True

    def clear(self) -> None:
        self._processors.clear()
        self._trigger_mapping.clear()

    def has_processors(self, trigger: PostProcessTrigger | None = None) -> bool:
        if trigger is None:
            return bool(self._processors)
        return bool(self._trigger_mapping.get(trigger))

    def get_processors(
        self,
        trigger: PostProcessTrigger,
    ) -> list[PostProcessor]:
        return list(self._trigger_mapping.get(trigger, []))

    async def dispatch(
        self,
        trigger: PostProcessTrigger,
        ctx: PostProcessContext,
    ) -> None:
        if ctx.trigger != trigger:
            raise ValueError(
                f"postprocess trigger mismatch: dispatch={trigger.value}, "
                f"context={ctx.trigger.value}"
            )

        processors = self.get_processors(trigger)
        if not processors:
            logger.debug("postprocess(%s): no processors registered", trigger.value)
            return

        logger.debug(
            "postprocess(%s): dispatching %d processor(s)",
            trigger.value,
            len(processors),
        )
        for processor in processors:
            logger.debug(
                "postprocess(%s): start processor=%s",
                trigger.value,
                processor.name,
            )
            await processor.run(ctx)
            logger.debug(
                "postprocess(%s): finish processor=%s",
                trigger.value,
                processor.name,
            )
