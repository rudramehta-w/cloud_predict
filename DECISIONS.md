# Decisions

Every ambiguity call made during the AFK implementation run, in the order the
ground rules define: spec.md, then ADRs, then GLOSSARY.md, then the simplest
option consistent with all three.

Format: (ticket #, choice, one-line reason).

- (chore, `.github/*` + `!.github/workflows/` in `.gitignore`, ticket 02 requires a committed GitHub Actions workflow while the graphify installer had ignored all of `.github`).
- (chore, dropped Pydantic from the standards, a Pydantic-based domain violates ADR 0002's zero-dependency domain; stdlib dataclasses are used instead).
- (chore, deleted `repomix-output.xml` from tracking, it is a generated dump that was swept into the previous commit and is already gone from disk).
- (chore, `docs/standards/python.md` amended to sync + sqlite3, ADRs 0003/0005 override the old async/SQLAlchemy/aioboto3 mandates).
- (02, ticket closed as `Status: done`, the local tracker defines no closing label and `done` is the clearest terminal state next to the five triage roles).
- (02, dev tooling in a PEP 735 `[dependency-groups]` group, keeps runtime dependencies empty per ADR 0003 while CI installs gates).
- (01, Objects created uniformly over days 0–120 of the 180-day span, every Object exists for the whole evaluation window so the calibration target is meaningful).
- (01, one global Rate scale solved by bisection to hit the 20% expected hit fraction, the spec fixes the band and the seed, this is the simplest calibration that hits it deterministically).
- (03, `object_id` is `bucket/key`, the glossary addresses an Object by bucket and key and every later table keys on that string).
- (03, `ingest --source minio` raises `UnsupportedSourceError` naming ticket 05, ticket 05 is ready-for-human and the failure must be explicit rather than a silent no-op).
- (03, `reset()` before every file ingest, a rerun must produce the same store rather than accumulate duplicates; CSV rows are the source of truth).
- (04, Storage_Savings = price delta × size_gb × horizon_days/30, ADR 0001 prices a move per iteration and the Scoring Horizon is the natural billing window).
- (04, Egress_Penalty = target tier retrieval price × size_gb, one expected retrieval per moved Object is the only reading consistent with a binary access label).
- (04, the persisted decision is the best colder candidate even when not recommended, the report needs the EV distribution, not only the positives).
- (04, probabilities are quantized to 12 decimal places before Decimal conversion, float estimation must not leak binary noise into money math).
- (04, default `as_of` is the latest access event, scoring is reproducible against the synthetic log rather than wall-clock dependent).
- (01 follow-up, generator creation window defaults to 2/3 of the span, otherwise the 30-day smoke run creates Objects after their own evaluation window).
- (05, no MinIO adapter attempted, the environment returns HTTP 410 on binaries and timing-out registries; the CLI fails explicitly and the CSV path carries the ladder).
- (07, cutoffs every 6 days from day 30 to span−30, features only read data ≤ cutoff, so overlapping label windows across cutoffs are not leakage).
- (07, train on the train split only and report validation metrics without a tuning loop, hyperparameters are fixed and documented rather than searched on a short timeline).
- (07, models persist as pickles, the fitted objects are plain Python and pickle is stdlib).
- (07, one-hot over the top-20 training prefixes with the vocabulary persisted beside the model, encoding must match exactly at inference).
- (07, Objects created after `as_of` fall back to the decay prior, otherwise scoring would silently drop them from the report).
- (06, the evaluation decision point is last_event − horizon, otherwise the held-out window is empty and every rung reports zero false evictions).
- (06, rung 1 archives to COLD, that is the "archive after 30 idle days" policy ADR 0001 criticises).
- (06, the report recomputes recommendations at the evaluation cut instead of reading `score`'s rows, keeps the artifact self-contained and consistent with the ladder it prints).
- (06, realised EV = realised savings − realised retrieval cost; predicted EV stays in the recommendations table).
- (08, the learned rungs use the same FinOpsEngine gate as the heuristic, the only variable between rungs 2–4 is the estimator).
- (08, the heuristic's predictive metrics are recomputed on the identical test cutoffs as the learned models, a PR-AUC comparison across different samples would be meaningless).
- (08, `report` requires trained models and fails fast with `EstimatorNotTrainedError`, a silently 3-rung ladder would misrepresent the benchmark).
- (08, the Oracle moves every Object not accessed in the window to COLD, that is the definition of perfect foresight and the EV ceiling).
- (09, charts are hand-rendered inline SVG with no chart library, the report must open offline and a CDN is the failure mode a defence room hits).
- (10, the view is an ASGI app started with `uvicorn cloudpredict.web.app:app`, not a sixth CLI subcommand, the spec fixes five stage commands and a view is not a stage).
- (10, the artifact path comes from `CLOUDPREDICT_REPORT` with a `data/report/report.json` default, one environment variable is enough for a read-only projection).
- (10, filter and sort are vanilla inline JavaScript with no CDN, the view must work offline exactly like the HTML report).
- (review, `train` reads the Scoring Horizon from `[run] scoring_horizon_days` and the config loader rejects a mismatched `[generator] horizon_days`, one horizon must have one source of truth).
- (review, samples are purged so no label window crosses a split boundary; when H exceeds the validation width the validation split is empty and its metrics are null rather than optimistic).
- (review, the learned estimator always merges the decay fallback, an all-empty feature frame must not surface as a KeyError in `score`).
- (review, the report records the data seed from the generator manifest and the model seed from `metrics.json`, provenance must describe the data actually scored).
- (13, the Object row and its access events follow it to the new bucket, object identity currently includes the bucket; keying identity on the key alone is the production fix).
- (13, skipped Objects are not written to the ledger, idempotent re-runs must not duplicate ledger rows).
- (13, an ETag mismatch discards the destination copy and keeps the source, a wrong copy must never become the surviving version).
- (13, apply takes its inventory from the live hot bucket and the DB only for access history, the bucket is the source of truth for what exists).
- (13, re-seeding deletes stale cold copies and DB rows for the planned keys, otherwise the demo cannot restart from a clean slate).
- (14, SSE is delivered over a POST read with fetch streaming instead of EventSource, EventSource cannot send POST).
- (14, one process-wide lock serialises seed and apply, concurrent mutations would race the buckets and the ledger).
- (14, re-seeding clears the ledger rows for its keys, a demo restart must not show savings for Objects that are back in hot).
- (14, the "moved" card counts ledger rows, the visible table only shows the eight most recent).
