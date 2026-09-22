"""Contextual uncertainty-model architecture; training is intentionally absent."""

from __future__ import annotations

from typing import Self, Sequence

from .base import Context, DemandMatrix, UncertaintyEstimate, UncertaintyModel


class ContextualUncertaintyModel(UncertaintyModel):
    """Future model mapping observed context to conditional demand uncertainty."""

    def fit(
        self,
        contexts: Sequence[Context],
        demand_observations: Sequence[DemandMatrix],
    ) -> Self:
        raise NotImplementedError("contextual uncertainty fitting is not implemented")

    def predict(self, context: Context) -> UncertaintyEstimate:
        raise NotImplementedError("contextual uncertainty prediction is not implemented")
