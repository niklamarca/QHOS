# ADR — Brain Architecture v1 (Level-2 Shadow)

Status: **ACCEPTED / FROZEN FOR LEVEL-2 SHADOW** · 2026-09-03 · Architecture version 1

Each decision records what was actually observed, not what was intended. Where a
decision was forced by a mistake, the mistake is named.

---

## ADR-001 — Deterministic-first, not model-first

**Decision.** The always-on reasoning path is deterministic. No remote model
participates in the observation cycle.

**Why.** Determinism is what makes replay possible, and replay is what makes
every other claim auditable. A pipeline whose output depends on a remote model's
sampling cannot be replayed, so its results cannot be checked — which means a
regression in it is undetectable. Deterministic-first is not a statement that
models are weak; it is what allows us to *measure* whether they add anything.

It also turned out to be sufficient far more often than expected: across 15
controlled fault classes the deterministic arm found the root without help in
the large majority of cases.

**Cost.** Genuinely novel or ambiguous multi-component faults are where
determinism is weakest. That is exactly the population Real Hybrid is scoped to.

---

## ADR-002 — Real Hybrid is evidence-gated, not promoted

**Decision.** Real Hybrid stays `AVAILABLE_OPT_IN`, requires four independent
gates, and is never the default.

**Why.** The measured value is real but thin: 3 of 4 information-rich controlled
cases showed incremental value, and n=4 cannot support a promotion. More
importantly, the first pass at measuring that value was wrong in our favour
(MD-06): the classifier awarded root-cause credit for "diverging" from a
deterministic conclusion that was only a log title, so all four cases scored
identically and the arm looked better than it was. Re-scoring moved one case
down.

Having been wrong once in the flattering direction is a reason to keep the bar
high, not to lower it.

**Alternative rejected.** Enabling Hybrid by default for high-severity
candidates. Rejected: it would put a paid, non-replayable, non-deterministic
path in the always-on cycle for the sake of an unvalidated benefit.

---

## ADR-003 — Fable stays experimental, and must be deletable

**Decision.** `FABLE_DERIVED` is `EXPERIMENTAL`, never default, and the
deterministic core must run with `brain.fable` unimportable.

**Why.** Fable has produced `NO_MEASURED_VALUE` across every evaluation. Code
with no measured value is a liability if the working system depends on it. When
this phase audited the dependency direction, the deterministic Discovery engine
had a module-level `from brain.fable.effort import CognitiveEffortPolicy` — so
deleting the experiment would have broken the thing that works. The isolation
was assumed, not verified.

**Resolution.** Both Fable uses in Discovery are now guarded imports with a
deterministic fallback, and a test runs the full engine with `brain.fable`
blocked at the meta-path, asserting it still detects faults.

**Retained rather than deleted** because it is isolated, tested, and its concepts
(effort ceilings, completion guards) are the closest thing to a specification for
a future investigation loop. It costs nothing now that nothing depends on it.

---

## ADR-004 — Level 3 off, and not configurable

**Decision.** `SANDBOX_ENABLED = False` is a source constant, not an env var.

**Why.** A capability that can execute code in a worktree should require a code
change and a review to enable, not an environment variable that a misconfigured
deployment could set. The blast radius of the flag is larger than the
inconvenience of editing a file.

The same reasoning applies to shadow mode, and this is where the audit found
something uncomfortable: `brain_shadow_mode` exists in config, is documented in
`.env.example`, and is read by nothing — `runtime.py` passes `shadow=True` as a
literal. Every phase report that cited the flag as evidence was citing a value
with no causal connection to behaviour.

**Decision on that flag:** leave the hardcode, document the flag as
non-authoritative, record it as **MD-09**, and re-point the safety audit at the
hardcode. Wiring it would make shadow mode env-disableable, which is strictly
worse. The invariant held for a better reason than we had been claiming; the
claim was weak, not the behaviour.

---

## ADR-005 — Shadow-first, read-only Discovery

**Decision.** Discovery observes and recommends. It never writes, applies,
trades, deploys, or changes its own authority.

**Why.** A diagnostic system that can act on its conclusions must be right in a
way that a system that only reports does not. We do not yet have the false-
negative characterization to justify action, and we are honest that we do not:
"a guaranteed false-negative rate" is an explicit non-goal.

