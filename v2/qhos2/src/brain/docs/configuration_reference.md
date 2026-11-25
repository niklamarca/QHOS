# Brain Configuration Reference — Architecture v1

Generated from `brain/config_contract.py`. Do not edit by hand;
`scripts/emit_rc_manifest.py` regenerates it and a test asserts the
declared flags match `SystemConfig`.

**Authority** means flipping the flag changes what the system is *allowed*
to do, not merely how much of it it does. **Paid** means it can lead to a
billable provider call.

| Flag | Type | Default | Safe production default | Authority | Paid | Experimental | Purpose |
|---|---|---|---|---|---|---|---|
| `brain_autonomy_level` | int | `2` | `2` | **YES** | no | no | Autonomy ladder. 0 observe, 1 advise, 2 investigate (read-only). |
| `brain_cognitive_context_files` | int | `6` | `6` | no | no | no | Max files in a provider context packet. |
| `brain_cognitive_context_lines` | int | `80` | `80` | no | no | no | Max lines per file in a provider context packet. |
| `brain_cognitive_context_logs` | int | `20` | `20` | no | no | no | Max log rows in a provider context packet. |
| `brain_cognitive_max_llm_calls` | int | `3` | `3` | no | yes | no | Runtime reasoner per-investigation call budget. |
| `brain_cognitive_max_memory` | int | `6` | `6` | no | no | no | Max memory records in a provider context packet. |
| `brain_cognitive_max_rounds` | int | `4` | `4` | no | yes | no | Reasoner round budget. |
| `brain_cognitive_max_tokens` | int | `1200` | `1200` | no | yes | no | Reasoner context token budget (what is assembled). |
| `brain_cognitive_mode` | str | `deterministic_only` | `deterministic_only` | **YES** | no | no | Reasoning path. deterministic_only blocks the LLM branch entirely. |
| `brain_cycle_interval_s` | int | `60` | `60` | no | no | no | Seconds between observation cycles. |
| `brain_enabled` | bool | `True` | `True` | **YES** | no | no | Start the Brain observation loop at boot. |
| `brain_graph_ephemeral_max` | int | `48` | `48` | no | no | no | Ephemeral knowledge-graph node ceiling. |
| `brain_live_llm_shadow_enabled` | bool | `False` | `False` | no | yes | yes | Declares intent to allow live shadow LLM use. |
| `brain_live_shadow_soak_enabled` | bool | `False` | `False` | **YES** | no | no | Create a bounded read-only soak session at boot. |
| `brain_llm_api_enabled` | bool | `False` | `False` | **YES** | yes | no | Master gate for paid Brain provider calls. |
| `brain_llm_explicit_eval` | bool | `False` | `False` | **YES** | yes | no | Second gate required by brain.eval_cli for paid evaluation runs. |
| `brain_llm_max_calls` | int | `3` | `3` | no | yes | no | Eval CLI per-run call budget. |
| `brain_llm_max_tokens` | int | `1200` | `1200` | no | yes | no | Provider request max_tokens. |
| `brain_llm_model` | str | `` | `` | no | no | no | Model override; empty falls back to llm_model_alpha. |
| `brain_llm_provider` | str | `anthropic` | `anthropic` | no | no | no | Provider selection. Only anthropic is implemented. |
| `brain_llm_timeout_s` | float | `20.0` | `20.0` | no | no | no | Provider HTTP timeout. |
| `brain_max_concurrent_tasks` | int | `8` | `8` | no | no | no | Executive task concurrency ceiling. |
| `brain_max_depth` | int | `6` | `6` | no | no | no | Goal decomposition depth ceiling. |
| `brain_max_retries` | int | `2` | `2` | no | no | no | Task retry ceiling. |
| `brain_max_tasks_per_goal` | int | `32` | `32` | no | no | no | Task fan-out ceiling per goal. |
| `brain_paid_replay_enabled` | bool | `False` | `False` | **YES** | yes | no | Outermost gate for paid hybrid replay. |
| `brain_sandbox_command_timeout_s` | int | `45` | `45` | no | no | yes | Sandbox per-command timeout. |
| `brain_sandbox_max_commands` | int | `12` | `12` | no | no | yes | Sandbox command ceiling. |
| `brain_sandbox_max_diff_bytes` | int | `100000` | `100000` | no | no | yes | Sandbox diff size ceiling. |
| `brain_sandbox_max_duration_s` | int | `180` | `180` | no | no | yes | Sandbox wall-clock ceiling. |
| `brain_sandbox_max_files_changed` | int | `8` | `8` | no | no | yes | Sandbox changed-file ceiling. |
| `brain_sandbox_max_retries` | int | `1` | `1` | no | no | yes | Sandbox retry ceiling. |
| `brain_shadow_mode` | bool | `True` | `True` | no | no | no | Historically intended to toggle shadow mode. |
| `brain_soak_max_age_days` | int | `7` | `7` | no | no | no | Soak artifact retention age. |
| `brain_soak_max_duration_s` | int | `60` | `60` | no | no | no | Soak session wall-clock ceiling. |
| `brain_soak_max_events` | int | `4000` | `4000` | no | no | no | Observation ceiling per soak session. |
| `brain_soak_max_sessions` | int | `8` | `8` | no | no | no | Retained soak artifacts. |

