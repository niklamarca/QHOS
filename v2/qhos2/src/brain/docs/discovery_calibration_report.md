# Real Discovery Calibration — phase record

Analysis: 2026-09-03. Repo revision `5ec6e2e`. Authority unchanged throughout:
Level 2 INVESTIGATE, Shadow ON, `SANDBOX_ENABLED=false`, Level 3 OFF.
Soak LLM OFF. Only the explicitly gated selective replay called a remote model.

## A. Fresh baseline → end

| Suite | Start (this phase) | End |
|---|---|---|
| Brain-related | 259 passed | **326 passed** |
| Global | 371 passed / 14 failed / 6 collection errors | **438 passed / 14 failed / 6 collection errors** |

Same 14 known failures (`test_crypto_refresh` ×4, `test_edge_birth_panel` ×1,
`test_edge_gate` ×2, `test_search_persistence` ×7) and the same 6 known
collection errors. **NEW regressions: ZERO.** The +67 Brain tests are new
coverage added by this phase.

## B. Files changed

New: `brain/discovery/fingerprint.py`, `brain/discovery/pending.py`,
`brain/discovery/telemetry.py`, `tests/test_brain_calibration.py`,
`scripts/calibration_replay.py`, `scripts/long_soak_once.py`,
`scripts/post_soak_review.py`, `scripts/hybrid_replay_paid.py`.

Modified: `brain/discovery/engine.py`, `observation.py`, `score.py`,
`incident.py`, `improvement.py`, `metrics.py`, `runner.py`, `soak.py`,
`brain/gateway.py`, `brain/runtime.py`, `dashboard/_hybrid.html`,
`brain/docs/signal_inventory.md`.

## C. Root fingerprint architecture

`rootfp-v1`. Deterministic, no LLM, no randomness. Pipeline: parse pipe-delimited
log lines → extract exception type, dependency, error code, stack signature →
scrub high-cardinality tokens (timestamps, UUIDs, symbols, prices, PIDs, request
IDs, hex, numerals) → normalize component to a family → hash a tier-specific key.

Tiers, most to least specific:

| Tier | Key | Collapses across |
|---|---|---|
| `DEPENDENCY` | exception + dependency name | symbols, exchanges, components |
| `ERROR_CODE` | component family + exception + code | symbols, timestamps |
| `EXCEPTION` | component family + exception + stack-or-skeleton | symbols, timestamps |
| `STATE` | component family + normalized state | timestamps |
| `MESSAGE` | kind + component family + skeleton | timestamps, symbols |
| `TELEMETRY` | systemic unknown-pending collapse | components |

When nothing concrete is extracted the key falls back to a structure-preserving
message skeleton, so unrelated messages never share a root.

## D. IncidentOccurrence architecture

`IncidentOccurrence` (`kind=incident_occurrence`, `candidate=False`) is an
evidence row: occurrence id, root id, observation id, component, instance,
source, timestamp, severity, summary. `PotentialIncident` gained `root_id`,
`root_title`, `root_tier`, `root_signature`, `occurrences`, `occurrence_count`,
`affected_instances`, `affected_components`, `sources`. Many occurrences support
one candidate; an occurrence is never a candidate.

## E. Multi-symbol dedup results

Recorded 30s soak replayed through the new engine:

| | Before | After |
|---|---|---|
| Candidates | 12 | **1** |
| ccxt candidates | 6 (one per symbol) | 1 root, 6 occurrences, 6 instances |
| ccxt score / rank | 0.397–0.500, outranked by pending | **0.639, top ranked** |
| Unique improvements | 11 | **2** |

## F. Over-dedup tests

Same component + three different exceptions (`ValueError`, `KeyError`,
`ZeroDivisionError`) → 3 roots, 3 candidates. Different dependencies (`ccxt` vs
`polars`) → 2 roots. Same exception in different components → 2 roots. 60 symbols
sharing one dependency → 1 candidate with 60 occurrences (candidate-storm test).

## G. Pending-state model

`PendingTracker` tracks `first_seen`, `last_seen`, `duration_s`,
`cycles_pending`, `expected_duration_s`, lifecycle, per component. Classes:
`EXPECTED_INITIALIZATION`, `TRANSIENT_PENDING`, `DEGRADED_PENDING`,
`STALE_PENDING`, `FAILED_PENDING`, `UNKNOWN_PENDING`. The first two are benign
and never promote.

