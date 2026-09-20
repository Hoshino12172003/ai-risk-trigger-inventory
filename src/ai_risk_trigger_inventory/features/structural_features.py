"""Simple transformations of pre-solve structural feature groups."""

from ..data.schemas import DecisionState


def flatten_structural_features(state: DecisionState) -> dict[str, float]:
    """Flatten numeric pre-solve values without introducing oracle outcomes."""

    features = {"beta": state.beta, "Gamma": state.Gamma, "lambda_R": state.lambda_R}
    for group_name in (
        "demand_features",
        "inventory_features",
        "network_features",
        "cost_features",
    ):
        group = getattr(state, group_name)
        features.update({f"{group_name}.{name}": value for name, value in group.items()})
    return features
