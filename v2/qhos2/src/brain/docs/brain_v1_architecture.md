# Brain Architecture v1 — Map and Authority

Architecture version 1 · `LEVEL2_SHADOW_RC` · Frozen 2026-09-03

---

## 1. The pipeline

```
                        ┌─────────────────────────────────────┐
                        │  QHOS TRADER  (separate process)    │
                        │  execution · OMS · risk · evolution │
                        └──────────────┬──────────────────────┘
                                       │ observed only, never called
                                       ▼
  SIGNALS          registry · health · logs · resources · queue depth · git
                   · runtime attestation · telemetry counters
                        │
  PERCEPTION       brain/perception.py — collect a snapshot. Reads surfaces,
                        │                never OMS/risk/broker internals.
  NORMALIZATION    brain/discovery/observation.py — snapshot → Observation
                        │   redaction · trust level · sensitivity · pre-existing
                        │   baseline · pending lifecycle · fingerprint
  DISCOVERY        brain/discovery/engine.py
                        │   noise suppression → clustering → root fingerprint
                        │   → scoring (discovery-score-v2) → threshold 0.42
                        ▼
  CANDIDATES       PotentialIncident · ImprovementCandidate
                        │              · TelemetryQualityIssue
  CONSOLIDATION    brain/discovery/consolidation.py
                        │   PRIMARY_ROOT_CANDIDATE vs SECONDARY_EFFECT
  INVESTIGATION    brain/discovery/investigation.py  (read-only, budgeted)
                        │
  MEMORY+CONTEXT   brain/memory.py · brain/context.py  (redacted, budgeted)
                        │
  STRATEGY         deterministic (default) │ current_hybrid │ real_hybrid
                        │                  │ fable_derived (experimental)
  VERIFICATION     brain/verification.py — allowlisted read-only actions
                        │
  RECOMMENDATION   candidates + hypotheses + proposed verifications
                        │
  HUMAN REVIEW     brain/discovery/review.py — the only ground truth
                        │
  LEARNING         review dispositions → FP/FN ledgers → calibration proposals
                        │                                (never self-applied)
                        ▼
                   ═══ STOPS HERE AT LEVEL 2 ═══
```

Nothing crosses back into the Trader. The arrow into the Brain is one-way.

---

## 2. Planes

The system separates five concerns. The separation is what makes the authority
claim checkable rather than aspirational.

### Control plane — `brain/control_plane.py`, `brain/api.py`
Read-only projection for Laços and the dashboard. Consumes snapshots, produces
view models. Cannot reach the engine's mutable state.

### Orchestrator — `brain/executive.py`
Goals and tasks for the Brain's *own* investigation work. Explicitly **not** the
HPC orchestrator (`orchestrator.name == "BrainOrchestrator"`,
`not_hpc_orchestrator: True`). It schedules analysis, never strategy execution.

### Cognitive provider — `brain/gateway.py`, `brain/providers.py`, `brain/reasoner.py`
The only path to a remote model. Off by default, gated at four levels
(§4 below), with output classified by `brain/provider_contract.py` so that a
timeout, a truncation and a refusal can never be mistaken for a result.

### Trader — external
Observed. Never invoked. `brain/` contains no import of `execution/`, OMS, risk
or broker modules — verified by test.

### Laços — the UI surface
Consumes `brain.api.view_model()`. Sees a stable projection, never internal
classes.

---

## 3. Dependency direction

Verified by `test_brain_architecture_contracts.py`:

| Rule | Status |
|---|---|
| `brain/` never imports `dashboard/` | Holds |
| `brain/discovery/` never imports `dashboard/` | Holds |
| `brain/models.py` imports stdlib only | Holds |
| `brain/` never imports execution/OMS/risk/broker | Holds |
| Deterministic core has no unguarded `brain.fable` import | Holds (fixed this phase) |
| `brain.strategies` can be imported without loading Fable | Holds (fixed this phase) |
| Discovery runs with `brain.fable` unimportable | Holds (fixed this phase) |

**Known inversions, retained:**

- `brain/runtime.py` and `brain/api.py` import evaluation *views*. Runtime
  displays evaluation results; it does not take authority from them. Acceptable.
- `brain/eval_cli.py` and `brain/fable/eval_abcd.py` import the gateway. An eval
  harness that makes paid calls must reach the provider. Acceptable.
- `brain/discovery/soak.py` and `review.py` import `brain.eval_paths` for atomic
  JSON writes. A genuine layering smell — a persistence helper living in an eval
  module. Left alone: moving it churns import paths across every artifact writer
  for no behavioural gain.

