"""Single admission decision point for plugin-provided capabilities.

This module only decides whether a capability is allowed to take effect. It does
not execute capabilities and does not decide the Personal/Core target: target
resolution stays with ``plugin_runtime_targets`` and ``plugin_tool_targets``.

The model has two orthogonal axes (see
``docs/Yakumo/dev/plugin-capability-model-plan.md``):

``Permission``
    Whether the plugin is allowed to contribute this capability at all::

        global activation AND valid ownership AND plugin_set AND not session-disabled

``Applicability``
    Whether this capability suits the current event: the capability's own
    ``event_filter`` (platform, device, runtime availability).

A ``hard`` contribution is only exempt from *soft* failure handling (timeout,
``best_effort`` skip). It must still pass both axes.

Process-level capabilities (web API, provider, platform adapter, global task,
cron) deliberately do not participate: they are governed by plugin
enable/disable/unload lifecycle, not by a per-turn snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from astrbot.core import logger

PLUGIN_ADMISSION_SNAPSHOT_EXTRA_KEY = "_plugin_admission_snapshot"


class CapabilityKind(str, Enum):
    """Closed vocabulary of plugin capability kinds."""

    HANDLER = "handler"
    LLM_HOOK = "llm_hook"
    TOOL = "tool"
    PROMPT_EXTENSION = "prompt_extension"
    INTERACTION_PROMPT = "interaction_prompt"
    INTERACTION_RESULT = "interaction_result"
    STREAM_DECIDER = "stream_decider"
    LIFECYCLE_OBSERVER = "lifecycle_observer"
    PERSONA_EFFECT = "persona_effect"
    RUNTIME_SENSOR = "runtime_sensor"
    # Process-level kinds. They are listed for inventory/diagnostics only and
    # never enter the per-turn snapshot.
    EVENT_INJECTION = "event_injection"
    DIRECT_OUTPUT = "direct_output"
    POSTPROCESSOR = "postprocessor"
    WEB_API = "web_api"
    PROVIDER = "provider"
    PLATFORM_ADAPTER = "platform_adapter"
    GLOBAL_TASK = "global_task"
    CRON_JOB = "cron_job"
    AGENT_TOOL = "agent_tool"
    PLUGIN_PAGE = "plugin_page"
    MANAGEMENT_HOOK = "management_hook"


#: Kinds resolved per Interaction turn through the admission snapshot.
INTERACTION_CAPABILITY_KINDS: frozenset[CapabilityKind] = frozenset(
    {
        CapabilityKind.HANDLER,
        CapabilityKind.LLM_HOOK,
        CapabilityKind.TOOL,
        CapabilityKind.PROMPT_EXTENSION,
        CapabilityKind.INTERACTION_PROMPT,
        CapabilityKind.INTERACTION_RESULT,
        CapabilityKind.STREAM_DECIDER,
        CapabilityKind.LIFECYCLE_OBSERVER,
        CapabilityKind.PERSONA_EFFECT,
        CapabilityKind.RUNTIME_SENSOR,
    }
)

#: Kinds governed by plugin enable/disable/unload lifecycle instead.
PROCESS_CAPABILITY_KINDS: frozenset[CapabilityKind] = frozenset(
    {
        CapabilityKind.EVENT_INJECTION,
        CapabilityKind.DIRECT_OUTPUT,
        CapabilityKind.POSTPROCESSOR,
        CapabilityKind.WEB_API,
        CapabilityKind.PROVIDER,
        CapabilityKind.PLATFORM_ADAPTER,
        CapabilityKind.GLOBAL_TASK,
        CapabilityKind.CRON_JOB,
        CapabilityKind.AGENT_TOOL,
        CapabilityKind.PLUGIN_PAGE,
        CapabilityKind.MANAGEMENT_HOOK,
    }
)

#: Lifecycle hooks that must not be silenced by per-session plugin disabling.
#: ``star_handler`` already exempts these from the ``plugin_set`` whitelist.
SESSION_EXEMPT_KINDS: frozenset[CapabilityKind] = frozenset(
    {CapabilityKind.MANAGEMENT_HOOK}
)

#: Persona effect metadata key marking a mandatory-per-segment contribution.
HARD_EFFECT_METADATA_KEY = "required_per_segment"


def is_hard_contribution(metadata: Any) -> bool:
    """Return whether a contribution declares itself mandatory.

    A ``hard`` contribution is exempt only from *soft* failure handling: it may
    not be dropped by a timeout or by a ``best_effort`` context skip. It is NOT
    exempt from admission - global activation, ownership, ``plugin_set`` and the
    per-session disabled list still apply, so a user who disables the plugin
    stops receiving the mandatory contract.
    """
    return isinstance(metadata, dict) and (
        metadata.get(HARD_EFFECT_METADATA_KEY) is True
    )

AdmissionReason = Literal[
    "enabled",
    "owner_missing",
    "reserved",
    "plugin_inactive",
    "plugin_not_in_plugin_set",
    "session_disabled",
    "configured_disabled",
    "not_interaction_capability",
]


@dataclass(frozen=True, slots=True)
class CapabilityRef:
    """Identifies one plugin capability for admission."""

    kind: CapabilityKind
    owner_module_path: str | None = None
    owner_plugin_name: str | None = None
    plugin_id: str | None = None
    item_name: str | None = None


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    """The result of one admission check."""

    allowed: bool
    reason: AdmissionReason
    kind: CapabilityKind
    owner_module_path: str | None = None
    owner_plugin_name: str | None = None

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.allowed


@dataclass(slots=True)
class PluginAdmissionSnapshot:
    """Frozen admission state for one Interaction turn.

    Resolving once per turn keeps every consumer consistent, stops each registry
    from re-reading configuration, and prevents a mid-turn plugin reload from
    changing the answer between two capability lookups.
    """

    plugin_set: tuple[str, ...] | None = None
    disabled_plugins: frozenset[str] = frozenset()
    enabled_plugins: frozenset[str] = frozenset()
    session_id: str = ""
    #: Frozen activation state keyed by owner module path, so every capability of
    #: one plugin gets the same answer for the whole turn.
    owner_states: dict[str, _OwnerState] = field(default_factory=dict)
    decisions: dict[str, AdmissionDecision] = field(default_factory=dict)
    configuration_snapshot: dict[str, Any] = field(default_factory=dict)

    def decision_for(self, ref: CapabilityRef) -> AdmissionDecision:
        key = _ref_key(ref)
        cached = self.decisions.get(key)
        if cached is not None:
            return cached
        decision = resolve_capability_admission(event=None, ref=ref, snapshot=self)
        self.decisions[key] = decision
        return decision

    def allows(self, ref: CapabilityRef) -> bool:
        return self.decision_for(ref).allowed


def _ref_key(ref: CapabilityRef) -> str:
    return "|".join(
        (
            ref.kind.value,
            ref.owner_module_path or "",
            ref.owner_plugin_name or "",
            ref.item_name or "",
        )
    )


def _owner_metadata(owner_module_path: str | None):
    if not isinstance(owner_module_path, str) or not owner_module_path:
        return None
    # Imported lazily: ``astrbot.core.star`` pulls in ``star.context``, which
    # imports this module, so a module-level import would be circular.
    from astrbot.core.star.star import star_map

    direct = star_map.get(owner_module_path)
    if direct is not None:
        return direct
    for candidate_path, metadata in star_map.items():
        if owner_module_path == candidate_path or owner_module_path.startswith(
            f"{candidate_path}."
        ):
            return metadata
    return None


def resolve_capability_admission(
    *,
    event: Any = None,
    ref: CapabilityRef,
    snapshot: PluginAdmissionSnapshot | None = None,
) -> AdmissionDecision:
    """Decide whether one capability may take effect.

    ``snapshot`` carries the frozen per-turn state. When it is omitted the
    decision is computed from live state, which is only appropriate for
    non-Interaction paths and diagnostics.
    """
    kind = ref.kind
    base = {
        "kind": kind,
        "owner_module_path": ref.owner_module_path,
        "owner_plugin_name": ref.owner_plugin_name,
    }

    if kind not in INTERACTION_CAPABILITY_KINDS:
        # Process-level kinds are not resolved per turn.
        return AdmissionDecision(
            allowed=True,
            reason="not_interaction_capability",
            **base,
        )

    owner = _resolve_owner_state(ref.owner_module_path, snapshot)
    if owner is None:
        # No owning plugin metadata: treat as a system capability, matching the
        # historical behaviour of the registries.
        return AdmissionDecision(allowed=True, reason="owner_missing", **base)

    if owner.reserved:
        return AdmissionDecision(allowed=True, reason="reserved", **base)

    if not owner.activated:
        return AdmissionDecision(allowed=False, reason="plugin_inactive", **base)

    plugin_name = owner.plugin_name
    if not plugin_name:
        # An unnamed plugin cannot be selected by name anywhere; keep the
        # historical permissive behaviour rather than silently dropping it.
        return AdmissionDecision(allowed=True, reason="enabled", **base)

    plugin_set = snapshot.plugin_set if snapshot is not None else _resolve_plugin_set(event)
    if plugin_set is not None and plugin_name not in plugin_set:
        return AdmissionDecision(
            allowed=False,
            reason="plugin_not_in_plugin_set",
            **base,
        )

    if kind in SESSION_EXEMPT_KINDS:
        return AdmissionDecision(allowed=True, reason="enabled", **base)

    disabled = (
        snapshot.disabled_plugins
        if snapshot is not None
        else _resolve_session_disabled(event)
    )
    if plugin_name in disabled:
        return AdmissionDecision(allowed=False, reason="session_disabled", **base)

    return AdmissionDecision(allowed=True, reason="enabled", **base)


@dataclass(frozen=True, slots=True)
class _OwnerState:
    """Frozen view of one owning plugin's admission-relevant metadata."""

    plugin_name: str | None
    activated: bool
    reserved: bool


