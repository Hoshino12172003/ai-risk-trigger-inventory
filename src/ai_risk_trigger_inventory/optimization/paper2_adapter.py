"""Future boundary between uncertainty estimates and the frozen Paper-2 oracle."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping

from ..uncertainty.base import DemandMatrix, UncertaintyEstimate


@dataclass(frozen=True)
class Paper2DemandParameters:
    """Solver-neutral demand inputs for a future Paper-2 invocation."""

    demand_bar: DemandMatrix
    demand_hat: DemandMatrix
    metadata: Mapping[str, Any]

    @classmethod
    def from_estimate(cls, estimate: UncertaintyEstimate) -> "Paper2DemandParameters":
        return cls(
            demand_bar=estimate.demand_center,
            demand_hat=estimate.demand_deviation,
            metadata=estimate.metadata,
        )


class Paper2OptimizationAdapter(ABC):
    """Abstract future oracle boundary; it contains no solver implementation."""

    @abstractmethod
    def reconfigure(
        self,
        demand: Paper2DemandParameters,
        system_context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Invoke an externally configured frozen oracle implementation."""