**Measured semantics.** All 15 live modules report
`{status: pending, healthy: false, uptime: null, error_count: 0}` because
`core.registry` is unpopulated — `pending` is the endpoint's default, not a
lifecycle. Two rules follow:

1. No lifecycle evidence (`uptime is None and error_count == 0`) ⇒
   `UNKNOWN_PENDING`, not promotable. Elapsed time cannot age a static default
   into staleness. Real evidence (error count, dependent impact, missed SLA, or
   staleness once `uptime` exists) still promotes.
2. Five or more components sharing that shape for three or more cycles collapse
   into **one** systemic candidate (`root_tier=TELEMETRY`).

## H. Pending false-positive reduction

| | 30s soak (before) | 15m soak (after) |
|---|---|---|
| Pending observations | 78 | 2,236 |
| Pending candidates | 6 promoted, all reviewed noise | **0 per-component** |
| Systemic pending candidates | — | 1 (reviewed USEFUL) |

## I. Telemetry quality ledger

`TELEMETRY_QUALITY_LEDGER` over the 15m soak:

| Source | Observations | Share | Suppressed | Candidates | Reviewed useful | Duplicate rate |
|---|---|---|---|---|---|---|
| registry | 2,580 | 62% | 2,580 | 0 direct (1 systemic) | 1 | 0.00 |
| error_log | 1,038 | 25% | 1,014 | 4 | 1 | 0.98 |
| git | 519 | 13% | 519 | 0 | 0 | 0.99 |

Recommendations emitted: `CONSIDER_SAMPLING` (registry), `REVIEW_DEDUP_KEY`
(error_log, git). All carry `auto_applied=false`, `auto_disabled=false`,
`requires_human_review=true`. **No source is ever disabled automatically.**

## J. Score feature audit

`discovery-score-v2` returns per-feature `value`, `weight`, `contribution`, plus
`score_total`, `score_base`, `score_penalty`, `promotion_reason`. Weights sum to
1.0 and are asserted by test. Reviewed-candidate mean contributions
(`SCORE_FEATURE_AUDIT`, `auto_applied=false`): persistence 0.100, severity 0.090,
novelty 0.084, root_signature 0.070, root_occurrences 0.060, importance 0.055,
data_quality 0.042, temporal_maturity 0.034.

## K. Score before/after

| Feature | v1 | v2 | Rationale |
|---|---|---|---|
| frequency | 0.10 | **0.04** | one log line per symbol is one cause, not twenty |
| novelty | 0.20 | 0.14 | rebalance |
| severity | 0.20 | 0.18 | rebalance |
| importance | 0.12 | 0.10 | rebalance |
| recurrence | 0.08 | 0.06 | rebalance |
| data_quality | 0.08 | 0.06 | rebalance |
| root_signature | — | **0.14** | concrete exception/dependency extracted |
| root_occurrences | — | **0.06** | breadth across distinct instances |
| temporal_maturity | — | **0.04** | seen across more than one cycle |
| expected_state penalty | — | **−0.30** | benign startup is a state observation |

Effect on the two competing real signals:

| Candidate | v1 | v2 |
|---|---|---|
| ccxt missing dependency | 0.397–0.500 | **0.639–0.680** |
| storage pending (benign) | 0.457–0.517 | **0.246** (below 0.42 threshold) |

`model_confidence` is read and discarded; a test asserts identical scores for
0.01 and 0.99.

## L. Holdout calibration

Five holdout shapes plus an over-dedup guard, none of which were used to pick
the weights:

| Holdout | Observations | Candidates | Score |
|---|---|---|---|
| low-frequency real error | 1 | 1 | 0.444 |
| high-frequency benign warning | 40 | 0 | — |
| single severe exception | 1 | 1 | 0.584 |
| long pending, healthy dependency | 8 | 0 | — |
| many symbols, one dependency | 8 | 1 | 0.668 |
| same symbol, 3 unrelated exceptions | 3 | 3 | 0.494 each |

## M–T. Longer real soak

