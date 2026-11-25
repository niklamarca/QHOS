"""Diagnostico seguro e bootstrap shadow do Databento."""
from __future__ import annotations

import argparse
import json
import sys

from data.event_plane import EventPlane


def main() -> int:
    parser = argparse.ArgumentParser(description="Valida a camada Databento do QHOS")
    parser.add_argument("--bootstrap", action="store_true",
                        help="executa o probe shadow limitado apos o doctor")
    args = parser.parse_args()
    plane = EventPlane()
    status = plane.status()
    print("Quant HPC OS - Databento Doctor")
    print(f"SDK instalado: {'SIM' if status['databento_package_installed'] else 'NAO'}")
    print(f"Chave detectada: {'SIM' if status['databento_configured'] else 'NAO'}")
    print(f"Estado: {status['readiness']}")
    if not status["databento_configured"]:
        print("FALHA: DATABENTO_API_KEY nao foi configurada.")
        return 2
    if not args.bootstrap:
        request = plane.shadow_request()
        doctor = plane.databento.doctor(request.dataset, request.schema)
        cost = plane.estimate_databento(request)
        print(f"Autenticacao: {'OK' if doctor['authenticated'] else 'FALHA'}")
        print(f"Perfil: {request.dataset}/{request.schema} ({len(request.symbols)} simbolos)")
        print(f"Custo estimado do probe: US${cost:.6f}")
        return 0
    try:
        # Mesmo que o probe desta sessao ja exista, valida a chave atual antes
        # de reutilizar o commit. Trocar uma chave nunca produz falso sucesso.
        request = plane.shadow_request()
        plane.databento.doctor(request.dataset, request.schema)
        result = plane.bootstrap_shadow()
        compact = {
            "status": result.get("status"),
            "dataset": plane.shadow_request().dataset,
            "schema": plane.shadow_request().schema,
            "symbols": len(plane.shadow_request().symbols),
            "estimated_cost_usd": result.get("estimated_cost_usd"),
            "rows": (result.get("result") or {}).get("rows"),
            "request_id": (result.get("result") or {}).get("request_id"),
            "silver_status": (result.get("silver") or {}).get("status"),
            "reference_status": (result.get("reference") or {}).get("status"),
            "research_eligible": bool(
                ((result.get("silver") or {}).get("global") or {}).get(
                    "research_eligible", False
                )
            ),
        }
        print(json.dumps(compact, ensure_ascii=False, indent=2))
        print("SUCESSO: event plane shadow validado ponta a ponta.")
        return 0
    except Exception as exc:
        print(f"FALHA FECHADA: {type(exc).__name__}: {str(exc)[:500]}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
