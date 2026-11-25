# Controlled Fault Generalization — Report

Validation phase. Not an autonomy phase. Brain stayed at **LEVEL 2 — INVESTIGATE**,
`SHADOW=ON`, `LEVEL 3=OFF`, `SANDBOX_ENABLED=FALSE`, no continuous live LLM, no paid Fable.

The question this phase asked was not "did Brain find something" but: when the underlying
fault class changes, does Brain still get the signal, promote the right candidate, group the
right root, avoid inventing unrelated incidents, and know when it has no evidence.

---

## A. Fresh baseline

| | Brain-related | Global |
|---|---|---|
| Baseline (phase start) | 445 passed / 0 failed | 547 passed / 14 known failed / 6 known collection errors |
| Final (phase end) | 553 passed / 0 failed | 654 passed / 14 known failed / 6 known collection errors |

New regressions: **ZERO**. The 14 failures and 6 collection errors are the documented
pre-existing set (`test_crypto_refresh`, `test_edge_birth_panel`, `test_edge_gate`,
`test_search_persistence`; collection errors in `test_crypto_schedule`, `test_desktop_spawn`,
`test_gpu_stats_no_console`, `test_integrity_regressions`, `test_market_clock`, `test_orchestra`).

## B. Frozen policy revision

`DISCOVERY_POLICY_G1` (`brain/discovery/policy_g1.py`), frozen before any fault was injected:

```
config_hash                 9ec484d3b7ef36a9
scoring_hash                04f2ec81fc26e408   (byte-identical to DISCOVERY_CALIBRATION_V2 v2.1)
score_version               discovery-score-v2
fingerprint_version         rootfp-v1
pending_policy_revision     v2.1
signal_inventory_revision   signal-inventory-2026-09-03
observation_schema_version  qhos-observation-v2
replay_contract_version     qhos-replay-contract-v1
artifact_schema_version     qhos-fault-matrix-v1
repo_sha                    5ec6e2e4d4d4
```

Scoring was not tuned during the matrix. The `G1.1` proposal exists but is
`PROPOSED_NOT_ACTIVE` and inert unless entered through an explicit context manager.

## C. Fault harness architecture

`brain/discovery/faults.py`. Dev-only, explicitly enabled, bounded, reversible, isolated,
audited, deterministic. It mutates an in-memory snapshot through overlay functions — no
process is killed, no disk filled, no file deleted, no credential or firewall touched.
It refuses production targets and production ports outright, and `assert_no_manifest_leakage`
fails the run if any manifest identity field reaches the snapshot, so Discovery cannot read
the answer out of the question.

## D. Production isolation proof

Production `:8000` before and after: **pid 23028, start 2026-09-03T16:34:18** — unchanged.
No production restart, no config change, no OMS/risk/trade interaction. No leftover dev
listeners on 8001/8010–8014. `filesystem_writes=0`, `network_calls=0`, `writes=0` on every run.
Authority invariants at end: `change_requests=0`, `sandbox_runs=0`, `production_writes=0`,
`trading_actions=0`, `autonomy_changes=0`.

## E–G. Fault inventory and cohorts

15 classes × 2 independent manifestations = **30 manifests**; 15 development, 15 holdout.
The holdout manifestations were authored but not consulted while building the detectors.

| Cohort | n | Signal | Candidate | Family | Exact | Investigated | Precision | Noise escape |
|---|---|---|---|---|---|---|---|---|
| development | 15 | 1.0 | 1.0 | 1.0 | 1.0 | 0.857 | 1.0 | 0.0 |
| holdout | 15 | 1.0 | 0.929 | 0.929 | 0.929 | 0.714 | 1.0 | 0.0 |

The holdout gap is a single case (F13b) discussed below.

## H. Signal coverage matrix (per fault, before any aggregate)

`sig`/`cand`/`fam`/`exact`/`inv` are booleans; `c_obs`/`c_cand` are cycles to first
observation and to candidate; `eff` is linked secondary effects.

