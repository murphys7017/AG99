from __future__ import annotations

from pathlib import Path
from typing import Any

from astrbot.core import logger
from astrbot.core.provider.provider import Provider

from .analyzers.base import (
    BaseMemoryAnalyzer,
    MemoryAnalyzerConfigurationError,
    MemoryAnalyzerPromptError,
    MemoryAnalyzerProviderError,
    MemoryAnalyzerRequest,
    MemoryAnalyzerResult,
)
from .analyzers.prompt_json import PromptJsonMemoryAnalyzer
from .config import MemoryAnalysisConfig, MemoryAnalyzerConfig, get_memory_config


class MemoryAnalyzerManager:
    def __init__(
        self,
        analysis_config: MemoryAnalysisConfig | None = None,
    ) -> None:
        self.analysis_config = analysis_config or get_memory_config().analysis
        self.provider_manager = None
        self._implementations: dict[str, BaseMemoryAnalyzer] = {}
        self.register(PromptJsonMemoryAnalyzer())

    def register(self, analyzer: BaseMemoryAnalyzer) -> None:
        self._implementations[analyzer.kind] = analyzer

    def bind_provider_manager(self, provider_manager) -> None:
        self.provider_manager = provider_manager

    def get_stage_analyzers(self, stage: str) -> list[str]:
        stage_config = self.analysis_config.stages.get(stage)
        if stage_config is None:
            raise MemoryAnalyzerConfigurationError(
                f"memory analysis stage `{stage}` is not configured"
            )
        analyzers = list(stage_config.analyzers)
        if not analyzers:
            raise MemoryAnalyzerConfigurationError(
                f"memory analysis stage `{stage}` has no analyzers configured"
            )
        return analyzers

    async def dispatch_stage(
        self,
        stage: str,
        *,
        payload: dict[str, Any],
        umo: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, MemoryAnalyzerResult]:
        analyzer_names = self.get_stage_analyzers(stage)
        logger.info(
            "memory analyzer stage dispatch started: stage=%s umo=%s conversation_id=%s analyzers=%s strict=%s",
            stage,
            umo,
            conversation_id,
            analyzer_names,
            self.analysis_config.strict,
        )
        results: dict[str, MemoryAnalyzerResult] = {}
        for analyzer_name in analyzer_names:
            results[analyzer_name] = await self.run_analyzer(
                analyzer_name,
                payload=payload,
                stage=stage,
                umo=umo,
                conversation_id=conversation_id,
            )
        logger.info(
            "memory analyzer stage dispatch finished: stage=%s analyzers=%s",
            stage,
            list(results),
        )
        return results

    async def run_analyzer(
        self,
        analyzer_name: str,
        *,
        payload: dict[str, Any],
        stage: str | None = None,
        umo: str | None = None,
        conversation_id: str | None = None,
    ) -> MemoryAnalyzerResult:
        analyzer_config = self._get_analyzer_config(analyzer_name)
        implementation = self._implementations.get(analyzer_config.implementation)
        if implementation is None:
            raise MemoryAnalyzerConfigurationError(
                f"memory analyzer implementation `{analyzer_config.implementation}` "
                f"is not registered for `{analyzer_name}`"
            )

        provider = await self._resolve_provider(analyzer_name, analyzer_config)
        prompt_path = self._resolve_prompt_path(analyzer_name, analyzer_config)
        prompt_template = prompt_path.read_text(encoding="utf-8")
        request = MemoryAnalyzerRequest(
            analyzer_name=analyzer_name,
            stage=stage,
            payload=dict(payload),
            prompt_template=prompt_template,
            prompt_path=prompt_path,
            provider=provider,
            provider_id=analyzer_config.provider_id,
            model=None,
            output_schema=analyzer_config.output_schema,
            timeout_seconds=analyzer_config.timeout_seconds,
            temperature=analyzer_config.temperature,
            extra_body=analyzer_config.extra_body,
            umo=umo,
            conversation_id=conversation_id,
        )
        logger.info(
            "memory analyzer execution started: analyzer=%s implementation=%s provider_id=%s stage=%s",
            analyzer_name,
            analyzer_config.implementation,
            analyzer_config.provider_id,
            stage,
        )
        result = await implementation.analyze(request)
        logger.info(
            "memory analyzer execution finished: analyzer=%s keys=%s provider_id=%s",
            analyzer_name,
            sorted(result.data),
            result.provider_id,
        )
        return result

    def _get_analyzer_config(self, analyzer_name: str) -> MemoryAnalyzerConfig:
        if not self.analysis_config.enabled:
            raise MemoryAnalyzerConfigurationError("memory analysis is disabled")

        analyzer_config = self.analysis_config.analyzers.get(analyzer_name)
        if analyzer_config is None:
            raise MemoryAnalyzerConfigurationError(
                f"memory analyzer `{analyzer_name}` is not configured"
            )
        if not analyzer_config.enabled:
            raise MemoryAnalyzerConfigurationError(
                f"memory analyzer `{analyzer_name}` is disabled"
            )
        if not analyzer_config.provider_id:
            raise MemoryAnalyzerConfigurationError(
                f"memory analyzer `{analyzer_name}` has no provider_id configured"
            )
        if not analyzer_config.prompt_file:
            raise MemoryAnalyzerConfigurationError(
                f"memory analyzer `{analyzer_name}` has no prompt_file configured"
            )
        return analyzer_config

    async def _resolve_provider(
        self,
        analyzer_name: str,
        analyzer_config: MemoryAnalyzerConfig,
    ) -> Provider:
        if self.provider_manager is None:
            raise MemoryAnalyzerProviderError(
                "memory analyzer manager is not bound to ProviderManager"
            )

        provider = await self.provider_manager.get_provider_by_id(
            analyzer_config.provider_id
        )
        if provider is None:
            raise MemoryAnalyzerProviderError(
                f"memory analyzer `{analyzer_name}` provider "
                f"`{analyzer_config.provider_id}` was not found"
            )
        if not isinstance(provider, Provider):
            raise MemoryAnalyzerProviderError(
                f"memory analyzer `{analyzer_name}` provider "
                f"`{analyzer_config.provider_id}` is not a chat provider"
            )
        return provider

    def _resolve_prompt_path(
        self,
        analyzer_name: str,
        analyzer_config: MemoryAnalyzerConfig,
    ) -> Path:
        prompt_path = self.analysis_config.prompts_root / analyzer_config.prompt_file
        if not prompt_path.exists():
            raise MemoryAnalyzerPromptError(
                f"memory analyzer `{analyzer_name}` prompt file does not exist: "
                f"{prompt_path}"
            )
        return prompt_path
