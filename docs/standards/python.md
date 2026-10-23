# Python Coding Standards

## Tooling & Formatting
- **Python Version**: 3.12+
- **Package manager**: `uv`.
- **Linter/Formatter**: `ruff` (check and format).
- **Type Checker**: `mypy --strict` inside `cloudpredict.domain`; standard settings elsewhere.

## Architecture Rules
1. **Layering**: `domain/` is pure Python with zero external dependencies (stdlib only). Everything that touches I/O lives in `infrastructure/` and implements a Port from `domain/ports.py` (ADR 0002, ADR 0003).
2. **Synchronous I/O**: v1 is fully synchronous. No `async`/`await` in the codebase; do not put `asyncio` on the call path.
3. **Persistence**: stdlib `sqlite3` behind `RepositoryPort` (ADR 0005). SQLAlchemy is a planned production adapter, not used in v1.
4. **Object storage**: the adapter library is chosen at ticket 05. No `aioboto3` in v1 (ADR 0003).
5. **Data transfer objects**: stdlib `dataclasses` (`frozen=True`, `slots=True`) inside the domain. Pydantic is not used in v1.
6. **Money**: all financial math uses `decimal.Decimal` (ADR 0001). Never compute money in `float`.
7. **CLI**: one module per stage under `cloudpredict/cli/`; each stage prints a one-line summary.

## Testing
- **Framework**: `pytest` (synchronous only).
- **Property Testing**: `hypothesis` for mathematical domain logic (EV calculator, decay estimator).
- **Fixtures**: pytest fixtures for the SQLite repository and in-memory fakes. Ports get contract tests that run against both the fake and the real adapter. Never hit cloud services in tests.
- **Model accuracy**: never asserted in CI. The ladder reports what it measures; CI carries sanity bands only.

## Error Handling
- Define custom exceptions in `src/cloudpredict/domain/exceptions.py`.
- Never swallow exceptions with bare `except:` blocks.
