# Real Shadow Generalization — phase record

Multi-session validation of a frozen Discovery policy, false-negative audit,
runtime revision attestation, and selective paid Hybrid evaluation.

Authority unchanged throughout: **LEVEL 2 — INVESTIGATE**, `SHADOW=ON`,
`SANDBOX_ENABLED=FALSE`, `LEVEL3=OFF`. Zero writes, zero ChangeRequests, zero
SandboxRuns, zero restarts of the production process.

## A. Fresh baseline

| Suite | Start of phase | End of phase |
|---|---|---|
| Brain-related (`-k "brain or discovery or calibration or soak or replay or provider or fable or cognitive or shadow or proving or sandbox or control_plane or language or committee"`) | 326 passed | 445 passed, 0 failed |
| Global (`pytest tests/`) | 438 passed / 14 failed / 6 collection errors | 547 passed / 14 failed / 6 collection errors |

New regressions: **zero**. The 14 failures and 6 collection errors are the same
named set as before (`test_crypto_refresh`, `test_edge_birth_panel`,
`test_edge_gate`, `test_search_persistence`; collection errors in
`test_crypto_schedule`, `test_desktop_spawn`, `test_gpu_stats_no_console`,
`test_integrity_regressions`, `test_market_clock`, `test_orchestra`). The
increase in passing tests is entirely the new suites added this phase.

## B. Calibration V2 freeze revision

```
policy      DISCOVERY_CALIBRATION_V2
revision    v2.1
config_hash 04f2ec81fc26e408
score       discovery-score-v2
fingerprint rootfp-v1
inventory   signal-inventory-2026-09-03
frozen      true
```

`config_hash` is computed from the live constants in `score.py`, `pending.py`,
`fingerprint.py` and the engine threshold, not from a copy. It was
`04f2ec81fc26e408` before session A and is `04f2ec81fc26e408` now. Every change
made during the phase was to measurement, artifacts or the UI; none touched a
weight, a threshold or a fingerprint key.

Four findings are recorded against the frozen revision in
`brain/discovery/revision.py`. One is a policy failure found earlier;
three were found during this phase and are all measurement or artifact defects:

| id | category | what it was | effect on reported numbers |
|---|---|---|---|
| `CALIBRATION_V2_FAILURE_01` | FALSE_NEGATIVE | `UNKNOWN_PENDING` suppression was absolute because error text was attributed to component `logs` and never matched a registry component | fixed in v2.1 before the sessions ran |
| `MEASUREMENT_DEFECT_01` | MEASUREMENT | the FN audit read only summary-shaped keys, so the investigation stage matched nothing and every surfaced issue read as `INVESTIGATION_MISS` | recall 1.0 reported alongside three "misses" |
| `MEASUREMENT_DEFECT_02` | MEASUREMENT | a component-name mention counted as the issue having surfaced | recall read 3/3; true value 2/3 |
| `OBSERVABILITY_DEFECT_01` | REPLAY_FIDELITY | the soak artifact dropped `state` and `lifecycle_known`, so replay took a different branch than the run | replay produced 14 candidates where the run produced 3 |

## C. Runtime revision attestation

`brain/attest.py` captures, at import time, a content hash of the Brain package
as the interpreter actually loaded it, plus the git revision and process start.
It compares that against the same hash recomputed from disk.

Exposed read-only at `/api/brain → lacos.live_intelligence.runtime_revision`:

```json
{
  "running_revision": "00bd59f96418df69",
  "worktree_revision": "6d4e3ff47f79d9cb",
  "stale_runtime": true,
  "drift_reason": "brain_source_changed_on_disk",
  "git_revision": "5ec6e2e4d4d4",
  "discovery_policy": "DISCOVERY_CALIBRATION_V2 v2.1",
  "policy_config_hash": "04f2ec81fc26e408",
  "route_schema_version": "qhos-brain-v1",
  "process_uptime_s": 139.2,
  "read_only": true
}
```

No secrets: a content hash, a git short SHA, a policy name and an uptime. No
paths, no environment, no keys.

## D. Multi-session design

Three independent bounded sessions, LLM off, read-only, against the live
machine. Each records its own provenance block (`session_id`, start/end,
duration, repo SHA, runtime attestation, policy revision, fingerprint revision,
score revision, inventory revision, config hash, and explicit
`llm_enabled=false / level3_enabled=false / sandbox_enabled=false`).

