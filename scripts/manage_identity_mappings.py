from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if PROJECT_ROOT.as_posix() not in sys.path:
    sys.path.insert(0, PROJECT_ROOT.as_posix())

if TYPE_CHECKING:
    from astrbot.core.memory import MemoryConfig, MemoryService
    from astrbot.core.memory.identity import MemoryIdentityMappingService
    from astrbot.core.memory.store import MemoryStore


def _bootstrap_memory_runtime() -> tuple[
    MemoryConfig,
    MemoryStore,
    MemoryIdentityMappingService,
    MemoryService,
]:
    import runtime_bootstrap

    runtime_bootstrap.initialize_runtime_bootstrap()

    from astrbot.core.memory import (
        get_memory_config,
        get_memory_service,
    )
    from astrbot.core.memory.identity import MemoryIdentityMappingService
    from astrbot.core.memory.store import MemoryStore

    config = get_memory_config()
    store = MemoryStore(config=config)
    mapping_service = MemoryIdentityMappingService(store, config=config)
    memory_service = get_memory_service()
    return config, store, mapping_service, memory_service


async def _shutdown_memory_runtime() -> None:
    from astrbot.core.memory import shutdown_memory_service

    await shutdown_memory_service()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect and reload memory identity mappings from AstrBot config.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List bindings from memory.identity.bindings.")
    subparsers.add_parser("validate", help="Validate memory.identity.bindings.")
    subparsers.add_parser(
        "reload",
        help="Reload memory.identity.bindings into the runtime SQLite mapping table.",
    )

    return parser.parse_args()


async def run(args: argparse.Namespace) -> int:
    _config, store, mapping_service, memory_service = _bootstrap_memory_runtime()

    try:
        if args.command == "list":
            bindings = mapping_service.load_bindings()
            print(f"[identity-mappings] bindings: {len(bindings)}")
            for binding in bindings:
                print(
                    f"- {binding.platform_user_key} -> {binding.canonical_user_id}"
                    + (f" ({binding.nickname_hint})" if binding.nickname_hint else "")
                )
            return 0

        if args.command == "validate":
            bindings = mapping_service.load_bindings()
            print(f"[identity-mappings] valid: {len(bindings)} binding(s)")
            return 0

        if args.command == "reload":
            count = await memory_service.reload_identity_mappings()
            print(f"[identity-mappings] reloaded: {count} binding(s)")
            return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[identity-mappings] failed: {exc}", file=sys.stderr)
        return 1
    finally:
        await store.close()
        await _shutdown_memory_runtime()

    return 1


def main() -> int:
    return asyncio.run(run(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
