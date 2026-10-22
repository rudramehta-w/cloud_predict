# ADR 0002: Hexagonal Architecture for Domain Purity

## Status: Accepted (paths and dependency status corrected by ADR 0003)

## Context
Tightly coupling ML logic, database models, and S3 API calls makes the system untestable and fragile to infrastructure changes.

## Decision
Adopt Hexagonal Architecture (Ports and Adapters).
- **Domain Layer** (`src/domain/`): Pure Python. Contains `FinOpsEngine`, `FileMetadata` entities. Zero external dependencies.
- **Ports**: Python `typing.Protocol` defining interfaces for Storage and Repository.
- **Adapters** (`src/infrastructure/`): Implementations using `aioboto3` and SQLAlchemy.

## Consequences
- We can unit test the FinOps math without spinning up a database or MinIO.
- We can swap MinIO for AWS S3 by writing a new adapter without touching the domain logic.