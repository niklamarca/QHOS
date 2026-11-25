"""Observacao local do V2 para o Brain. Saude do processo nao qualifica mercado nem pesquisa."""
from __future__ import annotations

import os

from qhos2 import __version__
from qhos2.leitura import AGUARDANDO, ENCERRADA, FALHA

ROTAS = frozenset({"/api/health", "/api/status", "/api/truth", "/api/busy", "/api/qhos2/snapshot"})


def snapshot(coletor) -> dict:
    leitor = coletor.leitor
    resumo = leitor.resumo() if leitor is not None else {}
    estado = leitor.estado if leitor is not None else "SEM_CAPTURA"
    idade = coletor._idade_do_arquivo_s() if leitor is not None else None
    encerrada = estado == ENCERRADA
    recente = bool(idade is not None and idade <= 5 and estado not in (ENCERRADA, FALHA, AGUARDANDO))
    ambiente, sistema = coletor.ambiente_declarado()
    return {
        "schema_version": "qhos2.observation.v1",
        "observed_at": coletor._relogio(),
        "process": {"state": "PROCESS_ONLINE", "pid": os.getpid(), "component": "qhos2.coletor"},
        "runtime_release": {"package": "qhos2", "version": __version__},
        "environment": sistema,
        "source_kind": "synthetic_fixture" if ambiente == "synthetic" else
                       "historical_capture" if encerrada else "capture_file" if leitor else "none",
        "capture": {"id": leitor.pasta.name if leitor else None,
                    "symbol": leitor.simbolo if leitor else None,
                    "exchange": leitor.bolsa if leitor else None,
                    "closed": encerrada, "reader_state": estado,
                    "error_code": leitor.codigo if leitor else None,
                    "frames": resumo.get("quadros", 0), "bytes": resumo.get("bytes", 0)},
        "quality": {"book_valid": bool(resumo.get("livro_vale", False)),
                    "sequence_verified": False, "recent_file_activity": recente,
                    "file_age_seconds": idade,
                    "last_frame_received_ns": str(leitor.ultimo_recebido_ns)
                    if leitor is not None and leitor.ultimo_recebido_ns is not None else None,
                    "available_as_live": False,
                    "reason": "collector_observes_file_not_vendor_connection"},
        "permissions": {"can_execute": False, "research_only": True, "can_control_capture": False},
        "pipeline": coletor.percurso(),
    }


def resposta(coletor, caminho: str) -> dict:
    observado = snapshot(coletor)
    if caminho == "/api/qhos2/snapshot":
        return observado
    if caminho == "/api/health":
        return {"process": observado["process"], "runtime_release": observado["runtime_release"],
                "status": {"process_online": True, "trading_ready": False}}
    if caminho == "/api/status":
        return {"modules": {"qhos2_collector": {"state": "ONLINE"},
                            "qhos2_reader": {"state": observado["capture"]["reader_state"]}},
                "qhos2_observation": observado}
    if caminho == "/api/truth":
        return {"schema_version": "qhos2.truth.v1", "source": "qhos2.coletor",
                "process_online": True, "can_execute": False, "research_approved": False,
                "book_qualified": observado["quality"]["book_valid"],
                "available_as_live": False, "capture": observado["capture"]}
    if caminho == "/api/busy":
        return {"busy": False, "scope": "collector_has_no_order_or_execution_queue",
                "can_restart_capture": False}
    raise ValueError("rota_de_observacao_invalida")