| fault | cohort | class | verdict | sig | cand | fam | exact | inv | c_obs | c_cand | prec | roots | eff |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F01a | dev | MISSING_DEPENDENCY | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1.0 | 1 | 0 |
| F01b | holdout | MISSING_DEPENDENCY | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1.0 | 1 | 0 |
| F02a | dev | STALE_RUNTIME_REVISION | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 0 | 1 | 1 | 1.0 | 1 | 0 |
| F02b | holdout | STALE_RUNTIME_REVISION | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 0 | 1 | 1 | 1.0 | 1 | 0 |
| F03a | dev | CONNECTION_REFUSED | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1.0 | 1 | 0 |
| F03b | holdout | CONNECTION_REFUSED | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 0 | 1 | 1 | 1.0 | 1 | 0 |
| F04a | dev | REQUEST_TIMEOUT | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1.0 | 1 | 0 |
| F04b | holdout | REQUEST_TIMEOUT | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1.0 | 2 | 0 |
| F05a | dev | DATA_SCHEMA_MISMATCH | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1.0 | 1 | 0 |
| F05b | holdout | DATA_SCHEMA_MISMATCH | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1.0 | 1 | 0 |
| F06a | dev | DATA_QUALITY_ANOMALY | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1.0 | 1 | 0 |
| F06b | holdout | DATA_QUALITY_ANOMALY | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1.0 | 1 | 0 |
| F07a | dev | TELEMETRY_STALE_OR_MISLEADING | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 1 | 1 | **3** | 1.0 | 1 | 0 |
| F07b | holdout | TELEMETRY_STALE_OR_MISLEADING | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1.0 | 1 | 0 |
| F08a | dev | HEALTH_DEGRADATION | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 0 | 1 | 1 | 1.0 | 1 | 0 |
| F08b | holdout | HEALTH_DEGRADATION | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 0 | 1 | 1 | 1.0 | 1 | 0 |
| F09a | dev | UNKNOWN_PENDING_WITH_REAL_FAILURE | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1.0 | 1 | 1 |
| F09b | holdout | UNKNOWN_PENDING_WITH_REAL_FAILURE | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1.0 | 1 | 1 |
| F10a | dev | HIGH_FREQUENCY_BENIGN_WARNING | SUPPRESSED (correct) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1.0 | 0 | 0 |
| F10b | holdout | HIGH_FREQUENCY_BENIGN_WARNING | SUPPRESSED (correct) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1.0 | 0 | 0 |
| F11a | dev | PROVIDER_DEGRADED | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1.0 | 1 | 0 |
| F11b | holdout | PROVIDER_DEGRADED | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1.0 | 1 | 0 |
| F12a | dev | QUEUE_BACKLOG | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1.0 | 2 | 0 |
| F12b | holdout | QUEUE_BACKLOG | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1.0 | 2 | 0 |
| F13a | dev | TEST_REGRESSION | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1.0 | 1 | 0 |
| F13b | holdout | TEST_REGRESSION | **MISSED** | 1 | 0 | 0 | 0 | 0 | 1 | – | 1.0 | 0 | 0 |
| F14a | dev | MULTI_COMPONENT_SHARED_ROOT | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1.0 | 1 | 0 |
| F14b | holdout | MULTI_COMPONENT_SHARED_ROOT | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1.0 | 1 | 0 |
| F15a | dev | MULTIPLE_SIMILAR_DISTINCT_ROOTS | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1.0 | 2 | 0 |
| F15b | holdout | MULTIPLE_SIMILAR_DISTINCT_ROOTS | DETECTED_CORRECT_ROOT | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1.0 | 2 | 0 |

Signal source coverage: `error_log` 23/23, `registry` 5/5, `attestation` 2/2, `queue` 2/2,
`stream` 2/2, `metrics` 1/1 — all 1.0. Control arm: **0 background candidates**.

