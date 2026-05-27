import hashlib
import json

import pytest

from astrbot.cli.commands import cmd_init


@pytest.mark.asyncio
async def test_init_without_initial_password_env_does_not_create_config(
    monkeypatch,
    tmp_path,
):
    async def fake_check_dashboard(_data_path):
        return None

    monkeypatch.delenv(cmd_init.DASHBOARD_INITIAL_PASSWORD_ENV, raising=False)
    monkeypatch.setattr(cmd_init, "check_dashboard", fake_check_dashboard)
    (tmp_path / ".astrbot").touch()

    await cmd_init.initialize_astrbot(tmp_path)

    assert not (tmp_path / "data" / "cmd_config.json").exists()


@pytest.mark.asyncio
async def test_init_uses_initial_password_env_to_create_md5_config(
    monkeypatch,
    tmp_path,
):
    async def fake_check_dashboard(_data_path):
        return None

    initial_password = "AstrBotInitialPassword123"
    monkeypatch.setenv(cmd_init.DASHBOARD_INITIAL_PASSWORD_ENV, initial_password)
    monkeypatch.setattr(cmd_init, "check_dashboard", fake_check_dashboard)
    (tmp_path / ".astrbot").touch()

    await cmd_init.initialize_astrbot(tmp_path)

    config_path = tmp_path / "data" / "cmd_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))

    assert config["dashboard"]["password"] == hashlib.md5(
        initial_password.encode(),
    ).hexdigest()


@pytest.mark.asyncio
async def test_init_initial_password_env_does_not_overwrite_existing_config(
    monkeypatch,
    tmp_path,
):
    async def fake_check_dashboard(_data_path):
        return None

    monkeypatch.setenv(cmd_init.DASHBOARD_INITIAL_PASSWORD_ENV, "NewPassword123")
    monkeypatch.setattr(cmd_init, "check_dashboard", fake_check_dashboard)
    (tmp_path / ".astrbot").touch()
    config_dir = tmp_path / "data"
    config_dir.mkdir()
    config_path = config_dir / "cmd_config.json"
    config_path.write_text('{"dashboard": {"password": "existing"}}', encoding="utf-8-sig")

    await cmd_init.initialize_astrbot(tmp_path)

    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    assert config["dashboard"]["password"] == "existing"
