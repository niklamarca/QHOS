"""Itens do Quote Board (print das 19:36 de 29/10), como dado. A bolsa esperada e so a primeira tentativa:
quem decide bolsa e contrato e a resposta do servidor (front month 114 e dado do instrumento 15).

O ES fica fora: entra no DataSet por copia da captura do agendador, nunca por uma segunda assinatura com o ES do
agendador gravando. Excecao (decisao do autor, 30/10): quando o agendador ja nao vai gravar o pregao do dia (queda,
reinicio, guarda), o coletor assina o ES como reserva no resto do pregao (coletor.reserva_do_es).
O CL aparece duas vezes no print e entra uma vez. O ZT saiu do print e espera a decisao do autor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

FRONT, EXPLICITO = "front", "explicito"
BOLSAS_CME_GROUP = ("CME", "CBOT", "NYMEX", "COMEX")


@dataclass(frozen=True)
class Item:
    exibido: str          # o que o Quote Board mostra
    tipo: str             # FRONT (raiz) ou EXPLICITO (contrato exato)
    bolsa_esperada: str
    descricao: str
    raiz: Optional[str] = None  # EXPLICITO vencido passa ao front month desta raiz (a coleta nao morre no vencimento)


QUOTE_BOARD = (
    Item("YM", FRONT, "CBOT", "Front Month for YM"),
    Item("CL", FRONT, "NYMEX", "Front Month for CL"),
    Item("NQ", FRONT, "CME", "Front Month for NQ"),
    Item("ZN", FRONT, "CBOT", "Front Month for ZN"),
    Item("NG", FRONT, "NYMEX", "Front Month for NG"),
    Item("RF", FRONT, "CME", "Front Month for RF"),
    Item("GEZ2", EXPLICITO, "CME", "Eurodollar", raiz="GE"),
    Item("ZB", FRONT, "CBOT", "Front Month for ZB"),
    Item("ZF", FRONT, "CBOT", "Front Month for ZF"),
    Item("GCZ6", EXPLICITO, "COMEX", "COMEX Gold Futures", raiz="GC"),
    Item("6B", FRONT, "CME", "Front Month for 6B"),
    Item("6J", FRONT, "CME", "Front Month for 6J"),
    Item("BTCV6", EXPLICITO, "CME", "Bitcoin Futures", raiz="BTC"),
)
ES = Item("ES", FRONT, "CME", "Front Month for ES (pregao por copia do agendador; reserva do coletor se ele cair)")

# As 15 linhas do print das 19:36 de 29/10, na ordem da tela (o CL repetido incluido). A coleta assina a lista
# inteira; o front end (terminal/) e so leitura das capturas e pode mostrar menos graficos sem mudar nada aqui.
PRINT_1936 = ("ES", "YM", "CL", "CL", "NQ", "ZN", "NG", "RF", "GEZ2", "ZB", "ZF", "GCZ6", "6B", "6J", "BTCV6")


def conferir_lista(itens) -> None:
    """Recusa lista que nao seja exatamente o print, sem repeticao. A lista base e o print sem o ES; o ES so entra
    como reserva do agendador (no pregao normal ele vem por copia da captura do agendador)."""
    exibidos = [i.exibido for i in itens]
    base = set(PRINT_1936) - {ES.exibido}
    if len(exibidos) != len(set(exibidos)) or set(exibidos) not in (base, base | {ES.exibido}):
        raise ValueError("lista_diverge_do_print")


def cobertura(ativos: list) -> dict:
    """Cada linha distinta do print com o que aconteceu com ela. `ativos` = status_multi()['ativos']."""
    por_item = {a["exibido"]: a for a in ativos}
    linhas, faltando = [], []
    for exibido in dict.fromkeys(PRINT_1936):
        a = por_item.get(exibido)
        if exibido == ES.exibido and a is None:
            linhas.append({"exibido": exibido, "estado": "copia_da_captura_do_agendador"})
            continue
        if a is None:
            faltando.append(exibido)
            linhas.append({"exibido": exibido, "estado": "AUSENTE_DA_COLETA"})
            continue
        linhas.append({"exibido": exibido, "bolsa": a["bolsa"], "contrato": a["contrato"], "estado": a["estado"],
                       "motivo": a["motivo"], "quadros": a["quadros_total"]})
    com_eventos = [l["exibido"] for l in linhas if l.get("quadros")]
    sem_explicacao = [l["exibido"] for l in linhas
                      if l["estado"] in ("gravando",) and not l.get("quadros")]
    return {"linhas_do_print": len(PRINT_1936), "itens_distintos": len(linhas), "faltando": faltando,
            "com_eventos": com_eventos, "gravando_sem_eventos": sem_explicacao,
            "todos_cobertos": not faltando, "itens": linhas}
