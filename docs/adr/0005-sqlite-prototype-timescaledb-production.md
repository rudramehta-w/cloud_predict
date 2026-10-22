# ADR 0005: SQLite for the Prototype, TimescaleDB/PostgreSQL as the Production Path

## Status: Accepted

## Context
ADR 0002 lists SQLAlchemy as a planned adapter, and the approved synopsis names TimescaleDB. The prototype scores thousands of Objects, where an ORM plus a time-series cluster add cost without benefit.

## Decision
- The prototype persists through Python's stdlib `sqlite3`, behind the Repository Port.
- SQLAlchemy is deferred, not rejected.
- The production scaling path is documented as TimescaleDB/PostgreSQL. No Timescale-specific code is written for the prototype.

## Consequences
- The prototype carries zero persistence dependencies, and the Port keeps a later swap mechanical.
- Documentation must state the prototype/production split, so the synopsis's TimescaleDB mention is accounted for rather than contradicted.
