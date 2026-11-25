"""Calendario sem prazo: renovado a cada partida dos servicos, por regra, no formato de qhos2.agenda.

O calendario de 28/10 a 25/11 foi conferido a mao; este modulo continua a mesma janela (09:00-16:15 NY, uma por
dia util) sem data de fim, com estas regras (as mesmas do pregao do ES na CME e da NYSE):

  feriados sem pregao regular: Ano Novo, Martin Luther King (3a seg. jan), Presidents (3a seg. fev), Sexta-feira
    Santa, Memorial (ult. seg. mai), Juneteenth (19/6), Independencia (4/7), Labor (1a seg. set), Thanksgiving
    (4a qui. nov), Natal. Sabado vira sexta e domingo vira segunda, menos o Ano Novo no sabado (nao ha folga);
  sessao curta (fechamento 13:15 NY, janela ate 13:30): dia seguinte ao Thanksgiving, 24/12 e 3/7 em dia util;
  contrato do ES: trimestral (H M U Z), vence na 3a sexta; rola na quinta 8 dias antes (regra de rolagem da CME).

Cada calendario termina na vespera da proxima rolagem: o agendador esgota, sai com 0, o systemd o liga de novo e
o lancador gera o calendario seguinte com o contrato novo. Fechamento extraordinario (luto nacional, pane) NAO e
previsto: o dia e capturado como qualquer outro e o silencio fica registrado pela vigia.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, time as hora, timedelta
from pathlib import Path

from qhos2 import agenda

INICIO, FIM, FIM_CURTO = hora(9, 0), hora(16, 15), hora(13, 30)
HORIZONTE_DIAS = 120
MESES_ES = {3: "H", 6: "M", 9: "U", 12: "Z"}
FONTE = ("regra de coleta_multi.calendario_continuo: dias uteis 09:00-16:15 America/New_York; feriados de bolsa "
         "dos EUA (NYSE/CME) e sessoes curtas (13:30) calculados por regra; termina na vespera da rolagem do ES")


def _enesimo(ano: int, mes: int, dia_semana: int, n: int) -> date:
    d = date(ano, mes, 1)
    d += timedelta(days=(dia_semana - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _ultimo(ano: int, mes: int, dia_semana: int) -> date:
    d = date(ano, mes + 1, 1) - timedelta(days=1) if mes < 12 else date(ano, 12, 31)
    return d - timedelta(days=(d.weekday() - dia_semana) % 7)


def _pascoa(ano: int) -> date:
    a, b, c = ano % 19, ano // 100, ano % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 19 * l) // 433
    mes = (h + l - 7 * m + 90) // 25
    return date(ano, mes, (h + l - 7 * m + 33 * mes + 19) % 32)


def _observado(d: date) -> date:
    return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d


def feriados(ano: int) -> dict:
    """{data: nome} dos dias sem pregao regular no ano."""
    saida = {}
    ano_novo = date(ano, 1, 1)
    if ano_novo.weekday() != 5:  # no sabado nao ha folga (a sexta 31/12 tem pregao)
        saida[_observado(ano_novo)] = "ano_novo"
    saida[_enesimo(ano, 1, 0, 3)] = "martin_luther_king"
    saida[_enesimo(ano, 2, 0, 3)] = "presidents_day"
    saida[_pascoa(ano) - timedelta(days=2)] = "sexta_feira_santa"
    saida[_ultimo(ano, 5, 0)] = "memorial_day"
    if ano >= 2022:
        saida[_observado(date(ano, 6, 19))] = "juneteenth"
    saida[_observado(date(ano, 7, 4))] = "independencia"
    saida[_enesimo(ano, 9, 0, 1)] = "labor_day"
    saida[_enesimo(ano, 11, 3, 4)] = "thanksgiving"
    saida[_observado(date(ano, 12, 25))] = "natal"
    return saida


def sessoes_curtas(ano: int) -> dict:
    curtas = {_enesimo(ano, 11, 3, 4) + timedelta(days=1): "dia_seguinte_ao_thanksgiving"}
    for d, nome in ((date(ano, 12, 24), "vespera_de_natal"), (date(ano, 7, 3), "vespera_da_independencia")):
        if d.weekday() < 5 and d not in feriados(ano):
            curtas[d] = nome
    return curtas


def _vencimento_es(ano: int, mes: int) -> date:
    return _enesimo(ano, mes, 4, 3)


def rolagem_es(ano: int, mes: int) -> date:
    return _vencimento_es(ano, mes) - timedelta(days=8)


def contrato_es(dia: date) -> tuple:
    """(simbolo, dia da proxima rolagem): o contrato vigente em `dia` pela regra de rolagem."""
    ano = dia.year
    for _ in range(3):
        for mes, letra in MESES_ES.items():
            rolagem = rolagem_es(ano, mes)
            if dia < rolagem:
                return f"ES{letra}{ano % 10}", rolagem
        ano += 1
    raise ValueError("contrato_es_nao_encontrado")


def gerar(de: date, *, horizonte_dias: int = HORIZONTE_DIAS) -> dict:
    """Calendario no formato qhos2_calendario_v1, de `de` ate a vespera da proxima rolagem do ES."""
    simbolo, rolagem = contrato_es(de)
    ate = min(rolagem - timedelta(days=1), de + timedelta(days=horizonte_dias))
    fechados, curtos = {}, {}
    for ano in range(de.year, ate.year + 1):
        fechados.update(feriados(ano))
        curtos.update(sessoes_curtas(ano))
    janelas, dia = [], de
    while dia <= ate:
        if dia.weekday() < 5 and dia not in fechados:
            fim = FIM_CURTO if dia in curtos else FIM
            janelas.append({"dia": dia.isoformat(), "inicio": agenda._iso(datetime.combine(dia, INICIO, agenda.NY)),
                            "fim": agenda._iso(datetime.combine(dia, fim, agenda.NY))})
        dia += timedelta(days=1)
    calendario = {"schema": "qhos2_calendario_v1", "fonte": FONTE,
                  "valido_de": agenda._iso(datetime.combine(de, hora(0), agenda.NY)),
                  "valido_ate": agenda._iso(datetime.combine(ate + timedelta(days=1), hora(0), agenda.NY)),
                  "janela_ny": f"{INICIO:%H:%M}-{FIM:%H:%M} America/New_York (curta ate {FIM_CURTO:%H:%M})",
                  "dias_excluidos": sorted(d.isoformat() for d in fechados if de <= d <= ate),
                  "sessoes_curtas": sorted(d.isoformat() for d in curtos if de <= d <= ate),
                  "contrato_es": simbolo, "proxima_rolagem_es": rolagem.isoformat(), "janelas": janelas}
    if janelas:
        agenda.janelas(calendario)  # o mesmo validador do agendador
    return calendario


def atualizar(pasta: Path, hoje: date) -> tuple:
    """Escreve <pasta>/calendario-atual.json (atomico) e uma copia datada. Devolve (caminho, calendario)."""
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    calendario = gerar(hoje)
    texto = json.dumps(calendario, indent=2, sort_keys=True) + "\n"
    datado = pasta / f"calendario-{hoje:%Y%m%d}-{calendario['valido_ate'][:10].replace('-', '')}.json"
    for destino in (datado, pasta / "calendario-atual.json"):
        tmp = destino.with_name("." + destino.name + f".{os.getpid()}.tmp")
        tmp.write_text(texto, encoding="utf-8")
        os.replace(tmp, destino)
    return pasta / "calendario-atual.json", calendario


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="coleta_multi.calendario_continuo", description=__doc__.splitlines()[0])
    ap.add_argument("--de", type=date.fromisoformat, default=None)
    args = ap.parse_args(argv)
    c = gerar(args.de or datetime.now(agenda.NY).date())
    print(json.dumps({k: c[k] for k in ("contrato_es", "proxima_rolagem_es", "valido_de", "valido_ate",
                                        "dias_excluidos", "sessoes_curtas")}, indent=2))
    print(f"janelas: {len(c['janelas'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
