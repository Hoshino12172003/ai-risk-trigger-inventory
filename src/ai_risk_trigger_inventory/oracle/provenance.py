"""Read-only provenance for the external optimization oracle."""

from dataclasses import dataclass


@dataclass(frozen=True)
class OptimizationOracleProvenance:
    """Pinned identity of the Paper 2 oracle; this class performs no solve."""

    repository: str = "Hoshino12172003/budget-inventory-benders"
    commit_sha: str = "51aebd06edf8f5d6d124d0f3eebdbb901e63274f"
    model_name: str = "robust inventory reconfiguration model"
    algorithm_name: str = "PRB-Benders"
    frozen: bool = True
