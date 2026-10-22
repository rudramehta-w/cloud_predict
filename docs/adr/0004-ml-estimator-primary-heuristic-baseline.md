# ADR 0004: ML Estimator Primary, Heuristic Baseline for Comparison

## Status: Accepted

## Context
ADR 0001 needs an Access Probability for every Object. The project's academic premise — approved synopsis, supervisor sign-off, DPEC — commits to an ML classifier benchmarked against heuristic baselines. Real access history is not available, but a synthetic Zipfian access-log generator produces the training data.

## Decision
- The primary Access Probability estimator is a gradient-boosted classifier (XGBoost, with Random Forest available as the comparison model), trained on synthetic Zipfian access logs under a **temporal split**: train on earlier simulated events, evaluate on later ones.
- A decay-weighted frequency estimator ships alongside it as a **heuristic baseline**, implementing the same Port.
- The comparison between learned estimator and baseline, measured through the EV cost model, is a first-class deliverable — not a nice-to-have.

## Consequences
- `xgboost`/`scikit-learn` and their numeric stack become runtime dependencies. They live in the infrastructure layer, so the domain stays dependency-free.
- Both estimators are interchangeable behind one Port, so the evaluation harness treats them identically.
- The synthetic generator is a prerequisite deliverable, not test scaffolding.
