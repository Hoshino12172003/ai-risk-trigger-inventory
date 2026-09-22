# Contextual optimization notes

## Paper / repository

- Paper: Yash P. Patel, Sahana Rayan, and Ambuj Tewari, “Conformal Contextual
  Robust Optimization,” AISTATS 2024, PMLR 238:2485–2493.
- Paper page: <https://proceedings.mlr.press/v238/patel24a.html>.
- Official research repository: <https://github.com/yashpatel5400/csi>.

## Core method

Conformal-Predict-Then-Optimize constructs informative context-dependent
prediction regions with distribution-free coverage, then uses those regions
in a downstream robust decision problem.

## What we borrow

- The predict-then-optimize separation.
- Context-dependent uncertainty outputs with explicit calibration metadata.
- Evaluation of coverage and decision quality as different quantities.

## Paper-3 module mapping

- `context/feature_builder.py`: context inputs.
- `uncertainty/contextual.py`: conditional demand center/deviation prediction.
- `evaluation/calibration_metrics.py`: future coverage diagnostics.
- `optimization/paper2_adapter.py`: frozen-oracle handoff.

## What we do not borrow

- No conditional generative model, conformal algorithm, black-box optimizer,
  visualization implementation, or repository source.
- No PyTorch dependency and no assertion of conformal coverage in the current
  feasibility pilot.

## Dependencies

The research implementation uses a Python machine-learning stack for
conditional generative modeling and experiment-specific optimization. The
repository points newer users to a separate packaged project. Neither stack is
introduced by this architecture-only change.

## License note

The `yashpatel5400/csi` repository reports the MIT License. This repository
copies no code and would require a separate dependency/license review before
future integration.

## Citation information

```bibtex
@inproceedings{patel2024conformal,
  title={Conformal Contextual Robust Optimization},
  author={Patel, Yash P. and Rayan, Sahana and Tewari, Ambuj},
  booktitle={Proceedings of the 27th International Conference on Artificial Intelligence and Statistics},
  series={Proceedings of Machine Learning Research},
  volume={238},
  pages={2485--2493},
  year={2024}
}
```
