# Brain Domain Contract Map — Architecture v1 (LEVEL2_SHADOW_RC)

Frozen: 2026-09-03 · Architecture version 1 · Authority level 2 (INVESTIGATE)

Every major entity the Brain produces or persists, with one owner each. Where an
entity is re-created informally elsewhere, that is recorded rather than hidden:
the duplication is the finding.

Legend for **Provider safe?** — whether the entity may be sent to a remote model.
`REDACTED` means it must pass `brain.redact` first.

---

## 1. Core Discovery entities

### Observation
| | |
|---|---|
| Owner | `brain/discovery/observation.py` — `@dataclass Observation` |
| Purpose | The normalized unit of perception. Everything downstream reasons over these, never over raw snapshots. |
| Producer | `observations_from_snapshot()`; `brain/discovery/attestation.py` for runtime attestation rows |
| Consumer | Discovery engine, replay, soak persistence, provider context |
| Schema | `qhos-observation-v2` |
| Persistent | Yes — via `soak.sanitize_observation_row()` |
| Provider safe | REDACTED |
| Laços exposed | No (internal; the UI reads derived candidates) |
| Authority | Read-only. Producing an Observation authorizes nothing. |

**Decision-relevant fields** (the replay fidelity contract): `state` and
`lifecycle_known` are load-bearing and were once dropped, which is MD-03.

**Also shaped as a dict in:** `attestation.py:98` (pre-normalization rows, by
design — they pass through `normalize_row()`); `shadow.py:168` (legacy path,
`{kind, summary}` only, not the Discovery shape).

---

### PotentialIncident
| | |
|---|---|
| Owner | `brain/discovery/incident.py` — `@dataclass PotentialIncident` |
| Purpose | A scored root-cause candidate with its evidence and occurrence history. |
| Producer | `DiscoveryEngine.ingest()` |
| Consumer | Investigation, consolidation, soak, replay, review, UI view model |
| Schema | states in `INCIDENT_STATES`, classes in `SIGNAL_CLASSES`; score `discovery-score-v2` |
| Persistent | Yes (soak artifacts, fault matrix) |
| Provider safe | REDACTED, and review labels stripped first (`_sanitize_for_reasoner`) |
| Laços exposed | Yes, via the view model's `incidents` rows |
| Authority | Recommendation only. A candidate never triggers a change. |

**Canonical shape. This is the single source of truth for "candidate".**

**Known divergence — `brain/shadow.py:82` `_candidate()`** builds a second,
root-unaware candidate with a different key set (`system_confidence`,
`knowledge_state`, `feedback`, and no `root_id`/`score`/`promotion_reason`). The
dashboard renders both. Classified `DEPRECATED` in
`brain.architecture.FEATURES["LEGACY_SHADOW_CANDIDATES"]`; retained only because
the shadow feedback endpoint keys off it. **Not migrated in this phase** — the
migration touches a live UI panel and a POST endpoint, which is expansion, not
consolidation. It is the top item of remaining technical debt.

**Legitimate projections** (subsets, adapters — not duplicates):
`runner.py:195` `root_candidates`, `fault_matrix.py:327` `candidates_detail`,
`view_model._incident_row()`.

---

### IncidentOccurrence
| | |
|---|---|
| Owner | `brain/discovery/incident.py` |
| Purpose | One concrete sighting of a root, so recurrence is counted without inventing new incidents. |
| Producer | Discovery clustering |
| Consumer | `PotentialIncident.occurrences` |
| Persistent | Yes, nested |
| Laços exposed | As `occurrence_count` only |
| Authority | None |

---

### ImprovementCandidate
| | |
|---|---|
| Owner | `brain/discovery/improvement.py` |
| Purpose | An observed inefficiency that is not a failure. |
| Producer | `make_improvement()` / `dedupe_improvements()` |
| Consumer | UI view model, soak |
| Schema | states in `IMPROVEMENT_STATES`, `BENEFIT_CLASSES` |
| Persistent | Yes |
| Laços exposed | Yes |
| Authority | Recommendation only; `auto_apply` is hardcoded `False` in the projection. |

Impact/effort/risk are **estimates**, and the view model labels them
`kind="estimated"` so a UI cannot present them as measurements.

---

### TelemetryQualityIssue
| | |
|---|---|
| Owner | `brain/discovery/telemetry.py` — `telemetry_quality_issues()` |
| Purpose | An observability defect, surfaced apart from incidents because the corrective action is different. |
| Producer | Projection over TELEMETRY-tier candidates |
| Consumer | UI view model |
| Schema | `qhos-telemetry-ledger-v1` for the ledger |
| Persistent | Within the intelligence blob |
| Laços exposed | Yes — `telemetry_quality.issues` |
| Authority | Recommendation only |