| | duration | interval | session_id |
|---|---|---|---|
| A | 900 s | 5 s | `soak_a53bdd4c748f` |
| B | 900 s | 5 s | `soak_58a5fc6cb049` |
| C | 1800 s | 5 s | `soak_743e7edc32f4` |

Session C's first attempt died when its interpreter was killed mid-run. A and B
had already been persisted, so nothing was lost; C was re-run with an `--append`
mode rather than restarting the whole plan. Total wall time 60 minutes.

## E–G. Per-session metrics

| | A | B | C |
|---|---|---|---|
| duration | 900 s | 900 s | 1800 s |
| cycles | 171 | 174 | 344 |
| events received | 4,074 | 4,161 | 8,226 |
| suppressed as noise | 2,369 | 2,425 | 4,791 |
| duplicates | 1,680 | 1,711 | 3,392 |
| root-collapsed occurrences | 20 | 20 | 35 |
| pending suppressed | 2,028 | 2,076 | 4,104 |
| candidate incidents (unique roots) | 3 | 3 | 3 |
| improvement candidates | 3 | 3 | 3 |
| investigations opened / completed | 5 / 5 | 5 / 5 | 8 / 8 |
| reviewed useful | 2 | 2 | 2 |
| reviewed noise | 0 | 0 | 0 |
| reviewed duplicate | 1 | 1 | 1 |
| reviewed uncertain | 0 | 0 | 0 |
| **precision** | 0.6667 | 0.6667 | 0.6667 |
| **noise escape** | 0.000 | 0.000 | 0.000 |
| **duplicate escape** | 0.3333 | 0.3333 | 0.3333 |
| **candidate rate / min** | 0.20 | 0.20 | 0.10 |
| artifact size | 1.66 MB | 1.67 MB | 1.66 MB |
| mean processing latency | 0.00425 s | 0.00366 s | 0.00395 s |
| safety events | 0 | 0 | 0 |

All three sessions returned the same three roots with the same scores:

* `0448844a871af916` — DEPENDENCY, 0.680, `missing dependency 'ccxt'`, 6 occurrences
* `922a192f16149094` — STATE, 0.408, `ingestion state=pending`, 1 occurrence
* `sys-40990f8d611e35cd` — TELEMETRY, 0.5132, `registry reports 12 modules pending with no lifecycle telemetry`, 12 components

**The important caveat about this consistency.** It is repeatability evidence,
not generalization evidence. The underlying fault set did not change between
windows: the same two real problems were present the whole hour. Doubling the
window from 15 to 30 minutes produced no new candidates and no new noise, which
is the property being tested — but a genuinely different signal mixture was
never observed, because the machine did not produce one. Different error shapes
were covered by holdouts (§O below), not by these sessions.

## H. Aggregate metrics

| metric | value | n |
|---|---|---|
| macro precision | 0.6667 | 3 sessions |
| micro precision | 0.6667 | 9 reviewed candidates |
| noise escape | 0.000 | 9 |
| duplicate escape | 0.3333 | 9 |
| candidate rate / min (macro) | 0.1667 | 3 sessions |
| candidate rate / min (micro) | 0.15 | 60 minutes |
| improvement useful rate | 0.333 | 9 reviewed improvements |
| signals total | 16,461 | 60 minutes |
| zero-candidate sessions | none | — |

## I–L. Precision, rate, escape

Candidate precision is **0.667, not 1.000**, and the drop from the previous
phase's 1.000 is real rather than a regression. The `ingestion state=pending`
candidate is the v2.1 false-negative fix working as designed: `ingestion`
reports pending with no lifecycle telemetry, independent error evidence names
it, so `UNKNOWN_PENDING` is overridden and it is promoted. It is a correct
detection of a genuinely broken component. It is also *redundant*, because its
cause is the ccxt dependency already reported as a separate candidate, so it was
reviewed `DUPLICATE`.

That is the cost side of the fix and it should be reported as such: making
suppression non-absolute converted a false negative into a duplicate. Duplicate
escape 0.333 on n=3 is one row.

Noise escape is 0.000 across all three sessions: nothing promoted was judged
`NOT_USEFUL`.

## M–P. Root cluster quality

