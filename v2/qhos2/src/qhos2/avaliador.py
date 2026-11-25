"""Avaliador da hipotese 1 (secao 10 do plano): simula as operacoes sobre o livro ao fim de cada quadro.

Le os estados do livro (`desequilibrio.estados`) na ordem da captura e aplica as regras registradas:

  - quadro que conta: livro qualificado, os dois lados presentes, pregao regular de Nova York, fora da faixa do FOMC
    (13h55 as 16h nos dias informados);
  - sinal: D > P95 compra, D < P5 vende; uma posicao por vez; no inicio do pregao e depois de cada saida o sinal so
    vale quando D cruza o limiar vindo de dentro de [P5, P95] (rearme); quadro que nao conta nao dispara nem rearma;
  - execucao, 1 contrato: a ordem de entrada sai 0,5 s depois do sinal e e preenchida no livro vigente `latencia`
    depois (compra no melhor preco de venda, venda no melhor de compra); a ordem de saida sai 10 s depois da de
    entrada, preenchida do mesmo jeito do lado oposto. "Livro vigente" num instante e o estado do ultimo quadro
    recebido ate aquele instante;
  - resultado em ticks, menos a taxa de ida e volta convertida em ticks;
  - contam os pregoes completos e, desde 28/10 (N6), o pregao cortado por lacuna de entrega: um intervalo maior que
    LACUNA_MAX_NS entre quadros recebidos que toque o pregao regular encerra o dia no ultimo quadro antes dele
    (`completo_ate_lacuna`); o que vem depois e so contado e a operacao aberta e descartada e contada;
  - silencio final (decisao do autor, 29/10): se o ultimo quadro chega antes das 16h e a sessao foi encerrada
    normalmente as 16h ou depois (manifesto com `transport_closed_normally` e `criado_em`, escrito depois do logout),
    o dia vale ate o ultimo quadro (`completo_ate_silencio_final`), como na lacuna; sessao parada antes das 16h, ou
    sem fim confiavel, continua perdendo o dia (`terminou_antes_do_fechamento`);
  - veredito (C3, 28/10): dois criterios, e os dois tem de passar: o intervalo bicaudal 1-alfa por bootstrap em
    blocos de 5 minutos inteiro acima de zero, e o teste de permutacao por inversao de sinal sobre a soma liquida
    de cada pregao, unilateral, com p <= alfa/2;
  - recuo do relogio de recebimento (quadro com `recebido_ns` menor que o anterior) e preso ao ultimo instante visto
    e contado; o dia e recusado acima de RECUO_MAX_NS num recuo ou de RECUOS_MAX_POR_PREGAO recuos (N5, 28/10).

Regras de borda registradas no plano em 25/10 (secao 10, item 5): entrada cujo livro
vigente nao esta qualificado e cancelada e contada; saida cujo livro vigente nao esta qualificado e preenchida no
primeiro estado qualificado seguinte e contada; sinal cuja saida cairia depois das 16h e ignorado e contado; operacao
sem fim quando a captura acaba e descartada e contada.

Nada aqui olha dado por conta propria: a CLI recusa rodar antes do fim da janela de teste.
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import random
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

from qhos2 import desequilibrio as de
from qhos2 import eventos as ev

ESQUEMA = "qhos2.avaliacao.v1"
TICK_USD = 12.50
ATRASO_ENTRADA_NS = 500_000_000            # 0,5 s entre o sinal e a ordem de entrada
LATENCIA_NS = 150_000_000                  # piso da ida da ordem ate a bolsa (emenda de 25/10)
SEGURA_NS = 10_000_000_000                 # 10 s entre a ordem de entrada e a de saida
DIRECAO_NS = 10_000_000_000                # medida secundaria: preco medio 10 s depois do sinal
BLOCO_S = 300                              # bootstrap em blocos de 5 minutos
REAMOSTRAS = 10_000
SEMENTE = 20261109
FOMC_INICIO, FECHAMENTO = de.FOMC_INICIO, de.FECHAMENTO      # a faixa do FOMC e a mesma da calibracao
JANELA_TESTE = (date(2026, 11, 9), date(2026, 11, 20))
MIN_PREGOES = 7
LACUNA_MAX_NS = de.LACUNA_MAX_NS           # lacuna de entrega: o mesmo limiar da calibracao (60 s)
QUALIFICADO_MIN = 0.95                     # pregao completo: livro qualificado nessa fracao dos quadros
RECUO_MAX_NS = 100_000_000                 # recuo do relogio de recebimento: preso ao ultimo instante e contado;
RECUOS_MAX_POR_PREGAO = 100                # acima de 100 ms num recuo, ou de 100 recuos, o dia e recusado (N5, 28/10)
USADOS = ("completo", "completo_ate_lacuna", "completo_ate_silencio_final")   # pregoes que entram no veredito
_EPOCA = datetime(1970, 1, 1, tzinfo=timezone.utc)


class Parametros(NamedTuple):
    p5: float
    p95: float
    taxa_usd: float                        # ida e volta, por contrato
    alfa: float                            # bicaudal
    fomc: Tuple[date, ...] = ()
    latencia_ns: int = LATENCIA_NS


class Operacao(NamedTuple):
    lado: str                              # ev.COMPRA ou ev.VENDA
    sinal_ns: int
    entrada_ns: int                        # instante em que o preco de entrada foi tomado
    preco_entrada: int                     # ticks
    saida_ns: int
    preco_saida: int
    ticks: int                             # bruto
    acerto_direcao: Optional[bool]         # medida secundaria; None se o preco medio nao mudou ou faltou livro


class Dia(NamedTuple):
    data: Optional[date]
    completo: bool
    motivo: str
    operacoes: Tuple[Operacao, ...]
    contagens: Dict[str, int]
    corte_ns: Optional[int] = None         # ultimo quadro antes da lacuna de entrega (N6) ou do silencio final (29/10)


def _finito(valor) -> bool:
    try:
        return type(valor) in (int, float) and math.isfinite(valor)
    except OverflowError:
        return False


def validar_parametros(par: Parametros) -> None:
    """A API e a CLI aplicam a mesma validacao; NaN/Inf nunca viram resultado."""
    if (not all(_finito(v) for v in (par.p5, par.p95, par.taxa_usd, par.alfa))
            or not -1 <= par.p5 < par.p95 <= 1 or par.taxa_usd < 0 or not 0 < par.alfa < 1
            or type(par.latencia_ns) is not int or par.latencia_ns < LATENCIA_NS
            or any(type(d) is not date for d in par.fomc)):
        raise de.DesequilibrioError("parametros_invalidos")


def _meio(e: de.Estado) -> Optional[float]:
    if e is None or e.compra is None or e.venda is None:
        return None
    return (e.compra + e.venda) / 2


def _negociavel(e: Optional[de.Estado]) -> bool:
    return e is not None and e.qualificado and e.compra is not None and e.venda is not None


def fim_da_sessao_ns(pasta) -> Optional[int]:
    """Fim da sessao pelo manifesto (`criado_em`, escrito depois do logout), so se ela foi encerrada normalmente."""
    from qhos2.catalogo import _ler_manifesto
    try:
        manifesto = _ler_manifesto(Path(pasta), [])
    except OSError:
        return None
    if not isinstance(manifesto, dict) or manifesto.get("transport_closed_normally") is not True:
        return None
    try:
        fim = datetime.fromisoformat(str(manifesto["status"]["criado_em"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return None
    if fim.utcoffset() is None:
        return None
    delta = fim - _EPOCA
    return (delta.days * 86400 + delta.seconds) * 10 ** 9 + delta.microseconds * 1000


def simular(estados: Iterable[de.Estado], par: Parametros, *, fim_sessao_ns: Optional[int] = None) -> Dia:
    """Um pregao (uma captura): operacoes, contagens e se o pregao foi completo.

    `fim_sessao_ns`: fim da sessao encerrada normalmente (`fim_da_sessao_ns`); None mantem a regra sem silencio final.
    """
    validar_parametros(par)
    if fim_sessao_ns is not None and (type(fim_sessao_ns) is not int or fim_sessao_ns <= 0):
        raise de.DesequilibrioError("parametros_invalidos")
    estados = iter(estados)                # depois da lacuna o resto e so contado, nunca relido do inicio
    contagens: Counter = Counter()
    operacoes: List[Operacao] = []
    pendentes: List[list] = []             # [alvo_ns, tipo, dados], em ordem de alvo
    armado, posicao, aguardando_saida = False, None, None
    anterior: Optional[de.Estado] = None
    datas, ultimo_ns = set(), None
    no_pregao, qualificados, dentro_anterior = 0, 0, False
    comeco_antes, fim_depois, corte_ns = False, False, None

    def resolver(alvo_estado: Optional[de.Estado]):
        nonlocal posicao, aguardando_saida
        _, tipo, dados = pendentes.pop(0)
        if tipo == "entrada":
            lado, sinal_ns, ordem_ns = dados
            if not _negociavel(alvo_estado):
                contagens["entrada_cancelada_livro_nao_qualificado"] += 1
                posicao = None
                return
            preco = alvo_estado.venda if lado == ev.COMPRA else alvo_estado.compra
            posicao = (lado, sinal_ns, alvo_estado.recebido_ns, preco)
            _agendar(ordem_ns + SEGURA_NS + par.latencia_ns, "saida", None)
        elif tipo == "saida":
            if _negociavel(alvo_estado):
                _fechar(alvo_estado)
            else:
                contagens["saida_adiada_livro_nao_qualificado"] += 1
                aguardando_saida = True
        elif tipo == "direcao":
            lado, meio_sinal, chave = dados
            meio = _meio(alvo_estado)
            direcao[chave] = None if meio is None or meio == meio_sinal else (meio > meio_sinal) == (lado == ev.COMPRA)

    def _agendar(alvo_ns, tipo, dados):
        pendentes.append([alvo_ns, tipo, dados])
        pendentes.sort(key=lambda p: p[0])

    def _fechar(estado: de.Estado):
        nonlocal posicao, aguardando_saida
        lado, sinal_ns, entrada_ns, preco_entrada = posicao
        preco_saida = estado.compra if lado == ev.COMPRA else estado.venda
        ticks = preco_saida - preco_entrada if lado == ev.COMPRA else preco_entrada - preco_saida
        operacoes.append(Operacao(lado, sinal_ns, entrada_ns, preco_entrada, estado.recebido_ns, preco_saida, ticks,
                                  None))
        chaves_operacao.append(sinal_ns)
        posicao, aguardando_saida = None, None             # o rearme ja foi exigido no sinal

    direcao: Dict[int, Optional[bool]] = {}
    chaves_operacao: List[int] = []

    for e in estados:
        t = e.recebido_ns
        if type(t) is not int or t <= 0:
            raise ev.EventoError("tempo_invalido")
        if ultimo_ns is not None and t < ultimo_ns:          # recuo do relogio: preso e contado, nunca reordenado
            contagens["recuos_de_relogio"] += 1
            contagens["recuo_maior_ns"] = max(contagens["recuo_maior_ns"], ultimo_ns - t)
            t = ultimo_ns
            e = e._replace(recebido_ns=t)
        if e.d is not None and (not _finito(e.d) or not -1 <= e.d <= 1):
            raise ev.EventoError("desequilibrio_invalido")
        if any(v is not None and (type(v) is not int or v <= 0) for v in (e.compra, e.venda)):
            raise ev.EventoError("preco_invalido")
        momento = de.hora_de_nova_york(t)
        dentro = de.no_pregao(momento)
        if (dentro or dentro_anterior) and anterior is not None and t - anterior.recebido_ns > LACUNA_MAX_NS:
            corte_ns = anterior.recebido_ns            # lacuna de entrega que toca o pregao: o dia vale ate aqui
            break
        datas.add(momento.date())
        ultimo_ns = t
        if momento.time() < de.ABERTURA:
            comeco_antes = True
        if momento.time() >= FECHAMENTO:
            fim_depois = True
        if dentro:
            no_pregao += 1
            qualificados += e.qualificado
        dentro_anterior = dentro
        while pendentes and pendentes[0][0] < t:
            resolver(anterior)
        if aguardando_saida and _negociavel(e):
            _fechar(e)
        anterior = e
        fomc = de.na_faixa_do_fomc(momento, par.fomc)
        if not (dentro and not fomc and e.qualificado and e.d is not None):
            continue
        fora = e.d > par.p95 or e.d < par.p5
        ocupado = posicao is not None or any(p[1] == "entrada" for p in pendentes) or aguardando_saida
        if ocupado:
            if fora:
                contagens["sinal_ignorado_posicao_aberta"] += 1
            continue
        if not fora:
            armado = True
            continue
        if not armado:
            contagens["fora_sem_rearme"] += 1
            continue
        armado = False
        lado = ev.COMPRA if e.d > par.p95 else ev.VENDA
        ordem_ns = t + ATRASO_ENTRADA_NS
        fim = de.hora_de_nova_york(ordem_ns + SEGURA_NS + par.latencia_ns)
        if fim.date() != momento.date() or fim.time() >= FECHAMENTO:
            contagens["sinal_perto_do_fechamento"] += 1
            continue
        contagens["sinais"] += 1
        _agendar(ordem_ns + par.latencia_ns, "entrada", (lado, t, ordem_ns))
        _agendar(t + DIRECAO_NS, "direcao", (lado, _meio(e), t))
    if corte_ns is not None:
        contagens["quadros_depois_da_lacuna"] = 1 + sum(1 for _ in estados)
    silencio_final = False
    if corte_ns is None and not fim_depois and ultimo_ns is not None and len(datas) == 1 and fim_sessao_ns is not None:
        fim = de.hora_de_nova_york(fim_sessao_ns)
        if fim_sessao_ns >= ultimo_ns and (fim.date(), fim.time()) >= (min(datas), FECHAMENTO):
            corte_ns, silencio_final = ultimo_ns, True     # a sessao seguiu viva ate as 16h: o dia vale ate aqui
    while pendentes:                       # o que ainda cabe no ultimo estado conhecido
        if pendentes[0][0] > (ultimo_ns or 0):
            break
        resolver(anterior)
    if posicao is not None or any(p[1] == "entrada" for p in pendentes):
        contagens["operacao_sem_fim"] += 1
    operacoes = [o._replace(acerto_direcao=direcao.get(chave)) for o, chave in zip(operacoes, chaves_operacao)]

    data = min(datas) if len(datas) == 1 else None
    if data is None:
        motivo = "mais_de_um_dia_ou_vazia"
    elif (contagens["recuos_de_relogio"] > RECUOS_MAX_POR_PREGAO
            or contagens["recuo_maior_ns"] > RECUO_MAX_NS):
        motivo = "recuo_de_relogio_acima_do_limite"
    elif not comeco_antes:
        motivo = "comecou_depois_da_abertura"
    elif corte_ns is not None and no_pregao == 0:
        motivo = "lacuna_antes_da_abertura"
    elif corte_ns is None and not fim_depois:
        motivo = "terminou_antes_do_fechamento"
    elif no_pregao == 0 or qualificados / no_pregao < QUALIFICADO_MIN:
        motivo = "livro_qualificado_abaixo_de_95pct"
    else:
        motivo = ("completo" if corte_ns is None else
                  "completo_ate_silencio_final" if silencio_final else "completo_ate_lacuna")
    return Dia(data, motivo == "completo", motivo, tuple(operacoes), dict(sorted(contagens.items())), corte_ns)


def liquido(o: Operacao, taxa_usd: float) -> float:
    return o.ticks - taxa_usd / TICK_USD


def _soma_finita(valores):
    try:
        total = math.fsum(valores)
    except (OverflowError, ValueError):
        raise de.DesequilibrioError("resultado_nao_finito") from None
    if not _finito(total):
        raise de.DesequilibrioError("resultado_nao_finito")
    return total


def intervalo(operacoes: Sequence[Operacao], taxa_usd: float, alfa: float, *, reamostras: int = REAMOSTRAS,
              semente: int = SEMENTE) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Media liquida por operacao e intervalo bicaudal 1-alfa por bootstrap em blocos de 5 minutos (data, bloco)."""
    if (not _finito(taxa_usd) or taxa_usd < 0 or not _finito(alfa) or not 0 < alfa < 1
            or type(reamostras) is not int or not 1 <= reamostras <= 1_000_000):
        raise de.DesequilibrioError("parametros_invalidos")
    if any(not _finito(o.ticks) for o in operacoes):
        raise de.DesequilibrioError("resultado_nao_finito")
    if not operacoes:
        return None, None, None
    blocos: Dict[Tuple[date, int], List[float]] = {}
    for o in operacoes:
        momento = de.hora_de_nova_york(o.entrada_ns)
        chave = (momento.date(), (momento.hour * 3600 + momento.minute * 60 + momento.second) // BLOCO_S)
        blocos.setdefault(chave, []).append(liquido(o, taxa_usd))
    somas = [(_soma_finita(v), len(v)) for v in blocos.values()]
    media = _soma_finita(s for s, _ in somas) / sum(n for _, n in somas)
    sorteio = random.Random(semente)
    medias = []
    for _ in range(reamostras):
        escolhidas, quantos = [], 0
        for _ in range(len(somas)):
            s, n = somas[sorteio.randrange(len(somas))]
            escolhidas.append(s)
            quantos += n
        medias.append(_soma_finita(escolhidas) / quantos)
    medias.sort()
    def posto(q):
        return medias[min(len(medias) - 1, max(0, math.ceil(q * len(medias)) - 1))]
    return media, posto(alfa / 2), posto(1 - alfa / 2)


def _somas_de_subconjuntos(valores: Sequence[float]) -> List[float]:
    somas = [0.0]
    for v in valores:
        somas += [s + v for s in somas]
    return somas


def p_inversao_de_sinal(somas: Sequence[float], *, reamostras: int = REAMOSTRAS,
                        semente: int = SEMENTE) -> Tuple[Optional[float], Optional[float], bool]:
    """Teste de permutacao por inversao de sinal sobre a media de `somas`, unilateral: (media, p, exato).

    Nula: cada soma e simetrica em torno de zero. Inverter o sinal do subconjunto F da media T - 2*soma(F), que
    e >= a media observada exatamente quando soma(F) <= 0: o p exato e a fracao dos 2^n subconjuntos com soma
    nao positiva, contada por metades (meet in the middle). Exato quando 2^n cabe em `reamostras`; senao
    `reamostras` sorteios com a semente fixa e a correcao (b + 1) / (m + 1).
    """
    if type(reamostras) is not int or not 1 <= reamostras <= 1_000_000 or any(not _finito(v) for v in somas):
        raise de.DesequilibrioError("parametros_invalidos")
    n = len(somas)
    if not n:
        return None, None, True
    observado = _soma_finita(somas) / n
    exato = 2 ** n <= reamostras
    if exato:
        meio = n // 2
        esquerda = _somas_de_subconjuntos(somas[:meio])
        direita = sorted(_somas_de_subconjuntos(somas[meio:]))
        folga = 1e-9 * (1 + sum(abs(v) for v in somas))          # empate em zero conta como nao positivo
        pelo_menos = sum(bisect.bisect_right(direita, folga - a) for a in esquerda)
        p = pelo_menos / 2 ** n
    else:
        sorteio = random.Random(semente)
        pelo_menos = sum(_soma_finita(v if sorteio.random() < 0.5 else -v for v in somas) / n >= observado
                         for _ in range(reamostras))
        p = (pelo_menos + 1) / (reamostras + 1)
    return observado, p, exato


def por_pregao(dias: Sequence[Dia], taxa_usd: float, *, reamostras: int = REAMOSTRAS, semente: int = SEMENTE) -> dict:
    """Criterio por pregao (C3, 28/10): soma liquida de cada pregao usado e `p_inversao_de_sinal` sobre elas.

    Pregao usado sem operacao entra com soma zero.
    """
    if not _finito(taxa_usd) or taxa_usd < 0:
        raise de.DesequilibrioError("parametros_invalidos")
    somas = [_soma_finita(liquido(o, taxa_usd) for o in d.operacoes) if d.operacoes else 0.0 for d in dias]
    observado, p, exato = p_inversao_de_sinal(somas, reamostras=reamostras, semente=semente)
    return {"pregoes": len(somas), "media_por_pregao": observado, "p_valor": p, "exato": exato}


def julgar(pregoes_usados: int, limite_inferior: Optional[float], p_por_pregao: Optional[float], alfa: float) -> str:
    """Passa so com pregoes suficientes, o intervalo em blocos inteiro acima de zero E o criterio por pregao com
    p <= alfa/2 (C3, 28/10); a media sozinha nao decide."""
    if pregoes_usados < MIN_PREGOES:
        return "ADIADO"
    blocos = _finito(limite_inferior) and limite_inferior > 0
    pregao = _finito(p_por_pregao) and p_por_pregao <= alfa / 2
    return "PASSOU" if blocos and pregao else "FALHOU"


def avaliar(pastas, par: Parametros, *, janela: Tuple[date, date] = JANELA_TESTE, reamostras: int = REAMOSTRAS) -> dict:
    """Veredito da hipotese sobre as capturas da janela: pregoes completos ou cortados por lacuna ou silencio final,
    no minimo MIN_PREGOES."""
    validar_parametros(par)
    if len(janela) != 2 or any(type(d) is not date for d in janela) or janela[0] > janela[1]:
        raise de.DesequilibrioError("janela_invalida")
    dias: List[Tuple[str, Dia]] = []
    recusadas = []
    for pasta in pastas:
        nome = Path(pasta).name
        try:
            dias.append((nome, simular(de.estados_da_captura(pasta), par, fim_sessao_ns=fim_da_sessao_ns(pasta))))
        except ev.EventoError as erro:
            recusadas.append(f"{nome}: {erro}")
    por_data = Counter(d.data for _, d in dias if d.data is not None)
    validos, resumo_dias, contagens = [], [], Counter()
    completos = ate_lacuna = ate_silencio = 0
    for nome, dia in dias:
        motivo = dia.motivo
        if dia.data is not None and not janela[0] <= dia.data <= janela[1]:
            motivo = "fora_da_janela"
        elif dia.data is not None and por_data[dia.data] > 1:
            motivo = "mais_de_uma_captura_no_dia"
        resumo_dias.append({"captura": nome, "data": dia.data.isoformat() if dia.data else None, "motivo": motivo,
                            "operacoes": len(dia.operacoes), "corte_ns": dia.corte_ns})
        if motivo in USADOS:
            validos.append(dia)
            contagens.update(dia.contagens)
            completos += motivo == "completo"
            ate_lacuna += motivo == "completo_ate_lacuna"
            ate_silencio += motivo == "completo_ate_silencio_final"
    operacoes = [o for d in validos for o in d.operacoes]
    media, baixo, alto = intervalo(operacoes, par.taxa_usd, par.alfa, reamostras=reamostras)
    pregao = por_pregao(validos, par.taxa_usd, reamostras=reamostras)
    veredito = julgar(len(validos), baixo, pregao["p_valor"], par.alfa)
    acertos = [o.acerto_direcao for o in operacoes if o.acerto_direcao is not None]
    return {"esquema": ESQUEMA, "veredito": veredito, "criterio": "bloco_5min_e_por_pregao",
            "por_pregao": pregao, "pregoes_completos": completos,
            "pregoes_ate_lacuna": ate_lacuna, "pregoes_ate_silencio_final": ate_silencio,
            "pregoes_usados": len(validos), "operacoes": len(operacoes),
            "modo": "EXPLORATORIO_SEM_PRE_REGISTRO", "can_execute": False, "profitability_verified": False,
            "media_bruta_ticks": _soma_finita(o.ticks for o in operacoes) / len(operacoes) if operacoes else None,
            "media_liquida_ticks": media, "intervalo": [baixo, alto], "alfa": par.alfa,
            "taxa_usd": par.taxa_usd, "p5": par.p5, "p95": par.p95, "latencia_ms": par.latencia_ns / 1e6,
            "acerto_direcao": sum(acertos) / len(acertos) if acertos else None, "direcao_medida": len(acertos),
            "contagens": dict(sorted(contagens.items())), "dias": resumo_dias, "recusadas": recusadas}


def main(argv=None, *, hoje: Optional[date] = None) -> int:
    ap = argparse.ArgumentParser(prog="qhos2.avaliador", description=__doc__.splitlines()[0])
    ap.add_argument("capturas", nargs="+", help="pastas de captura da janela de teste")
    ap.add_argument("--p5", type=float, required=True, help="P5 congelado no plano")
    ap.add_argument("--p95", type=float, required=True, help="P95 congelado no plano")
    ap.add_argument("--taxa-usd", type=float, required=True, help="taxa de ida e volta registrada no plano")
    ap.add_argument("--alfa", type=float, required=True, help="alfa bicaudal registrado no plano")
    args = ap.parse_args(argv)
    hoje = hoje or date.today()
    if hoje <= JANELA_TESTE[1]:
        print(f"avaliacao recusada: a janela de teste termina em {JANELA_TESTE[1].isoformat()}; nada foi lido")
        return 2
    par = Parametros(args.p5, args.p95, args.taxa_usd, args.alfa)
    try:
        validar_parametros(par)
    except de.DesequilibrioError:
        print("avaliacao recusada: parametros invalidos")
        return 2
    try:
        r = avaliar([c for c in args.capturas if Path(c).is_dir()], par)
    except de.DesequilibrioError as erro:
        print(f"avaliacao impossivel: {erro}")
        return 2
    print(json.dumps(r, indent=2, sort_keys=True))
    return 2 if r["recusadas"] else 0


if __name__ == "__main__":
    sys.exit(main())
