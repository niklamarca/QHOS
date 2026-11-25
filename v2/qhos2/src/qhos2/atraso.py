"""Atraso entre os carimbos de tempo do protocolo e o recebimento local, numa captura verificada.

Le o protobuf no nivel de bytes (so varints), sem biblioteca e sem importar o V1. Compara
`received_ns` (relogio de parede local, `time.time_ns()`) com tres carimbos que a Rithmic grava
nas mensagens, sem interpretar o que cada um representa:

  servidor : ssboe + usecs             (campos 150100 e 150101)
  fonte    : source_ssboe + source_usecs (150400 e 150401); source_nsecs (150404) e ignorado
  jop      : jop_ssboe + jop_nsecs     (150600 e 150604), so se jop_nsecs for claramente
             "nanossegundos dentro do segundo" (algum valor >= 1_000_000)

ATENCAO: atraso = relogio local - carimbo remoto. Mistura latencia de rede com o desvio entre os
dois relogios. Valor negativo indica recebimento local anterior ao carimbo remoto; nao mede
latencia negativa nem identifica sozinho qual relogio esta errado. Serve como ordem de grandeza.

O negocio-retrato (150 com is_snapshot) fica fora: o carimbo dele e a hora do negocio original, que pode ter
sido horas antes da assinatura. O campo 110121 so significa is_snapshot no 150 (no 160 e o update_type).
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

from qhos2.catalogo import LINHA_MAX_BYTES, _arquivos_da_captura, _ler_manifesto, verificar_captura

CAMPOS = {154467: "template_id", 150100: "ssboe", 150101: "usecs", 150400: "source_ssboe",
          150401: "source_usecs", 150404: "source_nsecs", 150600: "jop_ssboe", 150604: "jop_nsecs",
          110121: "campo_110121"}
NEGOCIO = 150
PERCENTIS = (50, 90, 99, 99.9)
JOP_NSECS_CLARO = 1_000_000
RELOGIO_SUSPEITO_S = 1.0
ESQUEMA = "qhos2.atraso.v1"


class AtrasoError(ValueError):
    """Captura ou quadro que nao permite medir."""


def _varint(dados: bytes, i: int):
    valor, deslocamento = 0, 0
    while True:
        if i >= len(dados) or deslocamento > 63:
            raise AtrasoError("varint_invalido")
        byte = dados[i]
        i += 1
        valor |= (byte & 0x7F) << deslocamento
        if not byte & 0x80:
            return valor, i
        deslocamento += 7


def ler_campos(dados: bytes) -> Dict[str, int]:
    """Campos varint de interesse (o ultimo valor de cada um vence). Pula todo o resto."""
    achados: Dict[str, int] = {}
    i, n = 0, len(dados)
    while i < n:
        tag, i = _varint(dados, i)
        numero, tipo = tag >> 3, tag & 7
        if tipo == 0:
            valor, i = _varint(dados, i)
            nome = CAMPOS.get(numero)
            if nome:
                achados[nome] = valor
        elif tipo == 1:
            i += 8
        elif tipo == 2:
            tamanho, i = _varint(dados, i)
            i += tamanho
        elif tipo == 5:
            i += 4
        else:
            raise AtrasoError("tipo_de_fio_nao_suportado")
        if i > n:
            raise AtrasoError("campo_truncado")
    return achados


def _percentil(ordenados: List[float], p: float) -> float:
    return ordenados[min(len(ordenados) - 1, int(p / 100 * len(ordenados)))]


def _estatistica(amostras_ns: List[int]) -> Optional[dict]:
    if not amostras_ns:
        return None
    ms = sorted(x / 1e6 for x in amostras_ns)
    saida = {"n": len(ms), "min_ms": round(ms[0], 3), "max_ms": round(ms[-1], 3),
             "negativos": sum(1 for x in ms if x < 0)}
    for p in PERCENTIS:
        saida[f"p{p:g}_ms"] = round(_percentil(ms, p), 3)
    return saida


def medir(pasta) -> dict:
    """Mede o atraso por template. Recusa captura que o catalogo nao verifica."""
    pasta = Path(pasta).resolve()
    relatorio_cat = verificar_captura(pasta)
    if not relatorio_cat["verificada"]:
        raise AtrasoError("captura_nao_verificada:" + ",".join(relatorio_cat["divergencias"]))
    manifesto = _ler_manifesto(pasta, [])
    arquivos = _arquivos_da_captura(pasta, manifesto, [])

    amostras: Dict[int, Dict[str, List[int]]] = {}
    quadros: Dict[int, int] = {}
    jop_nsecs_max: Dict[int, int] = {}
    sem_carimbo = 0
    retrato_negocio = 0
    invalidos = 0
    for arquivo, _ in arquivos:
        with arquivo.open("rb") as f:
            for linha in f:
                if len(linha) > LINHA_MAX_BYTES:
                    raise AtrasoError("linha_grande_demais")
                obj = json.loads(linha)
                template, recebido = obj["template_id"], obj["received_ns"]
                quadros[template] = quadros.get(template, 0) + 1
                try:
                    campos = ler_campos(base64.b64decode(obj["protobuf_base64"], validate=True))
                except (AtrasoError, ValueError):
                    invalidos += 1
                    continue
                if template == NEGOCIO and campos.get("campo_110121"):
                    retrato_negocio += 1
                    continue
                por_campo = amostras.setdefault(template, {"servidor": [], "fonte": [], "jop": []})
                achou = False
                if "ssboe" in campos:
                    por_campo["servidor"].append(recebido - (campos["ssboe"] * 10 ** 9 + campos.get("usecs", 0) * 1000))
                    achou = True
                if "source_ssboe" in campos:
                    por_campo["fonte"].append(
                        recebido - (campos["source_ssboe"] * 10 ** 9 + campos.get("source_usecs", 0) * 1000))
                    achou = True
                if "jop_ssboe" in campos and "jop_nsecs" in campos:
                    jop_nsecs_max[template] = max(jop_nsecs_max.get(template, 0), campos["jop_nsecs"])
                    por_campo["jop"].append(recebido - (campos["jop_ssboe"] * 10 ** 9 + campos["jop_nsecs"]))
                    achou = True
                if not achou:
                    sem_carimbo += 1

    por_template = {}
    for template in sorted(quadros):
        campos_t = amostras.get(template, {})
        jop_ok = jop_nsecs_max.get(template, 0) >= JOP_NSECS_CLARO
        por_template[str(template)] = {
            "quadros": quadros[template],
            "servidor": _estatistica(campos_t.get("servidor", [])),
            "fonte": _estatistica(campos_t.get("fonte", [])),
            "jop": _estatistica(campos_t.get("jop", [])) if jop_ok else None,
            "jop_ambiguo": bool(campos_t.get("jop")) and not jop_ok,
        }
    alertas = []
    medianas = [e["p50_ms"] for t in por_template.values() for e in (t["servidor"], t["fonte"]) if e]
    if medianas and any(abs(m) > RELOGIO_SUSPEITO_S * 1000 for m in medianas):
        alertas.append("mediana_acima_de_1s: relogio local provavelmente desalinhado; confira a hora do WSL")
    if any(e and e["negativos"] for t in por_template.values() for e in (t["servidor"], t["fonte"], t["jop"])):
        alertas.append("atrasos_negativos: recebimento local anterior ao carimbo remoto; confira os relogios e a origem do carimbo")
    return {"esquema": ESQUEMA, "captura_id": pasta.name, "ambiente": relatorio_cat.get("ambiente"),
            "simbolo": relatorio_cat.get("simbolo"), "bolsa": relatorio_cat.get("bolsa"),
            "quadros": sum(quadros.values()), "quadros_sem_carimbo": sem_carimbo,
            "quadros_retrato_negocio": retrato_negocio,
            "quadros_protobuf_invalido": invalidos, "alertas": alertas, "por_template": por_template}


def _imprimir(r: dict) -> None:
    print(f"captura={r['captura_id']} ambiente={r['ambiente']} {r['bolsa']}/{r['simbolo']} "
          f"quadros={r['quadros']} sem_carimbo={r['quadros_sem_carimbo']} protobuf_invalido={r['quadros_protobuf_invalido']} "
          f"retrato_negocio_ignorado={r['quadros_retrato_negocio']}")
    for template, t in r["por_template"].items():
        for nome in ("servidor", "fonte", "jop"):
            e = t[nome]
            if e:
                print(f"  template {template:>4} {nome:<8} n={e['n']:<6} min={e['min_ms']:>10} p50={e['p50_ms']:>10} "
                      f"p90={e['p90_ms']:>10} p99={e['p99_ms']:>10} max={e['max_ms']:>10} (ms) negativos={e['negativos']}")
        if t["jop_ambiguo"]:
            print(f"  template {template:>4} jop      ambiguo (jop_nsecs nunca passou de 1_000_000): ignorado")
    for alerta in r["alertas"]:
        print(f"ALERTA: {alerta}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="qhos2.atraso", description=__doc__.splitlines()[0])
    ap.add_argument("capturas", nargs="+", help="pasta(s) de captura; uma que falhe nao impede as outras")
    args = ap.parse_args(argv)
    recusadas = 0
    for caminho in args.capturas:
        if not Path(caminho).is_dir():
            continue  # o curinga do shell pode trazer arquivos soltos
        try:
            _imprimir(medir(caminho))
        except AtrasoError as erro:
            recusadas += 1
            print(f"recusada: {Path(caminho).name}: {erro}")
    return 2 if recusadas else 0


if __name__ == "__main__":
    sys.exit(main())