Measured against an engine-independent text labelling of the recorded
observations (`ccxt` → `TR_dep_ccxt`, pending state rows → `TR_registry_pending`),
pairwise, on 3,166 labelled observations per session with 833 excluded as
unlabelled.

| layer | precision | recall | over-dedup | under-dedup |
|---|---|---|---|---|
| root signature (`rootfp-v1`) | 1.000 | 0.2417 | 0.000 | 0.7583 |
| delivered candidate | 1.000 | 0.8834 | 0.000 | 0.1166 |

Both layers are reported because they answer different questions. The signature
layer keeps one signature per component by design; the engine then collapses
those into a single systemic candidate one layer up. Judging only the signature
layer scores the engine for a split it immediately repairs, which is why the
signature-layer recall of 0.24 is not the number a reader should use.

At the delivered layer, over-dedup is **zero** — nothing was merged that did not
belong together, which is the dangerous direction, since a wrong merge hides a
distinct problem inside another candidate. The residual 0.117 under-dedup is the
`ingestion state=pending` row being delivered separately from the systemic
telemetry root, i.e. exactly the duplicate discussed above.

## Q–R. Useful candidates and improvements

Two useful candidates per session, both independently confirmed:

1. **`ccxt` is not installed.** Six crypto symbols fail at import every cycle.
   Confirmed by `ModuleNotFoundError` in `logs/errors.log` and by the package
   being absent from the runtime.
2. **The registry reports every module pending with no lifecycle telemetry.**
   Confirmed by `GET /api/status`: 12 modules, `uptime=null`, `error_count=0`.
   This is a status-endpoint default, not component health.

Improvements: 1 useful (`MISSING_DEPENDENCY / RELIABILITY`, which adds the
remediation framing the incident row lacks), 2 duplicate (two
`RECURRING_DEGRADATION` rows on `dataingestion` and `ingestion`, both restating
the same ccxt failure by occurrence count). Improvement fingerprinting collapses
identical rows but still does not cross-suppress a row that restates a
higher-confidence sibling — carried forward as a known gap.

## S. Telemetry-quality findings

The registry discovery is surfaced under its own kind rather than as an
incident: `TELEMETRY_QUALITY_ISSUE`, subtype `OBSERVABILITY_DEFECT`,
`is_component_failure=false`, 12 components. Reading it with incident semantics
would invite the wrong fix — restarting twelve healthy modules instead of
populating the registry.

Per-source health (§20), session A, identical in B and C:

| source | observations | share | suppression | duplicate rate | candidate yield | useful yield | noise yield | verdict |
|---|---|---|---|---|---|---|---|---|
| registry | 2,535 | 62.2% | 0.9996 | 0.066 | 0.0004 | 0.50 | 0.00 | reachable but **semantically misleading** |
| error_log | 1,026 | 25.2% | 0.9766 | 0.977 | 0.0039 | 1.00 | 0.00 | valid, extremely repetitive |
| git | 513 | 12.6% | 1.000 | 0.994 | 0.000 | — | — | **inert**: 12.6% of traffic, zero candidates |

Useful and noise yield required a fix to work at all: the summarised candidate
row dropped `sources`, so a reviewed disposition had nothing to attach to and
every source read 0.0 useful yield regardless of outcome. They are now computed
from the full candidate records against the reviewed dispositions.

The recommendation for `git` is `CONSIDER_SAMPLING`, and `auto_disable_enabled`
is `false` — no source is ever turned off automatically.

## T. UNKNOWN_PENDING safety analysis

The risk in §21 is real and was confirmed by direct trace, not by argument.
Before v2.1 the only override path was `dependent_impact`, computed by matching
a pending component's family against `error_components`. Every `error_tail`
observation is attributed to component `logs`, so `error_components` was always
`{"logs"}` and could never match a registry component family, while
`error_evidence` was never passed at all. Suppression was absolute: a component
reporting pending with no lifecycle telemetry stayed invisible no matter how much
independent error evidence existed.

v2.1 adds canonical component matching (`risk_engine`, `RiskEngine`,
`risk-engine` all resolve to `riskengine`) and attributes error text to the
components it names. Tested in both directions:

* `UNKNOWN_PENDING` with no independent evidence stays suppressed;
* `UNKNOWN_PENDING` plus error logs naming that component is promoted;
* promotion requires the *matching* component — an error naming a different
  component does not promote;
