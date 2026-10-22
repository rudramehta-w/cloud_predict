# Python Coding Standards

## Tooling & Formatting
- **Python Version**: 3.12+
- **Linter/Formatter**: `ruff` (check and format).
- **Type Checker**: `mypy --strict`. Every function must have explicit return types and parameter types.

## Architecture Rules
1. **Dependencies**: Use `uv` for package management.
2. **Async/Await**: All I/O bound operations (Database, S3, API calls) MUST be asynchronous.
3. **Pydantic**: Use Pydantic V2 `BaseModel` for all data transfer objects. Use `model_config = ConfigDict(strict=True)`.
4. **SQLAlchemy**: Use version 2.0 async style. Use `Mapped` and `mapped_column`.
5. **S3 Client**: Use `aioboto3` with `async with` context managers.

## Testing
- **Framework**: `pytest` and `pytest-asyncio`.
- **Property Testing**: Use `hypothesis` for testing mathematical domain logic (like the EV calculator).
- **Fixtures**: Use `pytest` fixtures for database sessions and mocked S3 clients. Never hit real MinIO/AWS in unit tests.

## Error Handling
- Define custom exceptions in `src/domain/exceptions.py`.
- Never swallow exceptions with bare `except:` blocks.