def _resolve_owner_state(
    owner_module_path: str | None,
    snapshot: PluginAdmissionSnapshot | None,
) -> _OwnerState | None:
    """Resolve the owning plugin's activation state.

    With a snapshot the state is read from the turn's frozen view, so a mid-turn
    reload/disable cannot make two capabilities of the same plugin disagree
    within one turn.

    A snapshot that carries no frozen registry (constructed directly rather than
    by :func:`build_plugin_admission_snapshot`) falls back to live metadata, so a
    partial snapshot degrades to the previous behaviour instead of making every
    owner look missing and admitting everything.
    """
    if snapshot is not None and snapshot.owner_states:
        return _frozen_owner_state(snapshot.owner_states, owner_module_path)

    metadata = _owner_metadata(owner_module_path)
    if metadata is None:
        return None
    return _OwnerState(
        plugin_name=getattr(metadata, "name", None),
        activated=bool(getattr(metadata, "activated", True)),
        reserved=bool(getattr(metadata, "reserved", False)),
    )


def _frozen_owner_state(
    owner_states: dict[str, _OwnerState],
    owner_module_path: str | None,
) -> _OwnerState | None:
    """Resolve an owner against the frozen registry using the live rule.

    Mirrors :func:`_owner_metadata`, including its module-prefix match. An exact
    dict lookup alone would miss capabilities whose recorded owner is a
    descendant of the registered plugin module, and would then admit them as
    ``owner_missing`` - bypassing ``plugin_set`` and the session disable list.
    """
    if not isinstance(owner_module_path, str) or not owner_module_path:
        return None
    direct = owner_states.get(owner_module_path)
    if direct is not None:
        return direct
    for candidate_path, state in owner_states.items():
        if owner_module_path.startswith(f"{candidate_path}."):
            return state
    return None


