from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from astrbot.core.db.po import Conversation
from astrbot.core.memory.analyzer import MemoryAnalyzerResult
from astrbot.core.memory.config import MemoryAnalysisConfig, load_memory_config
from astrbot.core.memory.document_search import DocumentSearchService
from astrbot.core.memory.history_source import RecentConversationSource
from astrbot.core.memory.identity import (
    MemoryIdentityMappingService,
    MemoryIdentityResolver,
    build_platform_user_key,
)
from astrbot.core.memory.postprocessor import MemoryPostProcessor
from astrbot.core.memory.service import MemoryService
from astrbot.core.memory.short_term_service import ShortTermMemoryService
from astrbot.core.memory.snapshot_builder import MemorySnapshotBuilder
from astrbot.core.memory.store import MemoryStore
from astrbot.core.memory.turn_record_service import TurnRecordService
from astrbot.core.memory.types import (
    DocumentSearchRequest,
    MemoryIdentity,
    MemoryUpdateRequest,
)
from astrbot.core.provider.entities import ProviderRequest

TEST_UMO = "test:private:user"
TEST_PLATFORM_ID = "test"
TEST_CANONICAL_USER_ID = "canonical-user-1"


class _StubShortTermAnalyzerManager:
    async def dispatch_stage(
        self,
        stage: str,
        *,
        payload: dict[str, object],
        umo: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, MemoryAnalyzerResult]:
        del payload, umo, conversation_id
        return {
            "topic_v1": MemoryAnalyzerResult(
                analyzer_name="topic_v1",
                stage=stage,
                data={
                    "current_topic": "Identity topic",
                    "topic_summary": "Identity topic summary",
                    "topic_confidence": 0.9,
                },
                raw_text="{}",
                provider_id="memory-lite",
                model="dummy-model",
            ),
            "focus_v1": MemoryAnalyzerResult(
                analyzer_name="focus_v1",
                stage=stage,
                data={"active_focus": "Identity focus"},
                raw_text="{}",
                provider_id="memory-lite",
                model="dummy-model",
            ),
            "summary_v1": MemoryAnalyzerResult(
                analyzer_name="summary_v1",
                stage=stage,
                data={"short_summary": "Identity summary"},
                raw_text="{}",
                provider_id="memory-lite",
                model="dummy-model",
            ),
        }


def _build_short_term_service(
    store: MemoryStore, history_source
) -> ShortTermMemoryService:
    return ShortTermMemoryService(
        store,
        history_source,
        analyzer_manager=_StubShortTermAnalyzerManager(),  # type: ignore[arg-type]
        analysis_config=MemoryAnalysisConfig(enabled=True, strict=True),
    )


def _write_memory_config(temp_dir: Path) -> tuple[object, Path]:
    config_path = temp_dir / "memory-config.yaml"
    mappings_path = temp_dir / "identity_mappings.yaml"
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "identity:",
                "  enabled: true",
                f'  mappings_path: "{mappings_path.as_posix()}"',
                "storage:",
                f'  sqlite_path: "{(temp_dir / "memory.db").as_posix()}"',
                f'  docs_root: "{(temp_dir / "long_term").as_posix()}"',
                f'  projections_root: "{(temp_dir / "projections").as_posix()}"',
            ]
        ),
        encoding="utf-8",
    )
    return load_memory_config(config_path), mappings_path


