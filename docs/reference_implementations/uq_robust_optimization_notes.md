# Contextual robust optimization with UQ notes

## Paper / repository

- Paper: Egon Peršak and Miguel F. Anjos, “Contextual Robust Optimisation with
  Uncertainty Quantification,” CPAIOR 2023, LNCS 13884, pages 124–132.
- DOI: <https://doi.org/10.1007/978-3-031-33271-5_9>.
- Official experimental repository:
  <https://github.com/EgoPer/Contextual-Robust-Optimisation-with-UQ>.

## Core method

The paper connects supervised-learning UQ to contextual ambiguity sets. It
studies moment uncertainty from conditional covariance estimates and sampled
reference distributions from methods such as deep ensembles, Gaussian
processes, and Monte Carlo dropout.

## What we borrow

- Represent center and deviation as first-class outputs rather than burying
  uncertainty inside a solver.
- Keep calibration metrics separate from downstream decision metrics.
- Carry method, coverage, and provenance details in estimate metadata.

## Paper-3 module mapping

- `uncertainty/base.py`: center/deviation/metadata schema.
- `uncertainty/contextual.py`: future conditional UQ model.
- `evaluation/calibration_metrics.py`: calibration reporting.
- `optimization/paper2_adapter.py`: translate estimates to `demand_bar` and
  `demand_hat` without changing Paper 2.

## What we do not borrow

- No deep ensemble, Gaussian process, Monte Carlo dropout, Wasserstein DRO,
  portfolio model, or tuning algorithm.
- No deep learning, PyTorch, CVXRO, MOSEK, or third-party source code.

## Dependencies

The experimental repository contains method-specific predictive-model and
optimization scripts rather than a dependency-stable library interface. Its
ML/UQ/optimization environment is not adopted; Paper 3 retains only its
existing project dependencies.

## License note

The official repository reports the MIT License. No code or experimental asset
is copied into Paper 3.

## Citation information

```bibtex
@inproceedings{persak2023contextual,
  title={Contextual Robust Optimisation with Uncertainty Quantification},
  author={Peršak, Egon and Anjos, Miguel F.},
  booktitle={Integration of Constraint Programming, Artificial Intelligence, and Operations Research},
  series={Lecture Notes in Computer Science},
  volume={13884},
  pages={124--132},
  year={2023},
  doi={10.1007/978-3-031-33271-5_9}
}
```
