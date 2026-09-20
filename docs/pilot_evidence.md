# Solver-Free Pilot Evidence

The Paper 1 repository contains a completed solver-free pilot. It is recorded
here only as proof-of-concept evidence, not as a formal Paper 2 experiment.

- Pilot commit: `92c79a836f5d233f783f0ccd00f2f88c87bbc282`
- Feature stability audit commit: `1c605b7d626eb8b8aa8fcc8e508a67a77aa4a80c`
- 136 unique states
- 65 material / 71 non-material
- 8-fold leave-one-case-out
- Best full Gradient Boosting balanced accuracy: 0.721
- Control-only balanced accuracy: 0.573
- Gamma-threshold balanced accuracy: 0.696
- Full-model false negatives: 18
- Gamma-threshold false negatives: 2
- Pilot classification: `WEAK_SIGNAL`

Feature stability results:

- Gradient Boosting top-5 mean Jaccard: 0.550
- Logistic Regression top-5 mean Jaccard: 0.409
- Decision Tree top-5 mean Jaccard: 0.435
- Final stability classification: `PARTIALLY_STABLE_SIGNAL`

The pilot does not establish deployment-level validity.
It shows limited but nonzero structural predictive signal and motivates
expanding the number of independent empirical networks / decision contexts.
