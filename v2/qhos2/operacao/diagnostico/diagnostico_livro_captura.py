"""Etapa 2, passo 7: ONDE e QUANDO o livro de uma captura deixa de ser confiavel.

So leitura. Percorre a captura quadro a quadro com o mesmo Livro da 0.2.5 e registra:
  - o quadro do RETRATO_FIM e a hora;
  - as primeiras 12 anomalias depois do retrato (quadro, hora UTC/BRT, tipo);
  - o primeiro quadro que termina cruzado e desde quando o livro ficou cruzado sem voltar;
  - por faixa de 15 min: quadros, anomalias de cada tipo, quadros cruzados no fim, spread no fim da faixa;
  - os 8 maiores intervalos entre quadros consecutivos (relogio local de chegada), com a hora.
Uso: python diagnostico_livro_captura.py <pasta da captura> <arquivo de saida .json>
"""
import itertools
import json
import sys
from datetime import datetime, timedelta, timezone

from qhos2 import eventos as ev
from qhos2.livro import Livro, PRONTO

CHAVES = ("remove_desconhecida", "novo_ja_vivo", "altera_desconhecida", "negocio_com_passiva_fora_do_livro",
          "retrato_apos_o_fim", "sem_id", "quantidade_invalida", "lado_ou_preco_invalido", "evento_desconhecido")
BRT = timezone(timedelta(hours=-3))


def hora(ns):
    t = datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
    return t.strftime("%H:%M:%S.%f")[:-3] + "Z / " + t.astimezone(BRT).strftime("%H:%M:%S") + " BRT"


def main(pasta, saida):
    livro = Livro()
    antes = {k: 0 for k in CHAVES}
    primeiras, faixas, gaps = [], {}, []
    fim_retrato = None
    primeiro_cruzado = None
    cruzado_desde = None          # inicio da sequencia atual de quadros terminando cruzados
    maior_sequencia = (0, None)   # (quadros, inicio)
    sequencia = 0
    anterior_ns = None
    quadros = 0
    for indice, grupo in itertools.groupby(ev.eventos_da_captura(pasta), key=lambda e: e.indice):
        eventos = list(grupo)
        ns = eventos[0].recebido_ns
        if anterior_ns is not None:
            gaps.append((ns - anterior_ns, indice, ns))
            if len(gaps) > 4000:
                gaps.sort(reverse=True); del gaps[8:]
        anterior_ns = ns
        era_pronto = livro.fase == PRONTO
        livro.aplicar_quadro(eventos)
        quadros += 1
        if not era_pronto and livro.fase == PRONTO:
            fim_retrato = {"quadro": indice, "hora": hora(ns)}
        f = faixas.setdefault(ns // (15 * 60 * 10**9), {"inicio": hora(ns), "quadros": 0, "cruzado_fim": 0,
                                                        **{k: 0 for k in CHAVES}})
        f["quadros"] += 1
        if livro.fase == PRONTO:
            for k in CHAVES:
                v = livro.anomalias.get(k, 0)
                if v != antes[k]:
                    f[k] += v - antes[k]
                    if len(primeiras) < 12:
                        primeiras.append({"quadro": indice, "hora": hora(ns), "tipo": k, "novas": v - antes[k],
                                          "spread_ticks": livro.resumo()["spread_ticks"]})
                    antes[k] = v
            if livro.cruzado:
                f["cruzado_fim"] += 1
                sequencia += 1
                if cruzado_desde is None:
                    cruzado_desde = {"quadro": indice, "hora": hora(ns)}
                if primeiro_cruzado is None:
                    primeiro_cruzado = {"quadro": indice, "hora": hora(ns), "spread_ticks": livro.resumo()["spread_ticks"]}
            else:
                if sequencia > maior_sequencia[0]:
                    maior_sequencia = (sequencia, cruzado_desde)
                sequencia, cruzado_desde = 0, None
        f["spread_fim"] = livro.resumo()["spread_ticks"]
    if sequencia > maior_sequencia[0]:
        maior_sequencia = (sequencia, cruzado_desde)
    gaps.sort(reverse=True)
    r = {"captura": str(pasta), "quadros": quadros, "resumo_final": livro.resumo(), "fim_retrato": fim_retrato,
         "primeiras_anomalias_depois_do_retrato": primeiras, "primeiro_quadro_cruzado": primeiro_cruzado,
         "cruzado_sem_voltar_desde": cruzado_desde, "maior_sequencia_cruzada": {"quadros": maior_sequencia[0],
                                                                                  "inicio": maior_sequencia[1]},
         "maiores_intervalos_entre_quadros": [{"segundos": round(g / 1e9, 3), "quadro": i, "hora": hora(n)}
                                              for g, i, n in gaps[:8]],
         "faixas_15min": [faixas[k] for k in sorted(faixas)]}
    with open(saida, "w") as fp:
        json.dump(r, fp, indent=1)
    print("fim do retrato:", fim_retrato)
    print("primeiro quadro cruzado:", primeiro_cruzado)
    print("cruzado sem voltar desde:", cruzado_desde, "| maior sequencia cruzada:", maior_sequencia[0], "quadros desde", maior_sequencia[1])
    print("primeiras anomalias depois do retrato:")
    for p in primeiras:
        print("  ", p)
    print("maiores intervalos entre quadros:")
    for g in r["maiores_intervalos_entre_quadros"]:
        print("  ", g)
    print("faixa de 15 min: inicio | quadros | cruzado_fim | remove_desc | novo_ja_vivo | altera_desc | passiva_fora | spread_fim")
    for f in r["faixas_15min"]:
        print(f"   {f['inicio']} | {f['quadros']} | {f['cruzado_fim']} | {f['remove_desconhecida']} | {f['novo_ja_vivo']} | "
              f"{f['altera_desconhecida']} | {f['negocio_com_passiva_fora_do_livro']} | {f.get('spread_fim')}")
    print("gravado em", saida)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
