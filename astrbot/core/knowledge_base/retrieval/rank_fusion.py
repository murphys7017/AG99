"""检索结果融合器

使用 Reciprocal Rank Fusion (RRF) 算法融合稠密检索和稀疏检索的结果
"""

import json
from dataclasses import dataclass

from astrbot.core.db.vec_db.base import Result
from astrbot.core.knowledge_base.kb_db_sqlite import KBSQLiteDatabase
from astrbot.core.knowledge_base.retrieval.sparse_retriever import SparseResult


@dataclass
class FusedResult:
    """融合后的检索结果"""

    chunk_id: str
    chunk_index: int
    doc_id: str
    kb_id: str
    content: str
    score: float


class RankFusion:
    """检索结果融合器

    职责:
    - 融合稠密检索和稀疏检索的结果
    - 全局校准稠密相似度，并按知识库校准 BM25 分数
    - 使用 Reciprocal Rank Fusion (RRF) 作为稳定的同分排序依据
    """

    def __init__(
        self,
        kb_db: KBSQLiteDatabase,
        k: int = 60,
        dense_weight: float = 0.9,
    ) -> None:
        """初始化结果融合器

        Args:
            kb_db: 知识库数据库实例
            k: RRF 同分排序的平滑参数
            dense_weight: 相对分数融合中的稠密检索权重

        """
        if not 0 <= dense_weight <= 1:
            raise ValueError("dense_weight must be between 0 and 1")
        self.kb_db = kb_db
        self.k = k
        self.dense_weight = dense_weight

    async def fuse(
        self,
        dense_results: list[Result],
        sparse_results: list[SparseResult],
        top_k: int = 20,
    ) -> list[FusedResult]:
        """融合稠密和稀疏检索结果

        在所有候选中对稠密相似度做 min-max 归一化，BM25 分数则在
        每个独立知识库内归一化，再按权重合并。RRF 分数仅用于融合
        分数相同时的稳定排序。最终结果只去除完全相同的文本块。

        Args:
            dense_results: 稠密检索结果
            sparse_results: 稀疏检索结果
            top_k: 返回结果数量

        Returns:
            List[FusedResult]: 融合后的结果列表

        """
        if top_k <= 0:
            return []

        # 1. 构建排名映射
        dense_ranks = {
            r.data["doc_id"]: (idx + 1) for idx, r in enumerate(dense_results)
        }  # 这里的 doc_id 实际上是 chunk_id
        sparse_ranks = {
            r.chunk_id: r.rank if r.rank is not None else idx + 1
            for idx, r in enumerate(sparse_results)
        }

        # 2. 收集所有唯一的 ID
        # 需要统一为 chunk_id
        all_chunk_ids = set()
        vec_doc_id_to_dense: dict[str, Result] = {}  # vec_doc_id -> Result
        chunk_id_to_sparse: dict[str, SparseResult] = {}  # chunk_id -> SparseResult

        # 处理稀疏检索结果
        for r in sparse_results:
            all_chunk_ids.add(r.chunk_id)
            chunk_id_to_sparse[r.chunk_id] = r

        # 处理稠密检索结果 (需要转换 vec_doc_id 到 chunk_id)
        for r in dense_results:
            vec_doc_id = r.data["doc_id"]
            all_chunk_ids.add(vec_doc_id)
            vec_doc_id_to_dense[vec_doc_id] = r

        # 3. 计算 RRF 分数，作为相同融合分数时的稳定排序依据。
        rrf_scores: dict[str, float] = {}

        for identifier in all_chunk_ids:
            score = 0.0

            # 来自稠密检索的贡献
            if identifier in dense_ranks:
                score += 1.0 / (self.k + dense_ranks[identifier])

            # 来自稀疏检索的贡献
            if identifier in sparse_ranks:
                score += 1.0 / (self.k + sparse_ranks[identifier])

            rrf_scores[identifier] = score

        # 4. 稠密分数在所有候选中校准，以保留跨知识库的语义强弱。
        normalized_dense: dict[str, float] = {}
        if vec_doc_id_to_dense:
            dense_scores = [
                result.similarity for result in vec_doc_id_to_dense.values()
            ]
            minimum = min(dense_scores)
            score_range = max(dense_scores) - minimum
            for identifier, result in vec_doc_id_to_dense.items():
                normalized_dense[identifier] = (
                    (result.similarity - minimum) / score_range
                    if score_range
                    else 1.0
                )

        # BM25 scores come from per-KB indexes and are not comparable across KBs.
        sparse_groups: dict[str, list[tuple[str, float]]] = {}
        for identifier, result in chunk_id_to_sparse.items():
            sparse_groups.setdefault(result.kb_id, []).append(
                (identifier, result.score)
            )

        normalized_sparse: dict[str, float] = {}
        for group in sparse_groups.values():
            sparse_scores = [score for _, score in group]
            minimum = min(sparse_scores)
            score_range = max(sparse_scores) - minimum
            for identifier, score in group:
                normalized_sparse[identifier] = (
                    (score - minimum) / score_range if score_range else 1.0
                )

        fusion_scores = {
            identifier: self.dense_weight * normalized_dense.get(identifier, 0.0)
            + (1 - self.dense_weight) * normalized_sparse.get(identifier, 0.0)
            for identifier in all_chunk_ids
        }

        # 5. Sort by calibrated relevance, then RRF to preserve deterministic ties.
        sorted_ids = sorted(
            all_chunk_ids,
            key=lambda cid: (
                -fusion_scores[cid],
                -rrf_scores[cid],
                dense_ranks.get(cid, float("inf")),
                sparse_ranks.get(cid, float("inf")),
                cid,
            ),
        )

        # 6. Construct results, keeping useful distinct chunks from one document.
        fused_results = []
        seen_contents: set[str] = set()
        for identifier in sorted_ids:
            # 优先从稀疏检索获取完整信息
            if identifier in chunk_id_to_sparse:
                sr = chunk_id_to_sparse[identifier]
                fused_result = FusedResult(
                    chunk_id=sr.chunk_id,
                    chunk_index=sr.chunk_index,
                    doc_id=sr.doc_id,
                    kb_id=sr.kb_id,
                    content=sr.content,
                    score=fusion_scores[identifier],
                )
            elif identifier in vec_doc_id_to_dense:
                # 从向量检索获取信息,需要从数据库获取块的详细信息
                vec_result = vec_doc_id_to_dense[identifier]
                chunk_md = json.loads(vec_result.data["metadata"])
                fused_result = FusedResult(
                    chunk_id=identifier,
                    chunk_index=chunk_md["chunk_index"],
                    doc_id=chunk_md["kb_doc_id"],
                    kb_id=chunk_md["kb_id"],
                    content=vec_result.data["text"],
                    score=fusion_scores[identifier],
                )
            else:
                continue

            if fused_result.content in seen_contents:
                continue
            seen_contents.add(fused_result.content)
            fused_results.append(fused_result)
            if len(fused_results) >= top_k:
                break

        return fused_results
