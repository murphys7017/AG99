from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from quart import Quart

from astrbot.core.provider.provider import EmbeddingProvider
from astrbot.dashboard.routes.knowledge_base import KnowledgeBaseRoute


class FakeEmbeddingProvider(EmbeddingProvider):
    def __init__(self):
        super().__init__({"type": "fake", "id": "embedding-1"}, {})

    async def get_embedding(self, text: str) -> list[float]:
        return [0.1, 0.2]

    async def get_embeddings(self, text: list[str]) -> list[list[float]]:
        return [[0.1, 0.2] for _ in text]

    def get_dim(self) -> int:
        return 2


def make_route(kb_manager) -> KnowledgeBaseRoute:
    route = KnowledgeBaseRoute.__new__(KnowledgeBaseRoute)
    route.core_lifecycle = SimpleNamespace(kb_manager=kb_manager)
    return route


def make_kb(kb_id: str = "kb-1", kb_name: str = "Docs"):
    return SimpleNamespace(
        kb_id=kb_id,
        kb_name=kb_name,
        description="description",
        emoji="book",
        embedding_provider_id="embedding-1",
        rerank_provider_id="rerank-1",
        chunk_size=512,
        chunk_overlap=50,
        top_k_dense=50,
        top_k_sparse=50,
        top_m_final=5,
        model_dump=lambda: {"kb_id": kb_id, "kb_name": kb_name},
    )


@pytest.mark.asyncio
async def test_list_kbs_applies_pagination():
    app = Quart(__name__)
    kb_manager = MagicMock()
    kb_manager.list_kbs = AsyncMock(
        return_value=[
            make_kb("kb-1", "one"),
            make_kb("kb-2", "two"),
            make_kb("kb-3", "three"),
        ],
    )
    kb_manager.get_kb = AsyncMock(return_value=SimpleNamespace(init_error=None))

    async with app.test_request_context("/api/kb/list?page=2&page_size=2"):
        result = await make_route(kb_manager).list_kbs()

    assert result["status"] == "ok"
    assert result["data"] == {
        "items": [{"kb_id": "kb-3", "kb_name": "three"}],
        "page": 2,
        "page_size": 2,
        "total": 3,
    }


@pytest.mark.asyncio
async def test_create_kb_accepts_legacy_name_field():
    app = Quart(__name__)
    kb = make_kb(kb_name="Legacy")
    kb_manager = MagicMock()
    kb_manager.provider_manager.get_provider_by_id = AsyncMock(
        return_value=FakeEmbeddingProvider(),
    )
    kb_manager.create_kb = AsyncMock(return_value=SimpleNamespace(kb=kb))

    async with app.test_request_context(
        "/api/kb/create",
        method="POST",
        json={
            "name": "Legacy",
            "embedding_provider_id": "embedding-1",
            "top_k_dense": 12,
        },
    ):
        result = await make_route(kb_manager).create_kb()

    assert result["status"] == "ok"
    kb_manager.create_kb.assert_awaited_once_with(
        kb_name="Legacy",
        description=None,
        emoji=None,
        embedding_provider_id="embedding-1",
        rerank_provider_id=None,
        chunk_size=None,
        chunk_overlap=None,
        top_k_dense=12,
        top_k_sparse=None,
        top_m_final=None,
    )


@pytest.mark.asyncio
async def test_update_kb_preserves_omitted_fields():
    app = Quart(__name__)
    kb = make_kb()
    kb_manager = MagicMock()
    kb_manager.get_kb = AsyncMock(return_value=SimpleNamespace(kb=kb))
    kb_manager.update_kb = AsyncMock(return_value=SimpleNamespace(kb=kb))

    async with app.test_request_context(
        "/api/kb/update",
        method="POST",
        json={"kb_id": "kb-1", "chunk_size": 1024},
    ):
        result = await make_route(kb_manager).update_kb()

    assert result["status"] == "ok"
    kb_manager.update_kb.assert_awaited_once_with(
        kb_id="kb-1",
        kb_name="Docs",
        description="description",
        emoji="book",
        embedding_provider_id="embedding-1",
        rerank_provider_id="rerank-1",
        chunk_size=1024,
        chunk_overlap=50,
        top_k_dense=50,
        top_k_sparse=50,
        top_m_final=5,
    )


@pytest.mark.asyncio
async def test_update_kb_allows_explicit_rerank_provider_clear():
    app = Quart(__name__)
    kb = make_kb()
    kb_manager = MagicMock()
    kb_manager.get_kb = AsyncMock(return_value=SimpleNamespace(kb=kb))
    kb_manager.update_kb = AsyncMock(return_value=SimpleNamespace(kb=kb))

    async with app.test_request_context(
        "/api/kb/update",
        method="POST",
        json={"kb_id": "kb-1", "rerank_provider_id": None},
    ):
        result = await make_route(kb_manager).update_kb()

    assert result["status"] == "ok"
    assert kb_manager.update_kb.await_args.kwargs["rerank_provider_id"] is None


@pytest.mark.asyncio
async def test_list_documents_passes_search_and_returns_total():
    app = Quart(__name__)
    doc = SimpleNamespace(model_dump=lambda: {"doc_id": "doc-1"})
    kb_helper = SimpleNamespace(
        list_documents=AsyncMock(return_value=[doc]),
        count_documents=AsyncMock(return_value=3),
    )
    kb_manager = MagicMock()
    kb_manager.get_kb = AsyncMock(return_value=kb_helper)

    async with app.test_request_context(
        "/api/kb/document/list?kb_id=kb-1&page=2&page_size=1&search= guide ",
    ):
        result = await make_route(kb_manager).list_documents()

    assert result["status"] == "ok"
    assert result["data"] == {
        "items": [{"doc_id": "doc-1"}],
        "page": 2,
        "page_size": 1,
        "total": 3,
    }
    kb_helper.list_documents.assert_awaited_once_with(
        offset=1,
        limit=1,
        search="guide",
    )
    kb_helper.count_documents.assert_awaited_once_with(search="guide")