`REAL_SOAK = EXECUTED`. Session `soak_a10d67ae1966`, **900.0s (15m)**, 173
cycles, 5s interval. Trader was not restarted; `/api/status` and `/api/brain`
were both reachable, so no source was degraded.

| Metric | Value |
|---|---|
| Signals received / normalized | 4,137 |
| Noise suppressed | 2,583 |
| Exact duplicates suppressed | 1,530 |
| Root-collapsed occurrences | 20 |
| Pending observations suppressed | 2,236 |
| **Unique root candidates** | **2** |
| Unique improvement candidates | 2 |
| Investigations opened | 1 |
| Candidate rate | 0.14 / minute |
| Remote LLM calls during soak | **0** |
| Writes / ChangeRequests / SandboxRuns | 0 / 0 / 0 |
| Safety violations | 0 |

Noise taxonomy: `EXPECTED_STATE` 2,236, `DUPLICATE` 1,530, `HEARTBEAT` 344,
`LOW_VALUE` 3, everything else 0.

Candidates:

| Root | Tier | Score | Occurrences | Instances |
|---|---|---|---|---|
| missing dependency `ccxt` (ModuleNotFoundError) | DEPENDENCY | 0.680 | 6 | 6 |
| registry reports 13 modules pending with no lifecycle telemetry | TELEMETRY | 0.515 | 13 | 13 |

## U–X. Review dataset and precision

Reviewed evidence dataset, **not training truth**
(`reviews_soak_a10d67ae1966.json`). Every label is justified by evidence
inspected outside the Brain (live `/api/status` payload, `logs/errors.log`).

| Metric | 30s soak | 15m soak |
|---|---|---|
| Reviewed incident candidates | 13 | 2 |
| Candidate precision | 0.154 | **1.000** |
| Noise escape rate | 0.462 | **0.000** |
| Duplicate escape rate | 0.385 | **0.000** |
| Improvement precision | — | 0.500 (1 useful, 1 duplicate) |

`THRESHOLD_RECOMMENDATION`: current 0.42, recommended 0.548,
`auto_applied=false`, `requires_human_review=true`. Not applied — with
`noise_n=0` there is no measured negative distribution to separate against.

## Y. 30s vs longer soak

| | 30s | 15m (pre-fix) | 15m (post-fix) |
|---|---|---|---|
| Duration | 30s | 900s | 900s |
| Signals | 138 | 4,200 | 4,137 |
| Unique candidates | 12 | 14 | **2** |
| Unique improvements | 11 | 9 | **2** |
| Candidate precision | 0.154 | not reviewed | **1.000** |
| Duplicate escape | 0.385 | — | **0.000** |

The pre-fix 15m run is retained as `long_soak_result_prefix.json`. It promoted
11 `STALE_PENDING` and 2 `DEGRADED_PENDING` per-component candidates — the
lifecycle rule in §G replaced those with one systemic candidate.

## Z–AB. Distributions and findings

Component distribution concentrates on `logs`/`dataingestion` (error_log) and
the 13 registry module names. Source distribution: registry 62%, error_log 25%,
git 13%. Telemetry findings: registry `pending` is unreliable as a health signal
(documented in `signal_inventory.md`); git commits produce 99% duplicates and
zero candidates.

## AC–AE. Retention and stability

Graph/session retention: 8 sessions, 7 days, 8MB caps. `prune_shadow` now covers
derived analysis artifacts (`reviews_`, `replay_`, `long_soak_`,
`hybrid_replay_`, `calibration_`) which previously escaped retention.

Storage: 15m session artifact **1.55MB** (pre-fix run 2.70MB; 30s run 88KB).
Shadow directory total 9.06MB across 16 files.

Stability across 173 cycles: processing latency 1.98ms → 1.15ms (no drift),
queue depth 0 throughout, `root_keys` flat at 4, `seen_keys` 9 → 27 (bounded
growth from genuinely new log lines), candidates 1 → 0 after the first cycle.
No runaway trend.

## AF. Deterministic replay

Prior 30s soak and all six holdout streams replayed deterministically, LLM=0,
writes=0. Results in §E and §L.

## AG–AJ. Real Hybrid smoke and replay

