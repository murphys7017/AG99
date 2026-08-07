from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from pathlib import Path

from astrbot import logger
from astrbot.core.message.components import BaseMessageComponent
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
from astrbot.core.utils.path_util import file_uri_to_path, local_path_to_file_uri

_LOCAL_MEDIA_FIELDS = ("file", "file_", "url", "path", "cover")


def _canonical_local_path(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw or raw.startswith(("http://", "https://", "base64://", "data:")):
        return None
    path = file_uri_to_path(raw) if raw.startswith("file:") else raw
    if not path:
        return None
    return os.path.normcase(os.path.abspath(os.path.expanduser(path)))


def _iter_message_components(message: MessageChain) -> list[BaseMessageComponent]:
    components: list[BaseMessageComponent] = []
    pending: list[object] = list(message.chain)
    seen: set[int] = set()
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        if isinstance(value, BaseMessageComponent):
            components.append(value)
            pending.extend(vars(value).values())
        elif isinstance(value, MessageChain):
            pending.extend(value.chain)
        elif isinstance(value, list | tuple):
            pending.extend(value)
        elif isinstance(value, dict):
            pending.extend(value.values())
    return components


class PluginJobMediaLease:
    """Own local media needed by one isolated Plugin Job and its delivery."""

    def __init__(
        self,
        *,
        parent_temporary_files: list[str],
        branch_message: MessageChain,
    ) -> None:
        self._parent_temporary_files = tuple(parent_temporary_files)
        self._branch_message = branch_message
        self._owned_files: dict[str, str] = {}
        self._materialized = False
        self._released = False

    async def materialize_inputs(self) -> None:
        if self._materialized or self._released:
            return
        self._materialized = True
        worker = asyncio.create_task(
            asyncio.to_thread(
                self._materialize_input_files,
                self._parent_temporary_files,
            )
        )
        try:
            replacements, failures = await asyncio.shield(worker)
        except asyncio.CancelledError:
            replacements, failures = await worker
            self._adopt_materialized_files(replacements, failures)
            self.cleanup()
            raise
        self._adopt_materialized_files(replacements, failures)

    def track_temporary_file(self, path: str) -> None:
        canonical = _canonical_local_path(path)
        if canonical is None or self._released:
            return
        self._owned_files.setdefault(canonical, os.path.abspath(path))

    def cleanup(self) -> None:
        if self._released:
            return
        self._released = True
        owned_files = tuple(self._owned_files.values())
        self._owned_files.clear()
        for path in owned_files:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError as exc:
                logger.warning(
                    "Failed to release Plugin Job media file %s: %s",
                    path,
                    exc,
                )

    @staticmethod
    def _materialize_input_files(
        source_paths: tuple[str, ...],
    ) -> tuple[dict[str, str], list[tuple[str, str]]]:
        replacements: dict[str, str] = {}
        failures: list[tuple[str, str]] = []
        target_root = Path(get_astrbot_temp_path()) / "plugin_jobs"
        target_root.mkdir(parents=True, exist_ok=True)
        for source_value in source_paths:
            canonical_source = _canonical_local_path(source_value)
            if canonical_source is None or not os.path.isfile(canonical_source):
                continue
            source = Path(canonical_source)
            target = target_root / f"plugin_{uuid.uuid4().hex}_{source.name}"
            try:
                try:
                    os.link(source, target)
                except OSError:
                    shutil.copy2(source, target)
            except OSError as exc:
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass
                failures.append((str(source), str(exc)))
                continue
            replacements[canonical_source] = str(target.resolve())
        return replacements, failures

    def _adopt_materialized_files(
        self,
        replacements: dict[str, str],
        failures: list[tuple[str, str]],
    ) -> None:
        if self._released:
            for path in replacements.values():
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass
            return
        for source, error in failures:
            logger.warning(
                "Failed to materialize Plugin Job input media %s: %s",
                source,
                error,
            )
        for target in replacements.values():
            canonical_target = _canonical_local_path(target)
            if canonical_target is not None:
                self._owned_files[canonical_target] = target
        self._rewrite_message_references(replacements)

    def _rewrite_message_references(self, replacements: dict[str, str]) -> None:
        if not replacements:
            return
        for component in _iter_message_components(self._branch_message):
            values = vars(component)
            for field_name in _LOCAL_MEDIA_FIELDS:
                if field_name not in values:
                    continue
                current = values[field_name]
                canonical = _canonical_local_path(current)
                replacement = replacements.get(canonical or "")
                if replacement is None:
                    continue
                rewritten = (
                    local_path_to_file_uri(replacement)
                    if isinstance(current, str) and current.startswith("file:")
                    else replacement
                )
                setattr(component, field_name, rewritten)
