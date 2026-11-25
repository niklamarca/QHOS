"""Selective REAL_HYBRID replay on reviewed real candidates. Explicitly paid.

TEST FIXTURE ONLY, adapted from the pinned historical harness. Provider calls
and runtime candidate loading are disabled. Pure analysis functions preserved.
Never invoked by pytest with paid flags. Requires --explicit and --i-accept-cost, a candidate
allowlist, and call/token/timeout budgets. Read-only: no ChangeRequest, no
SandboxRun, no writes to production state.

Usage:
  PYTHONPATH=. py -3.13 scripts/hybrid_replay_paid.py \
      --explicit --i-accept-cost --candidates root_a,root_b \
      --max-calls 2 --max-tokens 1200 --timeout 45 --tag smoke
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from brain.discovery.replay import compare_same_evidence, paid_replay_gate
from brain.discovery.review import strip_review_labels
from brain.provider_contract import call_metrics, classify_output
from brain.redact import redact_mapping

LONG = Path(".qhos-brain/shadow/long_soak_result.json")
MULTI = Path(".qhos-brain/shadow/multi_session_soak.json")
PRIOR = Path(".qhos-brain/shadow/soak_227d1e33f65f.json")
CALIB = Path(".qhos-brain/shadow/calibration_replay.json")
FAULT_MATRIX = Path(".qhos-brain/shadow/fault_matrix_g1_frozen_primary.json")
SHADOW = Path(".qhos-brain/shadow")

_WORD_RE = re.compile(r"[a-z][a-z0-9_./-]{3,}")
# Terms that are always in scope for a read-only investigator.
_ALLOWED_VOCAB = frozenset({
    "error", "errors", "exception", "module", "modules", "missing", "dependency",
    "dependencies", "import", "imports", "install", "installed", "package",
    "packages", "component", "components", "instance", "instances", "symbol",
    "symbols", "pending", "state", "status", "startup", "initialization",
    "duration", "threshold", "expected", "cycle", "cycles", "evidence",
    "verify", "verification", "check", "inspect", "logs", "read-only",
    "readonly", "hypothesis", "root", "cause", "causes", "confidence",
    "unknown", "uncertain", "environment", "runtime", "python", "requirements",
    "traceback", "stack", "occurrence", "occurrences", "score", "severity",
    "source", "sources", "health", "healthy", "unhealthy", "degraded", "stale",
    "failed", "timeout", "retry", "retries", "latency", "data", "ingestion",
    "provider", "exchange", "exchanges", "market", "config", "configuration",
})


def sanitized_evidence(candidate: Mapping[str, Any]) -> Dict[str, Any]:
    """Exactly what the Brain had at the time. No human labels, no score labels."""
    clean = strip_review_labels(candidate)
    for key in ("ground_truth", "expected", "answer", "review_notes", "notes"):
        clean.pop(key, None)
    sig = dict(clean.get("root_signature") or {})
    components = [str(c) for c in (clean.get("affected_components") or [])]
    return redact_mapping({
        "root_title": clean.get("root_title"),
        "root_tier": clean.get("root_tier"),
        "component": clean.get("component"),
        "affected_components": components[:8],
        # A silently truncated list next to an untruncated count reads as an
        # inconsistency in the evidence, and a careful reader will spend a
        # hypothesis on it. Declare the truncation instead.
        "affected_components_total": len(components),
        "affected_components_truncated": len(components) > 8,
        "affected_instances": clean.get("affected_instances"),
        "occurrence_count": clean.get("occurrence_count"),
        "severity": clean.get("severity"),
        "first_seen": clean.get("first_seen"),
        "last_seen": clean.get("last_seen"),
        "evidence": list(clean.get("evidence") or [])[:8],
        "observation_samples": [
            str(o.get("summary") or "")[:200] for o in list(clean.get("occurrences") or [])[:6]
        ],
        "root_signature": {
            "exception_type": sig.get("exception_type"),
            "dependency": sig.get("dependency"),
            "error_code": sig.get("error_code"),
            "component_family": sig.get("component_family"),
            "normalized_message": sig.get("normalized_message"),
        },
        "authority": "read_only_investigation_level_2",
        "allowed_actions": ["inspect_logs", "inspect_health", "request_verification"],
        "forbidden_actions": ["patch", "restart", "deploy", "trade", "change_config"],
    })


def deterministic_arm(candidate: Mapping[str, Any]) -> Dict[str, Any]:
    """What the deterministic engine already concluded, without any model."""
    sig = dict(candidate.get("root_signature") or {})
    dependency = sig.get("dependency") or ""
    interpretation = str(candidate.get("root_title") or "")
    verifications = ["inspect_logs", "inspect_health"]
    if dependency:
        verifications = ["inspect_logs", "verify_import_availability"]
    return {
        "strategy": "deterministic",
        "interpretation": interpretation,
        "root_hypothesis": interpretation,
        "identified_dependency": dependency,
        "identified_exception": sig.get("exception_type") or "",
        "cross_component": list(candidate.get("affected_components") or [])[:8],
        "recommended_verifications": verifications,
        "uncertainty": "no causal claim; occurrence correlation only",
        "recommendation": "human review; do not patch",
        "hypotheses": [],
        "llm_calls": 0,
        "tokens": 0,
        "unsupported_claims": 0,
    }


def _evidence_vocab(evidence: Mapping[str, Any]) -> set:
    blob = json.dumps(evidence, default=str).lower()
    return set(_WORD_RE.findall(blob)) | _ALLOWED_VOCAB


# Abbreviations that survive tokenisation and look like dotted module paths.
_ABBREVIATIONS = frozenset({"e.g", "i.e", "etc", "vs", "cf"})


def _looks_like_an_entity(word: str) -> bool:
    """Whether a token names a thing, rather than being punctuation debris.

    ``core.data_ingestion`` is an entity the model may have invented. ``e.g`` is
    a comma with ambitions, and flagging it as an unsupported claim discredits
    the whole heuristic.
    """
    if word in _ABBREVIATIONS:
        return False
    if "_" in word or "/" in word:
        return True
    if "." not in word or word.endswith("."):
        return False
    # A real dotted path has substantial segments on both sides of every dot.
    return all(len(part) >= 3 for part in word.split(".") if part)


def unsupported_claims(packet: Mapping[str, Any], evidence: Mapping[str, Any]) -> Dict[str, Any]:
    """Heuristic: flag nouns asserted by the model that are absent from evidence.

    Reported as a heuristic signal, not a proof of hallucination.
    """
    vocab = _evidence_vocab(evidence)
    flagged: List[Dict[str, Any]] = []
    for hypothesis in list(packet.get("hypotheses") or [])[:8]:
        if not isinstance(hypothesis, Mapping):
            continue
        statement = str(hypothesis.get("statement") or "")
        words = {w.strip(".,;:!?") for w in _WORD_RE.findall(statement.lower())}
        novel = sorted({w for w in words if w and w not in vocab})
        # Only technical-looking tokens matter (module paths, snake_case, dotted).
        # Strip sentence punctuation first: "environment." is not a new entity.
        novel = [w for w in novel if _looks_like_an_entity(w)]
        if novel:
            flagged.append({"statement": statement[:200], "novel_terms": novel[:6]})
    return {"count": len(flagged), "flagged": flagged, "method": "heuristic_vocabulary_diff"}


def hybrid_arm(*args, **kwargs):
    """Historical test fixture: external providers are deliberately unavailable."""
    raise RuntimeError("historical_fixture_network_disabled")


INCREMENTAL_VALUE_CLASSES = (
    "NO_INCREMENTAL_VALUE",
    "VERIFICATION_VALUE",
    "UNCERTAINTY_VALUE",
    "ROOT_CAUSE_VALUE",
    "CROSS_COMPONENT_VALUE",
    "MEMORY_VALUE",
    "NEGATIVE_VALUE",
)


def incremental_value(
    det: Mapping[str, Any],
    hyb: Mapping[str, Any],
    *,
    cost_usd: float = 0.0,
) -> Dict[str, Any]:
    """What Hybrid added *beyond* the deterministic arm, not whether it was right.

    A correct answer that the deterministic engine already produced is not value;
    it is a restatement that cost money. Classes are additive and reported with
    the evidence for each, so a reader can disagree with the verdict.
    """
    if not hyb.get("counts_as_hybrid_success"):
        return {
            "classes": ["NO_INCREMENTAL_VALUE"],
            "primary": "NO_INCREMENTAL_VALUE",
            "reason": f"no usable model output ({hyb.get('output_status')})",
            "cost_usd": round(cost_usd, 4),
            "restated_deterministic": False,
        }

    # The whole deterministic conclusion, not just its headline. For an
    # ERROR_CODE-tier candidate the interpretation is the log title, so scoring
    # overlap against it alone makes every model answer look novel.
    # Only the fields where the deterministic arm asserts something about the
    # root. Its `uncertainty` and `recommendation` strings are fixed boilerplate:
    # counting their words would make every candidate look like a stated root.
    det_claim = " ".join(str(det.get(k) or "") for k in (
        "interpretation", "root_hypothesis", "identified_exception",
        "identified_dependency",
    )).lower()
    det_text = det_claim + " " + " ".join(
        str(c) for c in (det.get("cross_component") or [])
    ).lower()
    hyps = list(hyb.get("hypotheses") or [])
    verifs = list(hyb.get("recommended_verifications") or [])
    unsupported = int((hyb.get("unsupported_claims") or {}).get("count") or 0)

    classes: List[str] = []
    evidence: Dict[str, Any] = {}

    # Did it reach the same conclusion the deterministic arm already had?
    top = str((hyps[0] if hyps else {}).get("statement") or "").lower()
    det_tokens = {t for t in re.findall(r"[a-z]{4,}", det_text)}
    top_tokens = {t for t in re.findall(r"[a-z]{4,}", top)}
    overlap = len(det_tokens & top_tokens) / max(1, len(det_tokens)) if det_tokens else 0.0
    restated = overlap >= 0.4
    evidence["top_hypothesis_overlap_with_deterministic"] = round(overlap, 3)

    # A deterministic arm that only echoed an error code never made a root claim,
    # so "the model disagreed with it" is not evidence of insight. Both regimes
    # can earn ROOT_CAUSE_VALUE, but they earn it for different reasons and a
    # reader must be able to tell which one applied.
    claim_tokens = {t for t in re.findall(r"[a-z]{4,}", det_claim)}
    baseline_root_claim = len(claim_tokens) >= 6
    evidence["deterministic_root_claim"] = baseline_root_claim

    # Alternatives beyond the single deterministic reading.
    alternatives = [h for h in hyps[1:] if float(h.get("model_confidence") or 0) > 0.0]
    if baseline_root_claim:
        earned_root = len(alternatives) >= 2
        root_basis = "differential alternatives against a stated deterministic root"
    else:
        # Supplying a mechanism where there was none only counts if the mechanism
        # is grounded in the evidence that was actually sent.
        flagged = {
            str(f.get("statement") or "")[:200]
            for f in ((hyb.get("unsupported_claims") or {}).get("flagged") or [])
        }
        top_grounded = str((hyps[0] if hyps else {}).get("statement") or "")[:200] not in flagged
        earned_root = len(alternatives) >= 2 and top_grounded
        root_basis = (
            "supplied a grounded mechanism where deterministic had only a log title"
            if earned_root else
            "deterministic made no root claim and the model's top hypothesis is ungrounded"
        )
        evidence["top_hypothesis_grounded_in_evidence"] = top_grounded
    evidence["root_cause_basis"] = root_basis
    if earned_root:
        classes.append("ROOT_CAUSE_VALUE")
        evidence["differential_hypotheses"] = len(alternatives) + 1

    # Verifications the deterministic arm did not propose.
    det_verifs = {str(v).lower() for v in (det.get("recommended_verifications") or [])}
    new_verifs = [v for v in verifs if str(v.get("target") or "").lower() not in det_verifs]
    if len(new_verifs) >= 2:
        classes.append("VERIFICATION_VALUE")
        evidence["new_verifications"] = len(new_verifs)
        evidence["ranked_by_information_gain"] = sum(
            1 for v in verifs if v.get("expected_information_gain")
        )

    # Calibrated uncertainty rather than a claimed root cause.
    spread = [float(h.get("model_confidence") or 0.0) for h in hyps]
    if hyb.get("stop_recommendation") in {"NEEDS_MORE_EVIDENCE", "LIKELY_CAUSE"} and (
        len(spread) >= 2 and (max(spread) - min(spread)) >= 0.2
    ):
        classes.append("UNCERTAINTY_VALUE")
        evidence["confidence_spread"] = round(max(spread) - min(spread), 3)
        evidence["stop_recommendation"] = hyb.get("stop_recommendation")

    # Reasoning across components rather than about one line.
    text = json.dumps(hyps + list(hyb.get("risk_notes") or []), default=str).lower()
    mentioned = {c for c in ("kill_switch", "risk_engine", "storage", "execution",
                             "portfolio", "ingestion", "registry", "dashboard")
                 if c.replace("_", "") in text.replace("_", "")}
    if len(mentioned) >= 3:
        classes.append("CROSS_COMPONENT_VALUE")
        evidence["components_reasoned_about"] = sorted(mentioned)

    if unsupported >= 3:
        classes.append("NEGATIVE_VALUE")
        evidence["unsupported_claims"] = unsupported

    # MEMORY_VALUE means the model connected this incident to a *previous* one.
    # The sanitized packet carries only this root's own window, so there is no
    # prior-incident context to connect to and the class cannot be earned here.
    # Saying so is more useful than leaving a silently unreachable label.
    memory_context = bool(det.get("memory_context") or hyb.get("memory_context"))
    evidence["memory_value_evaluable"] = memory_context
    if not memory_context:
        evidence["memory_value_blocker"] = "no prior-incident memory in the evidence packet"

    if not classes:
        classes.append("NO_INCREMENTAL_VALUE")

    # §32: a model that reaches the deterministic conclusion and then lists
    # alternatives around it has not found a root cause we did not have. The
    # alternatives are still worth something, but crediting them as
    # ROOT_CAUSE_VALUE would report "the model found the cause" for a
    # restatement. Rank the honest contribution first in that case.
    order = ("NEGATIVE_VALUE", "ROOT_CAUSE_VALUE", "CROSS_COMPONENT_VALUE",
             "VERIFICATION_VALUE", "UNCERTAINTY_VALUE", "MEMORY_VALUE",
             "NO_INCREMENTAL_VALUE")
    if restated:
        order = ("NEGATIVE_VALUE", "VERIFICATION_VALUE", "UNCERTAINTY_VALUE",
                 "CROSS_COMPONENT_VALUE", "ROOT_CAUSE_VALUE", "MEMORY_VALUE",
                 "NO_INCREMENTAL_VALUE")
    primary = next((c for c in order if c in classes), "NO_INCREMENTAL_VALUE")
    return {
        "classes": classes,
        "primary": primary,
        "primary_qualifier": (
            "differential alternatives around a restated primary hypothesis"
            if restated and "ROOT_CAUSE_VALUE" in classes else
            root_basis if "ROOT_CAUSE_VALUE" in classes and not baseline_root_claim else ""
        ),
        "restated_deterministic": restated,
        "evidence": evidence,
        "cost_usd": round(cost_usd, 4),
        "tokens": int(hyb.get("tokens") or 0),
        "latency_s": hyb.get("latency_s"),
        "reason": (
            f"reached the same conclusion as deterministic (overlap {overlap:.2f}) "
            f"and added: {', '.join(c for c in classes if c != 'NO_INCREMENTAL_VALUE') or 'nothing'}"
            if restated else
            f"deterministic asserted no root ({len(claim_tokens)} content tokens); "
            f"{root_basis}; added: {', '.join(classes)}"
            if not baseline_root_claim else
            f"diverged from a stated deterministic root (overlap {overlap:.2f}); "
            f"added: {', '.join(classes)}"
        ),
    }


# Anthropic Sonnet list price at time of run, for cost-adjusted reporting.
USD_PER_MTOK_IN = 3.0
USD_PER_MTOK_OUT = 15.0


def call_cost_usd(tokens_in: int, tokens_out: int) -> float:
    return (tokens_in / 1e6) * USD_PER_MTOK_IN + (tokens_out / 1e6) * USD_PER_MTOK_OUT


def load_candidates():
    """Historical test fixture: no access to runtime data or credentials."""
    raise RuntimeError("historical_fixture_runtime_io_disabled")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--explicit", action="store_true")
    ap.add_argument("--i-accept-cost", dest="accept_cost", action="store_true")
    ap.add_argument("--candidates", default="")
    ap.add_argument("--max-calls", type=int, default=0)
    ap.add_argument("--max-tokens", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=45.0)
    ap.add_argument("--tag", default="smoke")
    args = ap.parse_args()

    requested = [c.strip() for c in str(args.candidates).split(",") if c.strip()]
    gate = paid_replay_gate(
        explicit=bool(args.explicit),
        accept_cost=bool(args.accept_cost),
        candidate_ids=requested,
        max_calls=int(args.max_calls),
        max_tokens=int(args.max_tokens),
    )
    checkpoint = SHADOW / f"hybrid_replay_{args.tag}.json"
    done: Dict[str, Any] = {}
    if checkpoint.exists():
        # Resume must only skip rows that actually reached the provider. A row
        # that failed the gate or errored is unfinished work: treating it as done
        # silently replays the failure and reports zero calls as if budgeted.
        done = {
            r["root_id"]: r
            for r in json.loads(checkpoint.read_text(encoding="utf-8")).get("rows") or []
            if int((r.get("real_hybrid") or {}).get("llm_calls") or 0) > 0
        }

    if not gate.get("allowed"):
        print(json.dumps({"gate": gate, "llm_calls": 0, "writes": 0}, indent=2))
        return 0

    pool = load_candidates()
    rows: List[Dict[str, Any]] = list(done.values())
    calls_used = 0
    for root_id in gate["candidates"]:
        if root_id in done:
            continue
        if calls_used >= int(args.max_calls):
            break
        candidate = pool.get(root_id)
        if candidate is None:
            rows.append({"root_id": root_id, "error": "candidate_not_in_pool"})
            continue
        evidence = sanitized_evidence(candidate)
        det = deterministic_arm(candidate)
        hyb = hybrid_arm(candidate, evidence, timeout_s=float(args.timeout), max_tokens=int(args.max_tokens))
        calls_used += int(hyb.get("llm_calls") or 0)
        cost = call_cost_usd(int(hyb.get("tokens_in") or 0), int(hyb.get("tokens_out") or 0))
        rows.append({
            "root_id": root_id,
            "root_title": candidate.get("root_title"),
            "root_tier": candidate.get("root_tier"),
            # SHA-256 truncado: estavel entre processos; hash() depende de PYTHONHASHSEED (QHOS-AUD-311).
            "same_evidence_hash": int(hashlib.sha256(json.dumps(evidence, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:8], 16),
            "evidence_sent": evidence,
            "deterministic": det,
            "real_hybrid": hyb,
            # Whether Hybrid added anything the deterministic arm did not have,
            # which is a different question from whether it was correct.
            "incremental_value": incremental_value(det, hyb, cost_usd=cost),
            "cost_usd": round(cost, 4),
            "routing_row": {
                "root_id": root_id,
                "candidate_family": candidate.get("root_tier"),
                "deterministic_sufficient": bool(det.get("identified_dependency"))
                or str(candidate.get("root_tier") or "") == "DEPENDENCY",
                "hybrid_incremental_value": (incremental_value(det, hyb, cost_usd=cost))["primary"],
                "cost_usd": round(cost, 4),
                "output_status": hyb.get("output_status"),
                "auto_routing_enabled": False,
            },
            "label_leakage": any(
                k in json.dumps(evidence, default=str)
                for k in ("human_disposition", "final_category", "ground_truth")
            ),
            "checkpoint_at": time.time(),
        })
        # Checkpoint after every paid call so a crash never repeats spend.
        checkpoint.write_text(json.dumps({
            "schema": "qhos-hybrid-replay-v1",
            "tag": args.tag,
            "gate": gate,
            "rows": rows,
            "writes": 0,
            "change_request": None,
            "sandbox": None,
            "level3_enabled": False,
        }, indent=2, default=str), encoding="utf-8")

    summary = {
        "gate": gate,
        "rows": len(rows),
        "llm_calls": sum(int((r.get("real_hybrid") or {}).get("llm_calls") or 0) for r in rows),
        "tokens": sum(int((r.get("real_hybrid") or {}).get("tokens") or 0) for r in rows),
        "fallbacks": sum(1 for r in rows if (r.get("real_hybrid") or {}).get("fallback_used")),
        "errors": [(r.get("real_hybrid") or {}).get("error") for r in rows if (r.get("real_hybrid") or {}).get("error")],
        "output_statuses": {
            status: sum(1 for r in rows if (r.get("real_hybrid") or {}).get("output_status") == status)
            for status in sorted({
                str((r.get("real_hybrid") or {}).get("output_status") or "") for r in rows
            }) if status
        },
        "hybrid_successes": sum(
            1 for r in rows if (r.get("real_hybrid") or {}).get("counts_as_hybrid_success")
        ),
        "cost_usd": round(sum(float(r.get("cost_usd") or 0.0) for r in rows), 4),
        "incremental_value": {
            str(r.get("root_id"))[:20]: (r.get("incremental_value") or {}).get("primary")
            for r in rows if r.get("incremental_value")
        },
        "routing_dataset": [r.get("routing_row") for r in rows if r.get("routing_row")],
        "auto_routing_enabled": False,
        "label_leakage": any(r.get("label_leakage") for r in rows),
        "clients_closed": all((r.get("real_hybrid") or {}).get("client_closed") for r in rows if r.get("real_hybrid")),
        "compare": compare_same_evidence(
            {"same_evidence": True, "label_leakage": False},
            {"same_evidence": True, "label_leakage": False},
        ),
        "checkpoint": str(checkpoint),
        "writes": 0,
        "change_request": None,
        "sandbox": None,
        "level3_enabled": False,
    }
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
