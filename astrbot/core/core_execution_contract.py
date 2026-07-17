"""Shared prompt contract for delegated Core execution."""

CORE_PERSONA_COORDINATION_INSTRUCTION = (
    "The Persona layer has an independent fast-response branch for this turn. "
    "Do not produce greetings, acknowledgements, progress filler, or restate the "
    "user's request. Execute the delegated task directly and return only "
    "substantive result material. The Persona layer will produce the final "
    "user-visible wording."
)


__all__ = ["CORE_PERSONA_COORDINATION_INSTRUCTION"]
