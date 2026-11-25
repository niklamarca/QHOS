# Brain Frontend Contract — handoff for the Laços UI redesign

Architecture version 1 · view schema `qhos-brain-view-v1` · Frozen 2026-09-03

This document defines **data and semantics**, not visual design. It tells you
what exists, what it means, how fresh it is, and what you are not allowed to
imply. Layout, hierarchy and styling are the next phase's work.

---

## 1. Start here

```python
from brain import api
view = api.view_model()      # stable, read-only, bounded
```

Bind to this. Do **not** bind to `api.public_snapshot()` — it is the internal
shape, it is unbounded, and it will change with the engine.

Every response carries `schema`, `architecture_version`, `release_channel`,
`generated_at`, `read_only`, `sections`, `projection_limits` and
`absence_markers`. Check `schema` before parsing; refuse to render an unknown
major version rather than guessing.

---

## 2. Absence semantics — the one rule that matters most

**The backend never invents a number.** When something was not measured you get
an explicit marker, not a zero and not an empty string:

| Marker | Meaning |
|---|---|
| `UNKNOWN` | The field exists; its value could not be determined. |
| `NOT_AVAILABLE` | The source was not reachable or the data does not exist yet. |
| `NOT_MEASURED` | Nobody has measured this. Not the same as measuring zero. |
| `NOT_VERIFIED` | No human has confirmed this. Used for dispositions. |

`0` and `false` are **real measurements** and pass through unchanged.

A panel must render a marker as a marker. `NOT_MEASURED` displayed as `0` is the
single most damaging thing this UI can do: it turns "we do not know" into a
confident claim, and a Brain whose purpose is honest diagnosis cannot have a
dashboard that lies by omission. Prefer an em-dash, a greyed state, or the word
itself.

---

## 3. Truncation is explicit

Every list arrives as an envelope, never as a bare array:

```json
{ "items": [...], "total": 137, "shown": 20, "limit": 20, "truncated": true }
```

Show `shown of total` whenever `truncated` is true. Do not render `items.length`
as if it were the count — that is how a UI implies it is showing everything.

Limits are declared in `view.projection_limits`. They apply **only** to display:
they never affect what the Brain decided, what was persisted, or what can be
replayed. (That separation is deliberate — a display cap on the decision path was
a real defect, MD-07, and it corrupted replay for months.)

---

## 4. Field provenance

Selected fields arrive as provenance envelopes rather than raw values:

```json
{ "value": "medium", "source": "heuristic", "observed_at": 1756900000.0,
  "kind": "estimated", "authority": "read_only",
  "confidence": "NOT_MEASURED", "measured": false }
```

`kind` is the important one:

- `observed` — read from a real source
- `estimated` — computed by a heuristic
- `derived` — calculated from other fields

**Never present an `estimated` value as a measurement.** Improvement
impact/effort/risk are all estimates and are labelled as such.

Freshness comes from `system.last_cycle_at` and `system.staleness_s`. If
`staleness_s` exceeds a couple of cycle intervals, the whole view is stale and
should be visibly marked — the data is not wrong, it is old, and those are
different failures.

---

## 5. Sections

`view.sections` is the authoritative list and is stable.

### `system`
`brain_available`, `brain_running`, `cycles`, `uptime_s`, `last_cycle_at`,
`staleness_s`, `resources`, `schema_versions`.

### `trader`
`autonomy_mode`, `brain_can_trade` (always `false`), `brain_is_not_a_trading_path`,
`separate_from_brain_autonomy`.

**Trading autonomy and Brain autonomy are different ladders.** Rendering them in
one control would imply the Brain can trade at level 2. It cannot, at any level
currently reachable.

### `brain`
`authority_level`, `authority_name`, `shadow`, `can_apply`, `can_trade`,
`sandbox_enabled`, `level3_enabled`, `health_score`, `health_tone`,
`critical_issues`, `warnings`, `heartbeat`, `configuration_violation`.

