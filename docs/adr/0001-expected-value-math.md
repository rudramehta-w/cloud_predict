# ADR 0001: Expected Value (EV) Cost-Penalty Matrix

## Status: Accepted

## Context
Naive tiering systems move files based on time (e.g., 30 days). This causes "false evictions" where a file is archived but immediately needed, incurring high retrieval (egress) costs that wipe out months of storage savings.

## Decision
We will implement an Expected Value (EV) mathematical model for every tiering decision.
EV = (P(inactive) * Storage_Savings) - (P(access) * Egress_Penalty)
A file is ONLY migrated if EV > 0.

## Consequences
- **Positive**: Financial decisions are auditable and mathematically bounded.
- **Technical**: Financial math MUST use Python's `decimal.Decimal` to prevent floating-point rounding errors.