* component matching rejects keys under four characters, so a short name cannot
  match everything.

On real data the fix fired exactly once per session, on `ingestion`, correctly.
Its cost is the duplicate in §I.

The paid Hybrid replay (§AB) then found the weak spot in the fix that the tests
did not: `ingestion` was promoted at `pending_duration_s=0.0` with
`cycles_pending=1` and severity `high`. Firing at first detection with zero
elapsed pending time is thin evidence for a high-severity candidate. Not changed
here — the policy is frozen — but recorded for the next calibration.

## U. Known issue recall

Denominator is three independently confirmed issues. This is **not** a global
false-negative rate; it is recall over the issues we happen to know about.

| id | issue | surfaced | investigated | category |
|---|---|---|---|---|
| KI-01 | ccxt not installed | yes | yes | `NOT_A_DISCOVERY_FAILURE` |
| KI-02 | registry reports every module pending | yes | yes | `NOT_A_DISCOVERY_FAILURE` |
| KI-03 | the production process runs an older Brain revision | **no** | no | `NO_SIGNAL` |

**KNOWN_ISSUE_RECALL = 0.667 (2/3).** Investigated rate 2/3.

KI-03 is an honest miss with a known cause: Discovery has no signal source for
interpreter revision, so no observation about it ever reaches the engine. That
is a coverage gap, not a scoring or suppression failure, and it is the gap the
attestation work in §C closes at the reporting layer — the Brain still cannot
*discover* it, but a human can now see it.

This number was 1.0 (3/3) before `MEASUREMENT_DEFECT_02` was fixed. The old
matcher counted a candidate saying `dashboard state=pending` as evidence that
"the dashboard runs stale code" had surfaced, purely because both mention the
dashboard.

## V–W. False-negative retrospective and categories

Every known issue is walked back through the pipeline stage by stage and the
earliest stage that lost it is named. Funnel for the pooled retrospective:

| stage | KI-01 | KI-02 | KI-03 |
|---|---|---|---|
| raw matches | 1,008 | 2,158 | 0 |
| normalized | 1,008 | 2,158 | 0 |
| suppressed | 984 | 1,980 | 0 |
| duplicates | 984 | 1,527 | 0 |
| clusters | 1 | 13 | 0 |
| candidates | 1 | 13 | 0 |
| investigations | 4 | 98 | 0 |

All eight categories in §25 are reachable and individually tested:
`NO_SIGNAL`, `NORMALIZATION_MISS`, `SUPPRESSION_MISS`, `DEDUP_MISS`,
`SCORING_MISS`, `CLUSTERING_MISS`, `INVESTIGATION_MISS`,
`NOT_A_DISCOVERY_FAILURE`.

**A caveat that matters more than the funnel numbers.** These numbers come from
replaying the recorded observations, and that replay is *not faithful* to the
session it replays. The artifact dropped `state` and `lifecycle_known`
(`OBSERVABILITY_DEFECT_01`), so on replay every registry row defaults to
`lifecycle_known=True` and takes the `EXPECTED_INITIALIZATION` branch instead of
`UNKNOWN_PENDING` — producing 14 candidates where the live run produced 3. The
review artifact now carries `replay_faithful: false` with that explanation for
sessions A, B and C. The fix is in place for future sessions; these three were
already recorded and cannot be repaired retroactively.

Replay also covers only a prefix of each window, because the artifact stores at
most `brain_soak_max_events=4000` observations: **98.2%** of session A, **96.1%**
of B, and **48.6%** of C.

## X–Y. Review dataset growth and revisions

| | before | after |
|---|---|---|
| rows | 17 | 35 |
| sessions | 2 | 5 |
| policy revisions | (unstamped) | `pre_v2_freeze`, `v2.1` |

Earlier phases wrote one review file per session and no cumulative view. Those
17 rows are now folded in rather than re-reviewed, stamped `pre_v2_freeze` so
they are never attributed to the frozen policy. No earlier session was dropped
or overwritten.

`review_id` is now derived from `(session, candidate_type, candidate_id)` rather
than being random. With a random id, re-running the review script silently
appended a second copy of every row — and precision is a ratio over that count.
Re-review is supported through `revise_review`, which moves the previous
disposition into `revision_history` with a reason rather than replacing it.
Revisions used this phase: **0**.

