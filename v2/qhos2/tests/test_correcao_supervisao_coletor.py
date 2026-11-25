"""Campanha de correcao, lote l5c-leitura: `Selecao` do supervisor (QHOS-AUD-091).

Sem xfail: caracterizacao das construcoes contraditorias e teste de que `selecionar()` so produz selecoes coerentes.
`supervisao_coletor.py` nao muda nesta janela; impor no tipo e decisao, com uma opcao em
`EVIDENCIAS/QHOS-AUD-091/patch_proposto.diff`.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from qhos2 import supervisao_coletor as su

AGORA = 1_800_000_000
ATIVAS = {"captura_em_curso", "captura_encerrada"}


def coerente(s: su.Selecao) -> bool:
    """O invariante que o achado pede: pasta so com codigo ativo, e identidade (dev, ino[, sha256]) so quando ativa."""
    ativa = s.codigo in ATIVAS
    return (s.pasta is not None) == ativa and isinstance(s.identidade, tuple) and (
        len(s.identidade) in (2, 3) if ativa else s.identidade == ())


@pytest.mark.parametrize("args", [
    (None, "captura_em_curso"),                       # ativa sem pasta
    (Path("x"), "sem_captura"),                       # inativa com pasta
    (Path("x"), "captura_em_curso"),                  # ativa sem identidade
    (Path("x"), "captura_encerrada", (1, 2, 3, 4)),   # identidade de aridade 4
    (None, "sem_captura", (1, 2)),                    # inativa com identidade
], ids=["ativa_sem_pasta", "inativa_com_pasta", "ativa_sem_identidade", "identidade_aridade_4", "inativa_com_identidade"])
def test_caracterizacao_selecao_contraditoria_e_construivel(args):
    """QHOS-AUD-091, caracterizacao: `Selecao` (supervisao_coletor.py:39-47) e permissivo; o invariante vale porque
    `selecionar()` so produz selecoes coerentes (teste abaixo), nao pelo tipo. tests/test_supervisao_coletor.py:139
    constroi `Selecao(pasta, "fixture")`. Impor no tipo e desenho (patch em EVIDENCIAS/QHOS-AUD-091)."""
    s = su.Selecao(*args)
    assert not coerente(s)


def _captura(raiz, nome, *, criada=None, mtime=AGORA, simbolo="ESZ6"):
    pasta = raiz / "capturas" / nome
    pasta.mkdir(parents=True)
    (pasta / "market_frames.jsonl").write_bytes(b"NAO LER DADO DE MERCADO\n")
    os.utime(pasta / "market_frames.jsonl", (mtime, mtime))
    info = (pasta / "market_frames.jsonl").stat()
    identidade = {"schema": "qhos2.capture_identity.v1", "capture_id": nome, "system_name": "Rithmic Paper Trading",
                  "requested_symbol": simbolo, "exchange": "CME", "raw_dev": info.st_dev, "raw_ino": info.st_ino}
    (pasta / "capture_identity.json").write_text(json.dumps(identidade))
    (pasta / "capture_identity.json").chmod(0o600)
    if criada is not None:
        manifesto = {"schema": su.SCHEMA_MANIFESTO, "status": {"requested_symbol": simbolo, "exchange": "CME",
                     "criado_em": datetime.fromtimestamp(criada, timezone.utc).isoformat(), "sessao": {"estado": "FECHADA"}}}
        (pasta / "manifest.json").write_text(json.dumps(manifesto))
    return pasta


def test_toda_selecao_produzida_por_selecionar_e_coerente(tmp_path):
    """O codigo cumpre o invariante (o tipo e que nao o impoe): vazia, ativa, encerrada e duas ativas."""
    assert coerente(su.selecionar(tmp_path, agora=AGORA))                       # sem_captura
    _captura(tmp_path, "ontem", criada=AGORA - 100)
    encerrada = su.selecionar(tmp_path, agora=AGORA)
    assert encerrada.codigo == "captura_encerrada" and coerente(encerrada) and len(encerrada.identidade) == 3
    _captura(tmp_path, "agora")
    ativa = su.selecionar(tmp_path, agora=AGORA)
    assert ativa.codigo == "captura_em_curso" and coerente(ativa) and len(ativa.identidade) == 2
    _captura(tmp_path, "outra")
    assert coerente(su.selecionar(tmp_path, agora=AGORA))                       # mais_de_uma_captura_em_curso
