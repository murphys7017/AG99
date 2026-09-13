import json

import pytest

from astrbot.core.db.vec_db.base import Result
from astrbot.core.knowledge_base.retrieval.rank_fusion import RankFusion
from astrbot.core.knowledge_base.retrieval.sparse_retriever import SparseResult


def make_dense_result(
    chunk_id: str,
    similarity: float,
    *,
    kb_id: str = "kb",
    doc_id: str | None = None,
    content: str | None = None,
) -> Result:
    return Result(
        similarity=similarity,
        data={
            "doc_id": chunk_id,
            "text": content or chunk_id,
            "metadata": json.dumps(
                {
                    "chunk_index": 0,
                    "kb_doc_id": doc_id or f"doc-{chunk_id}",
                    "kb_id": kb_id,
                }
            ),
        },
    )


def make_sparse_result(
    chunk_id: str,
    score: float,
    *,
    kb_id: str = "kb",
    doc_id: str | None = None,
    content: str | None = None,
    rank: int | None = None,
) -> SparseResult:
    return SparseResult(
        chunk_index=0,
        chunk_id=chunk_id,
        doc_id=doc_id or f"doc-{chunk_id}",
        kb_id=kb_id,
        content=content or chunk_id,
        score=score,
        rank=rank,
    )


@pytest.mark.asyncio
async def test_rank_fusion_retains_distinct_chunks_from_one_document():
    dense_results = [
        make_dense_result("first", 0.99, doc_id="doc-a"),
        make_dense_result("second", 0.98, doc_id="doc-a"),
        make_dense_result("other", 0.97, doc_id="doc-b"),
    ]
    sparse_results = [
        make_sparse_result("first", 10.0, doc_id="doc-a"),
        make_sparse_result("second", 9.0, doc_id="doc-a"),
        make_sparse_result("other", 8.0, doc_id="doc-b"),
    ]

    results = await RankFusion(kb_db=None).fuse(
        dense_results=dense_results,
        sparse_results=sparse_results,
        top_k=3,
    )

    assert [result.chunk_id for result in results] == ["first", "second", "other"]


@pytest.mark.asyncio
async def test_rank_fusion_deduplicates_only_exact_chunk_text():
    dense_results = [
        make_dense_result("best", 0.99, content="same text"),
        make_dense_result("duplicate", 0.98, content="same text"),
        make_dense_result("near", 0.97, content="same text "),
    ]
    sparse_results = [
        make_sparse_result("best", 10.0, content="same text"),
        make_sparse_result("duplicate", 9.0, content="same text"),
        make_sparse_result("near", 8.0, content="same text "),
    ]

    results = await RankFusion(kb_db=None).fuse(
        dense_results=dense_results,
        sparse_results=sparse_results,
    )

    assert [result.chunk_id for result in results] == ["best", "near"]


@pytest.mark.asyncio
async def test_rank_fusion_keeps_low_scoring_single_kb_result_last():
    dense_results = [
        make_dense_result("strong", 0.99, kb_id="large"),
        make_dense_result("moderate", 0.80, kb_id="large"),
        make_dense_result("weak", 0.10, kb_id="small"),
    ]
    sparse_results = [
        make_sparse_result("strong", 10.0, kb_id="large"),
        make_sparse_result("moderate", 5.0, kb_id="large"),
        make_sparse_result("weak", 0.01, kb_id="small"),
    ]

    results = await RankFusion(kb_db=None).fuse(
        dense_results=dense_results,
        sparse_results=sparse_results,
    )

    assert [result.chunk_id for result in results] == ["strong", "moderate", "weak"]
    assert results[-1].score == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_rank_fusion_uses_local_sparse_rank_for_rrf_ties():
    dense_results = [
        make_dense_result("small-exact", 0.99, kb_id="small"),
        make_dense_result("large-first", 0.99, kb_id="large"),
    ]
    sparse_results = [
        make_sparse_result("large-first", 12.0, kb_id="large", rank=1),
        make_sparse_result("large-second", 10.0, kb_id="large", rank=2),
        make_sparse_result("small-exact", 0.00001, kb_id="small", rank=1),
    ]

    results = await RankFusion(kb_db=None).fuse(
        dense_results=dense_results,
        sparse_results=sparse_results,
    )

    assert [result.chunk_id for result in results] == [
        "small-exact",
        "large-first",
        "large-second",
    ]