`configuration_violation` is derived exclusively from the raw requested
autonomy value via `resolve_autonomy_level()`. A `configuration_violation`
supplied on the snapshot cannot replace, invent, or contradict that
derivation. Valid requested levels 0–2 emit the absence marker `UNKNOWN`.
Requested values above the ceiling emit `ARCHITECTURE_V1_LEVEL2_CEILING`
with the real requested value and `enforced=2`. Invalid values preserve the
raw requested value and `invalid_autonomy_level`. Floats are not integers:
`2.0` is not Level 2. A public document may carry a float only as
`{"__qhos_requested__": "float", "literal": "<python-repr>"}` — a bare
fractional number is rejected by both `validate_view` and `acceptView`.
Reserved markers accept only the tags `float` and `conflict`, with the
required own fields, canonical literals, and no invented keys. A malformed
reserved marker is not a generic mapping. Non-reserved mappings and dense
lists remain explicit invalid raw values. `requested_level` is raw authority
only when it is coherent with the enforced `level` (and `level_name` when
present). `level_name="sandbox"` always projects as Level 2 +
`ARCHITECTURE_V1_LEVEL2_CEILING`, even when it is the only autonomy field.
Any present `level_name` is validated: `None`, numbers, mappings, lists,
unknown strings, or incoherent names emit a conservative conflict
violation — never a silent `UNKNOWN`. A raw Level 3 in `requested_level` or
`level` also projects the ceiling. Other contradictory raw fields project
`{"__qhos_requested__": "conflict", ...}`. Absence of the autonomy object
does not invent a violation. Arrays that affect acceptance (`sections`,
`absence_markers`, operations lists, envelope `items`, identifier lists)
must expose every required index as an own property: holes and
prototype-inherited values are rejected. The workspace may show the field as
attention text. It is not a control and does not change autonomy.

### `live_intelligence`
`observer`, `soak_active`, `signals_total`, `candidates_total`,
`suppressed_total`, `budget`, `llm_called`, `pending_model`, `telemetry_ledger`.

`suppressed_total` is a feature, not waste: most signals are correctly
suppressed noise, and a high suppression rate with a low candidate count is the
system working.

### `incidents`
Bounded envelope, plus `root_incident_count` and `secondary_effect_count`.

Each row: `root_id`, `candidate_id`, `title`, `component`, `tier`, `status`,
`signal_class`, `severity`, `score`, `occurrence_count`, `affected_components`,
`affected_instances`, `sources`, `first_seen`, `last_seen`, `promotion_reason`,
`pending_class`, `role`, `derived_from`, `relationship`, `evidence`,
`human_disposition`, `is_training_truth`.

`affected_instances` is a breadth measurement, not a place to invent identities.
Discovery's `PotentialIncident` stores it as an integer count
(`RootCluster.affected_instances = len(instances) or 1`). The view model
emits that count when the producer supplied an integer (including `0`); a
sequence of identifiers when the producer already supplied one; `[]` when
the producer supplied an empty identifier list; `NOT_AVAILABLE` when the
field is absent or null; `UNKNOWN` when the value is malformed. A known
count must not become `[]`.

**`role` drives the relationship model:**

- `PRIMARY_ROOT_CANDIDATE` — a root cause
- `SECONDARY_EFFECT` — a real, distinct symptom caused by another root, linked
  by `derived_from` and qualified by `relationship`

A secondary effect is **not a duplicate**. Duplicates are suppressed and you
never see them. A secondary effect is genuine evidence that happens to have a
known upstream cause; collapsing or hiding it loses information. Show it nested
under its root, or linked to it, but show it.

`relationship` values, in descending strength: `SAME_ROOT`,
`LIKELY_DERIVED_EFFECT`, `TEMPORALLY_CORRELATED`, `INDEPENDENT`, `UNKNOWN`.
`TEMPORALLY_CORRELATED` is deliberately weak — it means "these co-occurred", not
"one caused the other". Do not render it with a causal arrow.

`human_disposition` defaults to `NOT_VERIFIED`. Only a human sets it, and
`is_training_truth` is false until they do.

