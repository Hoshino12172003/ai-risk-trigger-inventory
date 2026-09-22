"""Common data and behavior contracts for uncertainty estimation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping, Self, Sequence


DemandMatrix = Sequence[Sequence[float]]
Context = Mapping[str, Any]


@dataclass(frozen=True)
class UncertaintyEstimate:
    """Demand center and nonnegative deviation consumed by the frozen oracle."""

    demand_center: DemandMatrix
    demand_deviation: DemandMatrix
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        center_shape = _matrix_shape(self.demand_center, "demand_center")
        deviation_shape = _matrix_shape(
            self.demand_deviation, "demand_deviation"
        )
        if center_shape != deviation_shape:
            raise ValueError("demand center and deviation shapes must match")
        if any(
            float(value) < 0
            for row in self.demand_deviation
            for value in row
        ):
            raise ValueError("demand deviation must be nonnegative")


class UncertaintyModel(ABC):
    """Interface shared by static, contextual, and decision-aware methods."""

    @abstractmethod
    def fit(
        self,
        contexts: Sequence[Context],
        demand_observations: Sequence[DemandMatrix],
    ) -> Self:
        """Fit method-specific state and return the model."""

    @abstractmethod
    def predict(self, context: Context) -> UncertaintyEstimate:
        """Return an oracle-neutral uncertainty estimate for one context."""


def _matrix_shape(matrix: DemandMatrix, name: str) -> tuple[int, int]:
    rows = len(matrix)
    if rows == 0:
        raise ValueError(f"{name} must not be empty")
    columns = len(matrix[0])
    if columns == 0 or any(len(row) != columns for row in matrix):
        raise ValueError(f"{name} must be a nonempty rectangular matrix")
    return rows, columns