## Z. Stability and storage

| | A (900 s) | B (900 s) | C (1800 s) |
|---|---|---|---|
| mean processing latency | 0.00425 s | 0.00366 s | 0.00395 s |
| max processing latency | 0.00606 s | 0.00613 s | 0.00620 s |
| queue depth (max) | 0 | 0 | 0 |
| `seen_keys` (fingerprint memory) | 28 | 28 | 46 |
| `root_keys` | 5 | 5 | 5 |
| artifact size | 1.66 MB | 1.67 MB | 1.66 MB |

Latency is flat and does not degrade with duration: doubling the window changed
the mean by less than a microsecond and the max by 0.14 ms. `root_keys` is
constant at 5 across a 2× window. `seen_keys` grows sublinearly (2× duration →
1.64× keys) because most new signals are repeats of known fingerprints.

Artifact size is flat only because the 4,000-event cap binds in every session;
it is not evidence that the artifact scales.

Directory totals: `.qhos-brain/shadow` 16.44 MB across 35 files;
`.qhos-brain` 18.73 MB across 390 files. Retention keeps at most 8 soak
artifacts and drops any single file over 8 MB, but there is **no aggregate
budget** — see remaining risks.

## AA. Deterministic replay

Holdout families that score v2 was not tuned against, all passing under the
frozen policy: permission error, disk/resource exhaustion, connection refused,
DNS/provider error, schema/data-quality failure, timeout, stale registry data,
healthy-but-delayed initialization, one dependency failing across several
components, different dependencies with similar messages, high-frequency benign
warnings, and one low-frequency severe error.

Same root / different surface: the same missing dependency across different
symbols, exchanges, wrappers, log prefixes and correlation ids collapses to one
root. Different root / similar text: messages that look alike but have different
causes stay separate.

## AB. Selective real Hybrid replay

Two paid calls. Eligibility followed §31: the `ccxt` dependency failure was
**excluded** as `controlled_comparison_only` because deterministic already
reaches the correct conclusion and it was measured as a controlled comparison in
the previous phase. The two selected cases are the ones where semantic reasoning
had something to add.

| | `sys-40990f8d611e35cd` | `922a192f16149094` |
|---|---|---|
| family | TELEMETRY | STATE |
| why eligible | telemetry conflict, multi-component | uncertain root cause |
| output status | VALID | VALID |
| fallback | no | no |
| input / output tokens | 576 / 1,762 | 580 / 1,404 |
| latency | 38.75 s | 27.97 s |
| cost | $0.0282 | $0.0228 |
| breaker | closed | closed |
| unsupported claims | 0 | 0 |

## AC–AG. Hybrid incremental value

Both rows: **primary `VERIFICATION_VALUE`**, classes
`{ROOT_CAUSE_VALUE, VERIFICATION_VALUE, UNCERTAINTY_VALUE}` plus
`CROSS_COMPONENT_VALUE` on the telemetry case, qualified as *differential
alternatives around a restated primary hypothesis*.

The qualifier is the point. Both models' top hypothesis restated the
deterministic conclusion (token overlap 0.571 and 1.000). Crediting that as
`ROOT_CAUSE_VALUE` would report "the model found the cause" for a restatement,
so the classifier now ranks the honest contribution first when
`restated_deterministic` is true.

* **Verification value.** 5 new verification targets per case, every one ranked
  by expected information gain, against the deterministic arm's fixed
  `inspect_logs, inspect_health`.
* **Uncertainty value.** Both returned `NEEDS_MORE_EVIDENCE` with a real
  confidence spread (0.30 and 0.25) rather than claiming a cause.
* **Root-cause value (secondary).** 4 and 3 differential hypotheses. On the
  telemetry case these are genuinely distinct: a default status source, a shared
  stalled dependency, a broken metrics exporter, a startup race — each with a
  different verification path.
* **Cross-component value.** The telemetry case reasoned across `registry`,
  `storage`, `execution`, `kill_switch` and `risk_engine`, and produced the
  correct safety note: *"error_count=0 with uptime=null should not be
  interpreted as all components healthy"*.
* **Negative value.** None. Zero unsupported claims on both after the
  abbreviation fix below.
* **Memory value.** Not evaluable. The sanitized packet carries only the
  candidate's own window, so there is no prior-incident context to connect to.
  Reported as `memory_value_evaluable: false` with the blocker stated, rather
  than left as a silently unreachable label.

