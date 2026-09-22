# LROpt / decision-focused uncertainty-set notes

## Paper / repository

- Paper: Irina Wang, Bart Van Parys, and Bartolomeo Stellato, “Learning
  Decision-Focused Uncertainty Sets in Robust Optimization,” arXiv:2305.19225.
- Historical repository URL: <https://github.com/stellatogrp/lropt>.
- Current repository destination: <https://github.com/stellatogrp/cvxro>.
  The historical URL now redirects to CVXRO; this distinction must be retained
  when reproducing older LROpt results.

## Core method

Learn contextual uncertainty-set parameters using downstream robust decisions
and constraint-risk guarantees, rather than optimizing prediction error alone.

## What we borrow

- A clean boundary between context, uncertainty-set prediction, and downstream
  optimization.
- The idea that decision consequences may inform uncertainty calibration.
- A common output contract independent of the learning implementation.

## Paper-3 module mapping

- `uncertainty/decision_aware.py`: future decision-aware estimator.
- `uncertainty/base.py`: common `UncertaintyEstimate` contract.
- `optimization/paper2_adapter.py`: explicit downstream-oracle boundary.
- `evaluation/decision_metrics.py`: decision-consequence reporting.

## What we do not borrow

- No source code, differentiable solver layer, robust counterpart, uncertainty
  set implementation, or training loop.
- No CVXRO dependency or replacement of the frozen Paper-2 model.
- No claim that differentiating through Paper 2 is currently supported.

## Dependencies

The current redirected CVXRO repository is built on CVXPY and external CVXPY
solvers. Historical LROpt work additionally concerns differentiable learning
around optimization. None of those packages are introduced here.

## License note

The current `stellatogrp/cvxro` repository reports Apache-2.0. This note is not
a license grant for historical revisions; verify the exact revision before any
future reuse. This repository currently reuses no third-party code.

## Citation information

```bibtex
@article{wang2023learning,
  title={Learning Decision-Focused Uncertainty Sets in Robust Optimization},
  author={Wang, Irina and Van Parys, Bart and Stellato, Bartolomeo},
  journal={arXiv preprint arXiv:2305.19225},
  year={2023}
}
```