**Distinguishing property:** `is_component_failure=False`. A telemetry issue read
with incident semantics invites a fix to the wrong system — you would go and
"repair" a component that is working, when the mapping is what is broken.
Severity derives from breadth (`component_count`) rather than from impact,
because a mapping defect affecting 20 components is worse than one affecting 1
regardless of how loud either is. It enters Improvement Intelligence as a
`DATA_QUALITY` benefit class, never as an Incident.

*Structural note:* this is a projection function, not a dataclass — the one
first-class UI entity without a typed owner. Left as-is because typing it would
mean touching the telemetry ledger, and the projection is already single-source.

---

### Investigation
| | |
|---|---|
| Owner | `brain/discovery/investigation.py` — `@dataclass Investigation` |
| Purpose | A bounded, read-only inquiry into one candidate. |
| Producer | `open_investigation()` |
| Consumer | Engine, UI |
| Persistent | Within the intelligence blob and checkpoints |
| Laços exposed | Yes (count and summary) |
| Authority | Read-only: `read_only=True`, `write_pending=False`, `change_request=None`. Actions restricted to `ALLOWED_ACTIONS`. |

**Known divergence:** `investigation.py:10` and `verification.py:12` declare
different `ALLOWED_ACTIONS` sets. Both are allowlists, so both are safe — but
they should be one list. Recorded as debt; not merged here because the two are
consumed by different authority paths and merging them without tests over each
would widen one of them.

---

### Secondary effect relationships
| | |
|---|---|
| Owner | `brain/discovery/consolidation.py` |
| Purpose | Link a downstream symptom to its root without asserting causation the evidence does not support. |
| Vocabulary | `SAME_ROOT`, `LIKELY_DERIVED_EFFECT`, `TEMPORALLY_CORRELATED`, `INDEPENDENT`, `UNKNOWN` |
| Roles | `PRIMARY_ROOT_CANDIDATE`, `SECONDARY_EFFECT` |
| Laços exposed | Yes — `role`, `derived_from`, `relationship` |
| Authority | None |

**A secondary effect is not a duplicate.** A duplicate is the same signal seen
twice and is suppressed. A secondary effect is a *different, real* signal caused
by the same root, and suppressing it would delete evidence. `TEMPORALLY_CORRELATED`
is deliberately weaker than `LIKELY_DERIVED_EFFECT`: co-occurrence in a window is
not a causal claim. The view model counts roots and effects separately so a panel
cannot inflate its unique-incident number with effects.

---

## 2. Reasoning entities

### Hypothesis
| | |
|---|---|
| Owner | `brain/models.py` — `@dataclass Hypothesis` |
| Producer | `HypothesisEngine`, providers, strategies |
| Schema | `HypothesisStatus` enum |
| Provider safe | Yes (generated by it) |
| Authority | None |

Discovery holds hypotheses as `List[Dict]` rather than the dataclass — an
informal shape kept because Discovery hypotheses are generated deterministically
and never round-trip through a provider.

### Evidence
| | |
|---|---|
| Owner | `brain/models.py` — `@dataclass Evidence` (`signal`, `source`, `detail`, `weight`, `observed_at`) |
| Authority | None |

**Three shapes exist.** The dataclass (weighted, used by `Confidence`);
`List[str]` on Discovery entities (human-readable lines); execution-result dicts
on verifications. Consolidating them would change `confidence_from_evidence()`
inputs and therefore scores, which §14 forbids in this phase. Recorded as debt.

### VerificationRequest
| | |
|---|---|
| Owner | `brain/models.py` |
| Purpose | A proposed **read-only** action that would discriminate between hypotheses. |
| Consumer | `brain/verification.py` — authorize then execute |
| Authority | Read-only, allowlisted. A request is not an authorization; `authorize()` is a separate step. |

### Recommendation
No single type. Four dict families with distinct `kind` strings: shadow
(`recommendation`), telemetry (`TELEMETRY_QUALITY_RECOMMENDATION`), provider
(`stop_recommendation`, a `StopDecision`), calibration (threshold proposals,
never self-applied). Unifying them is deferred: they have genuinely different
consumers and payloads, and a premature union type would be a lowest common
denominator.

---

## 3. Memory and provenance

### MemoryRecord
| | |
|---|---|
| Owner | `brain/models.py`; store `brain/memory.py` |
| Schema | `MemoryKind` enum |
| Persistent | Yes — JSONL |
| Provider safe | REDACTED |
| Laços exposed | Yes — `memory` (bounded to 12) |
| Authority | None |

### Runtime attestation
| | |
|---|---|
| Owner | `brain/attest.py` — `runtime_attestation()` |
| Purpose | Which code the running process actually loaded, versus the worktree. |
| Schema | `qhos-runtime-attestation-v1`; route `qhos-brain-v1` |
| Consumer | Discovery (as an Observation source), UI |
| Laços exposed | Yes — `runtime_attestation` |
| Authority | None |