def resolve_event_plugins_name(runtime_config: Any) -> list[str] | None:
    """Derive ``event.plugins_name`` from a runtime config's ``plugin_set``.

    Single source of truth for the plugin whitelist rule, so pipeline events and
    internally constructed events (proactive output, runtime observations)
    resolve the same plugin set:

    * missing / ``["*"]`` -> ``None`` meaning "no restriction";
    * an explicit list (including the empty list) is used verbatim, so ``[]``
      denies every ordinary plugin.
    """
    plugin_set: Any = None
    getter = getattr(runtime_config, "get", None)
    if callable(getter):
        plugin_set = getter("plugin_set", None)
    if plugin_set is None or not isinstance(plugin_set, list):
        return None
    if plugin_set == ["*"]:
        return None
    return [str(name) for name in plugin_set]


def _resolve_plugin_set(event: Any) -> tuple[str, ...] | None:
    """Return the plugin whitelist.

    ``None`` means "no restriction" (every plugin allowed). A tuple - including
    the **empty** tuple - is an actual whitelist, so ``()`` denies every ordinary
    plugin. This mirrors the original semantics in
    ``star_handler.get_handlers_by_event_type``, where ``plugin_set == []``
    ("use no plugins") excludes everything. Turning ``[]`` into ``None`` here
    would let capabilities bypass an intentionally empty whitelist.
    """
    if event is None:
        return None
    plugins_name = getattr(event, "plugins_name", None)
    if plugins_name is None:
        return None
    if isinstance(plugins_name, str):
        # A bare string would otherwise be iterated character by character.
        names = (plugins_name,)
    else:
        try:
            names = tuple(str(name) for name in plugins_name)
        except TypeError:
            # Not iterable: keep the historical permissive behaviour rather than
            # silently denying every plugin.
            return None
    if names == ("*",):
        return None
    return names


