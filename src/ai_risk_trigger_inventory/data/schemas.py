"""Schema definitions for pre-solve states and oracle-produced labels."""

from dataclasses import dataclass, field
from typing import Mapping


FeatureMap = Mapping[str, float]


@dataclass(frozen=True)
class DecisionState:
    """Information observable before deciding whether to call the oracle."""

    domain_id: str
    network_id: str
    time_id: str
    beta: float
    Gamma: float
    lambda_R: float
    demand_features: FeatureMap = field(default_factory=dict)
    inventory_features: FeatureMap = field(default_factory=dict)
    network_features: FeatureMap = field(default_factory=dict)
    cost_features: FeatureMap = field(default_factory=dict)


@dataclass(frozen=True)
class OracleLabel:
    """Outcomes supplied by the frozen optimizer, never pre-solve features."""

    material_reconfiguration: bool
    optimization_value: float
    oracle_status: str
