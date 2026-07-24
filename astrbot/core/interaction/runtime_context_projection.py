"""Bounded structured projections for Runtime Observation facts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .observation_inbox import ObservationBatch

_MAX_OBSERVATIONS = 24
_MAX_MAPPING_ITEMS = 24
_MAX_SEQUENCE_ITEMS = 24
_MAX_STRING_LENGTH = 1200
_MAX_NESTING_DEPTH = 5


def project_observation_batch(batch: ObservationBatch) -> dict[str, Any]:
    """Return the bounded fact view shared by Policy and Core Planner."""
    observations = batch.observations[-_MAX_OBSERVATIONS:]
    return {
        "batch_id": batch.batch_id,
        "opened_at": batch.opened_at,
        "closed_at": batch.closed_at,
        "source_counts": project_runtime_value(batch.source_counts),
        "observation_count": len(batch.observations),
        "projected_observation_count": len(observations),
        "truncated": len(observations) != len(batch.observations),
        "observations": [
            {
                "observation_id": observation.observation_id,
                "kind": observation.kind,
                "source": observation.source,
                "occurred_at": observation.occurred_at,
                "expires_at": observation.expires_at,
                "correlation_id": observation.correlation_id,
                "payload": project_runtime_value(observation.payload),
            }
            for observation in observations
        ],
    }


def project_runtime_value(value: Any, *, depth: int = 0) -> Any:
    """Bound arbitrary immutable Observation values before prompt rendering."""
    if depth >= _MAX_NESTING_DEPTH:
        return "[nested value omitted]"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return (
            value
            if len(value) <= _MAX_STRING_LENGTH
            else f"{value[:_MAX_STRING_LENGTH]}..."
        )
    if isinstance(value, bytes):
        return f"[bytes:{len(value)}]"
    if isinstance(value, Mapping):
        return {
            str(key): project_runtime_value(item, depth=depth + 1)
            for key, item in list(value.items())[:_MAX_MAPPING_ITEMS]
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [
            project_runtime_value(item, depth=depth + 1)
            for item in list(value)[:_MAX_SEQUENCE_ITEMS]
        ]
    return str(value)[:_MAX_STRING_LENGTH]


__all__ = ["project_observation_batch", "project_runtime_value"]
