import asyncio
import os
import threading

import numpy as np


class EmbeddingStorage:
    def __init__(self, dimension: int, path: str | None = None) -> None:
        try:
            import faiss
        except ModuleNotFoundError as e:
            raise ImportError(
                "faiss 未安装。请使用 'pip install faiss-cpu' 或 'pip install faiss-gpu' 安装。",
            ) from e
        self._faiss = faiss
        self.dimension = dimension
        self.path = path
        self.index = None
        self._io_lock = threading.Lock()
        if path and os.path.exists(path):
            self.index = faiss.read_index(path)
        else:
            if dimension <= 0:
                raise ValueError(
                    f"无效的嵌入向量维度: {dimension}。请检查该知识库使用的 Embedding "
                    "Provider 是否正确配置了 embedding_dimensions。",
                )
            base_index = faiss.IndexFlatL2(dimension)
            self.index = faiss.IndexIDMap(base_index)

    async def insert(self, vector: np.ndarray, id: int) -> None:
        """插入向量

        Args:
            vector (np.ndarray): 要插入的向量
            id (int): 向量的ID
        Raises:
            ValueError: 如果向量的维度与存储的维度不匹配

        """
        assert self.index is not None, "FAISS index is not initialized."
        if vector.shape[0] != self.dimension:
            raise ValueError(
                f"向量维度不匹配, 期望: {self.dimension}, 实际: {vector.shape[0]}",
            )
        await asyncio.to_thread(self._insert_sync, vector, id)

    async def insert_batch(self, vectors: np.ndarray, ids: list[int]) -> None:
        """批量插入向量

        Args:
            vectors (np.ndarray): 要插入的向量数组
            ids (list[int]): 向量的ID列表
        Raises:
            ValueError: 如果向量的维度与存储的维度不匹配

        """
        assert self.index is not None, "FAISS index is not initialized."
        if vectors.shape[1] != self.dimension:
            raise ValueError(
                f"向量维度不匹配, 期望: {self.dimension}, 实际: {vectors.shape[1]}",
            )
        await asyncio.to_thread(self._insert_batch_sync, vectors, ids)

    async def search(self, vector: np.ndarray, k: int) -> tuple:
        """搜索最相似的向量

        Args:
            vector (np.ndarray): 查询向量
            k (int): 返回的最相似向量的数量
        Returns:
            tuple: (距离, 索引)

        """
        assert self.index is not None, "FAISS index is not initialized."
        return await asyncio.to_thread(self._search_sync, vector, k)

    async def delete(self, ids: list[int]) -> None:
        """删除向量

        Args:
            ids (list[int]): 要删除的向量ID列表

        """
        assert self.index is not None, "FAISS index is not initialized."
        id_array = np.array(ids, dtype=np.int64)
        await asyncio.to_thread(self._delete_sync, id_array)

    async def save_index(self) -> None:
        """保存索引

        Args:
            path (str): 保存索引的路径

        """
        if self.index is None:
            return
        await asyncio.to_thread(self._save_index_locked_sync)

    def _insert_sync(self, vector: np.ndarray, id: int) -> None:
        with self._io_lock:
            assert self.index is not None
            self.index.add_with_ids(vector.reshape(1, -1), np.array([id]))
            self._save_index_sync()

    def _insert_batch_sync(self, vectors: np.ndarray, ids: list[int]) -> None:
        with self._io_lock:
            assert self.index is not None
            self.index.add_with_ids(vectors, np.array(ids))
            self._save_index_sync()

    def _search_sync(self, vector: np.ndarray, k: int) -> tuple:
        with self._io_lock:
            assert self.index is not None
            self._faiss.normalize_L2(vector)
            return self.index.search(vector, k)

    def _delete_sync(self, ids: np.ndarray) -> None:
        with self._io_lock:
            assert self.index is not None
            self.index.remove_ids(ids)
            self._save_index_sync()

    def _save_index_locked_sync(self) -> None:
        with self._io_lock:
            self._save_index_sync()

    def _save_index_sync(self) -> None:
        if self.index is None:
            return
        self._faiss.write_index(self.index, self.path)
