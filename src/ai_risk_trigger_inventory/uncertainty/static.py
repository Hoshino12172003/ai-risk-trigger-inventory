"""Static uncertainty-model architecture; estimation is intentionally absent."""

from __future__ import annotations

from typing import Self, Sequence

from .base import Context, DemandMatrix, UncertaintyEstimate, UncertaintyModel


class StaticUncertaintyModel(UncertaintyModel):
    """Future context-independent baseline uncertainty model."""

    def fit(
        self,
        contexts: Sequence[Context],
        demand_observations: Sequence[DemandMatrix],
    ) -> Self:
        raise NotImplementedError("static uncertainty fitting is not implemented")

    def predict(self, context: Context) -> UncertaintyEstimate:
        raise NotImplementedError("static uncertainty prediction is not implemented")
