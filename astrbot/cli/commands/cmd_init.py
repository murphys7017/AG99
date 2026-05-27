import asyncio
import copy
import json
import os
from pathlib import Path

import click
from filelock import FileLock, Timeout

from ..utils import check_dashboard, get_astrbot_root
from .cmd_conf import _validate_dashboard_password

DASHBOARD_INITIAL_PASSWORD_ENV = "ASTRBOT_DASHBOARD_INITIAL_PASSWORD"


def _initialize_config_from_env(astrbot_root: Path) -> None:
    initial_password = os.environ.get(DASHBOARD_INITIAL_PASSWORD_ENV)
    if initial_password is None:
        return

    config_path = astrbot_root / "data" / "cmd_config.json"
    if config_path.exists():
        return

    from astrbot.core.config.default import DEFAULT_CONFIG

    config = copy.deepcopy(DEFAULT_CONFIG)
    config["dashboard"]["password"] = _validate_dashboard_password(initial_password)
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8-sig",
    )
    click.echo("Initialized data/cmd_config.json with dashboard initial password.")


async def initialize_astrbot(astrbot_root: Path) -> None:
    """Execute AstrBot initialization logic"""
    dot_astrbot = astrbot_root / ".astrbot"

    if not dot_astrbot.exists():
        if click.confirm(
            f"Install AstrBot to this directory? {astrbot_root}",
            default=True,
            abort=True,
        ):
            dot_astrbot.touch()
            click.echo(f"Created {dot_astrbot}")

    paths = {
        "data": astrbot_root / "data",
        "config": astrbot_root / "data" / "config",
        "plugins": astrbot_root / "data" / "plugins",
        "temp": astrbot_root / "data" / "temp",
    }

    for name, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        click.echo(f"{'Created' if not path.exists() else 'Directory exists'}: {path}")

    _initialize_config_from_env(astrbot_root)

    await check_dashboard(astrbot_root / "data")


@click.command()
def init() -> None:
    """Initialize AstrBot"""
    click.echo("Initializing AstrBot...")
    astrbot_root = get_astrbot_root()
    lock_file = astrbot_root / "astrbot.lock"
    lock = FileLock(lock_file, timeout=5)

    try:
        with lock.acquire():
            asyncio.run(initialize_astrbot(astrbot_root))
            click.echo("Done! You can now run 'astrbot run' to start AstrBot")
    except Timeout:
        raise click.ClickException(
            "Cannot acquire lock file. Please check if another instance is running"
        )

    except Exception as e:
        raise click.ClickException(f"Initialization failed: {e!s}")
