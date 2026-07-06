from unittest.mock import AsyncMock

import pytest

from astrbot.core.knowledge_base.kb_db_sqlite import KBSQLiteDatabase
from astrbot.core.knowledge_base.models import KBDocument, KBMedia, KnowledgeBase


@pytest.mark.asyncio
async def test_delete_document_by_id_removes_related_media_only(tmp_path):
    db = KBSQLiteDatabase(str(tmp_path / "kb.db"))
    await db.initialize()
    vec_db = AsyncMock()

    try:
        kb = KnowledgeBase(
            kb_name="kb",
            embedding_provider_id="embedding",
        )
        target_doc = KBDocument(
            doc_id="doc-target",
            kb_id=kb.kb_id,
            doc_name="target.txt",
            file_type="txt",
            file_size=1,
            file_path="/tmp/target.txt",
        )
        other_doc = KBDocument(
            doc_id="doc-other",
            kb_id=kb.kb_id,
            doc_name="other.txt",
            file_type="txt",
            file_size=1,
            file_path="/tmp/other.txt",
        )
        target_media = KBMedia(
            media_id="media-target",
            doc_id=target_doc.doc_id,
            kb_id=kb.kb_id,
            media_type="image",
            file_name="target.png",
            file_path="/tmp/target.png",
            file_size=1,
            mime_type="image/png",
        )
        other_media = KBMedia(
            media_id="media-other",
            doc_id=other_doc.doc_id,
            kb_id=kb.kb_id,
            media_type="image",
            file_name="other.png",
            file_path="/tmp/other.png",
            file_size=1,
            mime_type="image/png",
        )

        async with db.get_db() as session, session.begin():
            session.add(kb)
            session.add(target_doc)
            session.add(other_doc)
            session.add(target_media)
            session.add(other_media)

        await db.delete_document_by_id(target_doc.doc_id, vec_db)

        assert await db.get_document_by_id(target_doc.doc_id) is None
        assert await db.list_media_by_doc(target_doc.doc_id) == []
        assert await db.get_document_by_id(other_doc.doc_id) is not None
        assert len(await db.list_media_by_doc(other_doc.doc_id)) == 1
        vec_db.delete_documents.assert_awaited_once_with(
            metadata_filters={"kb_doc_id": target_doc.doc_id},
        )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_update_kb_stats_counts_chunks_for_target_kb(tmp_path):
    db = KBSQLiteDatabase(str(tmp_path / "kb.db"))
    await db.initialize()
    vec_db = AsyncMock()
    vec_db.count_documents.return_value = 7

    try:
        kb = KnowledgeBase(
            kb_name="kb",
            embedding_provider_id="embedding",
        )
        doc = KBDocument(
            doc_id="doc",
            kb_id=kb.kb_id,
            doc_name="doc.txt",
            file_type="txt",
            file_size=1,
            file_path="/tmp/doc.txt",
        )
        async with db.get_db() as session, session.begin():
            session.add(kb)
            session.add(doc)

        await db.update_kb_stats(kb.kb_id, vec_db)

        updated = await db.get_kb_by_id(kb.kb_id)
        assert updated is not None
        assert updated.doc_count == 1
        assert updated.chunk_count == 7
        vec_db.count_documents.assert_awaited_once_with(
            metadata_filter={"kb_id": kb.kb_id},
        )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_list_documents_by_kb_supports_search_and_total(tmp_path):
    db = KBSQLiteDatabase(str(tmp_path / "kb.db"))
    await db.initialize()

    try:
        kb = KnowledgeBase(
            kb_name="kb",
            embedding_provider_id="embedding",
        )
        docs = [
            KBDocument(
                doc_id="doc-guide-1",
                kb_id=kb.kb_id,
                doc_name="setup-guide.txt",
                file_type="txt",
                file_size=1,
                file_path="/tmp/setup-guide.txt",
            ),
            KBDocument(
                doc_id="doc-guide-2",
                kb_id=kb.kb_id,
                doc_name="user-guide.txt",
                file_type="txt",
                file_size=1,
                file_path="/tmp/user-guide.txt",
            ),
            KBDocument(
                doc_id="doc-notes",
                kb_id=kb.kb_id,
                doc_name="notes.txt",
                file_type="txt",
                file_size=1,
                file_path="/tmp/notes.txt",
            ),
        ]
        async with db.get_db() as session, session.begin():
            session.add(kb)
            for doc in docs:
                session.add(doc)

        listed = await db.list_documents_by_kb(
            kb_id=kb.kb_id,
            offset=0,
            limit=1,
            search="guide",
        )
        total = await db.count_documents_by_kb(kb.kb_id, search="guide")

        assert len(listed) == 1
        assert listed[0].doc_name in {"setup-guide.txt", "user-guide.txt"}
        assert total == 2
    finally:
        await db.close()