The most useful single output was a critique of our own work: on
`922a192f16149094` the model observed that the alert fired at
`pending_duration_s=0.0` with one cycle observed, and offered "premature alert
trigger" as a 0.35-confidence alternative. That is a real weakness in the v2.1
false-negative fix, and the deterministic arm could not have produced it.

## AH–AJ. Tokens, cost, latency

2 calls, 1,156 input + 3,166 output = **4,322 tokens**, **$0.051** at Sonnet list
price, 38.75 s and 27.97 s latency. Cumulative paid spend on Hybrid replay
across both phases remains under $0.20.

## AK–AL. Provider output statuses and fallbacks

`{"VALID": 2}`. Fallbacks: **0**. Hybrid successes: 2/2.

Every paid row carries the full integrity block: `llm_called`, `provider`,
`model`, `input_tokens`, `output_tokens`, `total_tokens`, `latency_s`,
`output_status`, `fallback`, `breaker_state`, `counts_as_hybrid_success`. A
fallback can never be counted as a real-model row: `counts_as_hybrid_success` is
forced false whenever the provider reports anything other than `VALID`.

## AM. Validator robustness

The taxonomy is `VALID`, `TRUNCATED`, `SCHEMA_INVALID`, `FALLBACK`, `TIMEOUT`,
`CIRCUIT_OPEN`, `PROVIDER_ERROR`. Infrastructure status wins over schema status,
so a timeout is never reported as a schema failure.

Adversarial cases covered by tests: string where a list is expected, list where
a string is expected, a single mapping where a list of hypotheses is expected,
truncated JSON, extra unknown fields (dropped, and reported rather than merged),
missing fields, numeric strings, invalid confidence, invalid information gain,
and oversized outputs (bounded).

The two regression tests from the previous phase are preserved: a bare-string
`evidence_needed` must not iterate as characters, and `expected_information_gain`
must not be truncated to 8 characters. The raw model text is now kept alongside
the normalised value in `expected_information_gain_text`.

One heuristic was fixed this phase: the unsupported-claim detector flagged `e.g`
as an invented module path. A hallucination check that fires on abbreviations
trains the reader to ignore it. After the fix both paid rows report zero
unsupported claims, and `core.data_ingestion`-shaped tokens are still caught.

## AN. Runtime / worktree mismatch detection

Demonstrated live, three ways:

1. **Fresh dev process:** `running_revision == worktree_revision`,
   `stale_runtime=false`, `drift_reason=running_matches_worktree`.
2. **Source edited under a running process:** editing `brain/discovery/revision.py`
   while the dev server was serving flipped it to
   `running 00bd59f96418 · worktree 4768e1d9d060`, `STALE_RUNTIME = TRUE`,
   `drift_reason=brain_source_changed_on_disk`, with no restart and no change to
   `policy_config_hash`.
3. **The production process on :8000** returns no `runtime_revision` field at
   all. It started before `brain/attest.py` existed, so it cannot report its own
   revision. That is `NOT_ATTESTABLE` — the strongest form of stale — and it is
   precisely the observability gap this phase was asked to close.

Production was **not restarted**. Reporting the staleness is the deliverable.

## AO. Laços clickthrough

**LIVE_UI_CLICKTHROUGH = VERIFIED**, on a development runtime on port 8012, with
the production process on :8000 left untouched.

Verified by rendering the page in a browser, opening the Brain node in the map
inspector, and reading the resulting DOM:

* **Live Intelligence** — observer, soak, `llm_called`, `read_only` flags.
* **Incident panel** — `missing dependency 'ccxt' (ModuleNotFoundError)`,
  `INVESTIGATING`, score 0.639, root `0448844a871a`, tier `DEPENDENCY`,
  **occurrences 6 · instances 6**, first/last seen, and the promotion reason
  with its score components.
* **Improvement panel** — pattern, benefit class, fingerprint.
* **Telemetry quality** — per-source observation/suppression/candidate/useful/
  noise counts, the `CONSIDER_SAMPLING` recommendation for `git`, and
  `auto_applied=false`.
* **Runtime revision** — running, worktree, `STALE_RUNTIME`, drift reason,
  policy, config hash, git revision, route schema, uptime.
