"""Safe boundary for the frozen Paper 2 optimization oracle."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from .provenance import OptimizationOracleProvenance


class OracleIntegrationBlocked(RuntimeError):
    """Raised when no verified frozen-oracle adapter is available."""


class DecisionSensitivityOracle(Protocol):
    """Contract for KEEP evaluation and frozen-oracle reoptimization."""

    provenance: OptimizationOracleProvenance
    solver_calls: int

    def evaluate(self, decision_state: Mapping[str, object]) -> Mapping[str, object]:
        """Return auditable KEEP and REOPTIMIZE cost components."""
        ...


class BlockedOracleAdapter:
    """Non-executing adapter used until a safe integration is verified."""

    provenance = OptimizationOracleProvenance()
    solver_calls = 0

    def evaluate(self, decision_state: Mapping[str, object]) -> Mapping[str, object]:
        del decision_state
        raise OracleIntegrationBlocked(
            "BLOCKED: frozen oracle commit has no verified decision-sensitivity "
            "dispatch interface; no solver call was made"
        )


def calculate_decision_value(total_cost_keep: float, total_cost_reopt: float) -> float:
    """Return C_keep - C_reopt without adding reconfiguration friction again."""

    return float(total_cost_keep - total_cost_reopt)
