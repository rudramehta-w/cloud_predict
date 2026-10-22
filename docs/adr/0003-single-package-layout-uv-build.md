# ADR 0003: Single-Package Layout Under uv_build

## Status: Accepted

## Context
ADR 0002 describes the domain living at `src/domain/` with adapters using `aioboto3` and SQLAlchemy. The project builds with `uv_build`, which packages exactly one top-level directory under `src/` matching the project name — `src/cloudpredict/` — so a literal `src/domain/` would never be shipped. Neither `aioboto3` nor SQLAlchemy is present in the dependency set.

## Decision
All code lives inside the single package:

- `src/cloudpredict/domain/` — pure domain (`FinOpsEngine`, `Object` metadata), zero external dependencies
- `src/cloudpredict/domain/ports.py` — `typing.Protocol` ports for Storage and Repository
- `src/cloudpredict/infrastructure/` — adapters implementing the ports

`aioboto3` and SQLAlchemy remain **planned** adapters, not present dependencies. An adapter library is added only when its backend decision is made.

## Consequences
- The domain stays dependency-free without fighting the build backend.
- ADR 0002's path and dependency statements are corrected here rather than rewritten there.