* **Budget** — llm/hour, active investigations, deferred.
* **Strategy** — cognitive strategy, effort, memory, fable status.
* **Controls** — `PROMOTE_FOR_REVIEW não abre sandbox · Level 3 permanece
  desligado`, `LEVEL 3 CAPABLE · ENABLED=false · SHADOW MODE · auto_merge=false`,
  and on the stale path `informativo · o processo não é reiniciado
  automaticamente`. **No restart button exists.**

Screenshots: `.qhos-brain/shadow/lacos_panels_live.png` and
`.qhos-brain/shadow/lacos_stale_runtime_true.png`.

One panel was **not** seen with live data: the `TELEMETRY_QUALITY_ISSUE` row.
A single Brain cycle does not emit the systemic candidate — that needs the
multi-cycle threshold — so the row had nothing to render. Its payload and
rendering are covered by tests, but that is not the same as having watched it
draw, and it is reported here as such.

This also corrected an over-claim in the verification script itself: it had
reported `has_runtime_revision: true` while the served payload's
`live_intelligence` was an empty object, because the routes read the
process-wide Brain and no cycle had run in that process.

## AP. Security

No secrets in any artifact, payload or model packet. The attestation exposes a
content hash, a git short SHA, a policy name and an uptime. Evidence sent to the
provider is redacted and label-stripped; `label_leakage` is `false` on every
paid row, checked for `human_disposition`, `final_category` and `ground_truth`.
The two fields added to the soak artifact this phase are a status word and a
boolean. No paid provider calls occur in pytest.

## AQ. Safety invariants

Verified in configuration at the end of the phase:

```
brain_autonomy_level          = 2
brain_shadow_mode             = True
brain_llm_api_enabled         = False
brain_live_llm_shadow_enabled = False
brain_live_shadow_soak_enabled= False
brain_paid_replay_enabled     = False
brain_cognitive_mode          = deterministic_only
```

Every session recorded `llm_enabled=false`, `level3_enabled=false`,
`sandbox_enabled=false`, `safety_ok=true`, and zero remote LLM calls. Writes: 0.
ChangeRequests: 0. SandboxRuns: 0. Production restarts: 0. Trades: 0.

## AR–AT. Tests

445 Brain-related passed (0 failed), 547 global passed with the same 14 known
failures and 6 known collection errors. **Zero new regressions.**

New coverage this phase (`tests/test_brain_generalization.py`, 109 tests):
frozen calibration revision; root cluster precision, recall, over-dedup,
under-dedup; `UNKNOWN_PENDING` with and without independent evidence; known
issue recall with an explicit denominator; every FN category; multi-session
provenance; review revision history and re-review idempotence; adversarial
provider schemas; truncation handling; fallback labelling; token metric
integrity; runtime revision attestation; stale-runtime detection; the UI
revision payload; storage stability; authority invariants; telemetry as a
first-class kind; and incremental Hybrid value classification.

## AU–AW. Value classification

* **REAL_DISCOVERY_VALUE = CANDIDATE.** Not promoted to
  `VALIDATED_ON_LIMITED_MULTI_SESSION_SOAK`. Three sessions and 16,461 signals
  produced zero noise escape and zero over-dedup, which is real evidence. But
  the reviewed denominator is 9 rows covering **two distinct faults on one
  machine**, the three sessions saw the same fault set rather than different
  ones, and the sessions cannot audit themselves because the artifact replay is
  not faithful. Success as defined in §58 requires the calibration to work
  across *independent* conditions; these conditions were independent in time
  only.
* **REAL_HYBRID_REPLAY_VALUE = CANDIDATE.** Unchanged. Four real cases now
  across two phases, zero fallbacks, zero unsupported claims, correct
  uncertainty on all of them, and one case where the model found a genuine
  weakness in our own promotion rule. Still one or two cases per pattern, and
  both cases this phase restated the deterministic conclusion before adding
  anything — so the measured contribution is verification and uncertainty, not
  diagnosis.
* **FABLE_VALUE = NO_MEASURED_VALUE.** Unchanged. No paid Fable evaluation was
  run; `fable_included=false` on every replay.

## AX–AZ. Readiness

* **Discovery: CALIBRATED_CANDIDATE.** Dedup, pending semantics and holdouts
  validated; precision denominator still small and single-environment.