def _resolve_session_disabled(event: Any) -> frozenset[str]:
    """Read the session plugin config from the event's frozen config snapshot."""
    if event is None:
        return frozenset()
    get_extra = getattr(event, "get_extra", None)
    if not callable(get_extra):
        return frozenset()
    extra = get_extra("_session_plugin_config", None)
    if isinstance(extra, dict):
        return frozenset(str(name) for name in extra.get("disabled_plugins", []) or [])
    return frozenset()


async def build_plugin_admission_snapshot(
    *,
    event: Any,
    plugin_context: Any = None,
) -> PluginAdmissionSnapshot:
    """Resolve and freeze admission state for the current Interaction turn."""
    del plugin_context  # reserved for future per-capability declarations
    plugin_set = _resolve_plugin_set(event)

    session_id = ""
    disabled: frozenset[str] = frozenset()
    enabled: frozenset[str] = frozenset()
    raw_session_id = getattr(event, "unified_msg_origin", None)
    if isinstance(raw_session_id, str) and raw_session_id:
        session_id = raw_session_id
        try:
            from astrbot.core import sp

            session_plugin_config = await sp.get_async(
                scope="umo",
                scope_id=session_id,
                key="session_plugin_config",
                default={},
            )
            if isinstance(session_plugin_config, dict):
                session_config = session_plugin_config.get(session_id, {})
                if isinstance(session_config, dict):
                    disabled = frozenset(
                        str(name)
                        for name in session_config.get("disabled_plugins", []) or []
                    )
                    enabled = frozenset(
                        str(name)
                        for name in session_config.get("enabled_plugins", []) or []
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to read session plugin config for admission: session=%s error=%s",
                session_id,
                exc,
                exc_info=True,
            )

    snapshot = PluginAdmissionSnapshot(
        plugin_set=plugin_set,
        disabled_plugins=disabled,
        enabled_plugins=enabled,
        session_id=session_id,
        owner_states=_freeze_owner_states(),
    )
    # The turn state is the single source of truth; the event extra is only a
    # read-through projection kept for consumers that have no turn state.
    set_extra = getattr(event, "set_extra", None)
    if callable(set_extra):
        set_extra(PLUGIN_ADMISSION_SNAPSHOT_EXTRA_KEY, snapshot)
    return snapshot


