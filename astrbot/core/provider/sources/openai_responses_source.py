import copy
import inspect
import random
from collections.abc import AsyncGenerator
from typing import Any, Literal

from json_repair import repair_json
from openai.types.responses import Response

import astrbot.core.message.components as Comp
from astrbot import logger
from astrbot.core.agent.message import ContentPart, Message
from astrbot.core.agent.tool import ToolSet
from astrbot.core.exceptions import EmptyModelOutputError
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.output_contract import CompiledOutputContract, OutputContract
from astrbot.core.provider.entities import LLMResponse, TokenUsage, ToolCallsResult

from ..register import register_provider_adapter
from .openai_source import ProviderOpenAIOfficial


@register_provider_adapter(
    "openai_responses",
    "OpenAI API Responses 提供商适配器",
    prompt_renderer_family="openai",
)
class ProviderOpenAIResponses(ProviderOpenAIOfficial):
    """OpenAI Responses API adapter kept separate from Chat Completions."""

    _MAX_RECOVERY_ATTEMPTS = 3

    def __init__(self, provider_config, provider_settings) -> None:
        provider_config = dict(provider_config)
        if not provider_config.get("custom_headers") and isinstance(
            provider_config.get("http_headers"), dict
        ):
            provider_config["custom_headers"] = dict(provider_config["http_headers"])
        super().__init__(provider_config, provider_settings)
        self.default_params = inspect.signature(
            self.client.responses.create
        ).parameters.keys()
        self.store_responses = self._resolve_store_setting(provider_config)

    @staticmethod
    def _resolve_store_setting(provider_config: dict) -> bool | None:
        def as_bool(value: Any) -> bool:
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            return bool(value)

        if "store" in provider_config:
            return as_bool(provider_config["store"])
        if "disable_response_storage" in provider_config:
            return not as_bool(provider_config["disable_response_storage"])
        return None

    @staticmethod
    def _content_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict)
                and part.get("type")
                in {
                    "text",
                    "input_text",
                    "output_text",
                }
            )
        return "" if content is None else str(content)

    @staticmethod
    def _response_content_parts(content: Any, role: str) -> Any:
        if not isinstance(content, list):
            return content

        parts: list[dict] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type in {"text", "input_text", "output_text"}:
                # Historical messages are sent as Responses input items. The
                # SDK accepts `input_text` for both user and assistant roles;
                # `output_text` is an output-only content type.
                parts.append({"type": "input_text", "text": str(part.get("text", ""))})
            elif part_type == "image_url":
                image = part.get("image_url")
                if isinstance(image, dict) and image.get("url"):
                    image_part = {
                        "type": "input_image",
                        "image_url": image["url"],
                        "detail": image.get("detail") or "auto",
                    }
                    parts.append(image_part)
            elif part_type in {"input_audio", "audio_url"}:
                raise ValueError("OpenAI Responses API 当前不支持音频输入")
            elif part_type in {"input_image", "input_file"}:
                parts.append(copy.deepcopy(part))
        return parts

    @classmethod
    def _messages_to_response_input(
        cls, messages: list[dict]
    ) -> tuple[str, list[dict]]:
        instructions: list[str] = []
        response_input: list[dict] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            content = message.get("content")
            if role in {"system", "developer"}:
                text = cls._content_text(content).strip()
                if text:
                    instructions.append(text)
                continue
            if role == "assistant" and message.get("tool_calls"):
                if content not in (None, "", []):
                    response_input.append(
                        {
                            "role": "assistant",
                            "content": cls._response_content_parts(content, role),
                        }
                    )
                for tool_call in message["tool_calls"]:
                    if not isinstance(tool_call, dict):
                        continue
                    function = tool_call.get("function", {})
                    response_input.append(
                        {
                            "type": "function_call",
                            "call_id": tool_call.get("id", ""),
                            "name": function.get("name", ""),
                            "arguments": function.get("arguments", "{}"),
                        }
                    )
                continue
            if role == "tool":
                response_input.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.get("tool_call_id", ""),
                        "output": cls._content_text(content),
                    }
                )
                continue
            if role not in {"user", "assistant"}:
                continue
            item = {"role": role, "content": cls._response_content_parts(content, role)}
            response_input.append(item)
        return "\n\n".join(instructions), response_input

    @staticmethod
    def _responses_tools(tools: ToolSet | None) -> list[dict]:
        if not tools:
            return []
        converted: list[dict] = []
        for item in tools.openai_schema():
            function = item.get("function", {})
            tool = {"type": "function", "name": function.get("name", "")}
            for field in ("description", "parameters", "strict"):
                if field in function:
                    tool[field] = function[field]
            converted.append(tool)
        return converted

    async def _prepare_response_payload(
        self,
        prompt: str | None,
        image_urls: list[str] | None = None,
        audio_urls: list[str] | None = None,
        contexts: list[dict] | list[Message] | None = None,
        system_prompt: str | None = None,
        tool_calls_result: ToolCallsResult | list[ToolCallsResult] | None = None,
        model: str | None = None,
        extra_user_content_parts: list[ContentPart] | None = None,
        **kwargs,
    ) -> tuple[dict, list[dict]]:
        payloads, context_query = await super()._prepare_chat_payload(
            prompt,
            image_urls,
            audio_urls,
            contexts,
            system_prompt,
            tool_calls_result,
            model=model,
            extra_user_content_parts=extra_user_content_parts,
        )
        instructions, response_input = self._messages_to_response_input(context_query)
        payload = {"model": payloads["model"], "input": response_input}
        if instructions:
            payload["instructions"] = instructions
        payload.update(kwargs)
        return payload, response_input

    @staticmethod
    def _parse_arguments(arguments: Any) -> dict:
        if isinstance(arguments, dict):
            return arguments
        try:
            parsed = repair_json(str(arguments or "{}"), return_objects=True)
            return parsed if isinstance(parsed, dict) else {}
        except Exception as exc:
            logger.error("解析 Responses 工具参数失败: %s", exc)
            return {}

    @staticmethod
    def _extract_usage(response: Any) -> TokenUsage | None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        details = getattr(usage, "input_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) if details else 0
        return TokenUsage(
            input_other=max(
                (getattr(usage, "input_tokens", 0) or 0) - (cached or 0), 0
            ),
            input_cached=cached or 0,
            output=getattr(usage, "output_tokens", 0) or 0,
        )

    @classmethod
    def _parse_response(cls, response: Response, tools: ToolSet | None) -> LLMResponse:
        llm_response = LLMResponse("assistant")
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_args: list[dict] = []
        tool_names: list[str] = []
        tool_ids: list[str] = []
        for item in getattr(response, "output", []) or []:
            item_type = getattr(item, "type", None)
            if item_type == "message":
                for content in getattr(item, "content", []) or []:
                    if getattr(content, "type", None) == "output_text":
                        text_parts.append(getattr(content, "text", ""))
                    elif getattr(content, "type", None) == "refusal":
                        text_parts.append(getattr(content, "refusal", ""))
            elif item_type == "reasoning":
                for summary in getattr(item, "summary", []) or []:
                    if getattr(summary, "type", None) == "summary_text":
                        reasoning_parts.append(getattr(summary, "text", ""))
            elif item_type == "function_call":
                tool_args.append(cls._parse_arguments(getattr(item, "arguments", "{}")))
                tool_names.append(getattr(item, "name", ""))
                tool_ids.append(
                    getattr(item, "call_id", None) or getattr(item, "id", "")
                )
        text = getattr(response, "output_text", None) or "".join(text_parts)
        if text:
            llm_response.result_chain = MessageChain().message(text.strip())
        if reasoning_parts:
            llm_response.reasoning_content = "\n".join(reasoning_parts)
        if tool_args:
            llm_response.role = "tool"
            llm_response.tools_call_args = tool_args
            llm_response.tools_call_name = tool_names
            llm_response.tools_call_ids = tool_ids
        if not text.strip() and not reasoning_parts and not tool_args:
            raise EmptyModelOutputError(
                f"Responses response has no usable output. response_id={getattr(response, 'id', None)}"
            )
        llm_response.raw_completion = response
        llm_response.id = getattr(response, "id", None)
        llm_response.usage = cls._extract_usage(response)
        return llm_response

    def _request_options(
        self,
        payload: dict,
        tools: ToolSet | None,
        tool_choice: str,
    ) -> dict:
        request = dict(payload)
        response_tools = self._responses_tools(tools)
        if response_tools:
            request["tools"] = response_tools
            request["tool_choice"] = tool_choice
        if self.store_responses is not None:
            request["store"] = self.store_responses
        custom_extra_body = self.provider_config.get("custom_extra_body", {})
        if isinstance(custom_extra_body, dict):
            request.update(copy.deepcopy(custom_extra_body))
        # Model entries may use the provider-agnostic ``reasoning`` switch.
        # Responses API expects an object, so translate the explicit disabled
        # form instead of silently ignoring it. Explicit custom_extra_body
        # values remain authoritative for provider-specific tuning.
        if "reasoning" not in request:
            reasoning = self.provider_config.get("reasoning")
            if isinstance(reasoning, bool):
                if reasoning is False:
                    request["reasoning"] = {"effort": "none"}
            elif isinstance(reasoning, str):
                normalized = reasoning.strip().lower()
                if normalized in {"false", "disabled", "off", "none"}:
                    request["reasoning"] = {"effort": "none"}
                elif normalized in {"minimal", "low", "medium", "high", "xhigh"}:
                    request["reasoning"] = {"effort": normalized}
            elif isinstance(reasoning, dict):
                request["reasoning"] = copy.deepcopy(reasoning)
        allowed = set(self.default_params)
        extra_body = {
            key: request.pop(key) for key in list(request) if key not in allowed
        }
        return {**request, "extra_body": extra_body} if extra_body else request

    async def _query(
        self,
        payload: dict,
        tools: ToolSet | None,
        tool_choice: str,
    ) -> LLMResponse:
        request = self._request_options(payload, tools, tool_choice)
        response = await self.client.responses.create(**request)
        if not isinstance(response, Response):
            raise TypeError(f"Responses API 返回类型错误: {type(response)}: {response}")
        return self._parse_response(response, tools)

    async def _query_stream(
        self, payload: dict, tools: ToolSet | None, tool_choice: str
    ) -> AsyncGenerator[LLMResponse, None]:
        request = self._request_options(payload, tools, tool_choice)
        request["stream"] = True
        stream = await self.client.responses.create(**request)
        response_id: str | None = None
        final_response: Response | None = None
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: dict[str, dict[str, str]] = {}
        async for event in stream:
            event_type = getattr(event, "type", "")
            if event_type in {"response.failed", "response.incomplete", "error"}:
                response = getattr(event, "response", None)
                error = getattr(response, "error", None) if response else None
                detail = getattr(error, "message", None) or str(error or event)
                raise RuntimeError(f"Responses 流式请求失败: {detail}")
            if event_type in {"response.created", "response.completed"}:
                response = getattr(event, "response", None)
                response_id = getattr(response, "id", None) or response_id
                if event_type == "response.completed":
                    final_response = response
            elif event_type == "response.output_item.added":
                item = getattr(event, "item", None)
                if getattr(item, "type", None) == "function_call":
                    item_id = getattr(item, "id", "")
                    tool_calls[item_id] = {
                        "id": getattr(item, "call_id", None) or item_id,
                        "name": getattr(item, "name", ""),
                        "arguments": "",
                    }
            if event_type == "response.output_text.delta":
                delta = getattr(event, "delta", "")
                if delta:
                    text_parts.append(delta)
                    yield LLMResponse(
                        "assistant",
                        result_chain=MessageChain(chain=[Comp.Plain(delta)]),
                        is_chunk=True,
                        id=response_id,
                    )
            elif event_type in {
                "response.reasoning_text.delta",
                "response.reasoning_summary_text.delta",
            }:
                delta = getattr(event, "delta", "")
                if delta:
                    reasoning_parts.append(delta)
                    yield LLMResponse(
                        "assistant",
                        reasoning_content=delta,
                        is_chunk=True,
                        id=response_id,
                    )
            elif event_type == "response.function_call_arguments.done":
                item_id = getattr(event, "item_id", "")
                call = tool_calls.setdefault(
                    item_id,
                    {
                        "id": item_id,
                        "name": getattr(event, "name", ""),
                        "arguments": "",
                    },
                )
                call["name"] = getattr(event, "name", "") or call["name"]
                call["arguments"] = getattr(event, "arguments", "")
        if final_response is not None:
            yield self._parse_response(final_response, tools)
            return
        if text_parts or reasoning_parts or tool_calls:
            fallback = LLMResponse(
                "tool" if tool_calls else "assistant",
                completion_text="".join(text_parts) or None,
                reasoning_content="".join(reasoning_parts) or None,
                tools_call_args=[
                    self._parse_arguments(call["arguments"])
                    for call in tool_calls.values()
                ],
                tools_call_name=[call["name"] for call in tool_calls.values()],
                tools_call_ids=[call["id"] for call in tool_calls.values()],
                id=response_id,
            )
            yield fallback
            return
        raise EmptyModelOutputError("Responses 流式请求未返回可用输出")

    async def text_chat(
        self,
        prompt=None,
        session_id=None,
        image_urls=None,
        audio_urls=None,
        func_tool=None,
        contexts=None,
        system_prompt=None,
        tool_calls_result=None,
        model=None,
        extra_user_content_parts=None,
        tool_choice: Literal["auto", "required"] = "auto",
        output_contract: OutputContract | None = None,
        compiled_output_contract: CompiledOutputContract | None = None,
        **kwargs,
    ) -> LLMResponse:
        payload, _ = await self._prepare_response_payload(
            prompt,
            image_urls,
            audio_urls,
            contexts,
            system_prompt,
            tool_calls_result,
            model,
            extra_user_content_parts,
            **kwargs,
        )
        self.ensure_output_contract_supported(
            output_contract=output_contract,
            compiled_output_contract=compiled_output_contract,
        )
        func_tool, tool_choice = self._resolve_output_contract(
            output_contract, compiled_output_contract, func_tool, tool_choice
        )
        for attempt in range(self._MAX_RECOVERY_ATTEMPTS):
            try:
                self.client.api_key = self.chosen_api_key
                return await self._query(payload, func_tool, tool_choice)
            except Exception:
                if attempt + 1 >= self._MAX_RECOVERY_ATTEMPTS:
                    raise
                if self.api_keys:
                    self.chosen_api_key = random.choice(self.api_keys)
                logger.warning(
                    "Responses API 请求失败，正在重试 (%s/%s)",
                    attempt + 1,
                    self._MAX_RECOVERY_ATTEMPTS,
                )
        raise RuntimeError("Responses recovery loop exited unexpectedly")

    async def text_chat_stream(
        self,
        prompt=None,
        session_id=None,
        image_urls=None,
        audio_urls=None,
        func_tool=None,
        contexts=None,
        system_prompt=None,
        tool_calls_result=None,
        model=None,
        extra_user_content_parts=None,
        tool_choice: Literal["auto", "required"] = "auto",
        output_contract: OutputContract | None = None,
        compiled_output_contract: CompiledOutputContract | None = None,
        **kwargs,
    ) -> AsyncGenerator[LLMResponse, None]:
        payload, _ = await self._prepare_response_payload(
            prompt,
            image_urls,
            audio_urls,
            contexts,
            system_prompt,
            tool_calls_result,
            model,
            extra_user_content_parts,
            **kwargs,
        )
        self.ensure_output_contract_supported(
            output_contract=output_contract,
            compiled_output_contract=compiled_output_contract,
        )
        func_tool, tool_choice = self._resolve_output_contract(
            output_contract, compiled_output_contract, func_tool, tool_choice
        )
        async for response in self._query_stream(payload, func_tool, tool_choice):
            yield response