### `improvements`
Bounded envelope. Rows carry `impact`/`effort`/`risk` as provenance envelopes
with `kind="estimated"`, plus `measured` and `auto_apply` (always `false`).

### `investigations`, `root_clusters`
Bounded envelopes.

### `telemetry_quality`
`issues` and `recommendations`, both bounded.

**Telemetry-quality issues are not incidents** and must not share a list with
them. Each carries `is_component_failure: false`. The distinction is
actionable: an incident means fix the component; a telemetry issue means the
component may be fine and the *observability* is broken. A UI that merges them
sends people to repair working systems.

Severity comes from breadth (`component_count`), not from noise volume.

### `cognition`
`mode`, `strategy`, `default_strategy` (`"deterministic"`),
`llm_calls_this_cycle`, `continuous_live_llm` (always `false`).

### `memory`
Bounded envelope of recent memory records.

### `provider`
`api_enabled`, `mode`, `live_llm_shadow_enabled`,
`paid_requires_explicit_gate` (always `true`), `performance`.

### `evaluation`
`proving_ground`, `held_out`, `real_model_experiment`. Any of these may be
`NOT_MEASURED`.

### `governance`
`change_requests_pending`, `automatic_change_requests` (always `0`),
`automatic_sandbox_runs` (always `0`), `requires_human_review`, `activity`.

### `runtime_attestation`
Frozen states: **`CURRENT`**, **`STALE_RUNTIME`**, **`NOT_ATTESTABLE`**.

Fields: `state`, `allowed_states`, `running_revision`, `worktree_revision`,
`config_hash`, `route_schema_version`, `process_start_time`, `process_uptime_s`,
`drift_reason`, `deployment_context`, `informational_only`.

This answers "is the page I am looking at served by the code I think is
running?" A served page can contain new panels while the interpreter runs an
older revision.

**Respect `informational_only`.** In development the worktree moves ahead
constantly; a red banner on every edit trains people to ignore it. In
production, `STALE_RUNTIME` is real and should be prominent.

---

## 6. Read-only guarantee

`view.operations` declares the surface:

```json
{ "available": ["record_human_disposition"],
  "unavailable_at_level_2": ["enable_sandbox", "enable_level3", "apply_change",
    "deploy", "restart_production", "submit_trade", "merge", "push",
    "change_autonomy_level"] }
```

The only write in the entire Level-2 UI is a human disposition:

```
POST /api/brain/shadow/feedback   { fingerprint, action }
```

`action` ∈ `CONFIRMED`, `DISMISSED`, `DUPLICATE`, `USEFUL`, `NOT_USEFUL`,
`UNCERTAIN`, `NEEDS_MORE_EVIDENCE`. It records metadata and an audit entry. It
does not open a change request, start a sandbox run, or trigger anything.

**Do not build affordances for anything in `unavailable_at_level_2`** — not even
disabled ones. A greyed-out "Deploy" button implies deploy is a thing this system
does when permissions allow. It is not.

---

## 7. Transport (§53, §54)

### Current state — accurate as of this freeze

| Property | Value |
|---|---|
| Endpoint | `ws://<host>:<port>/ws` |
| Payload | **Full snapshot every push.** No incremental events. |
| Interval | ~2s |
| Brain data | `state.brain` (full `public_snapshot()`), attached per push |
| Root schema version | **Absent** — `state` has no top-level `schema` |
| Heartbeat | Client sends `ping`, server replies `{"pong":1}` |
| Reconnect | Client reloads the whole page after 4s |
| Brain HTTP | `GET /api/brain`, `/health`, `/tasks`, `/audit` — tooling snapshots (`qhos-brain-v1`) |
| Brain UI transport | `GET /api/brain/view` — source of truth for `qhos-brain-view-v1` |
| Brain UI events | `GET /api/brain/events` — one-way `qhos-brain-events-v1` snapshots |

