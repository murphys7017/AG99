import pytest
from sqlmodel import text

from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.knowledge_base.kb_db_sqlite import KBSQLiteDatabase


@pytest.mark.asyncio
async def test_main_sqlite_database_sets_busy_timeout(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "data.db"))
    try:
        await db.initialize()
        async with db.get_db() as session:
            result = await session.execute(text("PRAGMA busy_timeout"))

        assert result.scalar_one() == 30000
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_knowledge_base_sqlite_database_sets_busy_timeout(tmp_path):
    db = KBSQLiteDatabase(str(tmp_path / "kb.db"))
    try:
        await db.initialize()
        async with db.get_db() as session:
            result = await session.execute(text("PRAGMA busy_timeout"))

        assert result.scalar_one() == 30000
    finally:
        await db.engine.dispose()