**Latent cycles**, currently broken by deferred imports rather than by structure:
`reasoner ↔ fable.strategy`, `api ↔ runtime`, `discovery.revision → engine`.
None fail at import time. Documented rather than restructured, because
restructuring them is architecture change, not consolidation.

---

## 4. Authority map

**A Brain recommendation is not an execution authorization.** Recommending and
being permitted are separate faculties; nothing in the system converts one into
the other automatically.

| Capability | Level 2 Shadow |
|---|---|
| Read telemetry, health, registry, logs, git, attestation | **YES** |
| Read its own memory, audit and evaluation artifacts | **YES** |
| Produce incidents, improvements, telemetry-quality issues | **YES** (recommend) |
| Produce hypotheses and proposed verifications | **YES** (recommend) |
| Execute allowlisted read-only verification actions | **YES** (test path) |
| Deterministic replay of persisted observations | **YES** (test path) |
| Controlled fault injection against in-memory dev snapshots | **YES** (test path, dev-only) |
| Execute anything in a sandbox | **NO** — capable, hardcoded off |
| Apply a code change | **NO** |
| Open/merge a PR, push | **NO** |
| Deploy or restart production | **NO** |
| Submit/modify/cancel an order | **NO** |
| Change risk limits or kill-switch | **NO** |
| Mutate OMS or broker state | **NO** |
| Change its own autonomy level | **NO** |
| Enable sandbox or Level 3 | **NO** |
| Call a paid model without an explicit gate | **NO** |

### The four gates on a paid call

All four must pass. They are independent, and the outermost is a deployment
decision rather than a script argument:

1. `brain_paid_replay_enabled` (deployment) — outermost, wired this phase
2. `brain_llm_api_enabled` **and** `brain_cognitive_mode != deterministic_only`
3. Per-run explicit flags: `--explicit --i-accept-cost`
4. Budgets: call count, token cap, timeout, session breaker, provider-health
   breaker, redaction, checkpoint, close lifecycle

### How the invariants are enforced

| Invariant | Mechanism | Kind |
|---|---|---|
| Shadow mode on | `AutonomyPolicy(shadow=True)` literal in `runtime.py` | Hardcoded |
| Sandbox off | `SANDBOX_ENABLED = False` in `sandbox.py` | Hardcoded |
| Autonomy ≤ 2 | `SCHEMA_PHASE_CAP` / `ARCHITECTURE_LEVEL_CAP` in `policies.py` | Hard ceiling. Requested values above 2 clamp to INVESTIGATE and record `configuration_violation`. Level 3 / Sandbox is never produced. |
| Paid calls off | Four independent gates | Config + runtime |
| No continuous LLM | The observation cycle contains no provider call | Structural |

The first two are deliberately **not** configurable. A flag that can turn shadow
mode off is strictly less safe than a literal that cannot.

> `brain_shadow_mode` exists in config and is read by nothing. Shadow mode is
> genuinely on, but for a better reason than earlier reports claimed. Tracked as
> **MD-09**, and the audit now asserts against the hardcode.

---

## 5. Feature boundary

| Feature | Status | Evidence |
|---|---|---|
| Deterministic Discovery | **ACTIVE** | 15 fault classes × 2 manifestations |
| Runtime attestation | **ACTIVE** | source coverage 1.0 where expected |
| Live shadow soak | AVAILABLE_OPT_IN | 3 sessions, 60 min, 16,461 signals |
| Real Hybrid | AVAILABLE_OPT_IN / evidence-gated | 3 of 4 controlled cases; n too small |
| Fable-derived | EXPERIMENTAL | NO_MEASURED_VALUE |
| Level-3 sandbox | CAPABLE_NOT_ENABLED | not evaluated |
| Continuous live LLM | DISABLED | asserted by tests |
| Legacy shadow candidates | DEPRECATED | superseded by `PotentialIncident` |

---

## 6. Brain self-observability (§56)

The Brain reports its own health without generating incidents from it.
`RUNTIME_REVISION_MATCH` is in `NOISE_KINDS` precisely for this reason: a
heartbeat confirming the absence of a problem must not consume candidate budget
every cycle. Without that suppression the Brain would investigate itself
perpetually.

Exposed: observer state, soak active, cycle count, last cycle timestamp and
staleness, cognitive budget, candidate/investigation counts, provider status,
health score and tone, attestation state.

---

## 7. Non-goals

Architecture v1 at Level-2 Shadow does **not** provide, and does not claim:

- automatic remediation
- production autonomy
- a guaranteed false-negative rate
- universal LLM superiority over deterministic analysis
- Fable superiority
- continuous remote cognition
- self-modification

Level-3 code exists and is isolated. The Level-2 runtime does not require it.