## I–L. Recall (denominator 28; the two suppression cases are excluded, not counted as misses)

| Metric | Value |
|---|---|
| Signal Recall | **1.0** (28/28) |
| Candidate Recall | **0.9643** (27/28) |
| Root-Family Recall | **0.9643** (27/28) |
| Exact Root Recall | **0.9643** (27/28) |
| Investigation rate | 0.7857 (22/28) |

## M–Q. Precision and dedup

| Metric | Value |
|---|---|
| Candidate Precision | **1.0** |
| Noise Escape | **0.0** |
| Duplicate Escape | 0.0882 |
| Over-Dedup Rate | 0.0 |
| Under-Dedup Rate | 0.0 |
| Suppression breaches | 0 |

34 candidates total: 29 fault roots, 5 fault symptoms, **0 unrelated**. No fault run produced
a candidate about something the fault did not touch.

## R–S. Time to detection

Measured in ingest cycles, because wall-clock here reflects interpreter speed rather than
deployment latency (mean 1.6 ms to candidate — not a meaningful figure to quote).

- Cycles to observation: max 1, mean 1.0 (every fault was visible on its first cycle)
- Cycles to candidate: max 3, mean 1.074
- Slower than one cycle: **F07a only** (3 cycles) — a stale-heartbeat fault has to be observed
  repeatedly before staleness is distinguishable from a slow cycle. That is the correct shape.

## T–X. Clustering, secondary effects, inflation

| Metric | Value |
|---|---|
| Root Cluster Precision | **1.0** |
| Root Cluster Recall | 0.9643 |
| Unique Root Inflation | 0.0 on all single-root cases |

`brain/discovery/consolidation.py` labels candidates `PRIMARY_ROOT_CANDIDATE` /
`SECONDARY_EFFECT` using the relationship vocabulary `SAME_ROOT`, `LIKELY_DERIVED_EFFECT`,
`TEMPORALLY_CORRELATED`, `INDEPENDENT`, `UNKNOWN`. **`CAUSES` is never asserted.** Missing
timestamps do not manufacture a link; generic components are excluded from linking so a
shared bland name cannot create a relationship; symmetric temporal correlations are reported
once per pair.

- **F14a/F14b** (one root, four components): 4 IncidentOccurrences → **1** principal
  PotentialIncident. `root_incidents=1`, no inflation.
- **F09a/F09b**: the ccxt-style root plus the ingestion-pending consequence are kept as
  1 root + 1 linked secondary effect rather than two independent incidents. The linked
  effect is **not** counted as noise and does **not** inflate the unique root count — both
  numbers are reported separately.
- **F15a/F15b** (similar messages, different causes): stayed **2 distinct roots**. Over-consolidation
  protection holds in the direction that matters.

## Y–Z. UNKNOWN_PENDING and the `pending_duration_s=0.0` finding

Preserved as a finding under frozen G1 rather than quietly patched.

| Probe | Setup | G1 behaviour | Expected |
|---|---|---|---|
| A | single cycle, severity high, no independent evidence | **not promoted**; reason `no_lifecycle_telemetry_duration_not_evidence` | correct |
| B | single cycle + concrete independent failure evidence | promoted, `error_evidence_during_pending`, score 0.408, **severity high at `pending_duration_s=0.0`** | promotion correct, severity is the weakness |
| C | multiple cycles + downstream degradation | promoted and stays justified across cycles | correct |

The weakness is narrow and now precisely stated: G1 is right to promote on independent
evidence at zero elapsed duration (probe A proves duration alone is not treated as evidence),
but reporting that first-cycle promotion as `severity=high` overstates temporal maturity.
`G1_FINDING_03` records this. G1.1 reports it as `medium` after `MATURITY_MIN_CYCLES_FOR_HIGH=2`,
derived from cycles observed and independent evidence rather than an arbitrary seconds constant.

## AA–AB. Runtime attestation and KI-03