## Flags that do not control behaviour

### `brain_live_llm_shadow_enabled` — TELEMETRY_ONLY

Surfaced in snapshots; the observation cycle never calls a model regardless.

### `brain_shadow_mode` — NON_AUTHORITATIVE

Not read. runtime.py constructs AutonomyPolicy(shadow=True) with the literal, so shadow mode cannot be turned off by configuration. Retained because removing it would suggest shadow mode became optional. See MD-09.

## Hardcoded, deliberately not configurable

- **`SANDBOX_ENABLED` = `False`** (brain/sandbox.py) — Level 3 requires a source change and a review, not an env var.
- **`AutonomyPolicy(shadow=...)` = `True`** (brain/runtime.py) — Shadow mode is not disableable by configuration. See MD-09.

## Safe-default audit

- Flags declared: **37**
- Deviations from safe default: **none**
- Declared but missing from `SystemConfig`: **none**

| Invariant | Holds |
|---|---|
| `deterministic_mode` | YES |
| `paid_calls_off` | YES |
| `explicit_eval_off` | YES |
| `paid_replay_off` | YES |
| `live_llm_shadow_off` | YES |
| `soak_off` | YES |
| `autonomy_at_most_2` | YES |
| `sandbox_off` | YES |
| `fable_not_default` | YES |

**AUDIT OK: True**

## Notes on overlapping flags

- `brain_cognitive_max_llm_calls` — brain_llm_max_calls (eval CLI path). Same default, different consumer.
- `brain_cognitive_max_tokens` — brain_llm_max_tokens is the provider request cap (what is sent).
- `brain_llm_max_calls` — brain_cognitive_max_llm_calls (runtime reasoner path).

- `brain_autonomy_level` — Frozen Architecture v1 ceiling is Level 2 / INVESTIGATE. Only non-bool integers 0, 1, and 2 are directly valid. Requested values above 2 are clamped to 2 and recorded as ARCHITECTURE_V1_LEVEL2_CEILING. Negative integers enforce 0 and record invalid_autonomy_level. Strings, floats, bools, null, mappings, and sequences are invalid and enforce Level 2 with a violation projected on brain.configuration_violation. Level 3 / Sandbox is never produced.
- `brain_enabled` — The subsystem is on by default, but every capability inside it is off.
- `brain_llm_api_enabled` — Necessary but not sufficient; the mode and a per-run gate also apply.
- `brain_llm_max_tokens` — 1200 truncated every response during the paid fault-generalization replay; that run passed 4000 explicitly. The default is unchanged because a low cap fails safe: it costs a wasted call, not a wrong conclusion, and TRUNCATED is an explicit output status.
- `brain_paid_replay_enabled` — Was declared but unread until the consolidation phase wired it.
- `brain_sandbox_max_commands` — Bounds a capability that SANDBOX_ENABLED=False makes unreachable.
