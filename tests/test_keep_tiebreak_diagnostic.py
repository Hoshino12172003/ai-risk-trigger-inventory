from pathlib import Path
import sys


EXPERIMENT = Path(__file__).resolve().parents[1] / "experiments" / "decision_sensitivity_pilot"
sys.path.insert(0, str(EXPERIMENT))

from diagnose_keep_tiebreak import _absolute_range, exact_robust_recourse_cost


def test_exact_robust_recourse_cost_uses_block_optima() -> None:
    scenarios = [(), ((0, 0),), ((0, 1),)]
    block_optima = {
        (0, ()): 10.0,
        (0, (0,)): 15.0,
        (1, ()): 20.0,
        (1, (0,)): 22.0,
    }

    assert exact_robust_recourse_cost(scenarios, block_optima, 2) == 35.0


def test_absolute_range_excludes_zero_and_preserves_scale() -> None:
    assert _absolute_range([0.0, -0.25, 8.0]) == {
        "min_nonzero": 0.25,
        "max": 8.0,
        "nonzero_count": 2,
    }