Read-only also makes controlled fault injection safe, which is what produced the
generalization evidence in the first place.

---

## ADR-006 — Replay fidelity is a contract, not a metric

**Decision.** Deterministic replay of newly generated controlled artifacts must
reach 1.0 on suppression, candidate roots, score, promotion and relationships.
Historical unfaithful artifacts stay labelled and are never re-scored.

**Why.** Fidelity below 1.0 does not mean "mostly good"; it means the artifact
cannot audit its own run, so any retrospective computed from it is
unfalsifiable. Two defects proved this concretely:

- **MD-03** — persistence dropped `state` and `lifecycle_known`, so every replay
  took a different pending branch from the run it replayed.
- **MD-07** — the engine truncated `observations` to 40 rows inside the artifact
  that soak and fault runs persist. A limit whose only purpose was presentation
  was silently deciding what could be replayed.

MD-07 is the reason for the rule now enforced in code: **the intelligence blob
carries no display caps.** Bounding belongs in `brain.view_model` (what is shown)
and `brain.context` (what is sent), never on the decision path. After the fix,
fidelity across the same 30 artifacts went from 0.9333 to 1.0 with no remaining
divergence.

Sessions A/B/C remain `replay_faithful=false`. Rewriting history to make old
numbers look better would destroy the only record of why we distrust them.

---

## ADR-007 — Provenance and a comparability gate

**Decision.** Evaluation artifacts carry a provenance stamp, and comparison is
gated on it. A comparison returns `COMPARABLE`, `COMPARABLE_WITH_CAVEATS`,
`NOT_COMPARABLE` or `INSUFFICIENT_PROVENANCE` — never a bare diff.

**Why.** Three evaluators changed semantics mid-project (MD-01, MD-04, MD-06).
Numbers produced before and after those changes are not on the same scale, but
they look identical in a JSON file. Without a gate, a "regression" between two
artifacts might just be a changed evaluator — and the reader has no way to tell.

The evaluator *name* is part of identity, not only its revision: two unrelated
evaluators that happen to sit at revision "v2" are still not comparable. That was
a bug in the first version of this gate, caught while testing it.

---

## ADR-008 — A central measurement defect ledger

**Decision.** `brain/defect_ledger.py` records every defect found in the
measurement apparatus, with a `result_impact` classification.

**Why.** Nine defects were found across the project, and every one of them was in
the evaluator rather than in the system being evaluated. Four of them flattered
the results. Recording them centrally, with which artifacts and which metrics
they invalidate, is what lets an old number stay interpretable instead of merely
surviving.

`metric_is_trustworthy()` makes this queryable, so a report cannot cite a number
that a known-defective evaluator produced without the defect surfacing.

---

## ADR-009 — A UI view model, not raw internals

**Decision.** The frontend binds to `brain.api.view_model()`, a stable
projection. The raw snapshot remains for tooling but is internal.

**Why.** Three reasons, in order of importance.

1. It puts display truncation somewhere it cannot damage the decision path —
   the structural fix for MD-07.
2. It lets internal models evolve without breaking the UI. The dashboard
   currently reads two different candidate shapes (Discovery and legacy shadow)
   and has to know both.
3. It makes "no fake data" enforceable. Absent values become explicit
   `UNKNOWN` / `NOT_AVAILABLE` / `NOT_MEASURED` / `NOT_VERIFIED` markers, so a
   panel cannot render a missing measurement as a confident zero. Estimates
   carry `kind="estimated"`. Human dispositions default to `NOT_VERIFIED`, never
   to confirmed.

---

## ADR-010 — Consolidate by deleting and documenting, not by rewriting

**Decision.** This phase removed one dead script, wired one dead flag, fixed one
isolation violation and one layering defect. It did **not** migrate the legacy
shadow candidate, unify the three `Evidence` shapes, or merge the two
`ALLOWED_ACTIONS` lists.

**Why.** Each deferred item requires changing behaviour that is currently
correct, in a phase whose purpose is to stop changing behaviour. Unifying
`Evidence` would alter `confidence_from_evidence()` inputs and therefore scores,
which the phase brief explicitly forbids. Migrating the legacy candidate touches
a live UI panel and a POST endpoint.

They are recorded as technical debt with reasons, which is more useful than a
half-finished migration inside a freeze.
