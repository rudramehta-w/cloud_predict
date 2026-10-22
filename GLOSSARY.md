# cloudPredict

cloudPredict scores stored objects and recommends when moving one to a cheaper storage tier is worth its retrieval cost.

## Language

**Object**:
A single stored item in a bucket, addressed by bucket and key; the unit cloudPredict scores and makes tier recommendations about.
_Avoid_: file, FileMetadata, blob

**Object Version**:
One stored version of an Object. When bucket versioning is enabled, every version — current or noncurrent — is scored as its own Object.
_Avoid_: revision, snapshot

**Scoring Horizon**:
The time span over which Access Probability is defined; access history is measured over an equal-length lookback.
_Avoid_: window, period

**Access Probability**:
The probability that an Object is read at least once during the Scoring Horizon.
_Avoid_: hit rate, popularity, hotness

**Inactivity Probability**:
One minus Access Probability over the same Scoring Horizon. The two are always complements and are never estimated independently.
_Avoid_: idle rate, coldness

**Storage Tier**:
A cost class an Object can be placed in — Hot, Warm, or Cold — mapped to a backend storage class by configuration.
_Avoid_: storage class (reserved for the backend's own term), lifecycle rule

**False Eviction**:
A tier move that is followed by enough reads that retrieval cost exceeds the savings the move produced.
_Avoid_: bad move, regret

**Oracle**:
A hypothetical estimator with perfect foresight of future accesses; the ceiling of achievable EV.
_Avoid_: ground truth, upper bound

**EV Capture**:
An estimator's total EV expressed as a fraction of the Oracle's total EV; the headline number of the benchmark.
_Avoid_: score, accuracy, uplift