`brain/discovery/attestation.py` makes runtime attestation a first-class Observation source
(`RUNTIME_REVISION_MATCH`, `STALE_RUNTIME`, `NOT_ATTESTABLE`, `CONFIG_REVISION_MISMATCH`,
`BRAIN_ROUTE_SCHEMA_MISMATCH`), registered in the signal inventory and wired through
`observations_from_snapshot`.

Context is preserved as §13 requires: a stale dev runtime during normal worktree development
is **informational, low severity**; a production runtime stale against its expected deployment
revision is **high severity and candidate-eligible**. `RUNTIME_REVISION_MATCH` is in
`NOISE_KINDS`, so the healthy heartbeat does not become a stream of candidates.

**KI-03 is closed.** The previous phase recorded `NO_SIGNAL` for interpreter/runtime revision.
Both stale-runtime manifestations (F02a dev, F02b holdout) now produce a signal and the exact
correct root, with attestation source coverage 1.0. Known-issue recall on that gap moved from
missed to detected.

## AC–AE. Observability gaps and telemetry quality

- Fault classes with **NO_SIGNAL: none**. No `OBSERVABILITY_GAP` was recorded, and therefore
  no `OBSERVABILITY_IMPROVEMENT_CANDIDATE` was generated this phase.
- Telemetry semantic validity: F07a/F07b are the interesting cases — the registry reports
  healthy while heartbeats are frozen. Discovery reached the correct root from the conflict
  rather than trusting the registry's self-report, though F07a needed 3 cycles.
- **Known limitation** (`G1_FINDING_05`): the internal `intel` projection caps observations at
  40 rows per cycle. It does not change any decision, but it means high-volume benign warnings
  report a different `suppressed` count on replay (see AG).

## AF–AH. Replay schema, fidelity, and historical artifacts

`brain/discovery/fidelity.py` defines `observation_schema_version=qhos-observation-v2`,
`replay_contract_version=qhos-replay-contract-v1`, and the decision-relevant field set —
including `state` and `lifecycle_known`, the two fields whose absence made the previous soaks
unfaithful.

| Metric | Value |
|---|---|
| Replay Fidelity Rate | **0.9333** (28/30 faithful) |
| Live vs Replay Candidate Agreement | **1.0** |
| Live vs Replay Score Agreement | **1.0** |
| Live vs Replay Root Agreement | **1.0** |
| Diverged on | `suppressed` count, 2 artifacts (F10a, F10b) |

Every decision-relevant output replays exactly. The two non-faithful artifacts diverge only on
the *count* of suppressed rows, caused by the 40-row projection cap, and each is labeled
`replay_faithful=false` **with the reason stated**. No silent divergence.

Historical A/B/C soak artifacts remain `replay_faithful=false` and were **not** re-scored or
mutated. A test asserts they stay labeled that way.

## AI. Deterministic results by fault — failure taxonomy

- `NOT_A_DISCOVERY_FAILURE`: 24 faults
- `INVESTIGATION_MISS`: **F02a, F02b, F03b, F08a, F08b** — candidate promoted with the correct
  exact root, but scored below the investigation threshold. Detection worked; escalation did not.
- `SCORING_MISS`: **F13b** — see below
- `NO_SIGNAL` / `NORMALIZATION_MISS` / `SUPPRESSION_MISS` / `CLUSTERING_MISS` /
  `CONSOLIDATION_MISS`: **none**

**F13b, the one genuine miss, honestly:** the manifest injects a collection error naming
`tests/test_market_clock.py`, which is in `KNOWN_COLLECTION_ERRORS`. Baseline suppression
correctly classified it as `KNOWN_BASELINE`, so the expected candidate never appeared. By the
letter of the manifest that is a `SCORING_MISS`; on adjudication it is **correct engine
behaviour against a badly authored manifest**, and it incidentally demonstrates that baseline
suppression generalizes. The manifest was deliberately **left unchanged** — editing a holdout
after seeing its result would destroy it as evidence (`G1_MANIFEST_DEFECT_01`).

