"""Decision-aware uncertainty architecture; training is intentionally absent."""

from __future__ import annotations

from typing import Self, Sequence

from .base import Context, DemandMatrix, UncertaintyEstimate, UncertaintyModel


class DecisionAwareUncertaintyModel(UncertaintyModel):
    """Future model calibrated against downstream decision consequences."""

    def fit(
        self,
        contexts: Sequence[Context],
        demand_observations: Sequence[DemandMatrix],
    ) -> Self:
        raise NotImplementedError("decision-aware uncertainty fitting is not implemented")

    def predict(self, context: Context) -> UncertaintyEstimate:
        raise NotImplementedError("decision-aware uncertainty prediction is not implemented")