* **Live Shadow: SOAK_VALIDATED_PARTIAL.** Not `MULTI_SESSION_VALIDATED`: three
  sessions ran, but over one unchanging fault set, so the multi-session evidence
  is about stability rather than about generalization.
* **Level 3: OFF / NOT_READY.** Unchanged by this phase. No autonomy promotion.

## BA. Level 3 evidence gaps

| Gap | Status after this phase |
|---|---|
| Real issue recall | 2/3 on independently confirmed issues, denominator explicit. Needs tens of issues across fault types. |
| False-negative visibility | Now measured rather than absent, with eight named categories and a stage-by-stage funnel. Undermined by unfaithful replay on A/B/C; fixed for future sessions. |
| Multi-session precision | 0.667 macro and micro over 3 sessions, n=9. Same fault set in all three. |
| Long-duration stability | 60 minutes total, 30 in one window. Latency and `root_keys` flat; `seen_keys` sublinear. 24h+ still untested. |
| Hybrid incremental value | Measured, not assumed: verification and uncertainty value confirmed, root-cause value only as differential alternatives around a restatement. |
| Telemetry semantic quality | Now first-class: `TELEMETRY_QUALITY_ISSUE`, per-source coverage/validity/yield, one source identified as misleading and one as inert. |
| Runtime revision observability | Closed at the reporting layer. Detects fresh, drifted and not-attestable processes. Discovery still has no *signal source* for it (KI-03 = `NO_SIGNAL`). |
| UI validation | Live clickthrough verified on a dev runtime, with screenshots. One panel (`TELEMETRY_QUALITY_ISSUE`) unverified live. |
| Human review volume | 3 candidates per session, 6 rows including improvements. Comfortably reviewable. Untested under an incident burst. |

## BB. Remaining risks

1. **The three sessions saw one fault set.** Consistency across A, B and C is
   repeatability, not generalization. Nothing here shows the policy holds when
   the signal mixture actually changes.
2. **Precision fell to 0.667 and that is the honest number**, driven by a
   duplicate the false-negative fix introduced. Making suppression non-absolute
   trades false negatives for duplicates; the trade looks right at n=9 and is
   not established beyond that.
3. **Sessions A, B and C cannot audit themselves.** Their artifacts lack
   `state` and `lifecycle_known`, so replay diverges from the run. Fixed
   forward; these three are permanently unfaithful.
4. **Replay covers only the first 4,000 observations**, i.e. 48.6% of session C.
   Anything the second half would have revealed is outside the retrospective.
5. **Shadow storage has no aggregate budget.** Retention caps count (8 sessions)
   and per-file size (8 MB) but not the directory, currently 16.44 MB. Eight
   full-size artifacts plus derived files could reach the tens of megabytes
   without any rule firing.
6. **The v2.1 promotion rule fires at zero elapsed pending duration**, on the
   first cycle, at severity `high` — found by the paid replay, not by our tests.
7. **`error_log` re-reads the same lines every cycle**: 97.7% duplicate rate,
   1,002 of 1,026 observations. Cheap, but wasted work at every scale.
8. **`git` contributes 12.6% of traffic and zero candidates** across all three
   sessions. Recommended for sampling; nothing is auto-disabled.
9. **Improvement rows still restate incidents.** Two of three improvements per
   session are duplicates of the ccxt candidate.
10. **The unsupported-claim detector remains a vocabulary heuristic.** Better
    after the abbreviation fix, still not a hallucination proof.

## BC. Recommended next phase

Generalization was measured against holdouts but never against a genuinely
different live signal mixture, because the machine did not produce one. The next
phase should stop waiting for that and create it: inject known, read-only,
non-destructive faults into a dev process — permission denial on a read path, a
provider timeout, a schema violation, a slow dependency — run bounded sessions
against each, and report recall and precision *per injected fault class*. That
converts known-issue recall from a denominator of three into a denominator worth
quoting, and it is the only way to separate "the policy generalizes" from "the
same two problems were present all hour".

Alongside that: re-run one session on the fixed artifact format so a faithful
replay exists; add an aggregate storage budget; re-examine whether promotion at
`pending_duration_s=0.0` should require a second cycle; and suppress improvement
rows that restate a higher-confidence sibling.

Level 3 stays off. Sandbox stays off. Continuous live LLM stays off.
