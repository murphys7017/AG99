from __future__ import annotations

import json

from ..analyzer_prompt import render_prompt_template
from .base import (
    BaseMemoryAnalyzer,
    MemoryAnalyzerExecutionError,
    MemoryAnalyzerRequest,
    MemoryAnalyzerResult,
)

OUTPUT_SCHEMA_CONTRACTS: dict[str, str] = {
    "TopicStateResult": (
        'Return exactly one JSON object with keys: "current_topic", '
        '"topic_summary", "topic_confidence". '
        '"current_topic" and "topic_summary" must be non-empty strings. '
        '"topic_confidence" must be a number between 0 and 1.'
    ),
    "ShortTermFocusResult": (
        'Return exactly one JSON object with key: "active_focus". '
        '"active_focus" must be a non-empty string.'
    ),
    "ShortTermSummaryResult": (
        'Return exactly one JSON object with key: "short_summary". '
        '"short_summary" must be a non-empty string.'
    ),
    "SessionInsightResult": (
        'Return exactly one JSON object with keys: "topic_summary", '
        '"progress_summary", "summary_text". All values must be non-empty strings.'
    ),
    "ExperienceExtractResult": (
        'Return exactly one JSON object with key: "experiences". '
        '"experiences" must be a list of objects. Each object must include '
        '"category", "summary", "detail_summary", "importance", and "confidence".'
    ),
    "LongTermPromoteResult": (
        'Return exactly one JSON object with key: "actions". '
        '"actions" must be a list of objects. Each object must include '
        '"action", "target_memory_id", "category", "reason", and "experience_ids".'
    ),
    "LongTermComposeResult": (
        'Return exactly one JSON object with keys: "title", "summary", '
        '"detail_summary", "tags", "importance", "confidence", and "status".'
    ),
}


class PromptJsonMemoryAnalyzer(BaseMemoryAnalyzer):
    kind = "prompt_json"

    async def analyze(self, request: MemoryAnalyzerRequest) -> MemoryAnalyzerResult:
        prompt_template = _append_output_schema_contract(
            request.prompt_template,
            request.output_schema,
        )
        prompt = render_prompt_template(prompt_template, request.payload)
        llm_response = await request.provider.text_chat(
            prompt=prompt,
            temperature=request.temperature,
            extra_body=request.extra_body,
        )
        raw_text = (llm_response.completion_text or "").strip()
        if not raw_text:
            raise MemoryAnalyzerExecutionError(
                f"analyzer `{request.analyzer_name}` returned empty completion text"
            )

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise MemoryAnalyzerExecutionError(
                f"analyzer `{request.analyzer_name}` returned non-json output"
            ) from exc

        if not isinstance(data, dict):
            raise MemoryAnalyzerExecutionError(
                f"analyzer `{request.analyzer_name}` returned non-object json"
            )

        return MemoryAnalyzerResult(
            analyzer_name=request.analyzer_name,
            stage=request.stage,
            data=data,
            raw_text=raw_text,
            provider_id=request.provider_id,
            model=None,
        )


def _append_output_schema_contract(template: str, output_schema: str) -> str:
    schema_name = output_schema.strip()
    if not schema_name:
        return template

    contract = OUTPUT_SCHEMA_CONTRACTS.get(schema_name)
    if contract is None:
        return template

    return (
        f"{template.rstrip()}\n\n"
        "Output schema contract:\n"
        f"- Schema: {schema_name}\n"
        f"- {contract}\n"
        "- Do not omit required keys. Do not rename keys. Do not return null or empty strings for required fields.\n"
    )