Three gated paid runs. Gate required `--explicit`, `--i-accept-cost`, a candidate
allowlist, call budget, token budget, timeout; checkpoints after every call;
client closed; session breaker reset before each call.

| Run | Calls | Tokens in/out | Fallbacks | Errors |
|---|---|---|---|---|
| `smoke` (1200 cap) | 2 | 1,552 / 2,400 | 2 | 0 |
| `smoke2` (4000 cap) | 2 | 1,552 / 3,825 | 0 | 0 |
| `final` (4000 cap, post-fix) | 2 | 1,552 / 3,845 | 0 | 0 |

The first run's fallbacks were a real defect, not a provider failure: output hit
the 1,200-token cap and returned truncated JSON, so `validate_reasoner_output`
rejected it with `missing_fields`. Two bugs were found and fixed:

- `evidence_needed`/`risk_notes` given as a bare string were iterated
  character-by-character (`["O","u","t","p","u","t"]`). `_string_list` now coerces
  a string to one item.
- `expected_information_gain` was truncated to 8 characters, producing
  `"HIGH — D"`. Cap widened to 48.

**ccxt case (§36).** Deterministic identified the missing dependency and
proposed `verify_import_availability`. Hybrid's top hypothesis (confidence 0.88)
was the same conclusion, and it added four hedged alternatives — wrong
interpreter/venv (0.72), removed by a recent deployment (0.65), image built
without it (0.55) — plus five read-only verifications ranked by information gain
(`pip show ccxt` 0.95, CI/CD logs 0.80, dependency manifest 0.75). It did not
claim a confirmed root cause: `stop_recommendation = NEEDS_MORE_EVIDENCE`. No
unrelated causes were invented. Human labels were absent from the input
(`label_leakage=false`, asserted by test).

