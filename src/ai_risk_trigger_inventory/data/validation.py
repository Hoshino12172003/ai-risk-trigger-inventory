"""Minimal validation helpers for decision-state inputs."""

from dataclasses import fields

from .schemas import DecisionState


FORBIDDEN_INPUT_FIELDS = frozenset({"objective", "RI", "runtime", "iterations"})


def decision_state_field_names() -> frozenset[str]:
    """Return the immutable set of fields accepted by ``DecisionState``."""

    return frozenset(field.name for field in fields(DecisionState))


def has_post_solve_leakage() -> bool:
    """Report whether the current input contract exposes forbidden outcomes."""

    return bool(decision_state_field_names() & FORBIDDEN_INPUT_FIELDS)