def _freeze_owner_states() -> dict[str, _OwnerState]:
    """Snapshot activation state for every registered plugin.

    Freezing the whole registry once is cheaper than resolving per capability and
    guarantees that every capability of one plugin agrees for the whole turn.
    """
    from astrbot.core.star.star import star_map

    frozen: dict[str, _OwnerState] = {}
    for module_path, metadata in star_map.items():
        if not isinstance(module_path, str) or not module_path:
            continue
        frozen[module_path] = _OwnerState(
            plugin_name=getattr(metadata, "name", None),
            activated=bool(getattr(metadata, "activated", True)),
            reserved=bool(getattr(metadata, "reserved", False)),
        )
    return frozen


def get_plugin_admission_snapshot(event: Any) -> PluginAdmissionSnapshot | None:
    """Return the frozen snapshot for this event, if one was built.

    The turn state is authoritative; the event extra is a compatibility
    projection for consumers that run without a turn (and would otherwise
    observe a different snapshot than the turn's own consumers).
    """
    try:
        from astrbot.core.interaction.turn_state import (
            get_interaction_turn_plugin_admission,
        )
    except Exception:  # noqa: BLE001 - import cycle safety
        get_interaction_turn_plugin_admission = None

    if get_interaction_turn_plugin_admission is not None:
        try:
            snapshot = get_interaction_turn_plugin_admission(event)
        except Exception:  # noqa: BLE001
            snapshot = None
        if snapshot is not None:
            return snapshot

    get_extra = getattr(event, "get_extra", None)
    if not callable(get_extra):
        return None
    snapshot = get_extra(PLUGIN_ADMISSION_SNAPSHOT_EXTRA_KEY, None)
    return snapshot if isinstance(snapshot, PluginAdmissionSnapshot) else None


def capability_allowed(
    event: Any,
    *,
    kind: CapabilityKind,
    owner_module_path: str | None,
    owner_plugin_name: str | None = None,
    item_name: str | None = None,
) -> bool:
    """Convenience wrapper used by capability consumers."""
    snapshot = get_plugin_admission_snapshot(event)
    ref = CapabilityRef(
        kind=kind,
        owner_module_path=owner_module_path,
        owner_plugin_name=owner_plugin_name,
        item_name=item_name,
    )
    if snapshot is not None:
        return snapshot.allows(ref)
    return resolve_capability_admission(event=event, ref=ref).allowed


def call_capability_lister(
    lister: Any,
    *,
    event: Any,
) -> Any:
    """Call a ``Context.list_*`` capability accessor with the turn event.

    The admission-aware accessors take an optional ``event`` keyword. Older or
    third-party ``Context`` implementations - and lightweight test doubles - may
    not, so retry without the keyword. Only a keyword mismatch is retried: a
    ``TypeError`` raised inside the accessor itself still propagates.
    """
    try:
        return lister(event=event)
    except TypeError as exc:
        message = str(exc)
        if "event" not in message or "argument" not in message:
            raise
        return lister()


__all__ = [
    "AdmissionDecision",
    "AdmissionReason",
    "CapabilityKind",
    "CapabilityRef",
    "INTERACTION_CAPABILITY_KINDS",
    "PLUGIN_ADMISSION_SNAPSHOT_EXTRA_KEY",
    "PROCESS_CAPABILITY_KINDS",
    "PluginAdmissionSnapshot",
    "SESSION_EXEMPT_KINDS",
    "build_plugin_admission_snapshot",
    "call_capability_lister",
    "capability_allowed",
    "get_plugin_admission_snapshot",
    "is_hard_contribution",
    "resolve_capability_admission",
    "resolve_event_plugins_name",
]