**Frozen states for frontend use:** `CURRENT`, `STALE_RUNTIME`, `NOT_ATTESTABLE`.
Carries `running_revision`, `worktree_revision`, `config_hash`,
`route_schema_version`, `process_start_time`, `process_uptime_s`.

Context matters: a worktree that has moved ahead during development is
informational; a stale *production* runtime is not. The view model sets
`informational_only` from `deployment_context` rather than treating every drift
as an incident.

---

## 4. Artifacts

| Entity | Owner | Schema | Provenance | Notes |
|---|---|---|---|---|
| SoakSession | `brain/discovery/soak.py` | `qhos-brain-soak-v1` | policy block | Read-only collection; candidates stored `UNREVIEWED` + `not_training_truth` |
| Replay artifact | `brain/discovery/replay.py` | `qhos-brain-replay-v1` | policy block | Deterministic and hybrid variants |
| Replay fidelity | `scripts/replay_fidelity_check.py` | `qhos-replay-fidelity-v1` | **stamped** | 1.0 as of this phase |
| Fault matrix | `brain/discovery/fault_matrix.py` | `qhos-fault-matrix-v1` | policy block | Dev-only injection |
| Review row | `brain/discovery/review.py` | `qhos-brain-review-v1` | policy block | Human disposition is the only ground truth |
| Evaluation provenance | `brain/provenance.py` | `qhos-evaluation-provenance-v1` | — | The stamp itself |
| Defect ledger | `brain/defect_ledger.py` | `qhos-measurement-defects-v1` | — | 9 defects, 8 fixed forward, 1 open |
| Perf baseline | `scripts/perf_baseline.py` | `qhos-brain-perf-baseline-v1` | **stamped** | Level-2 read-only path |
| RC manifest | `brain/architecture.py` | `qhos-brain-rc-manifest-v1` | — | The release artifact |

### The full-fidelity rule

The intelligence blob (`qhos-brain-intelligence-v1`) is the **full internal
decision artifact** and carries `full_fidelity: True`. It has no presentation
caps, because soak and fault runs persist it and replay consumes it. A display
limit on that path silently decides what can be replayed — that was MD-07, and it
held replay fidelity at 0.9333.

Bounding lives in exactly two places, both of which affect only what is shown or
sent, never what was decided:

- `brain/view_model.py` — `PROJECTION_LIMITS`, for the UI
- `brain/context.py` — provider packet limits

A test (`test_engine_source_contains_no_slice_on_decision_relevant_lists`) parses
the engine's AST and fails if a slice reappears on a decision-relevant key.

---

## 5. State vocabularies public to artifacts or UI

Renaming any of these breaks historical artifacts. Append, never edit.

| Vocabulary | Defined in |
|---|---|
| `INCIDENT_STATES`, `SIGNAL_CLASSES` | `discovery/incident.py` |
| `IMPROVEMENT_STATES`, `BENEFIT_CLASSES` | `discovery/improvement.py` |
| `PENDING_CLASSES` | `discovery/pending.py` |
| `REVIEW_DISPOSITIONS` | `discovery/review.py` |
| `NOISE_CLASSES` | `discovery/metrics.py` |
| `RELATIONSHIPS`, candidate roles | `discovery/consolidation.py` |
| `DETECTION_VERDICTS` | `discovery/known_issue.py` |
| `FN_CATEGORIES` | `discovery/evaluation.py` |
| `ATTESTATION_KINDS` | `discovery/attestation.py` |
| `OUTPUT_STATUSES` | `provider_contract.py` |
| `REPLAY_STATUSES`, `SOAK_STATUSES` | `discovery/replay.py`, `discovery/soak.py` |
| `ROOT_TIERS` | `discovery/fingerprint.py` |
| Attestation states | `view_model._attestation_view()` |
| Feature states, compatibility, defect states | `architecture.py`, `provenance.py`, `defect_ledger.py` |

Most are module-level tuples rather than `Enum`s. Left as tuples: they are
already single-source and centralized per module, and converting them to enums
would change their JSON serialization, breaking every historical artifact for no
behavioural gain.

---

## 6. Single-source-of-truth audit — summary

| Entity | Verdict |
|---|---|
| Observation | **Single source.** Pre-normalization dicts are inputs, not duplicates. |
| PotentialIncident | **Duplicated** by legacy `shadow.py:_candidate()`. Marked DEPRECATED, not migrated. |
| ImprovementCandidate | Single source. |
| TelemetryQualityIssue | Single source, untyped (projection function). |
| Investigation | Single source; `ALLOWED_ACTIONS` split across two modules. |
| Hypothesis | Typed in `models.py`; Discovery uses dicts. Acceptable. |
| Evidence | **Three shapes.** Deferred — merging changes confidence scores. |
| Recommendation | **Four families.** Deferred by design. |
| MemoryRecord | Single source. |
| Runtime attestation | Single source, with a declared UI projection. |
| Candidate projections | Adapters, not duplicates, now that the view model exists. |