`GET /api/brain/view` is read-only. It calls `brain.api.view_model()` on the
runtime already bound via `peek_brain()`. If no runtime is bound it returns
HTTP 503 `{ok: false, reason: NOT_AVAILABLE}` — not a manufactured view
document. It does not call `public_snapshot()`, does not read `state.brain`,
and does not use the legacy `/ws` snapshot. Conditional `ETag` /
`If-None-Match` may yield 304. The ETag hashes the document after stripping
request-time clocks (`generated_at`, `runtime_attestation.process_uptime_s`).
`system.staleness_s` is visible freshness and is **included** in the ETag, so
a 304 cannot leave an old staleness value on screen. The HUD must not invent
elapsed freshness in the browser. A change to heartbeat, staleness, incidents,
evidence, envelopes, relationships, autonomy, or `can_trade` must produce a
new ETag.

`GET /api/brain/events` is a dedicated SSE stream. The first frame is always a
canonical snapshot or an explicit `unavailable` transport event. Later frames
are snapshot replacements when the ETag changes. The stream never mutates
Brain, never creates a Brain, and is not a trading or autonomy channel. Event
loss falls back to `GET /api/brain/view`. The workspace JS source is
`dashboard/brain_workspace/workspace.js`, served at
`/static/brain/workspace.js`.

### What the redesign should target

The current transport works but has three properties a new frontend should not
inherit:

1. **No incremental updates.** A full snapshot every 2 seconds is wasteful and
   makes it impossible to animate a transition, because you cannot tell what
   changed.
2. **No schema version at the root.** A client cannot detect a breaking change;
   it can only fail at a missing key.
3. **Reload-on-disconnect.** Losing all client state on a brief network blip is
   a poor experience and hides genuine connectivity problems.

Recommended target contract, for the frontend phase to implement:

```
→ on connect     { type: "snapshot",  schema: "qhos-brain-events-v1",
                   seq: N, view: <full view model> }
→ on change      { type: "patch",     schema: ..., seq: N+1,
                   section: "incidents", ops: [...] }
→ every 10s      { type: "heartbeat", schema: ..., seq: N, generated_at: ... }
← from client    { type: "resume",    last_seq: N }   → server replies with a
                                                        patch run or a fresh
                                                        snapshot if seq is stale
```

`brain.architecture.SCHEMAS["event_stream"]` (`qhos-brain-events-v1`) is
registered for this. The design is deliberately **not** implemented here: this
phase defines contracts, and building a new transport is a capability change.

**Do not poll per panel.** One subscription feeds every section; the view model
is built as a single document for exactly this reason.

### Expected update frequency

| Data | Changes |
|---|---|
| Health, resources, heartbeat | every cycle (~60s default) |
| Incidents, improvements, investigations | on cycle, when Discovery finds something |
| Runtime attestation | on process restart or worktree change |
| Evaluation, provider performance | only when an evaluation is explicitly run |
| Memory, governance activity | on event |

The Brain cycle is 60s by default, so a 2s transport is oversampling by ~30×.
Most pushes carry identical data.

---

## 8. What is not in the contract

Honesty about gaps, so the frontend does not plan around things that do not exist.

- **Historical/time-series APIs.** There is no endpoint for "incidents over
  time". Artifacts exist on disk (`.qhos-brain/shadow/`) but have no HTTP
  surface. If the redesign wants trends, that is new backend work.
- **Pagination.** Lists are bounded, not paginated. There is no cursor.
- **Filtering or search.** Not server-side. Filter client-side within the
  bounded set, and be honest that you are filtering a truncated list.
- **Per-incident detail endpoint.** Everything is in the snapshot; there is no
  "fetch incident by id".
- **Legacy shadow candidates.** The current dashboard renders a second,
  root-unaware candidate shape from `brain/shadow.py`. It is `DEPRECATED` and
  **absent from the view model on purpose.** Do not reintroduce it — build
  against `incidents`, which is the canonical shape.

---

## 9. Contract status

**READY_FOR_FRONTEND_REDESIGN**, with one caveat: the view model is complete and
tested, but the *transport* is still full-snapshot-over-WebSocket. A redesign can
begin against `api.view_model()` immediately. Implementing the incremental event
contract in §7 is a small, well-scoped backend task that can proceed in parallel.
