from __future__ import annotations

from pathlib import Path

import pytest

from astrbot.core.memory.analyzer import (
    MemoryAnalyzerConfigurationError,
    MemoryAnalyzerManager,
    MemoryAnalyzerPromptError,
    MemoryAnalyzerProviderError,
    render_prompt_template,
)
from astrbot.core.memory.config import load_memory_config
from astrbot.core.provider.entities import LLMResponse
from astrbot.core.provider.provider import Provider


class DummyProvider(Provider):
    def __init__(self) -> None:
        super().__init__({"id": "memory-lite", "type": "openai"}, {})
        self.last_prompt = ""
        self.last_model = None
        self.last_temperature = None

    def get_current_key(self) -> str:
        return ""

    def set_key(self, key: str) -> None:
        del key

    async def get_models(self) -> list[str]:
        return ["dummy-model"]

    async def text_chat(
        self,
        prompt: str | None = None,
        session_id: str | None = None,
        image_urls: list[str] | None = None,
        func_tool=None,
        contexts=None,
        system_prompt: str | None = None,
        tool_calls_result=None,
        model: str | None = None,
        extra_user_content_parts=None,
        tool_choice: str = "auto",
        **kwargs,
    ) -> LLMResponse:
        del (
            session_id,
            image_urls,
            func_tool,
            contexts,
            system_prompt,
            tool_calls_result,
            extra_user_content_parts,
            tool_choice,
        )
        self.last_prompt = prompt or ""
        self.last_model = model
        self.last_temperature = kwargs.get("temperature")
        return LLMResponse(
            role="assistant",
            completion_text='{"emotion":"calm","topic":"memory"}',
        )


class DummyProviderManager:
    def __init__(self, provider: Provider | None) -> None:
        self.provider = provider

    async def get_provider_by_id(self, provider_id: str):
        if self.provider and self.provider.provider_config.get("id") == provider_id:
            return self.provider
        return None


def test_render_prompt_template_preserves_json_braces():
    rendered = render_prompt_template(
        'Output JSON only: {"emotion":"..."}\nInput: {text}',
        {"text": "hello"},
    )

    assert '{"emotion":"..."}' in rendered
    assert "Input: hello" in rendered


