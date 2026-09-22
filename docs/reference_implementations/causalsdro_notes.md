# CausalSDRO notes

## Paper / repository

- Paper: Fenglin Zhang and Jie Wang, “Contextual Distributionally Robust
  Optimization with Causal and Continuous Structure: An Interpretable and
  Tractable Approach,” arXiv:2601.11016.
- Official repository: <https://github.com/Arthas819/CausalSDRO>.

## Core method

The work combines contextual DRO with causal and continuous structure and
evaluates contextual decision rules on newsvendor, inventory, and portfolio
problems.

## What we borrow

- Keep context construction distinct from uncertainty estimation.
- Preserve causal assumptions and provenance in metadata rather than hiding
  them in optimizer payloads.
- Compare static, contextual, and decision-aware methods through one contract.

## Paper-3 module mapping

- `context/feature_builder.py`: deterministic context assembly.
- `uncertainty/contextual.py`: context-dependent uncertainty estimates.
- `uncertainty/decision_aware.py`: future decision-sensitive extensions.
- `evaluation/calibration_metrics.py`: uncertainty-quality reporting.

## What we do not borrow

- No repository source, causal estimator, decision rule, DRO trainer, neural
  network, or optimization formulation.
- No causal interpretation is assigned to the Favorita pilot variables.
- No PyTorch, CUDA, or repository-specific Gurobi training code is introduced.

## Dependencies

The official README describes Python 3.8 experiments, PyTorch 2.0.1 with CUDA
11.8 for GPU work, and a Gurobi-based inventory inner optimizer, alongside its
research data/analysis stack. These dependencies are not adopted here.

## License note

The official repository reports the MIT License. Paper 3 currently copies no
code and treats the work only as a methodological reference.

## Citation information

```bibtex
@article{zhang2026contextual,
  title={Contextual Distributionally Robust Optimization with Causal and Continuous Structure: An Interpretable and Tractable Approach},
  author={Zhang, Fenglin and Wang, Jie},
  journal={arXiv preprint arXiv:2601.11016},
  year={2026}
}
```