**Pending case (§37).** Hybrid ranked the *telemetry-artifact* explanation
first (0.65: "registry is reporting a default pending state because
per-component lifecycle telemetry has not been emitted") and down-ranked
"modules genuinely stalled" to 0.25. It flagged `kill_switch` conditionally —
"if it is genuinely stalled (not merely a telemetry artifact)". It did **not**
over-interpret the noise.

## AK–AO. Hybrid gain, cost, claims

Gain, on 2 cases: differential hypotheses with calibrated confidence, and
concrete verification commands the deterministic arm cannot generate. No gain on
identifying the root itself — deterministic already had it. Uncertainty
calibration was correct on both (`NEEDS_MORE_EVIDENCE`, `authority=false`,
`is_evidence=false`, `epistemic_status=HYPOTHESIS`).

Cost: 6 calls, 4,656 input / 10,070 output tokens across three runs
(≈US$0.17 at Sonnet list price). Latency 35–41s per call.

Unsupported claims (heuristic vocabulary diff): 3 flagged in the final run. On
inspection `"e.g"` ×2 are heuristic artifacts; `"requirements.txt"` is a hedged
inference (confidence 0.55) about a file not present in the evidence. No
confident unsupported assertion was found.

## AP. Safety

Asserted at end of phase: ChangeRequests 0, SandboxRuns 0, production writes 0,
trades 0, autonomy changes 0, LLM calls during soak 0, safety violations 0,
`level3_enabled=false`, `SANDBOX_ENABLED=false`. Trader was never restarted;
`trader_unaffected=true`.

## AQ–AR. Laços and live clickthrough

Incident panel now renders root fingerprint, tier, occurrence count, affected
instances/components, first/last seen and `promotion_reason` instead of one row
per duplicate. Improvement panel shows pattern, benefit class and occurrence
count. A `TELEMETRY QUALITY` panel shows per-source observations, suppressed,
candidates, reviewed useful/noise plus recommendations. No control enables a
source, applies a change, opens a sandbox, or touches Level 3 — asserted by test.

`LIVE_UI_CLICKTHROUGH = NOT_VERIFIED`. `/api/brain` is reachable and returns
`autonomy.level=2`, `shadow=true`, `can_apply=false`, `can_trade=false`, and the
served HTML contains the new panels. The running process executes pre-change
Python, so `root_clusters`, `pending_model` and `telemetry_ledger` are absent
from the live payload. Production was not restarted to close that gap.

## AS–AU. Tests

Brain-related **326 passed**; global **438 passed / 14 failed / 6 collection
errors**; **NEW regressions ZERO**. `tests/test_brain_calibration.py` adds 67
tests covering root fingerprint normalization, multi-symbol and cross-exchange
collapse, over-dedup protection, candidate storm, occurrence-vs-candidate
separation, pending lifecycle and all six classes, lifecycle-evidence rule,
systemic collapse thresholds, score component explanation, expected-state
penalty, feature audit, six holdout shapes, telemetry ledger and
never-auto-disable, improvement fingerprint dedup, checkpoint/resume of root and
pending state, long-soak stability, storage retention, Laços observability, the
paid gate, evidence sanitization, label-leak prevention, the reasoner
string-coercion fix, and zero-authority invariants. No test can trigger a paid
call.

## AV–AX. Classification

- **REAL_DISCOVERY_VALUE = CANDIDATE.** Precision moved 0.154 → 1.000 and
  duplicate escape 0.385 → 0.000 on a reviewed 15m soak, with holdouts passing.
  `VALIDATED_ON_REVIEWED_SOAK` is withheld: the reviewed denominator is 2
  candidates on one machine with one real fault plus one telemetry artifact.
- **REAL_HYBRID_REPLAY_VALUE = CANDIDATE.** Two real cases, zero fallbacks after
  the token-budget fix, correct uncertainty on both, correct down-ranking of the
  pending artifact, no invented causes. One case per pattern is too thin for
  `VALIDATED_FOR_SPECIFIC_REAL_PATTERNS`.
- **FABLE_VALUE = NO_MEASURED_VALUE** (unchanged; no Fable paid benchmark run,
  `fable_included=false` on every replay).

## AY–BA. Readiness

- Discovery: **CALIBRATED_CANDIDATE** — dedup and pending semantics validated on
  real data and holdouts; precision denominator still small.
- Live Shadow: **SOAK_VALIDATED** for 15 minutes, read-only, zero writes.
- Level 3: **NOT_ENABLED / NOT_READY.** Unchanged by this phase.

## BB. Level 3 evidence gaps (updated with real data)

| Gap | Status |
|---|---|
| Real discovery precision | 1.000 on n=2 reviewed. Needs a denominator in the tens across multiple fault types. |
| False-negative visibility | Still foundation-only. No measured recall; unknown what the 15m soak missed. |
| Real hybrid reasoning | 2 cases, both sound. No adversarial or ambiguous case tested. |
| Duplicate control | Validated (0.000 escape, storm and over-dedup tests). |
| Noise control | Validated (0.000 escape; 2,583 of 4,137 signals suppressed with reasons). |
| Human review volume | 2 candidates / 15 min is reviewable. Untested under a real incident burst. |
| Live UI | NOT_VERIFIED against a live payload carrying the new fields. |
| Long-duration stability | 15 min flat. 24h+ untested; `seen_keys` growth needs a longer bound check. |

## BC. Remaining risks

1. Precision 1.000 rests on 2 reviewed candidates. One environment, one real
   fault. Do not generalize.
2. False negatives are unmeasured. The pending rules deliberately suppress a
   large signal class; a genuinely stuck component whose source never reports
   `uptime` would now be missed until it errors or impacts a dependent.
3. `error_log` shows a 98% duplicate rate — the error tail re-reads the same
   lines each cycle. Cheap now, but it is wasted work.
4. The unsupported-claim detector is a vocabulary heuristic and produces
   punctuation-class false positives. It is not a hallucination proof.
5. Systemic collapse thresholds (5 components, 3 cycles) are judgment calls,
   tested but not tuned against a large corpus.
6. The recurrence improvement row still restates a dependency failure as generic
   degradation (reviewed DUPLICATE). Improvement fingerprinting collapses
   identical rows but does not yet cross-suppress against a stronger sibling.

## BD. Recommended next phase

Measure **false negatives**, not more precision. Concretely: inject known
read-only faults into a dev process, run a bounded soak, and report recall per
fault class; add a soak of several hours to bound memory and `seen_keys` growth;
suppress improvement rows that restate a higher-confidence sibling; and only then
consider whether a live-payload UI clickthrough justifies a supervised dev
restart. Level 3 stays off.