**Two measurement defects were found and fixed in the harness, not the engine:**
`G1_MEASUREMENT_DEFECT_01` (the first matrix run evaluated only the final cycle's candidates,
under-counting five faults) and `G1_MEASUREMENT_DEFECT_02` (`root_title` reintroduced
component-name matching through the side door — the exact v1 defect §14 prohibits).

## G1 vs G1.1 (explicit before/after; G1 evidence preserved)

G1.1 is `PROPOSED_NOT_ACTIVE`, `config_hash=fed0e01c3d7a1918`, off by default.

| Metric | G1 | G1.1 | Δ |
|---|---|---|---|
| Signal Recall | 1.0 | 1.0 | 0 |
| Candidate Recall | 0.9643 | 0.9643 | 0 |
| Root-Family Recall | 0.9643 | 0.9286 | −0.0357 |
| Exact Root Recall | 0.9643 | 0.9286 | −0.0357 |
| Investigation rate | 0.7857 | **0.9286** | **+0.1429** |
| Candidate Precision | 1.0 | 1.0 | 0 |
| Root Cluster Precision | 1.0 | 0.963 | −0.037 |

G1.1 converts F02a/F02b/F08a/F08b from `INVESTIGATION_MISS` to investigated by granting half
root-signature credit to registry/attestation rows that assert a component state directly. The
apparent recall regression is **F03b only**: G1.1's dotted-exception extraction correctly parses
`socket.gaierror`, which produces a *better* fingerprint that no longer matches the expected
root family the manifest was written against. Adjudication: manifest artifact, not a true
regression. Both result sets are persisted; nothing was overwritten. **Recommendation: G1.1
remains a proposal pending a fault matrix authored against its fingerprinting.**

## AJ–AS. Selective paid Hybrid replay

Four cases selected for information value (§41), not for obvious deterministic detections:
F02b (uncertain evidence — the process is opaque about its own revision), F07b (telemetry
conflict), F12b (symptom vs cause — the queue is not failing, its consumers are), F14b
(multi-component shared root).

| fault | primary value | basis | out tok | latency | cost |
|---|---|---|---|---|---|
| F02b | ROOT_CAUSE_VALUE | differential alternatives against a *stated* deterministic root (overlap 0.22) | 1668 | 40.3 s | $0.0266 |
| F07b | **VERIFICATION_VALUE** | deterministic made no root claim **and the model's top hypothesis was ungrounded** | 1720 | 34.6 s | $0.0277 |
| F12b | ROOT_CAUSE_VALUE | supplied a grounded mechanism where deterministic had only a log title | 1973 | 41.8 s | $0.0316 |
| F14b | ROOT_CAUSE_VALUE | supplied a grounded mechanism where deterministic had only a log title | 2107 | 40.7 s | $0.0339 |

Totals: **4 calls, 2,569 input tokens, 7,468 output tokens, $0.1198, mean latency 39.4 s,
4/4 VALID, 0 fallbacks.** All four also earned `VERIFICATION_VALUE` and `UNCERTAINTY_VALUE`.

- **AL. Verification Value** is real, not awarded for volume: 5–7 verifications per case that
  the deterministic arm did not propose, each read-only, each ranked by expected information
  gain, targeting discriminating evidence (worker lease rejections, `pg_postmaster_start_time`,
  NTP/clock state).
- **AM. Root Cause Value** — awarded to 3 of 4, and only after fixing the classifier (below).
  F12b named worker-lease starvation as distinct from queue depth; F14b identified a single
  backing-service connection drop behind four components' symptoms.
- **AN. Cross Component Value**: not awarded. F14b reasoned about one shared dependency, not
  three-plus named subsystems.
- **AO. Negative Value**: not awarded, but 1–2 unsupported claims per case were flagged
  heuristically (`pgBouncer`, `NIC failover`, `CODE_REVISION`/`GIT_COMMIT` env keys) — plausible
  entities not present in the evidence sent. F07b's ungrounded top hypothesis cost it root credit.
