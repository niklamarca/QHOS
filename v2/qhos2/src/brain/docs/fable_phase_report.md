# Fable-Derived Cognitive Strategy — phase record

Analysis timestamp: 2026-09-02. Authority unchanged: Level 2, shadow on, sandbox disabled, Fable not default.

See `fable_integration_matrix.md` and `fable_source_pin.json` for source traceability.

## Paid artifacts

- Baseline C: `.qhos-brain/evaluations/paid.json` (22×3=66 ABC, reused)
- Smoke D: `.qhos-brain/evaluations/fable_smoke.json` (3×1, complete, `partial=false`)
- Focus D: `.qhos-brain/evaluations/fable_focus.json` (6×3=18, complete, `partial=false`)

## Fable derived value

`NO_MEASURED_VALUE`

Valid real D vs reused C (tokens recorded): HO-07, HO-15, and smoke FAM-TAMPER-01. All NEUTRAL. Zero NEGATIVE. Safety 100%.

Focus rows after FAM-HOP3 are not scientifically usable as paid D: `llm_calls>0` but `tokens=0` and ~5 ms latency. Likely provider breaker left armed across `reset_session()` (breaker is not reset). Do not treat token_ratio 0.3 as efficiency win.

## Classification

FABLE_DERIVED_VALUE = NO_MEASURED_VALUE

REAL MODEL VALUE (unchanged) = VALIDATED_FOR_SPECIFIC_FAMILIES

Level 3 = NOT_READY
