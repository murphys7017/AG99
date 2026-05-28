import copy
import json

from click.testing import CliRunner

from astrbot.cli.commands.cmd_password import password
from astrbot.core.config.default import DEFAULT_CONFIG
from astrbot.core.utils.auth_password import verify_dashboard_password


def _write_config(root):
    (root / ".astrbot").touch()
    data_dir = root / "data"
    data_dir.mkdir()
    config = copy.deepcopy(DEFAULT_CONFIG)
    config_path = data_dir / "cmd_config.json"
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8-sig",
    )
    return config_path


def _read_config(config_path):
    return json.loads(config_path.read_text(encoding="utf-8-sig"))


def test_password_command_changes_dashboard_password(monkeypatch, tmp_path):
    config_path = _write_config(tmp_path)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        password,
        input="AstrbotChanged123\nAstrbotChanged123\n",
    )

    assert result.exit_code == 0
    config = _read_config(config_path)
    assert verify_dashboard_password(
        config["dashboard"]["pbkdf2_password"],
        "AstrbotChanged123",
    )
    assert verify_dashboard_password(config["dashboard"]["password"], "AstrbotChanged123")
    assert config["dashboard"]["password_storage_upgraded"] is True
    assert config["dashboard"]["password_change_required"] is False


def test_password_command_can_update_dashboard_username(monkeypatch, tmp_path):
    config_path = _write_config(tmp_path)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        password,
        ["--username", "astrbot-admin"],
        input="AstrbotChanged123\nAstrbotChanged123\n",
    )

    assert result.exit_code == 0
    config = _read_config(config_path)
    assert config["dashboard"]["username"] == "astrbot-admin"
    assert verify_dashboard_password(
        config["dashboard"]["pbkdf2_password"],
        "AstrbotChanged123",
    )


def test_password_command_rejects_empty_username(monkeypatch, tmp_path):
    _write_config(tmp_path)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        password,
        ["--username", ""],
        input="AstrbotChanged123\nAstrbotChanged123\n",
    )

    assert result.exit_code != 0
    assert "Username cannot be empty" in result.output
