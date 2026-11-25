"""Libera o bruto do staging do coletor depois que a copia do DataSet foi reconferida. Nenhum dado historico some.

O .tar.xz do DataSet guarda manifest.json, capture_identity.json e market_frames.jsonl byte a byte (xz e sem
perda). O bruto do staging e uma copia descompactada do mesmo conteudo. Ele so e removido quando, no momento da
remocao:
  1. o dia tem pelo menos DIAS_MINIMOS (2) e tem INDEX.json e SHA256SUMS.txt no DataSet;
  2. o ativo esta no INDEX como publicado, com o mesmo sha256 do xz no INDEX e no SHA256SUMS.txt;
  3. o xz do DataSet e lido de volta e SHA-256, bytes e quadros do JSONL batem com o manifesto do bruto, e o
     manifesto e a identidade batem byte a byte;
  4. a copia local em staging/<dia>/.xz/ tambem tem o mesmo sha256 (fica guardada: segunda copia fora do OneDrive).
Qualquer falha deixa o bruto onde esta. Nunca toca no ES do agendador (as capturas dele ficam fora do staging),
nem em captura nao publicada, nem fora de staging/<AAAA-MM-DD>/. O ES gravado pelo coletor como reserva segue as
mesmas regras; no INDEX ele tem outro nome de arquivo e e achado pelo captura_id.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

from coleta_multi import fechamento

DIAS_MINIMOS = 2  # com a sessao Globex inteira (23 h), 7 dias de bruto encheriam o disco antes de sair
_DIA = re.compile(r"\d{4}-\d{2}-\d{2}")


def _somas(arquivo: Path) -> dict:
    saida = {}
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        sha, _, nome = linha.partition("  ")
        saida[nome] = sha
    return saida


def _conferir_ativo(entrada: dict, bruto: Path, xz_dataset: Path, xz_local: Path, somas: dict) -> str | None:
    """None se pode remover; senao o motivo de manter."""
    nome = entrada["arquivo"][: -len(".tar.xz")]
    if not str(entrada.get("estado", "")).startswith("publicado") or \
            entrada.get("origem") not in (None, fechamento.ORIGEM_RESERVA):
        return "nao_e_captura_publicada_do_coletor"
    sha = entrada.get("sha256_xz")
    if not sha or somas.get(entrada["arquivo"]) != sha:
        return "sha256sums_diverge_do_index"
    if not xz_dataset.is_file() or fechamento._sha(xz_dataset) != sha:
        return "xz_do_dataset_ausente_ou_diverge"
    if not xz_local.is_file() or fechamento._sha(xz_local) != sha:
        return "copia_local_ausente_ou_diverge"
    manifesto = json.loads((bruto / "manifest.json").read_text(encoding="utf-8"))
    pequenos = {a: hashlib.sha256((bruto / a).read_bytes()).hexdigest() for a in fechamento.ARQUIVOS[:2]}
    try:
        fechamento.conferir(xz_dataset, nome, manifesto, pequenos)
    except Exception as erro:  # qualquer falha de leitura mantem o bruto
        return f"releitura_falhou_{type(erro).__name__}"
    return None


def liberar(staging: Path, destino: Path, hoje: date, *, dias_minimos: int = DIAS_MINIMOS,
            executar: bool = True) -> dict:
    staging, destino = Path(staging), Path(destino)
    relatorio = {"removidos": [], "mantidos": [], "bytes_liberados": 0}
    if not staging.is_dir():
        return relatorio
    limite = hoje - timedelta(days=dias_minimos)
    for pasta_dia in sorted(p for p in staging.iterdir() if p.is_dir() and _DIA.fullmatch(p.name)):
        if date.fromisoformat(pasta_dia.name) > limite:
            continue
        index_arq, somas_arq = destino / pasta_dia.name / "INDEX.json", destino / pasta_dia.name / "SHA256SUMS.txt"
        if not index_arq.is_file() or not somas_arq.is_file():
            relatorio["mantidos"].append({"dia": pasta_dia.name, "motivo": "dia_sem_index_no_dataset"})
            continue
        index, somas = json.loads(index_arq.read_text(encoding="utf-8")), _somas(somas_arq)
        if somas.get("INDEX.json") != hashlib.sha256(index_arq.read_bytes()).hexdigest():
            relatorio["mantidos"].append({"dia": pasta_dia.name, "motivo": "index_diverge_do_sha256sums"})
            continue
        por_arquivo = {e["arquivo"]: e for e in index.get("ativos", []) if e.get("arquivo")}
        # reserva do ES: o arquivo tem outro nome; a pasta do staging e o captura_id (vem antes do nome do arquivo)
        por_captura = {e["captura_id"]: e for e in index.get("ativos", [])
                       if e.get("arquivo") and e.get("captura_id") and e.get("origem") == fechamento.ORIGEM_RESERVA}
        for bruto in sorted(p for p in pasta_dia.iterdir() if p.is_dir() and not p.name.startswith(".")):
            entrada = por_captura.get(bruto.name) or por_arquivo.get(f"{bruto.name}.tar.xz")
            if entrada is None or not (bruto / "manifest.json").is_file():
                relatorio["mantidos"].append({"dia": pasta_dia.name, "captura": bruto.name, "motivo": "fora_do_index"})
                continue
            motivo = _conferir_ativo(entrada, bruto, destino / pasta_dia.name / entrada["arquivo"],
                                     pasta_dia / ".xz" / entrada["arquivo"], somas)
            if motivo:
                relatorio["mantidos"].append({"dia": pasta_dia.name, "captura": bruto.name, "motivo": motivo})
                continue
            tamanho = sum(f.stat().st_size for f in bruto.iterdir() if f.is_file())
            if executar:
                removendo = pasta_dia / f".removendo-{bruto.name}"
                os.replace(bruto, removendo)  # some do staging de uma vez; so depois e apagado
                shutil.rmtree(removendo)
            relatorio["removidos"].append({"dia": pasta_dia.name, "captura": bruto.name, "bytes": tamanho})
            relatorio["bytes_liberados"] += tamanho
    return relatorio


def main(argv=None) -> int:
    from qhos2 import agenda
    from coleta_multi.coletor import DATASET_PADRAO
    from datetime import datetime
    ap = argparse.ArgumentParser(prog="coleta_multi.limpeza", description=__doc__.splitlines()[0])
    ap.add_argument("--executar", action="store_true", help="sem isto so mostra o que seria removido")
    ap.add_argument("--destino", type=Path, default=DATASET_PADRAO)
    args = ap.parse_args(argv)
    raiz = Path(os.environ.get("QHOS2_DATA_ROOT") or Path.home() / "qhos-v2-data")
    r = liberar(raiz / "coleta_multi" / "staging", args.destino, datetime.now(agenda.NY).date(), executar=args.executar)
    print(json.dumps(r, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
