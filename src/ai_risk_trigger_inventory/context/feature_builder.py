"""Deterministic context-to-feature mapping without model training."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ContextFeatureBuilder:
    """Select an ordered numeric feature vector from a context record."""

    fields: tuple[str, ...]

    def build(self, context: Mapping[str, Any]) -> tuple[float, ...]:
        missing = [field for field in self.fields if field not in context]
        if missing:
            raise KeyError(f"context is missing fields: {missing}")
        return tuple(float(context[field]) for field in self.fields)
