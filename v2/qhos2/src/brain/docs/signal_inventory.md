# Existing signal sources

Source of truth: `brain.discovery.inventory.inventory()`.
No new telemetry bus was added. Discovery consumes what already exists.

Prefer EventBus / existing health events. Cycle-interval polling is the budget
(`brain_cycle_interval_s`, default 60s). Never poll per LLM call.

| Source | Event type | Frequency | Reliability | Component | Sensitivity | Adapter | Push | Poll | Why poll if any |
|---|---|---|---|---|---|---|---|---|---|
| event_bus | EventType.* | push | high | core.events | mixed | BrainRuntime._on_bus_event | yes | no | — |
| registry | module health snapshot | per_cycle | medium | core.registry | low | perception.observe_registry | no | yes | no change stream |
| current_state | CURRENT_STATE.json | per_cycle | medium | governance.epistemics | low-medium | perception.observe_truth | no | yes | file snapshot |
| error_log | errors.log tail | per_cycle | medium | logs | high | perception.observe_errors | no | yes | tail only |
| git | recent commits / dirty | per_cycle | high | repository | low | perception.observe_git | no | yes | no in-process hook |
| hpc_orchestrator | cycle/queue/kill | per_cycle + JOB_* | medium | orchestrator | medium | perception.observe_hpc | yes | yes | snapshot complements JOB_FAILED |
| resources | cpu/ram | per_cycle | medium | host | low | perception.observe_resources | no | yes | psutil snapshot |
| committee | committee_loop.json | per_cycle | low | evolution.committee | low | perception.observe_committee | no | yes | not Brain cognition |
| health_engine | derived health | per_cycle | high | brain.health | low | health.evaluate | no | no | derived |
| provider_ledger | eval/provider rows | on_eval | high | brain.ledger | low | ProviderLedger | no | no | eval only |
| strategy_ledger | strategy rows | on_eval | high | brain.fable.ledger | low | StrategyLedger | no | no | eval only |
| knowledge_graph | entity/relation | on_cycle | medium | brain.memory | low | KnowledgeGraph | no | no | in-memory |
| incident_history | Incident | on_open | high | brain.runtime | low | BrainRuntime.incidents | no | no | existing |

`duplicate_telemetry = false`.

## Registry `status` semantics (measured 2026-09-03)

`/api/status.modules[*]` returns `{status, healthy, uptime, error_count, last_error}`.
Against the live Trader every one of 15 modules reported:

```
{"status": "pending", "healthy": false, "uptime": null, "error_count": 0, "last_error": null}
```

`pending` there is the endpoint's default for a module absent from
`core.registry`, not a component that is initializing. Discovery therefore treats
`uptime is None and error_count == 0` as **no lifecycle evidence**:

- `healthy: false` alone never makes a `health` observation out of a pending status;
- elapsed time cannot age a static default into `STALE_PENDING`
  (`brain.discovery.pending.PendingTracker`, class `UNKNOWN_PENDING`);
- five or more components sharing that shape for three or more cycles collapse
  into **one** systemic candidate (`root_tier = TELEMETRY`), not one per module;
- promotion still happens on real evidence: `error_count > 0`, a dependent
  component erroring, a missed SLA, or staleness once `uptime` is populated.

Reliability of the `registry` source is therefore **low for `pending`** and
medium for `ok`/`error`. See `TELEMETRY_QUALITY_LEDGER` for measured yield.
