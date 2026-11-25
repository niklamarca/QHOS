"""Passo 11 da etapa 3: calibracao, checklists e pre-registro da hipotese 1, em duas fases.

Roda de um clone limpo do commit congelado (condicao R4 de docs/provas/etapa3_20261029/conferencia_passo10.md):

    PYTHONPATH=<clone>/qhos2/src <venv>/bin/python <clone>/qhos2/operacao/wsl/passo11.py medir --dados <data_root>
    PYTHONPATH=<clone>/qhos2/src <venv>/bin/python <clone>/qhos2/operacao/wsl/passo11.py registrar \\
        --dados <data_root> --rodada <pasta escrita pela fase medir> --proposta-sha256 <sha que o autor aprovou>

Fase medir (so depois do fim da janela de calibracao, 6/11 16h20 de Nova York): lista as capturas pela regra abaixo,
calibra P5/P95 (`desequilibrio.calibrar`) e mede o movimento e o custo (`custo.medir`) em paralelo, roda o checklist 1;
se ele seguir, conta os sinais que a regra daria so com D (`poder.contar_sinais_das_capturas`), roda o checklist 2 e o
ensaio sintetico com os numeros reais (`avaliador_sintetico`), e escreve `proposta.json` com o SHA-256 dela. Nao
registra nada. Captura recusada em qualquer medida para tudo (achados AUD-234 e AUD-235: quem decide e o autor).
Checklist que pede abandono para tudo: a microestrutura termina e nada e registrado.

Fase registrar: confere o codigo congelado, que a rodada seguiu nos dois checklists e que o SHA-256 de `proposta.json`
e o aprovado, e chama `pesquisa.registrar_plano` (que recusa fora de 7 e 8/11 UTC, alfa diferente de 5 %/3 e o
orcamento esgotado). Nada e recalculado entre o que o autor viu e o que e registrado.

Regra de selecao das capturas: toda pasta de `<data_root>/capturas` com manifesto, do sistema, simbolo e bolsa do
protocolo (Rithmic Paper Trading, ESZ6, CME), cujo primeiro quadro cai, pela data de Nova York, na janela de
calibracao. As demais sao listadas com o motivo. So agregados sao lidos: distribuicao de D, movimento do meio sem
sinal, spread e contagem de sinais; nenhum resultado de operacao (secao 10, item 3).

`--ensaio` (so para ensaio em dado sintetico): aceita `--agora`, outra taxa e outra identidade de captura, e nao
confere a hora do fim da calibracao.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from qhos2 import avaliador as av
from qhos2 import avaliador_sintetico as si
from qhos2 import custo as cu
from qhos2 import desequilibrio as de
from qhos2 import pesquisa as pq
from qhos2 import poder as po

ESQUEMA = "qhos2.passo11.v1"
HASH_CONGELADO = "43a1de514b22461b2008a3b978d3c101d265b64d3962ee3ef6ff07a94ee7dcea"
FIM_CALIBRACAO_UTC = datetime(2026, 11, 6, 21, 20, tzinfo=timezone.utc)      # 16h20 de Nova York (EST)
TAXA_TETO_USD = 12.5                                                          # secao 10, item 6, emenda de 28/10
IDENTIDADE = ("Rithmic Paper Trading", "ESZ6", "CME")
CORRIDAS, REAMOSTRAS_ENSAIO = 100, 300                                        # as do ensaio do passo 8
FAMILIA = "desequilibrio_3_niveis_esz6_10s"


def _json(caminho: Path, valor) -> None:
    caminho.write_text(json.dumps(valor, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n",
                       encoding="utf-8")


def _sha(caminho: Path) -> str:
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def conferir_codigo() -> dict:
    ident = pq.identidade_codigo()
    if ident["sha256"] != HASH_CONGELADO:
        raise SystemExit(f"PARE: codigo_diferente_do_congelado ({ident['sha256']} em {Path(pq.__file__).parent})")
    return {"sha256": ident["sha256"], "arquivos": len(ident["arquivos"]), "python": ident["python"],
            "implementacao": ident["implementacao"], "pacote": str(Path(pq.__file__).parent)}


def selecionar(dados: Path, identidade: tuple) -> tuple:
    """(pastas incluidas, excluidas com motivo), pela regra escrita no topo."""
    incluidas, excluidas = [], []
    inicio, fim = de.JANELA_CALIBRACAO
    for pasta in sorted(p for p in (dados / "capturas").iterdir() if p.is_dir()):
        manifesto = pasta / "manifest.json"
        if not manifesto.is_file():
            excluidas.append({"captura": pasta.name, "motivo": "sem_manifesto"})
            continue
        try:
            status = json.loads(manifesto.read_text(encoding="utf-8")).get("status", {})
            with (pasta / "market_frames.jsonl").open("rb") as arquivo:
                primeiro = json.loads(arquivo.readline())["received_ns"]
        except (OSError, ValueError, KeyError, TypeError):
            excluidas.append({"captura": pasta.name, "motivo": "manifesto_ou_primeiro_quadro_ilegivel"})
            continue
        tripla = (status.get("system_name"), status.get("requested_symbol"), status.get("exchange"))
        dia = de.hora_de_nova_york(primeiro).date()
        if tripla != identidade:
            excluidas.append({"captura": pasta.name, "motivo": "outra_identidade", "identidade": list(tripla)})
        elif not inicio <= dia <= fim:
            excluidas.append({"captura": pasta.name, "motivo": "fora_da_janela", "dia_ny": dia.isoformat()})
        else:
            incluidas.append({"captura": pasta.name, "dia_ny": dia.isoformat(), "pasta": str(pasta)})
    return incluidas, excluidas


def planos_registrados(dados: Path) -> int:
    caminho = dados / "pesquisa" / "registro.sqlite3"
    if not caminho.is_file():
        return 0
    try:
        conexao = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True)
        try:
            return conexao.execute("SELECT count(*) FROM objetos WHERE tipo='plano'").fetchone()[0]
        finally:
            conexao.close()
    except sqlite3.Error:
        return -1                                          # registro ilegivel: aparece no resumo, a fase registrar decide


def _proposta(cal: dict, check1: dict, sinais: dict, check2: dict, taxa: float) -> dict:
    p5, p95, alfa = cal["p5"], cal["p95"], pq.ALFA_POR_TESTE
    pergunta = (
        f"No ESZ6, no pregao regular de Nova York de {av.JANELA_TESTE[0].isoformat()} a {av.JANELA_TESTE[1].isoformat()} "
        f"(so quadros com o livro qualificado, fora da faixa do FOMC), comprar quando o desequilibrio D dos 3 melhores "
        f"niveis passa de P95 = {p95} e vender quando cai abaixo de P5 = {p5}, com rearme e uma posicao por vez, ordem "
        f"0,5 s depois do sinal preenchida no livro 150 ms depois e saida 10 s depois da ordem de entrada, da resultado "
        f"liquido medio por operacao acima de zero depois da taxa de US$ {taxa:.2f} por ida e volta? Na calibracao "
        f"({de.JANELA_CALIBRACAO[0].isoformat()} a {de.JANELA_CALIBRACAO[1].isoformat()}): custo total "
        f"{check1['custo_total_ticks']:.3f} ticks, movimento medio do meio em 10 s {check1['movimento_medio_abs_ticks']:.3f} "
        f"ticks, acerto necessario {check1['acerto_necessario']:.1%}, {sinais['sinais_por_pregao']:.1f} sinais por pregao; "
        f"menor efeito detectavel com poder de 80 %: {check2['mde_ticks']:.3f} tick liquido por operacao.")
    refutacao = (
        f"Refutada se, com pelo menos {av.MIN_PREGOES} pregoes validos na janela de teste, o limite inferior do intervalo "
        f"bicaudal de {1 - alfa:.2%} (bootstrap em blocos de 5 min, {av.REAMOSTRAS} reamostras, semente {av.SEMENTE}) nao "
        f"ficar acima de zero, ou o p do criterio por pregao (inversao de sinal, unilateral) passar de {alfa / 2:.3%}. "
        f"Com menos de {av.MIN_PREGOES} pregoes validos o teste e adiado, nunca refeito com outros parametros.")
    return {"pergunta": pergunta, "refutacao": refutacao, "familia": FAMILIA,
            "parametros": {"p5": p5, "p95": p95, "taxa_usd": taxa, "alfa": alfa,
                           "fomc": [d.isoformat() for d in de.FOMC]},
            "calibracao": [d.isoformat() for d in de.JANELA_CALIBRACAO],
            "teste": [d.isoformat() for d in av.JANELA_TESTE], "fontes": []}


def medir(args) -> int:
    codigo = conferir_codigo()
    agora = datetime.now(timezone.utc)
    if not args.ensaio and agora < FIM_CALIBRACAO_UTC:
        print(f"PARE: a janela de calibracao so fecha em {FIM_CALIBRACAO_UTC.isoformat()}; nada foi lido")
        return 2
    dados = Path(args.dados).resolve()
    identidade = tuple(args.identidade) if args.ensaio and args.identidade else IDENTIDADE
    taxa = args.taxa_usd if args.ensaio and args.taxa_usd is not None else TAXA_TETO_USD
    saida = Path(args.saida) if args.saida else dados / "etapa3_registro" / agora.strftime("%Y%m%dT%H%M%SZ")
    saida.mkdir(mode=0o700, parents=True, exist_ok=False)
    resumo = {"esquema": ESQUEMA, "fase": "medir", "inicio_utc": agora.isoformat(), "ensaio": bool(args.ensaio),
              "codigo": codigo, "taxa_usd": taxa, "identidade": list(identidade), "planos_ja_registrados":
              planos_registrados(dados), "veredito": None}

    def fim(veredito: str, codigo_saida: int, **campos) -> int:
        resumo.update(veredito=veredito, fim_utc=datetime.now(timezone.utc).isoformat(), **campos)
        _json(saida / "resumo.json", resumo)
        print(json.dumps({k: resumo[k] for k in ("veredito", "fim_utc") if k in resumo}, ensure_ascii=False))
        print(f"rodada: {saida}")
        return codigo_saida

    incluidas, excluidas = selecionar(dados, identidade)
    _json(saida / "capturas.json", {"incluidas": incluidas, "excluidas": excluidas})
    print(f"capturas: {len(incluidas)} incluidas, {len(excluidas)} excluidas "
          f"({', '.join(c['captura'][:8] + ' ' + c['dia_ny'] for c in incluidas)})")
    if not incluidas:
        return fim("PARE_SEM_CAPTURAS", 3)
    pastas = [c["pasta"] for c in incluidas]

    with ProcessPoolExecutor(max_workers=2) as executor:             # as duas leituras longas em paralelo
        f_cal = executor.submit(de.calibrar, pastas)
        f_med = executor.submit(cu.medir, pastas)
        cal, medicao = f_cal.result(), f_med.result()
    _json(saida / "calibracao.json", cal)
    _json(saida / "medicao_custo.json", medicao)
    print(f"calibracao: P5={cal['p5']} P95={cal['p95']} quadros={cal['quadros_usados']} por_dia={cal['por_dia']}")
    if cal["recusadas"] or medicao["recusadas"]:
        return fim("PARE_CAPTURA_RECUSADA", 3, recusadas={"calibracao": cal["recusadas"], "custo": medicao["recusadas"]})
    if cal["p5"] is None or cal["p95"] is None:
        return fim("PARE_SEM_QUADROS", 3)

    check1 = cu.checklist(medicao, taxa)
    _json(saida / "checklist1.json", check1)
    print(f"checklist 1: {check1['veredito']} (acerto necessario {check1.get('acerto_necessario')})")
    resumo["checklist1"] = {k: check1.get(k) for k in ("veredito", "custo_total_ticks", "movimento_medio_abs_ticks",
                                                         "acerto_necessario", "amostras")}
    if check1["veredito"] != "SEGUE":
        return fim(check1["veredito"], 4)

    sinais = po.contar_sinais_das_capturas(pastas, cal["p5"], cal["p95"])
    _json(saida / "sinais.json", sinais)
    if sinais["recusadas"]:
        return fim("PARE_CAPTURA_RECUSADA", 3, recusadas={"sinais": sinais["recusadas"]})
    if not sinais["sinais_por_pregao"]:
        return fim("PARE_SEM_SINAIS", 3)
    check2 = po.checklist(medicao, ops_por_pregao=sinais["sinais_por_pregao"], n_dias=po.DIAS_PREVISTOS,
                          alfa=pq.ALFA_POR_TESTE, custo_ticks=check1["custo_total_ticks"])
    _json(saida / "checklist2.json", check2)
    print(f"checklist 2: {check2['veredito']} (mde {check2['mde_ticks']} contra custo {check2['custo_ticks']})")
    resumo["checklist2"] = {k: check2.get(k) for k in ("veredito", "mde_ticks", "custo_ticks", "ops_por_pregao")}
    if check2["veredito"] != "SEGUE":
        return fim(check2["veredito"], 4)

    ops = max(1, round(sinais["sinais_por_pregao"]))
    comum = dict(sigma_ticks=medicao["desvio_ticks"], ops_por_pregao=ops, n_dias=po.DIAS_PREVISTOS,
                 alfa=pq.ALFA_POR_TESTE, taxa_usd=taxa, corridas=CORRIDAS, reamostras=REAMOSTRAS_ENSAIO)
    ensaio = {"sem_edge": si.ensaio_decisao(media_liquida_ticks=0.0, **comum),
              "com_edge_no_mde": si.ensaio_decisao(media_liquida_ticks=check2["mde_ticks"], **comum)}
    _json(saida / "ensaio_sintetico.json", ensaio)
    print(f"ensaio sintetico: falso positivo {ensaio['sem_edge']['fracao_passou']:.0%}, "
          f"poder no MDE {ensaio['com_edge_no_mde']['fracao_passou']:.0%}")
    resumo["ensaio_sintetico"] = {"falso_positivo": ensaio["sem_edge"]["fracao_passou"],
                                  "poder_no_mde": ensaio["com_edge_no_mde"]["fracao_passou"]}

    proposta = _proposta(cal, check1, sinais, check2, taxa)
    _json(saida / "proposta.json", proposta)
    sha = _sha(saida / "proposta.json")
    print(f"proposta.json sha256={sha}")
    return fim("SEGUE_PROPOSTA_PRONTA", 0, proposta_sha256=sha)


def registrar(args) -> int:
    codigo = conferir_codigo()
    rodada = Path(args.rodada).resolve()
    resumo = json.loads((rodada / "resumo.json").read_text(encoding="utf-8"))
    if resumo.get("veredito") != "SEGUE_PROPOSTA_PRONTA" or resumo.get("codigo", {}).get("sha256") != HASH_CONGELADO:
        print(f"PARE: rodada sem proposta pronta (veredito {resumo.get('veredito')})")
        return 2
    if resumo.get("ensaio") and not args.ensaio:
        print("PARE: rodada de ensaio nao se registra fora do ensaio")
        return 2
    sha = _sha(rodada / "proposta.json")
    if sha != args.proposta_sha256 or sha != resumo.get("proposta_sha256"):
        print("PARE: proposta.json nao e a aprovada (SHA-256 diferente)")
        return 2
    agora = datetime.fromisoformat(args.agora) if args.ensaio and args.agora else None
    proposta = json.loads((rodada / "proposta.json").read_text(encoding="utf-8"))
    try:
        plano = pq.registrar_plano(Path(args.dados).resolve(), proposta, agora=agora)
    except pq.PesquisaError as erro:
        print(f"PARE: registro recusado: {erro}")
        return 2
    registro = {"esquema": ESQUEMA, "fase": "registrar", "plano_id": plano, "proposta_sha256": sha,
                "codigo": codigo, "registrado_utc": (agora or datetime.now(timezone.utc)).isoformat(),
                "ensaio": bool(args.ensaio)}
    _json(rodada / "registro.json", registro)
    print(f"REGISTRADO: plano_id={plano}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="passo11", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="fase", required=True)
    m = sub.add_parser("medir", help="calibra, checklists, ensaio sintetico e proposta; nao registra")
    m.add_argument("--dados", required=True, help="raiz de dados (a de producao e /home/usuario/qhos-v2-data)")
    m.add_argument("--saida", help="pasta nova para a rodada (padrao: <dados>/etapa3_registro/<instante UTC>)")
    r = sub.add_parser("registrar", help="registra a proposta aprovada de uma rodada")
    r.add_argument("--dados", required=True)
    r.add_argument("--rodada", required=True)
    r.add_argument("--proposta-sha256", required=True)
    for p in (m, r):
        p.add_argument("--ensaio", action="store_true", help="so para ensaio em dado sintetico")
    m.add_argument("--taxa-usd", type=float, help="so com --ensaio")
    m.add_argument("--identidade", nargs=3, metavar=("SISTEMA", "SIMBOLO", "BOLSA"), help="so com --ensaio")
    r.add_argument("--agora", help="instante ISO com fuso; so com --ensaio")
    args = ap.parse_args(argv)
    if not args.ensaio and (getattr(args, "taxa_usd", None) is not None or getattr(args, "identidade", None)
                            or getattr(args, "agora", None)):
        ap.error("--taxa-usd, --identidade e --agora so valem com --ensaio")
    return medir(args) if args.fase == "medir" else registrar(args)


if __name__ == "__main__":
    sys.exit(main())