- **MEMORY_VALUE** is reported as *unevaluable*: the sanitized packet carries only this root's
  window, so there is no prior incident to connect to. Stated rather than left silently unreachable.

**AT/AU. Provider output validity:** 4/4 VALID, 0 fallbacks, 0 counted as Hybrid success from
fallback. A first attempt at `max_tokens=1500` returned 4/4 `TRUNCATED` with `stop_reason=max_tokens`;
that artifact is preserved as `hybrid_replay_faultgen_cap1500_truncated.json`. Truncation was
correctly reported as `TRUNCATED` and **not** as a model failure or a Hybrid success — the
robustness fix from the previous phase held.

**A measurement defect in the value classifier, found and fixed (`G1_MEASUREMENT_DEFECT_03`):**
value was originally scored against the deterministic arm's `interpretation` field alone. For an
ERROR_CODE-tier candidate that field is just the log title, so overlap was trivially 0.0 and all
four cases were credited `ROOT_CAUSE_VALUE` for "diverging" from a conclusion the engine never
asserted — precisely the inflation §44 prohibits. Overlap is now measured against the whole
deterministic conclusion, the constant `uncertainty` disclaimer is excluded from the substance
test, and where the engine made no root claim the model must supply a mechanism **grounded in
the evidence that was sent**. Re-scoring the same paid outputs offline moved F07b from
`ROOT_CAUSE_VALUE` to `VERIFICATION_VALUE`. Prior labels are retained per row.

**AP–AS / §46 paid safety:** candidate allowlist, call budget (clamped to the allowlist), token
budget, real network timeout, session breaker reset, provider-health breaker, checkpoint/resume,
explicit client close. `BRAIN_LLM_API_ENABLED` and `BRAIN_LLM_EXPLICIT_EVAL` were set for the
single paid command only and are `False` in the repository and in CI. A second defect surfaced
here (`G1_MEASUREMENT_DEFECT_04`): resume keyed "done" on row presence, so four
`provider_unavailable` rows from an ungated attempt were skipped on the gated rerun and reported
as zero calls at zero cost. Resume now only skips rows that actually reached the provider.

## AV–AW. UI / dev verification and attestation

No production restart, no dev UI restart this phase. The dev UI clickthrough demonstrated in the
previous phase (including `stale_runtime=true` rendering as informational) remains the standing
evidence; the new information is verified through artifacts and tests rather than a new dev
server. No Level 3 enable, no sandbox, no apply, no deploy, no trade, no merge, no push.

## AX. Source-control hygiene

- Tracked source changed vs HEAD: **11 files, +501 / −70**
- New source this phase: 11 Discovery modules, **3,340 lines**
- New tests this phase: `tests/test_brain_fault_generalization.py`, **1,011 lines, 107 tests**
- New scripts this phase: 5 files, **456 lines**
- Generated artifacts (all ignored, none tracked): `fault_matrix_g1*.json`,
  `g11_before_after.json`, `replay_fidelity_g1.json`, `hybrid_replay_faultgen*.json`
- `.qhos-brain/` is ignored and **zero** files under it are tracked
- **Fixed this phase:** `.pytest_tmp/` and `.pytest_cache/` were not ignored, so pytest scratch
  fixtures filled `git status` with hundreds of entries and hid real changes. Now ignored;
  `git status` went from several hundred entries to 69. A test asserts these rules stay in place.

## AY–BA. Tests and regressions

Brain-related **553 passed / 0 failed**. Global **654 passed / 14 known failed / 6 known
collection errors**. **New regressions: ZERO.**

Suites run: Fault Harness, Discovery, Calibration, Soak, Replay, Attestation, Provider
Validation, Hybrid Evaluation, Fable, Cognitive, Real Shadow, Proving Ground, Sandbox, Control
Plane, Language Contract, Committee, Global. CI makes no paid API calls and injects no
production faults — the paid path requires flags that are `False` by default.

