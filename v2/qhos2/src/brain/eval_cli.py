"""Explicit real-cognitive evaluation. Never called by pytest.

Usage:
  py -3.13 -m brain.eval_cli --explicit --offline-only
  py -3.13 -m brain.eval_cli --explicit --i-accept-cost --smoke
  py -3.13 -m brain.eval_cli --explicit --i-accept-cost --experiment paid --runs 3

Does not enable sandbox, Level 3, trading, merge, or push.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from brain.local_config import config

from .cognitive_eval import compact_experiment_view, evaluation_artifact, run_abc
from .eval_paths import atomic_write_json, resolve_artifact_path
from .eval_taxonomy import filter_scenarios
from .fable.eval_abcd import lookup_reused_c, run_abcd
from .fable.ledger import StrategyLedger
from .fable.resume import completed_eval_keys
from .gateway import CognitiveGateway
from .ledger import ProviderLedger
from .providers import AnthropicProvider
from .redact import sanitize_error
from .sandbox import SANDBOX_ENABLED


def _load_scenarios():
    from importlib.util import module_from_spec, spec_from_file_location
    root = Path(__file__).resolve().parents[1] / "tests" / "brain_scenarios" / "families.py"
    spec = spec_from_file_location("brain_families", root)
    mod = module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(mod)
    return mod.all_eval_scenarios()


def credential_present() -> bool:
    key = str(getattr(config, "anthropic_api_key", "") or "")
    return bool(key) and key not in {"disabled"} and "COLE" not in key


def paid_execution_allowed(args: argparse.Namespace) -> Tuple[bool, str]:
    if not getattr(args, "explicit", False):
        return False, "missing_explicit"
    if SANDBOX_ENABLED:
        return False, "sandbox_enabled"
    if getattr(args, "offline_only", False):
        return False, "offline_only"
    if not getattr(args, "accept_cost", False):
        return False, "missing_i_accept_cost"
    if not bool(getattr(config, "brain_llm_api_enabled", False)):
        return False, "brain_llm_api_disabled"
    if not bool(getattr(config, "brain_llm_explicit_eval", False)):
        return False, "brain_llm_explicit_eval_disabled"
    if not credential_present():
        return False, "credential_unavailable"
    return True, "ok"


def safe_config_banner(args: argparse.Namespace, scenarios: List[Any], allow_real: bool) -> Dict[str, Any]:
    max_calls = int(getattr(config, "brain_llm_max_calls", 3) or 3)
    runs = max(1, int(getattr(args, "runs", 1) or 1))
    return {
        "brain_cognitive_mode": str(getattr(config, "brain_cognitive_mode", "deterministic_only")),
        "provider": str(getattr(config, "brain_llm_provider", "anthropic")),
        "model": str(getattr(config, "brain_llm_model", "") or getattr(config, "llm_model_alpha", "") or "-"),
        "scenario_count": len(scenarios),
        "runs_per_scenario": runs,
        "max_provider_calls": max_calls,
        "max_token_budget": int(getattr(config, "brain_llm_max_tokens", 1200) or 1200),
        "timeout_s": float(getattr(config, "brain_llm_timeout_s", 20) or 20),
        "estimated_max_requests": len(scenarios) * runs * max_calls if allow_real else 0,
        "credential": "present" if credential_present() else "absent",
        "real": allow_real,
        "sandbox": SANDBOX_ENABLED,
        "level3": False,
        "live_llm_shadow": bool(getattr(config, "brain_live_llm_shadow_enabled", False)),
    }


def _print_safe(banner: Dict[str, Any]) -> None:
    blob = json.dumps(banner, indent=2)
    lowered = blob.lower()
    for tok in ("sk-ant", "api_key", "authorization", "bearer "):
        if tok in lowered:
            raise RuntimeError("refused_to_print_secret")
    print(blob, flush=True)


def open_real_gateway() -> CognitiveGateway:
    """One HTTP client for the whole evaluation. Caller must close()."""
    return CognitiveGateway(
        mode="hybrid",
        provider=AnthropicProvider(),
        max_llm_calls=int(getattr(config, "brain_llm_max_calls", 3) or 3),
        timeout_s=float(getattr(config, "brain_llm_timeout_s", 20) or 20),
    )


def write_eval_checkpoint(
    dest: Path,
    *,
    rows: List[Dict[str, Any]],
    provider: str,
    model: str,
    runs_per_scenario: int,
    extra: Dict[str, Any],
    partial: bool,
) -> Path:
    payload = dict(extra)
    payload["partial"] = partial
    payload["completed_rows"] = len(rows)
    if rows:
        payload["last_scenario_id"] = rows[-1].get("scenario_id")
        payload["last_run_index"] = rows[-1].get("run_index")
    artifact = evaluation_artifact(
        rows=rows,
        provider=provider,
        model=model,
        runs_per_scenario=runs_per_scenario,
        extra=payload,
    )
    artifact["partial"] = partial
    atomic_write_json(dest, artifact)
    return dest


def _smoke_ok(rows: List[Dict[str, Any]]) -> Tuple[bool, str]:
    if not rows:
        return False, "empty_rows"
    for row in rows:
        if row.get("leaked"):
            return False, "ground_truth_leak"
        real = row.get("real_hybrid")
        if real is None:
            return False, "real_hybrid_missing"
        if (real.get("sides") or {}).get("engine", {}).get("fallback") and not real.get("llm_calls"):
            return False, "provider_fallback"
    return True, "ok"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Trader Brain real cognitive evaluation (explicit only)")
    parser.add_argument("--explicit", action="store_true", help="required acknowledgement")
    parser.add_argument("--i-accept-cost", action="store_true", dest="accept_cost")
    parser.add_argument("--offline-only", action="store_true")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", type=str, default="")
    parser.add_argument("--scenario", action="append", default=[], help="scenario id (repeatable)")
    parser.add_argument("--family", action="append", default=[], help="family name (repeatable)")
    parser.add_argument("--tag", action="append", default=[], help="taxonomy tag (repeatable)")
    parser.add_argument("--mode", default="abc", help="abc|abcd|deterministic_only|offline_hybrid|real_hybrid|fable_derived")
    parser.add_argument("--smoke", action="store_true", help="3 ambiguous scenarios, 1 run")
    parser.add_argument("--experiment", default="", help="value|safety|paid|value-all|fable|fable-smoke")
    parser.add_argument("--resume", action="store_true", help="skip completed (scenario_id, run_index) pairs")
    parser.add_argument("--reuse-c", default="", dest="reuse_c", help="path to existing REAL_HYBRID artifact for baseline C")
    parser.add_argument("--fable-real", action="store_true", dest="fable_real", help="run D with the real provider")
    args = parser.parse_args(argv)
    if not args.explicit:
        print("refused: pass --explicit. This command never runs from pytest.")
        return 2
    if SANDBOX_ENABLED:
        print("refused: SANDBOX_ENABLED is true; aborting.")
        return 3
    if args.smoke:
        args.runs = 1
    if args.experiment == "fable-smoke":
        args.smoke = False
        args.runs = 1
    allow_real, reason = paid_execution_allowed(args)
    abcd = str(args.mode).lower() == "abcd" or str(args.experiment).startswith("fable")
    scenarios = filter_scenarios(
        _load_scenarios(),
        scenario_ids=args.scenario,
        families=args.family,
        tags=args.tag,
        smoke=args.smoke,
        experiment=args.experiment,
    )
    if args.limit:
        scenarios = scenarios[: args.limit]
    if not allow_real and not args.offline_only and reason != "offline_only":
        print(f"real provider blocked: {reason}", flush=True)
    banner = safe_config_banner(args, scenarios, allow_real)
    _print_safe(banner)
    provider_name = "offline"
    model = ""
    real_gw = None
    dest = resolve_artifact_path(args.out)
    if allow_real:
        real_gw = open_real_gateway()
        if not real_gw.available:
            allow_real = False
            real_gw.close()
            real_gw = None
            print("real provider unavailable; recording UNPROVEN", flush=True)
        else:
            provider_name = getattr(real_gw.provider, "name", "anthropic")
            model = getattr(real_gw.provider, "model", "")
    ledger = ProviderLedger()
    strategy_ledger = StrategyLedger()
    rows: List[Dict[str, Any]] = []
    extra: Dict[str, Any] = {
        "allow_real": allow_real,
        "blocked_reason": None if allow_real else reason,
        "smoke": bool(args.smoke),
        "experiment": args.experiment,
        "abcd": abcd,
        "fable_real": bool(args.fable_real),
        "filters": {
            "scenario": args.scenario,
            "family": args.family,
            "tag": args.tag,
        },
    }
    baseline_c = None
    if args.reuse_c:
        import json as _json
        from pathlib import Path as _Path
        src = _Path(args.reuse_c)
        if src.exists():
            try:
                baseline_c = _json.loads(src.read_text(encoding="utf-8"))
                extra["c_reused_from"] = str(src)
            except Exception:
                baseline_c = None
    if args.resume and dest.exists():
        try:
            import json as _json
            prior = _json.loads(dest.read_text(encoding="utf-8"))
            rows = [r for r in (prior.get("results") or []) if isinstance(r, dict)]
            extra["resumed"] = True
        except Exception:
            rows = []
    done = completed_eval_keys({"results": rows}) if args.resume else set()

    def _checkpoint(partial: bool) -> None:
        extra["ledger"] = ledger.to_dict()
        extra["strategy_ledger"] = strategy_ledger.to_dict()
        extra["allow_real"] = allow_real
        write_eval_checkpoint(
            dest,
            rows=rows,
            provider=provider_name,
            model=model,
            runs_per_scenario=args.runs,
            extra=extra,
            partial=partial,
        )

    try:
        for scenario in scenarios:
            for n in range(max(1, args.runs)):
                if args.resume and (scenario.scenario_id, n) in done:
                    continue
                if real_gw is not None:
                    real_gw.reset_session()
                if abcd:
                    reused = lookup_reused_c(baseline_c, scenario.scenario_id, n) if baseline_c else None
                    row = run_abcd(
                        scenario,
                        real_gateway=real_gw,
                        allow_real=allow_real,
                        reuse_c=reused,
                        run_fable_real=bool(args.fable_real),
                    )
                else:
                    row = run_abc(scenario, real_gateway=real_gw, allow_real=allow_real)
                row["run_index"] = n
                rows.append(row)
                done.add((scenario.scenario_id, n))
                for key, provider in (
                    ("deterministic_only", "null"),
                    ("offline_hybrid", "offline"),
                    ("real_hybrid", provider_name),
                    ("fable_derived", "fable"),
                ):
                    block = row.get(key)
                    if not block:
                        continue
                    ve = block.get("verification_efficiency") or {}
                    rec = {
                        "provider": provider,
                        "model": model if key in {"real_hybrid", "fable_derived"} else provider,
                        "scenario_id": scenario.scenario_id,
                        "scenario_family": row.get("family"),
                        "run_id": f"{scenario.scenario_id}:{n}",
                        "diagnosis": float((block.get("dimensions_pct") or {}).get("diagnosis") or 0) / 100.0,
                        "verification_efficiency": float(ve.get("score") or 0) if isinstance(ve, dict) else 0.0,
                        "safety": float((block.get("dimensions_pct") or {}).get("safety") or 0) / 100.0,
                        "latency_s": block.get("latency_s") or 0,
                        "tokens": block.get("tokens") or 0,
                        "input_tokens": block.get("input_tokens") or 0,
                        "output_tokens": block.get("output_tokens") or 0,
                        "stop": block.get("stop"),
                        "actual_correct": block.get("passed"),
                        "system_confidence": block.get("system_confidence"),
                        "model_confidence": block.get("model_confidence"),
                    }
                    ledger.record(rec)
                    strategy_ledger.record({
                        **rec,
                        "strategy": key,
                        "accuracy": rec["diagnosis"],
                        "memory_relevance": float((((block.get("replay") or {}).get("memory_metrics") or {}).get("precision") or 0)),
                        "context_efficiency": float((((block.get("replay") or {}).get("context_metrics") or {}).get("relevant_context_ratio") or 0)),
                        "tool_calls": int(block.get("tool_calls") or 0),
                        "effort": block.get("effort") or "",
                        "completion_state": block.get("completion_state") or "",
                    })
                _checkpoint(partial=True)
                print(
                    f"checkpoint {len(rows)}/{len(scenarios) * max(1, args.runs)} "
                    f"{scenario.scenario_id} run={n} partial={dest}",
                    flush=True,
                )
    except Exception as exc:
        print(sanitize_error(exc), flush=True)
        try:
            _checkpoint(partial=True)
            print(f"wrote partial {dest}", flush=True)
        except Exception:
            pass
        if args.smoke:
            print("smoke failed; paid experiment must not continue", flush=True)
            return 4
        raise
    finally:
        if real_gw is not None:
            real_gw.close()
            real_gw = None
    extra["ledger"] = ledger.to_dict()
    extra["strategy_ledger"] = strategy_ledger.to_dict()
    extra["allow_real"] = allow_real
    if allow_real and args.smoke:
        ok, smoke_reason = _smoke_ok(rows)
        extra["smoke_ok"] = ok
        extra["smoke_reason"] = smoke_reason
        if not ok:
            print(f"smoke failed: {smoke_reason}", flush=True)
            write_eval_checkpoint(
                dest, rows=rows, provider=provider_name, model=model,
                runs_per_scenario=args.runs, extra=extra, partial=True,
            )
            print(f"wrote {dest}", flush=True)
            return 4
    artifact = evaluation_artifact(
        rows=rows,
        provider=provider_name,
        model=model,
        runs_per_scenario=args.runs,
        extra={**extra, "partial": False},
    )
    artifact["partial"] = False
    atomic_write_json(dest, artifact)
    cmp = artifact["comparison"]
    print(json.dumps({
        "comparison": {
            k: cmp[k] for k in (
                "n", "root_cause", "incident_classification", "stop_accuracy",
                "safety", "cognitive_gain", "where_the_model_helps",
                "where_the_model_hurts", "real_model_value_readiness",
                "level3_readiness",
            ) if k in cmp
        },
        "view": compact_experiment_view(artifact),
    }, indent=2, default=str), flush=True)
    print(f"wrote {dest}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
