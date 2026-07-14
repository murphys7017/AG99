from collections.abc import AsyncGenerator
from typing import Any

from openai.lib.streaming.chat._completions import ChatCompletionStreamState
from openai.types.chat.chat_completion import ChatCompletion

import astrbot.core.message.components as Comp
from astrbot import logger
from astrbot.core.agent.tool import ToolSet
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.provider.entities import LLMResponse

from ..register import register_provider_adapter
from .openai_source import ProviderOpenAIOfficial


@register_provider_adapter(
    "deepseek_chat_completion",
    "DeepSeek Chat Completion 提供商适配器",
    prompt_renderer_family="openai",
)
class ProviderDeepSeek(ProviderOpenAIOfficial):
    @staticmethod
    def _extract_thinking_type(source: Any) -> str | None:
        if not isinstance(source, dict):
            return None
        thinking = source.get("thinking")
        if not isinstance(thinking, dict):
            return None
        thinking_type = thinking.get("type")
        if not isinstance(thinking_type, str):
            return None
        normalized = thinking_type.strip().lower()
        return normalized or None

    def _is_thinking_enabled(
        self,
        payloads: dict,
        extra_body: dict[str, Any] | None = None,
    ) -> bool:
        for source in (
            payloads,
            extra_body,
            self.provider_config.get("custom_extra_body", {}),
        ):
            thinking_type = self._extract_thinking_type(source)
            if thinking_type == "enabled":
                return True
            if thinking_type == "disabled":
                return False
        # DeepSeek documents thinking mode as enabled by default.
        return True

    def _prepare_request(
        self,
        payloads: dict,
        tools: ToolSet | None,
    ) -> tuple[dict, dict[str, Any], ToolSet | None]:
        if tools:
            tool_list = tools.get_func_desc_openai_style(
                omit_empty_parameter_field=False,
            )
            if tool_list:
                payloads["tools"] = tool_list

        extra_body: dict[str, Any] = {}
        to_del = []
        for key in payloads:
            if key not in self.default_params:
                extra_body[key] = payloads[key]
                to_del.append(key)
        for key in to_del:
            del payloads[key]

        custom_extra_body = self.provider_config.get("custom_extra_body", {})
        if isinstance(custom_extra_body, dict):
            extra_body.update(custom_extra_body)
        self._apply_provider_specific_extra_body_overrides(extra_body)

        if "tool_choice" in payloads:
            extra_body.pop("tool_choice", None)
        self._sanitize_assistant_messages(payloads)
        return payloads, extra_body, tools

    def _finally_convert_payload(self, payloads: dict) -> None:
        thinking_enabled = self._is_thinking_enabled(payloads)

        super()._finally_convert_payload(payloads)

        if thinking_enabled:
            return

        for message in payloads.get("messages", []):
            if isinstance(message, dict) and message.get("role") == "assistant":
                message.pop("reasoning_content", None)

    async def _query(self, payloads: dict, tools: ToolSet | None) -> LLMResponse:
        payloads, extra_body, tools = self._prepare_request(payloads, tools)

        completion = await self.client.chat.completions.create(
            **payloads,
            stream=False,
            extra_body=extra_body,
        )

        if not isinstance(completion, ChatCompletion):
            raise Exception(
                f"API 返回的 completion 类型错误：{type(completion)}: {completion}。",
            )

        logger.debug(f"completion: {completion}")

        return await self._parse_openai_completion(completion, tools)

    async def _query_stream(
        self,
        payloads: dict,
        tools: ToolSet | None,
    ) -> AsyncGenerator[LLMResponse, None]:
        payloads, extra_body, tools = self._prepare_request(payloads, tools)

        stream = await self.client.chat.completions.create(
            **payloads,
            stream=True,
            extra_body=extra_body,
            stream_options={"include_usage": True},
        )

        llm_response = LLMResponse("assistant", is_chunk=True)
        state = ChatCompletionStreamState()

        async for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            delta = choice.delta if choice else None

            if delta and (dtcs := delta.tool_calls):
                for idx, tc in enumerate(dtcs):
                    if tc.function and tc.function.arguments:
                        tc.type = "function"
                    if not hasattr(tc, "index") or tc.index is None:
                        tc.index = idx

            if delta is not None or chunk.usage:
                try:
                    state.handle_chunk(chunk)
                except Exception as e:
                    logger.error("Saving chunk state error: " + str(e))

            reasoning = self._extract_reasoning_content(chunk)
            has_delta = False
            llm_response.id = chunk.id
            llm_response.reasoning_content = None
            llm_response.completion_text = ""
            if reasoning is not None:
                llm_response.reasoning_content = reasoning
                has_delta = True
            if delta and delta.content:
                completion_text = self._normalize_content(delta.content, strip=False)
                llm_response.result_chain = MessageChain(
                    chain=[Comp.Plain(completion_text)],
                )
                has_delta = True
            if chunk.usage:
                llm_response.usage = self._extract_usage(chunk.usage)
            elif choice and (choice_usage := getattr(choice, "usage", None)):
                llm_response.usage = self._extract_usage(choice_usage)
                state.current_completion_snapshot.usage = choice_usage
            if has_delta:
                yield llm_response

        try:
            final_completion = state.get_final_completion()
            llm_response = await self._parse_openai_completion(final_completion, tools)
            yield llm_response
        except Exception as e:
            logger.error("get_final_completion error: " + str(e))
            return
