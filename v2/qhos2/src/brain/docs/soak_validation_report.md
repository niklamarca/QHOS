# Real Shadow Soak Validation — phase record

Analysis: 2026-09-03. Authority unchanged: Level 2 INVESTIGATE, Shadow ON,
`SANDBOX_ENABLED=false`, soak LLM OFF, Level 3 OFF. Repo revision `5ec6e2e`.

## Fresh baseline → end

| Suite | Start | End |
|---|---|---|
| Brain-related | 236 passed | **259 passed** |
| Global | 348 / 14 failed / 6 collection errors | **371 / 14 failed / 6 collection errors** |

Known failure/error set unchanged (PRE_EXISTING). NEW regressions: **ZERO**.

## Breaker

`SessionCircuitBreaker` vs `ProviderHealthCircuitBreaker` in `brain/policies.py`.
`CognitiveGateway.reset_session()` clears session breaker only.
Offline tests in `tests/test_brain_gateway_breaker.py`. Fable paid bench NOT rerun.

## Real soak

- `REAL_SOAK = EXECUTED` (out-of-process, HTTP+filesystem perception; did not restart Trader)
- Duration: **30.0s**, 6 cycles, LLM=0, writes=0, CR=0, sandbox=0
- Session: `soak_227d1e33f65f` (~87KB under `.qhos-brain/shadow/`)
- Signals: 138 received, 119 suppressed, 110 duplicates
- Candidates: 12 PotentialIncidents
- Live `/api/brain` = HTTP 404 (process predates Brain routes) → `LIVE_UI_CLICKTHROUGH = NOT_VERIFIED`
- Reviewed: 1 USEFUL ccxt missing-dep + 5 DUPLICATE siblings; 6 pending modules NOT_USEFUL; 1 USEFUL logs improvement
- Precision (reviewed evidence, not training truth): useful≈0.15; duplicate_escape≈0.38; noise_escape≈0.46
- Threshold recommendation: not auto-applied (useful scores not above noise)
- Deterministic replay: noticed=true, candidate_n=12, llm=0
- Real Hybrid: gate denied; paid hybrid not run

> **Superseded for calibration numbers.** The 12 candidates and ~0.15 precision
> recorded below are the pre-calibration baseline. See
> `discovery_calibration_report.md` for the root-fingerprinting and pending-model
> results that replay this same session down to 1 candidate.

## Classification

- REAL_DISCOVERY_VALUE = **CANDIDATE**
- REAL_HYBRID_REPLAY_VALUE = **NOT_MEASURED**
- FABLE_DERIVED_VALUE = **NO_MEASURED_VALUE**
- LIVE SHADOW = **SOAK_VALIDATED_PARTIAL**
- LEVEL 3 = **NOT_ENABLED / NOT_READY**