def _write_identity_yaml(mappings_path: Path, payload: dict) -> None:
    mappings_path.write_text(
        yaml.safe_dump(payload, allow_unicode=False, sort_keys=False),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_identity_mappings_reload_syncs_yaml_to_sqlite(temp_dir: Path):
    config, mappings_path = _write_memory_config(temp_dir)
    _write_identity_yaml(
        mappings_path,
        {
            "bindings": [
                {
                    "platform_id": "qq",
                    "sender_user_id": "10001",
                    "canonical_user_id": "aki",
                },
                {
                    "platform_id": "discord",
                    "sender_user_id": "aki-user",
                    "canonical_user_id": "aki",
                    "nickname_hint": "aki",
                },
            ]
        },
    )
    store = MemoryStore(config=config)
    mapping_service = MemoryIdentityMappingService(store, config=config)

    try:
        count = await mapping_service.reload_from_yaml()
        bindings = await store.list_all_identity_mappings()

        assert count == 2
        assert [item.platform_user_key for item in bindings] == [
            "discord:aki-user",
            "qq:10001",
        ]

        _write_identity_yaml(
            mappings_path,
            {
                "bindings": [
                    {
                        "platform_id": "qq",
                        "sender_user_id": "10001",
                        "canonical_user_id": "aki-updated",
                    }
                ]
            },
        )
        reloaded = await mapping_service.reload_from_yaml()
        reloaded_bindings = await store.list_all_identity_mappings()
    finally:
        await store.close()

    assert reloaded == 1
    assert len(reloaded_bindings) == 1
    assert reloaded_bindings[0].platform_user_key == "qq:10001"
    assert reloaded_bindings[0].canonical_user_id == "aki-updated"


@pytest.mark.asyncio
async def test_identity_mappings_reload_uses_config_bindings_before_yaml(
    temp_dir: Path,
):
    config, mappings_path = _write_memory_config(temp_dir)
    config.identity.bindings = [
        {
            "platform_id": "qq",
            "sender_user_id": "10001",
            "canonical_user_id": "aki-from-config",
            "nickname_hint": "aki",
        }
    ]
    _write_identity_yaml(
        mappings_path,
        {
            "bindings": [
                {
                    "platform_id": "discord",
                    "sender_user_id": "aki-user",
                    "canonical_user_id": "aki-from-yaml",
                }
            ]
        },
    )
    store = MemoryStore(config=config)
    mapping_service = MemoryIdentityMappingService(store, config=config)

    try:
        count = await mapping_service.reload_from_yaml()
        bindings = await store.list_all_identity_mappings()
    finally:
        await store.close()

    assert count == 1
    assert len(bindings) == 1
    assert bindings[0].platform_user_key == "qq:10001"
    assert bindings[0].canonical_user_id == "aki-from-config"
    assert bindings[0].nickname_hint == "aki"


def test_identity_mappings_validate_rejects_duplicate_platform_user_key(temp_dir: Path):
    config, mappings_path = _write_memory_config(temp_dir)
    _write_identity_yaml(
        mappings_path,
        {
            "bindings": [
                {
                    "platform_id": "qq",
                    "sender_user_id": "10001",
                    "canonical_user_id": "aki",
                },
                {
                    "platform_id": "qq",
                    "sender_user_id": "10001",
                    "canonical_user_id": "aki-duplicate",
                },
            ]
        },
    )
    store = MemoryStore(config=config)
    mapping_service = MemoryIdentityMappingService(store, config=config)

    with pytest.raises(ValueError, match="duplicate memory identity mapping"):
        mapping_service.validate_yaml()


@pytest.mark.asyncio
async def test_identity_resolver_returns_partial_identity_when_sender_missing(
    temp_dir: Path,
):
    config, _ = _write_memory_config(temp_dir)
    store = MemoryStore(config=config)
    resolver = MemoryIdentityResolver(
        MemoryIdentityMappingService(store, config=config)
    )
    event = MagicMock()
    event.unified_msg_origin = TEST_UMO
    event.get_platform_id.return_value = TEST_PLATFORM_ID
    event.get_sender_id.return_value = None
    event.get_sender_name.return_value = "tester"

    try:
        identity = await resolver.resolve_from_event(event)
    finally:
        await store.close()

    assert identity.umo == TEST_UMO
    assert identity.platform_id == TEST_PLATFORM_ID
    assert identity.sender_user_id is None
    assert identity.platform_user_key is None
    assert identity.canonical_user_id is None
    assert identity.sender_nickname == "tester"


@pytest.mark.asyncio
async def test_memory_postprocessor_builds_request_without_platform_user_key():
    history = [
        {"role": "user", "content": "Need a short-term update."},
        {"role": "assistant", "content": "Short-term update is ready."},
    ]
    conversation = Conversation(
        platform_id="test",
        user_id=TEST_UMO,
        cid="conv-1",
        history=json.dumps(history),
    )
    event = MagicMock()
    event.unified_msg_origin = TEST_UMO
    event.session_id = "session-1"
    memory_service = MagicMock()
    memory_service.update_from_postprocess = AsyncMock()
    memory_service.identity_resolver = MagicMock()
    memory_service.identity_resolver.resolve_from_event = AsyncMock(
        return_value=MemoryIdentity(
            umo=TEST_UMO,
            platform_id=None,
            sender_user_id=None,
            sender_nickname="tester",
            platform_user_key=None,
            canonical_user_id=None,
        )
    )
    processor = MemoryPostProcessor(memory_service)
    ctx = MagicMock()
    ctx.event = event
    ctx.conversation = conversation
    ctx.provider_request = ProviderRequest(prompt="hello", session_id="session-1")
    ctx.timestamp = datetime.now(UTC)

    req = await processor.build_update_request(ctx)

    assert req is not None
    assert req.platform_id is None
    assert req.platform_user_key is None
    assert req.canonical_user_id is None
    assert req.user_message["content"] == "Need a short-term update."
    assert req.assistant_message["content"] == "Short-term update is ready."


class _CapturingVectorIndex:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.config = MagicMock()

    async def search_long_term_memories(
        self,
        canonical_user_id: str,
        query: str,
        top_k: int,
        metadata_filters: dict | None = None,
    ) -> list:
        self.calls.append(
            {
                "canonical_user_id": canonical_user_id,
                "query": query,
                "top_k": top_k,
                "metadata_filters": metadata_filters or {},
            }
        )
        return []


@pytest.mark.asyncio
async def test_document_search_does_not_infer_conversation_scope_filter():
    vector_index = _CapturingVectorIndex()
    store = MagicMock()
    service = DocumentSearchService(store, vector_index=vector_index)  # type: ignore[arg-type]

    results = await service.search_long_term_memories(
        DocumentSearchRequest(
            canonical_user_id=TEST_CANONICAL_USER_ID,
            query="memory roadmap",
            conversation_id="conv-1",
        )
    )

    assert results == []
    assert vector_index.calls[0]["metadata_filters"] == {}


@pytest.mark.asyncio
async def test_memory_service_reload_identity_mappings(temp_dir: Path):
    config, mappings_path = _write_memory_config(temp_dir)
    _write_identity_yaml(
        mappings_path,
        {
            "bindings": [
                {
                    "platform_id": "qq",
                    "sender_user_id": "10001",
                    "canonical_user_id": "aki",
                }
            ]
        },
    )
    store = MemoryStore(config=config)
    identity_mapping_service = MemoryIdentityMappingService(store, config=config)
    service = MemoryService(
        store,
        TurnRecordService(store),
        _build_short_term_service(store, MagicMock()),
        MemorySnapshotBuilder(store),
        identity_mapping_service=identity_mapping_service,
    )

    try:
        count = await service.reload_identity_mappings()
        bindings = await store.list_all_identity_mappings()
    finally:
        await store.close()

    assert count == 1
    assert len(bindings) == 1
    assert bindings[0].platform_user_key == "qq:10001"


@pytest.mark.asyncio
async def test_memory_service_short_term_survives_without_platform_user_key(
    temp_dir: Path,
):
    config, _ = _write_memory_config(temp_dir)
    store = MemoryStore(config=config)
    history_source = RecentConversationSource(store, recent_turns_window=8)
    service = MemoryService(
        store,
        TurnRecordService(store),
        _build_short_term_service(store, history_source),
        MemorySnapshotBuilder(store),
        identity_mapping_service=MemoryIdentityMappingService(store, config=config),
    )
    timestamp = datetime.now(UTC)
    history = [
        {"role": "user", "content": "Please keep this short-term only."},
        {"role": "assistant", "content": "I will keep it in short-term memory."},
    ]

    try:
        turn = await service.update_from_postprocess(
            MemoryUpdateRequest(
                umo=TEST_UMO,
                conversation_id="conv-1",
                platform_id=None,
                platform_user_key=None,
                canonical_user_id=None,
                session_id="session-1",
                provider_request={"conversation_history": history},
                user_message=history[0],
                assistant_message=history[1],
                message_timestamp=timestamp,
                source_refs=["conversation:conv-1"],
            )
        )
        topic_state = await store.get_topic_state(TEST_UMO, "conv-1")
        short_term_memory = await store.get_short_term_memory(TEST_UMO, "conv-1")
    finally:
        await store.close()

    assert turn.platform_user_key is None
    assert turn.canonical_user_id is None
    assert topic_state is not None
    assert short_term_memory is not None
    assert topic_state.current_topic == "Identity topic"
    assert short_term_memory.active_focus == "Identity focus"


@pytest.mark.asyncio
async def test_identity_mapping_service_bind_updates_yaml_and_store(temp_dir: Path):
    config, mappings_path = _write_memory_config(temp_dir)
    _write_identity_yaml(
        mappings_path,
        {
            "bindings": [
                {
                    "platform_id": "qq",
                    "sender_user_id": "10001",
                    "canonical_user_id": "aki",
                    "nickname_hint": "old-name",
                }
            ]
        },
    )
    store = MemoryStore(config=config)
    mapping_service = MemoryIdentityMappingService(store, config=config)

    try:
        binding = await mapping_service.bind_platform_user(
            "qq",
            "10001",
            "aki-updated",
            nickname_hint="new-name",
        )
        loaded = await store.get_identity_mapping("qq:10001")
        yaml_payload = yaml.safe_load(mappings_path.read_text(encoding="utf-8"))
    finally:
        await store.close()

    assert binding.platform_user_key == "qq:10001"
    assert binding.canonical_user_id == "aki-updated"
    assert loaded is not None
    assert loaded.nickname_hint == "new-name"
    assert yaml_payload["bindings"] == [
        {
            "platform_id": "qq",
            "sender_user_id": "10001",
            "canonical_user_id": "aki-updated",
            "nickname_hint": "new-name",
        }
    ]


@pytest.mark.asyncio
async def test_identity_mapping_service_unbind_updates_yaml_and_store(temp_dir: Path):
    config, mappings_path = _write_memory_config(temp_dir)
    _write_identity_yaml(
        mappings_path,
        {
            "bindings": [
                {
                    "platform_id": "qq",
                    "sender_user_id": "10001",
                    "canonical_user_id": "aki",
                }
            ]
        },
    )
    store = MemoryStore(config=config)
    mapping_service = MemoryIdentityMappingService(store, config=config)

    try:
        await mapping_service.reload_from_yaml()
        removed = await mapping_service.unbind_platform_user("qq:10001")
        removed_again = await mapping_service.unbind_platform_user("qq:10001")
        loaded = await store.get_identity_mapping("qq:10001")
        yaml_payload = yaml.safe_load(mappings_path.read_text(encoding="utf-8"))
    finally:
        await store.close()

    assert removed is True
    assert removed_again is False
    assert loaded is None
    assert yaml_payload == {"bindings": []}


@pytest.mark.asyncio
async def test_identity_mapping_service_lists_bindings_for_canonical_user(
    temp_dir: Path,
):
    config, mappings_path = _write_memory_config(temp_dir)
    _write_identity_yaml(
        mappings_path,
        {
            "bindings": [
                {
                    "platform_id": "discord",
                    "sender_user_id": "aki-user",
                    "canonical_user_id": "aki",
                },
                {
                    "platform_id": "qq",
                    "sender_user_id": "10001",
                    "canonical_user_id": "aki",
                },
                {
                    "platform_id": "telegram",
                    "sender_user_id": "other-user",
                    "canonical_user_id": "other",
                },
            ]
        },
    )
    store = MemoryStore(config=config)
    mapping_service = MemoryIdentityMappingService(store, config=config)

    try:
        await mapping_service.reload_from_yaml()
        bindings = await mapping_service.list_bindings_for_canonical_user("aki")
    finally:
        await store.close()

    assert [item.platform_user_key for item in bindings] == [
        "discord:aki-user",
        "qq:10001",
    ]


def test_identity_mappings_validate_rejects_non_list_payload(temp_dir: Path):
    config, mappings_path = _write_memory_config(temp_dir)
    _write_identity_yaml(mappings_path, {"bindings": "invalid"})
    store = MemoryStore(config=config)
    mapping_service = MemoryIdentityMappingService(store, config=config)

    with pytest.raises(ValueError, match="field `bindings` must be a list"):
        mapping_service.validate_yaml()


def test_identity_mapping_service_bind_rejects_yaml_write_for_config_bindings(
    temp_dir: Path,
):
    config, _ = _write_memory_config(temp_dir)
    config.identity.bindings = []
    store = MemoryStore(config=config)
    mapping_service = MemoryIdentityMappingService(store, config=config)

    with pytest.raises(RuntimeError, match="configured in Web config"):
        mapping_service.upsert_binding_in_yaml(
            "qq",
            "10001",
            "aki",
            nickname_hint=None,
        )


@pytest.mark.asyncio
async def test_identity_resolver_uses_message_obj_sender_nickname(temp_dir: Path):
    config, _ = _write_memory_config(temp_dir)
    store = MemoryStore(config=config)
    resolver = MemoryIdentityResolver(
        MemoryIdentityMappingService(store, config=config)
    )
    event = MagicMock()
    event.unified_msg_origin = TEST_UMO
    event.get_platform_id.return_value = "qq"
    event.get_sender_id.return_value = "10001"
    event.get_sender_name.return_value = None
    event.message_obj = MagicMock(sender=MagicMock(nickname="fallback-nickname"))

    try:
        identity = await resolver.resolve_from_event(event)
    finally:
        await store.close()

    assert identity.sender_nickname == "fallback-nickname"
    assert identity.platform_user_key == build_platform_user_key("qq", "10001")


@pytest.mark.asyncio
async def test_document_search_passes_combined_filters():
    vector_index = _CapturingVectorIndex()
    store = MagicMock()
    service = DocumentSearchService(store, vector_index=vector_index)  # type: ignore[arg-type]

    results = await service.search_long_term_memories(
        DocumentSearchRequest(
            canonical_user_id=TEST_CANONICAL_USER_ID,
            query="memory roadmap",
            scope_type="user",
            scope_id=TEST_CANONICAL_USER_ID,
            category="project_progress",
            top_k=3,
        )
    )

    assert results == []
    assert vector_index.calls[0]["metadata_filters"] == {
        "scope_type": "user",
        "scope_id": TEST_CANONICAL_USER_ID,
        "category": "project_progress",
    }