@pytest.mark.asyncio
async def test_memory_analyzer_manager_injects_configured_output_schema_contract(
    temp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ASTRBOT_ROOT", str(temp_dir / "astrbot-root"))
    config_path = temp_dir / "memory-config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "analysis:",
                "  enabled: true",
                '  prompts_root: "custom/prompts"',
                "  analyzers:",
                "    focus_v1:",
                '      implementation: "prompt_json"',
                '      provider_id: "memory-lite"',
                '      model: "dummy-model"',
                '      prompt_file: "focus_v1.md"',
                '      output_schema: "ShortTermFocusResult"',
                "  stages:",
                "    short_term_update:",
                '      analyzers: ["focus_v1"]',
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    prompt_path = config.analysis.prompts_root / "focus_v1.md"
    prompt_path.write_text("Analyze focus: {text}", encoding="utf-8")

    provider = DummyProvider()
    manager = MemoryAnalyzerManager(config.analysis)
    manager.bind_provider_manager(DummyProviderManager(provider))

    await manager.dispatch_stage(
        "short_term_update",
        payload={"text": "keep debugging memory"},
    )

    assert "Output schema contract:" in provider.last_prompt
    assert "Schema: ShortTermFocusResult" in provider.last_prompt
    assert '"active_focus" must be a non-empty string' in provider.last_prompt
    assert "Do not omit required keys" in provider.last_prompt


@pytest.mark.asyncio
async def test_memory_analyzer_manager_dispatches_stage_with_configured_prompt(
    temp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ASTRBOT_ROOT", str(temp_dir / "astrbot-root"))
    config_path = temp_dir / "memory-config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "analysis:",
                "  enabled: true",
                "  strict: true",
                '  prompts_root: "custom/prompts"',
                "  analyzers:",
                "    emotion_v1:",
                '      implementation: "prompt_json"',
                '      provider_id: "memory-lite"',
                '      model: "dummy-model"',
                '      prompt_file: "emotion_v1.md"',
                '      output_schema: "EmotionResult"',
                "      timeout_seconds: 11",
                "      temperature: 0.3",
                "  stages:",
                "    short_term_update:",
                '      analyzers: ["emotion_v1"]',
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    prompt_path = config.analysis.prompts_root / "emotion_v1.md"
    prompt_path.write_text("Analyze text: {text}", encoding="utf-8")

    provider = DummyProvider()
    manager = MemoryAnalyzerManager(config.analysis)
    manager.bind_provider_manager(DummyProviderManager(provider))

    results = await manager.dispatch_stage(
        "short_term_update",
        payload={"text": "how are you"},
        umo="test:private:user",
        conversation_id="conv-1",
    )

    assert list(results) == ["emotion_v1"]
    assert results["emotion_v1"].data["emotion"] == "calm"
    assert provider.last_prompt == "Analyze text: how are you"
    assert provider.last_model == "dummy-model"
    assert provider.last_temperature == 0.3


@pytest.mark.asyncio
async def test_memory_analyzer_manager_requires_prompt_file(
    temp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ASTRBOT_ROOT", str(temp_dir / "astrbot-root"))
    config_path = temp_dir / "memory-config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "analysis:",
                "  enabled: true",
                '  prompts_root: "custom/prompts"',
                "  analyzers:",
                "    emotion_v1:",
                '      implementation: "prompt_json"',
                '      provider_id: "memory-lite"',
                '      model: "dummy-model"',
                '      prompt_file: "missing.md"',
                "  stages:",
                "    short_term_update:",
                '      analyzers: ["emotion_v1"]',
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    manager = MemoryAnalyzerManager(config.analysis)
    manager.bind_provider_manager(DummyProviderManager(DummyProvider()))

    with pytest.raises(MemoryAnalyzerPromptError):
        await manager.dispatch_stage("short_term_update", payload={"text": "hello"})


@pytest.mark.asyncio
async def test_memory_analyzer_manager_requires_provider_id_configuration(
    temp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ASTRBOT_ROOT", str(temp_dir / "astrbot-root"))
    config_path = temp_dir / "memory-config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "analysis:",
                "  enabled: true",
                '  prompts_root: "custom/prompts"',
                "  analyzers:",
                "    emotion_v1:",
                '      implementation: "prompt_json"',
                '      prompt_file: "emotion_v1.md"',
                "  stages:",
                "    short_term_update:",
                '      analyzers: ["emotion_v1"]',
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    prompt_path = config.analysis.prompts_root / "emotion_v1.md"
    prompt_path.write_text("Analyze text: {text}", encoding="utf-8")
    manager = MemoryAnalyzerManager(config.analysis)

    with pytest.raises(
        MemoryAnalyzerConfigurationError,
        match="has no provider_id configured",
    ):
        await manager.dispatch_stage("short_term_update", payload={"text": "hello"})


@pytest.mark.asyncio
async def test_memory_analyzer_manager_requires_model_configuration(
    temp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ASTRBOT_ROOT", str(temp_dir / "astrbot-root"))
    config_path = temp_dir / "memory-config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "analysis:",
                "  enabled: true",
                '  prompts_root: "custom/prompts"',
                "  analyzers:",
                "    emotion_v1:",
                '      implementation: "prompt_json"',
                '      provider_id: "memory-lite"',
                '      prompt_file: "emotion_v1.md"',
                "  stages:",
                "    short_term_update:",
                '      analyzers: ["emotion_v1"]',
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    prompt_path = config.analysis.prompts_root / "emotion_v1.md"
    prompt_path.write_text("Analyze text: {text}", encoding="utf-8")
    manager = MemoryAnalyzerManager(config.analysis)
    manager.bind_provider_manager(DummyProviderManager(DummyProvider()))

    with pytest.raises(
        MemoryAnalyzerConfigurationError,
        match="has no model configured",
    ):
        await manager.dispatch_stage("short_term_update", payload={"text": "hello"})


@pytest.mark.asyncio
async def test_memory_analyzer_manager_raises_when_provider_not_found(
    temp_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ASTRBOT_ROOT", str(temp_dir / "astrbot-root"))
    config_path = temp_dir / "memory-config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "analysis:",
                "  enabled: true",
                '  prompts_root: "custom/prompts"',
                "  analyzers:",
                "    emotion_v1:",
                '      implementation: "prompt_json"',
                '      provider_id: "memory-lite"',
                '      model: "dummy-model"',
                '      prompt_file: "emotion_v1.md"',
                "  stages:",
                "    short_term_update:",
                '      analyzers: ["emotion_v1"]',
            ]
        ),
        encoding="utf-8",
    )
    config = load_memory_config(config_path)
    prompt_path = config.analysis.prompts_root / "emotion_v1.md"
    prompt_path.write_text("Analyze text: {text}", encoding="utf-8")
    manager = MemoryAnalyzerManager(config.analysis)
    manager.bind_provider_manager(DummyProviderManager(None))

    with pytest.raises(MemoryAnalyzerProviderError, match="was not found"):
        await manager.dispatch_stage("short_term_update", payload={"text": "hello"})
