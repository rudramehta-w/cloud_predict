"""Execution engine: copy → verify → delete, with a durable ledger.

Invariants:

- the source is only deleted after the destination copy's ETag matches the source;
- a failed copy leaves the source untouched;
- a failed delete after a verified copy is recorded as ``needs-cleanup`` and the
  run continues;
- Objects already in ``cold`` are skipped, so re-runs never copy twice.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from cloudpredict.domain.ev import FinOpsEngine, TierPrice
from cloudpredict.domain.exceptions import EstimatorNotTrainedError, StorageError
from cloudpredict.domain.models import MigrationRecord, StorageObject, Tier
from cloudpredict.domain.ports import AccessEstimatorPort, RepositoryPort, StorageExecutionPort
from cloudpredict.infrastructure.config import Config, load_config
from cloudpredict.infrastructure.estimators import build_estimator
from cloudpredict.infrastructure.silo_storage import SiloStorage
from cloudpredict.infrastructure.sqlite_repository import SqliteRepository

MOVED = "moved"
SKIPPED = "skipped"
FAILED = "failed"
NEEDS_CLEANUP = "needs-cleanup"
PLANNED = "planned"
MONEY = Decimal("0.01")
DEFAULT_MODELS_DIR = Path("data/models")
DEFAULT_DB = Path("data/cloudpredict.db")


@dataclass(frozen=True, slots=True)
class MovePlan:
    key: str
    size_bytes: int
    probability: Decimal
    expected_value: Decimal
    savings: Decimal


@dataclass(frozen=True, slots=True)
class MoveResult:
    key: str
    status: str
    size_bytes: int
    savings: Decimal
    etag: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ApplyProgress:
    index: int
    total: int
    key: str
    size_bytes: int
    status: str
    message: str


ProgressCallback = Callable[[ApplyProgress], None]


def format_megabytes(size_bytes: int) -> str:
    return f"{size_bytes / 1024**2:.0f}MB"


class ApplyEngine:
    """Synchronous executor; every side effect is recorded in the ledger."""

    def __init__(
        self,
        storage: StorageExecutionPort,
        repository: RepositoryPort,
        prices: Mapping[Tier, TierPrice],
        *,
        min_ev_margin: Decimal = Decimal("0"),
        horizon_days: int = 30,
        from_bucket: str = "hot",
        to_bucket: str = "cold",
    ) -> None:
        self._storage = storage
        self._repository = repository
        self._from = from_bucket
        self._to = to_bucket
        self._finops = FinOpsEngine(prices, min_ev_margin, horizon_days)

    def plan_moves(
        self, objects: Sequence[StorageObject], probabilities: Mapping[str, Decimal]
    ) -> list[MovePlan]:
        plans: list[MovePlan] = []
        for obj in objects:
            decision = self._finops.best_move(obj, probabilities[obj.object_id])
            if decision is None:
                continue
            plans.append(
                MovePlan(
                    key=obj.key,
                    size_bytes=obj.size_bytes,
                    probability=decision.access_probability,
                    expected_value=decision.expected_value,
                    savings=decision.storage_savings,
                )
            )
        plans.sort(key=lambda plan: plan.expected_value, reverse=True)
        return plans

    def execute(
        self,
        plans: Sequence[MovePlan],
        *,
        dry_run: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> list[MoveResult]:
        results: list[MoveResult] = []
        total = len(plans)
        for index, plan in enumerate(plans, start=1):
            result = (
                MoveResult(plan.key, PLANNED, plan.size_bytes, plan.savings, detail="dry-run")
                if dry_run
                else self._execute_one(plan)
            )
            results.append(result)
            if on_progress is not None:
                on_progress(
                    ApplyProgress(
                        index=index,
                        total=total,
                        key=plan.key,
                        size_bytes=plan.size_bytes,
                        status=result.status,
                        message=self._message(plan, result),
                    )
                )
        return results

    # -- single object ---------------------------------------------------

    def _execute_one(self, plan: MovePlan) -> MoveResult:
        source = self._storage.head(self._from, plan.key)
        destination = self._storage.head(self._to, plan.key)

        if destination is not None:
            if source is not None and source.etag == destination.etag:
                try:
                    self._storage.delete_object(self._from, plan.key)
                except StorageError as error:
                    self._repository.move_object(self._from, self._to, plan.key, Tier.COLD.value)
                    detail = f"source cleanup failed: {error}"
                    self._record(plan, NEEDS_CLEANUP, detail, destination.etag)
                    return MoveResult(
                        plan.key,
                        NEEDS_CLEANUP,
                        plan.size_bytes,
                        plan.savings,
                        etag=destination.etag,
                        detail=detail,
                    )
                self._repository.move_object(self._from, self._to, plan.key, Tier.COLD.value)
                self._record(plan, MOVED, "completed an interrupted migration", destination.etag)
                return MoveResult(
                    plan.key,
                    MOVED,
                    plan.size_bytes,
                    plan.savings,
                    etag=destination.etag,
                    detail="completed an interrupted migration",
                )
            self._repository.move_object(self._from, self._to, plan.key, Tier.COLD.value)
            return MoveResult(
                plan.key,
                SKIPPED,
                plan.size_bytes,
                plan.savings,
                etag=destination.etag,
                detail="already in cold",
            )

        if source is None:
            detail = "missing from source and destination"
            self._record(plan, FAILED, detail, None)
            return MoveResult(plan.key, FAILED, plan.size_bytes, plan.savings, detail=detail)

        try:
            destination_etag = self._storage.copy_object(self._from, plan.key, self._to)
        except StorageError as error:
            detail = f"copy failed: {error}"
            self._record(plan, FAILED, detail, None)
            return MoveResult(plan.key, FAILED, plan.size_bytes, plan.savings, detail=detail)

        if destination_etag != source.etag:
            with contextlib.suppress(StorageError):
                self._storage.delete_object(self._to, plan.key)
            detail = "ETag mismatch after copy; destination copy discarded"
            self._record(plan, FAILED, detail, destination_etag)
            return MoveResult(
                plan.key,
                FAILED,
                plan.size_bytes,
                plan.savings,
                etag=destination_etag,
                detail=detail,
            )

        try:
            self._storage.delete_object(self._from, plan.key)
        except StorageError as error:
            self._repository.move_object(self._from, self._to, plan.key, Tier.COLD.value)
            detail = f"copy verified but source delete failed: {error}"
            self._record(plan, NEEDS_CLEANUP, detail, destination_etag)
            return MoveResult(
                plan.key,
                NEEDS_CLEANUP,
                plan.size_bytes,
                plan.savings,
                etag=destination_etag,
                detail=detail,
            )

        self._repository.move_object(self._from, self._to, plan.key, Tier.COLD.value)
        self._record(plan, MOVED, "", destination_etag)
        return MoveResult(plan.key, MOVED, plan.size_bytes, plan.savings, etag=destination_etag)

    # -- bookkeeping -----------------------------------------------------

    def _record(self, plan: MovePlan, status: str, detail: str, etag: str | None) -> None:
        self._repository.append_migration(
            MigrationRecord(
                object_key=plan.key,
                from_bucket=self._from,
                to_bucket=self._to,
                size_bytes=plan.size_bytes,
                etag=etag,
                savings_usd=plan.savings,
                ev_usd=plan.expected_value,
                status=status,
                detail=detail,
                moved_at=datetime.now(UTC),
            )
        )

    def _message(self, plan: MovePlan, result: MoveResult) -> str:
        size = format_megabytes(plan.size_bytes)
        if result.status == MOVED:
            return f"Moving {plan.key} ({size}) from {self._from} to {self._to}... done"
        if result.status == PLANNED:
            return f"Would move {plan.key} ({size}) from {self._from} to {self._to}"
        if result.status == SKIPPED:
            return f"Skipping {plan.key}: already in {self._to}"
        if result.status == NEEDS_CLEANUP:
            return f"Copied {plan.key} but removing it from {self._from} failed"
        return f"Failed {plan.key}: {result.detail}"


def summarize(results: Sequence[MoveResult], estimator: str, dry_run: bool) -> dict[str, object]:
    counts = {MOVED: 0, SKIPPED: 0, FAILED: 0, NEEDS_CLEANUP: 0, PLANNED: 0}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    completed = [result for result in results if result.status in (MOVED, NEEDS_CLEANUP)]
    savings = sum((result.savings for result in completed), Decimal("0"))
    return {
        "plans": len(results),
        "moved": counts[MOVED],
        "skipped": counts[SKIPPED],
        "failed": counts[FAILED],
        "needs_cleanup": counts[NEEDS_CLEANUP],
        "planned": counts[PLANNED],
        "bytes_moved": sum(result.size_bytes for result in completed),
        "savings_usd": str(savings.quantize(MONEY)),
        "estimator": estimator,
        "dry_run": dry_run,
    }


def resolve_estimator(name: str, config: Config, models_dir: Path) -> AccessEstimatorPort:
    """``auto`` prefers a trained XGBoost model and falls back to the heuristic."""
    if name == "heuristic":
        return build_estimator("heuristic", config)
    if name in {"xgboost", "random-forest"}:
        return build_estimator(name, config, models_dir)
    if (models_dir / "xgboost.pkl").exists():
        try:
            return build_estimator("xgboost", config, models_dir)
        except EstimatorNotTrainedError:
            pass
    return build_estimator("heuristic", config)


def run_apply(
    config_path: Path = Path("config/cloudpredict.toml"),
    db_path: Path = DEFAULT_DB,
    models_dir: Path = DEFAULT_MODELS_DIR,
    *,
    objects: Sequence[StorageObject] | None = None,
    dry_run: bool = False,
    estimator_name: str = "auto",
    on_progress: ProgressCallback | None = None,
) -> dict[str, object]:
    """Score the live hot bucket and execute every recommendation."""
    config = load_config(config_path)
    storage = SiloStorage(
        endpoint_url=config.backend.endpoint_url,
        region=config.backend.region,
        bucket_tiers={
            config.backend.hot_bucket: Tier.HOT,
            config.backend.cold_bucket: Tier.COLD,
        },
    )
    repository = SqliteRepository(db_path)
    try:
        repository.initialize()
        if objects is None:
            try:
                objects = storage.list_objects(config.backend.hot_bucket)
            except Exception:
                objects = []
            if not objects:
                objects = [o for o in repository.list_objects() if o.tier == Tier.HOT]
                if objects and not dry_run:
                    dry_run = True
        events = repository.list_access_events()
        as_of = max((event.accessed_at for event in events), default=datetime.now(UTC))
        estimator = resolve_estimator(estimator_name, config, models_dir)
        probabilities = estimator.estimate(objects, events, as_of)
        engine = ApplyEngine(
            storage,
            repository,
            config.tiers,
            min_ev_margin=config.min_ev_margin,
            horizon_days=config.scoring_horizon_days,
            from_bucket=config.backend.hot_bucket,
            to_bucket=config.backend.cold_bucket,
        )
        plans = engine.plan_moves(objects, probabilities)
        results = engine.execute(plans, dry_run=dry_run, on_progress=on_progress)
        summary = summarize(results, estimator.name, dry_run)
        summary["scanned"] = len(objects)
        return summary
    finally:
        repository.close()
