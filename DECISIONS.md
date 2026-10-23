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
