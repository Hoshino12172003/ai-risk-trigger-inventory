"""Small, model-agnostic metrics for evaluating risk-trigger policies."""

from collections.abc import Sequence

def balanced_accuracy(y_true: Sequence[bool], y_pred: Sequence[bool]) -> float:
    """Return the mean recall across material and non-material classes."""

    pairs = list(zip(y_true, y_pred, strict=True))
    recalls = []
    for class_value in (False, True):
        class_pairs = [pair for pair in pairs if bool(pair[0]) is class_value]
        if class_pairs:
            correct = sum(bool(actual) == bool(predicted) for actual, predicted in class_pairs)
            recalls.append(correct / len(class_pairs))
    return sum(recalls) / len(recalls) if recalls else 0.0


def false_negative_rate(y_true: Sequence[bool], y_pred: Sequence[bool]) -> float:
    """Return the share of material reconfigurations missed by the trigger."""

    positives = sum(bool(actual) for actual in y_true)
    if positives == 0:
        return 0.0
    false_negatives = sum(
        bool(actual) and not bool(predicted)
        for actual, predicted in zip(y_true, y_pred, strict=True)
    )
    return false_negatives / positives


def decision_loss(ai_trigger_policy_cost: float, exact_optimal_cost: float) -> float:
    """Return cost under AI-trigger policy minus exact optimal cost."""

    return ai_trigger_policy_cost - exact_optimal_cost


def exact_solve_reduction_rate(trigger_decisions: Sequence[bool]) -> float:
    """Return the fraction of states for which an exact solve is avoided."""

    if not trigger_decisions:
        return 0.0
    exact_solves = sum(bool(decision) for decision in trigger_decisions)
    return 1.0 - exact_solves / len(trigger_decisions)