## BB–BG. Status

| | |
|---|---|
| **REAL_DISCOVERY_VALUE** | **VALIDATED_ON_CONTROLLED_MULTI_CLASS_FAULTS** |
| **REAL_HYBRID_REPLAY_VALUE** | **CANDIDATE** |
| **FABLE_VALUE** | NO_MEASURED_VALUE (unchanged; no paid Fable run) |
| **LIVE_SHADOW_READINESS** | **CONTROLLED_GENERALIZATION_VALIDATED** |
| **LEVEL3_STATUS** | **OFF / NOT_READY** |
| **ARCHITECTURE_FREEZE_GATE** | **YES_FOR_LEVEL2_SHADOW** |

Discovery moves off CANDIDATE because generalization was measured across 15 fault classes and
30 manifestations, with a held-out cohort, under a policy frozen before the first injection:
signal recall 1.0, exact-root recall 0.9643, candidate precision 1.0, noise escape 0, zero
unrelated candidates, zero over- and under-dedup, and one adjudicated miss that turned out to
be correct suppression. This is explicitly **not** production-general validation — the faults
were synthetic snapshot overlays in a controlled harness.

Hybrid stays **CANDIDATE**, not validated. It produced real verification and root-cause value on
3 of 4 information-rich cases at $0.03 per case, but n=4, every case flagged at least one
unsupported claim, and the value measurement itself had to be corrected mid-analysis before the
numbers meant anything.

**ARCHITECTURE_FREEZE_GATE = YES_FOR_LEVEL2_SHADOW.** Observation normalization, Discovery
scoring/fingerprinting/clustering, consolidation, and the replay contract all held across
changing fault classes without per-fault special-casing, and Discovery never branches on fault
identity. Deterministic decision-relevant outputs replay exactly. The remaining defects are in
escalation thresholds and evaluation harnesses, not in the architecture. This says nothing about
Level 3 readiness.

## BH. Remaining evidence gaps

1. **Investigation escalation is the weak link, not detection.** 5 of 28 faults produced the
   correct exact root but scored below the investigation threshold. G1.1 addresses it; G1.1 is
   not validated.
2. **F13b was never really tested.** Its manifest collides with a documented baseline failure,
   so one holdout manifestation of TEST_REGRESSION has no clean result.
3. **The 40-row `intel` projection cap** keeps 2 of 30 artifacts from being fully faithful on
   suppression counts.
4. **Hybrid n=4.** Too small for a value verdict, and the classifier was corrected during the
   analysis it was being used for.
5. **All faults were snapshot overlays.** No real process was degraded; timing was measured in
   cycles, not deployment latency.
6. **G1.1's recall regression is unresolved on paper** — adjudicated as a manifest artifact, but
   not re-measured against manifests authored for G1.1's fingerprinting.
7. **MEMORY_VALUE and CROSS_COMPONENT_VALUE remain unmeasured**, the former structurally
   unreachable with a single-root evidence packet.

## BI. Recommended next phase

Marked **RECOMMENDED**, not retroactively authoritative.

1. Re-author F13b against a fault not in the known-baseline set, and author a G1.1-native
   manifest set, then re-run the before/after so G1.1's recall delta is measured rather than
   adjudicated.
2. Investigation-threshold calibration as its own phase, since escalation is now the identified
   gap. Treat "correct root, not escalated" as the primary target metric.
3. Lift the 40-row projection cap or version the projection, to get replay fidelity to 1.0.
4. Expand selective Hybrid to roughly 12–15 information-rich cases with the corrected classifier
   frozen beforehand, to move Hybrid off CANDIDATE with a real n.
5. Controlled faults against a genuinely running isolated dev process, to convert cycle-based
   latency into wall-clock detection latency.
6. Do not enable Level 3. Do not enable sandbox. Autonomy readiness was not evaluated and no
   evidence here speaks to it.
