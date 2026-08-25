from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from openai.types.chat.chat_completion import ChatCompletion

import astrbot.core.message.components as Comp
from astrbot import logger
from astrbot.core.agent.tool import ToolSet
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.provider.entities import LLMResponse
from astrbot.core.utils.network_utils import (
    create_proxy_client,
    is_connection_error,
    log_connection_failure,
)

from ..register import register_provider_adapter
from .openai_source import ProviderOpenAIOfficial

_OLLAMA_OPTION_ALIASES = {
    "max_tokens": "num_predict",
    "temperature": "temperature",
    "top_p": "top_p",
    "stop": "stop",
}
_OLLAMA_PROTECTED_FIELDS = {"model", "messages", "tools", "tool_choice", "stream"}


def _normalize_ollama_api_base(api_base: str) -> str:
    normalized = (api_base or "http://127.0.0.1:11434").rstrip("/")
    for suffix in ("/api/chat", "/v1"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
    return normalized


@register_provider_adapter(
    "ollama_chat_completion",
    "Ollama 原生 Chat 提供商适配器",
    prompt_renderer_family="openai",
)
class ProviderOllamaNative(ProviderOpenAIOfficial):
    """Use Ollama's native API while preserving AstrBot's OpenAI agent contract."""

    def __init__(self, provider_config: dict, provider_settings: dict) -> None:
        normalized_config = dict(provider_config)
        configured_keys = normalized_config.get("key")
        if not isinstance(configured_keys, list) or not any(configured_keys):
            normalized_config["key"] = ["ollama"]
        super().__init__(normalized_config, provider_settings)
        self.ollama_api_base = _normalize_ollama_api_base(
            str(provider_config.get("api_base", ""))
        )
        configured_context_tokens = provider_config.get("max_context_tokens", 0)
        try:
            configured_context_tokens = int(configured_context_tokens)
        except (TypeError, ValueError):
            configured_context_tokens = 0
        self.configured_context_tokens = max(0, configured_context_tokens)
        self.ollama_client = create_proxy_client(
            "Ollama",
            normalized_config.get("proxy", ""),
            headers=self.custom_headers,
        )

    @staticmethod
    def _raise_for_ollama_error(response) -> None:
        if response.is_success:
            return
        try:
            body = response.json()
        except (TypeError, ValueError):
            body = None
        detail = body.get("error") if isinstance(body, dict) else None
        if not detail:
            detail = response.text.strip() or response.reason_phrase
        raise RuntimeError(
            f"Ollama API request failed: HTTP {response.status_code}: {detail}"
        )

    async def _handle_api_error(self, error, state):
        if is_connection_error(error):
            proxy = self.provider_config.get("proxy", "")
            log_connection_failure("Ollama", error, proxy)
            if state.transient_retry_count >= 1:
                raise error
            state.transient_retry_count += 1
            state.last_recovery_reason = "transient_network_error"
            return state
        return await super()._handle_api_error(error, state)

    async def get_models(self) -> list[str]:
        response = await self.ollama_client.get(
            f"{self.ollama_api_base}/api/tags",
            timeout=self.timeout,
        )
        self._raise_for_ollama_error(response)
        models = response.json().get("models", [])
        return sorted(
            str(model.get("name"))
            for model in models
            if isinstance(model, dict) and model.get("name")
        )

    def _build_ollama_payload(
        self,
        payloads: dict[str, Any],
        tools: ToolSet | None,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        request = {
            "model": payloads.get("model") or self.get_model(),
            "messages": self._to_ollama_messages(payloads.get("messages", [])),
            "stream": stream,
        }
        if tools and not tools.empty():
            request["tools"] = tools.get_func_desc_openai_style()
            request["tool_choice"] = payloads.get("tool_choice", "auto")

        custom_body = self.provider_config.get("custom_extra_body", {})
        custom_body = dict(custom_body) if isinstance(custom_body, dict) else {}
        options = custom_body.pop("options", {})
        options = dict(options) if isinstance(options, dict) else {}

        for source_key, option_key in _OLLAMA_OPTION_ALIASES.items():
            if source_key in custom_body:
                options[option_key] = custom_body.pop(source_key)
            if source_key in payloads:
                options[option_key] = payloads[source_key]

        if self.configured_context_tokens > 0:
            configured_num_ctx = options.get("num_ctx")
            if configured_num_ctx not in {None, self.configured_context_tokens}:
                logger.warning(
                    "Ollama options.num_ctx=%s conflicts with max_context_tokens=%s; "
                    "using max_context_tokens.",
                    configured_num_ctx,
                    self.configured_context_tokens,
                )
            options["num_ctx"] = self.configured_context_tokens

        if options:
            request["options"] = options

        for key, value in custom_body.items():
            if key in _OLLAMA_PROTECTED_FIELDS:
                logger.warning("Ignoring protected Ollama request field: %s", key)
                continue
            request[key] = value

        if self._ollama_disable_thinking_enabled():
            request["think"] = False
        self._drop_provider_only_request_keys(request)
        return request

    @classmethod
    def _to_ollama_messages(cls, messages: list[dict]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        tool_names_by_id: dict[str, str] = {}
        for source in messages:
            if not isinstance(source, dict):
                continue
            message: dict[str, Any] = {
                "role": source.get("role", "user"),
                "content": "",
            }
            content = source.get("content")
            if isinstance(content, str):
                message["content"] = content
            elif isinstance(content, list):
                text_parts: list[str] = []
                images: list[str] = []
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text":
                        text_parts.append(str(part.get("text", "")))
                    elif part.get("type") == "image_url":
                        image_ref = part.get("image_url", {})
                        image_ref = (
                            image_ref.get("url", "")
                            if isinstance(image_ref, dict)
                            else image_ref
                        )
                        if isinstance(image_ref, str) and image_ref:
                            images.append(image_ref.split(",", 1)[-1])
                    elif part.get("type") in {"input_audio", "audio_url"}:
                        raise ValueError(
                            "Ollama native chat does not support AstrBot audio input"
                        )
                message["content"] = "".join(text_parts)
                if images:
                    message["images"] = images
            elif content is not None:
                message["content"] = str(content)

            if reasoning := source.get("reasoning_content"):
                message["thinking"] = str(reasoning)
            if tool_calls := source.get("tool_calls"):
                converted_tool_calls = cls._to_ollama_tool_calls(tool_calls)
                message["tool_calls"] = converted_tool_calls
                for tool_call in converted_tool_calls:
                    tool_names_by_id[tool_call["id"]] = tool_call["function"]["name"]
            tool_name = source.get("name")
            if not tool_name and source.get("role") == "tool":
                tool_name = tool_names_by_id.get(str(source.get("tool_call_id", "")))
            if tool_name:
                message["tool_name"] = tool_name
            converted.append(message)
        return converted

    @staticmethod
    def _to_ollama_tool_calls(tool_calls: list) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function", {})
            if not isinstance(function, dict):
                continue
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}
            converted.append(
                {
                    "id": tool_call.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": function.get("name", ""),
                        "arguments": arguments if isinstance(arguments, dict) else {},
                    },
                }
            )
        return converted

    @staticmethod
    def _native_tool_calls_to_openai(tool_calls: Any) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        if not isinstance(tool_calls, list):
            return converted
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function", {})
            if not isinstance(function, dict):
                continue
            arguments = function.get("arguments", {})
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments or {}, ensure_ascii=False)
            converted.append(
                {
                    "id": tool_call.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": function.get("name", ""),
                        "arguments": arguments,
                    },
                }
            )
        return converted

    @classmethod
    def _to_openai_completion(cls, response: dict[str, Any]) -> ChatCompletion:
        message = response.get("message", {})
        if not isinstance(message, dict):
            message = {}
        tool_calls = cls._native_tool_calls_to_openai(message.get("tool_calls"))
        finish_reason = response.get("done_reason") or "stop"
        if tool_calls and finish_reason == "stop":
            finish_reason = "tool_calls"
        completion_message: dict[str, Any] = {
            "role": "assistant",
            "content": message.get("content", ""),
        }
        if thinking := message.get("thinking"):
            completion_message["reasoning_content"] = thinking
        if tool_calls:
            completion_message["tool_calls"] = tool_calls
        return ChatCompletion.model_validate(
            {
                "id": response.get("id") or f"ollama-{uuid.uuid4().hex}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": response.get("model", ""),
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": finish_reason,
                        "message": completion_message,
                    }
                ],
                "usage": {
                    "prompt_tokens": response.get("prompt_eval_count", 0) or 0,
                    "completion_tokens": response.get("eval_count", 0) or 0,
                    "total_tokens": (response.get("prompt_eval_count", 0) or 0)
                    + (response.get("eval_count", 0) or 0),
                },
            }
        )

    async def _query(self, payloads: dict, tools: ToolSet | None) -> LLMResponse:
        request = self._build_ollama_payload(payloads, tools, stream=False)
        response = await self.ollama_client.post(
            f"{self.ollama_api_base}/api/chat",
            json=request,
            timeout=self.timeout,
        )
        self._raise_for_ollama_error(response)
        completion = self._to_openai_completion(response.json())
        return await self._parse_openai_completion(completion, tools)

    async def _query_stream(
        self,
        payloads: dict,
        tools: ToolSet | None,
    ) -> AsyncGenerator[LLMResponse, None]:
        request = self._build_ollama_payload(payloads, tools, stream=True)
        aggregate: dict[str, Any] = {
            "model": request["model"],
            "message": {"role": "assistant", "content": "", "thinking": ""},
        }
        async with self.ollama_client.stream(
            "POST",
            f"{self.ollama_api_base}/api/chat",
            json=request,
            timeout=self.timeout,
        ) as response:
            if not response.is_success:
                await response.aread()
            self._raise_for_ollama_error(response)
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                message = chunk.get("message", {})
                if not isinstance(message, dict):
                    message = {}
                content = str(message.get("content", "") or "")
                thinking = str(message.get("thinking", "") or "")
                aggregate_message = aggregate["message"]
                aggregate_message["content"] += content
                aggregate_message["thinking"] += thinking
                if message.get("tool_calls"):
                    aggregate_message["tool_calls"] = message["tool_calls"]
                if chunk.get("done"):
                    aggregate.update(chunk)
                    aggregate["message"] = aggregate_message
                if content or thinking:
                    chunk_response = LLMResponse("assistant", is_chunk=True)
                    if content:
                        chunk_response.result_chain = MessageChain(
                            chain=[Comp.Plain(content)]
                        )
                    if thinking:
                        chunk_response.reasoning_content = thinking
                    yield chunk_response

        completion = self._to_openai_completion(aggregate)
        yield await self._parse_openai_completion(completion, tools)

    async def terminate(self) -> None:
        await self.ollama_client.aclose()
        await super().terminate()
