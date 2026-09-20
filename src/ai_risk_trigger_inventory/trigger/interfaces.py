"""Interface for models that screen exact optimization calls."""

from typing import Protocol

from ..data.schemas import DecisionState


class RiskTrigger(Protocol):
    """A screening model that decides whether the exact oracle is needed."""

    def should_invoke_oracle(self, state: DecisionState) -> bool:
        """Return ``True`` when exact reoptimization should be invoked."""
        ...
