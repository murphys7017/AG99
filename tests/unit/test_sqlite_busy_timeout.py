import sqlite3

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
async def test_main_sqlite_database_upgrades_personal_runtime_fingerprint_column(
    tmp_path,
):
    db_path = tmp_path / "legacy-personal-runtime.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE personal_runtime_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                config_id VARCHAR NOT NULL,
                persona_id VARCHAR NOT NULL,
                audience_key VARCHAR NOT NULL,
                privacy_scope VARCHAR NOT NULL,
                last_expression_at FLOAT,
                reply_cooldown_until FLOAT,
                no_action_cooldown_until FLOAT,
                mute_until FLOAT,
                usage_day VARCHAR,
                daily_policy_calls INTEGER NOT NULL DEFAULT 0,
                daily_proactive_outputs INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME,
                updated_at DATETIME,
                CONSTRAINT uix_personal_runtime_state_identity UNIQUE (
                    config_id, persona_id, audience_key, privacy_scope
                )
            )
            """
        )
        connection.commit()
    finally:
        connection.close()

    db = SQLiteDatabase(str(db_path))
    try:
        await db.initialize()
        async with db.get_db() as session:
            result = await session.execute(text("PRAGMA table_info(personal_runtime_states)"))

        columns = {row[1] for row in result.fetchall()}
        assert "last_expression_fingerprint" in columns
        assert "last_user_activity_at" in columns
        assert "last_idle_initiation_activity_at" in columns
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
