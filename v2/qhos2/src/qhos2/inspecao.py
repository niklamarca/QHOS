"""Inspecao dos campos de uma captura real (peca 4d): o que o MBO da Rithmic traz de fato.

So leitura. Recusa captura que o catalogo nao verifica. A saida tem so contagens: nenhum preco, tamanho
ou id de ordem e impresso, entao ela pode ser colada no chat. Responde, com os dados da propria captura:

  - quantas atualizacoes de ordem cada quadro 160 traz, e se as listas vem alinhadas;
  - se o id da ordem e a posicao na fila vem preenchidos;
  - como a sequencia anda (passo +1, repetida, salto, volta) em 116, 160, 161 e no mercado (160 + 161);
  - o ciclo de vida das ordens a partir do retrato: atualizacoes que apontam para ordens desconhecidas,
    NEW de ordem que ja existia, e se CHANGE mantem a prioridade e o preco. E o teste mais direto do
    alinhamento entre o retrato e as atualizacoes, separado em antes e depois do fim do retrato;
  - os negocios (150): lado agressor e se o id da ordem passiva ja foi visto e esta vivo no livro naquele
    instante, o que liga a execucao a remocao ou a reducao no MBO.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Dict, Optional

from qhos2 import mercado as m
from qhos2 import rprotocol as p
from qhos2.catalogo import LINHA_MAX_BYTES, _arquivos_da_captura, _ler_manifesto, verificar_captura

ESQUEMA = "qhos2.inspecao.v1"
_TIPOS = {m.NOVO: "NEW", m.ALTERACAO: "CHANGE", m.REMOCAO: "DELETE"}
_LADOS = {m.COMPRA: "compra", m.VENDA: "venda"}


class InspecaoError(ValueError):
    """Captura que nao pode ser inspecionada."""


def _somar(contador: Dict[str, int], chave: str, n: int = 1) -> None:
    contador[chave] = contador.get(chave, 0) + n


class _Sequencia:
    """Passos entre numeros de sequencia consecutivos de um fluxo."""

    def __init__(self):
        self.com, self.sem, self.anterior = 0, 0, None
        self.passos: Dict[str, int] = {}

    def ver(self, valor: Optional[int]) -> None:
        if valor is None:
            self.sem += 1
            return
        self.com += 1
        if self.anterior is not None:
            d = valor - self.anterior
            _somar(self.passos, "+1" if d == 1 else "0" if d == 0 else "salto" if d > 1 else "volta")
        self.anterior = valor

    def relatorio(self) -> dict:
        return {"com": self.com, "sem": self.sem, "passos": dict(sorted(self.passos.items()))}


class _Ciclo:
    """Ordens vivas pelo id; nunca guarda nada para a saida alem de contagens."""

    def __init__(self):
        self.vivas: Dict[str, tuple] = {}  # id -> (prioridade, preco)
        self.fases = {"durante_retrato": {}, "depois_do_retrato": {}}
        self.vistas: set = set()  # todo id que ja apareceu no retrato ou numa atualizacao

    def retrato(self, ids, prioridades, preco) -> int:
        duplicados = 0
        for i, ident in enumerate(ids):
            if not ident:
                continue
            self.vistas.add(ident)
            duplicados += ident in self.vivas
            self.vivas[ident] = (prioridades[i] if i < len(prioridades) else None, preco)
        return duplicados

    def atualizar(self, fase: str, tipo, ident, prioridade, preco) -> None:
        c = self.fases[fase]
        if not ident:
            _somar(c, "sem_id")
            return
        self.vistas.add(ident)
        anterior = self.vivas.get(ident)
        if tipo == m.NOVO:
            _somar(c, "novo_ja_viva" if anterior else "novo")
            self.vivas[ident] = (prioridade, preco)
        elif tipo == m.ALTERACAO:
            if anterior is None:
                _somar(c, "alteracao_desconhecida")
            else:
                _somar(c, "alteracao")
                if prioridade is not None and anterior[0] is not None:
                    _somar(c, "alteracao_mantem_prioridade" if prioridade == anterior[0] else "alteracao_muda_prioridade")
                if preco is not None and anterior[1] is not None and preco != anterior[1]:
                    _somar(c, "alteracao_muda_preco")
            self.vivas[ident] = (prioridade, preco)
        elif tipo == m.REMOCAO:
            _somar(c, "remocao" if anterior else "remocao_desconhecida")
            self.vivas.pop(ident, None)
        else:
            _somar(c, "tipo_desconhecido")


def _item(lista, i):
    return lista[i] if isinstance(lista, list) and i < len(lista) else None


def inspecionar(pasta) -> dict:
    """Conta o que a captura traz. Recusa captura que o catalogo nao verifica."""
    pasta = Path(pasta).resolve()
    relatorio_cat = verificar_captura(pasta)
    if not relatorio_cat["verificada"]:
        raise InspecaoError("captura_nao_verificada:" + ",".join(relatorio_cat["divergencias"]))
    manifesto = _ler_manifesto(pasta, [])
    arquivos = _arquivos_da_captura(pasta, manifesto, [])

    quadros: Dict[str, int] = {}
    invalidos: Dict[str, int] = {}
    carimbo: Dict[str, int] = {}
    seq = {"116": _Sequencia(), "150": _Sequencia(), "160": _Sequencia(), "161": _Sequencia(),
           "mercado": _Sequencia()}
    por_quadro: Dict[str, int] = {}
    entradas, desalinhados, maior = 0, 0, 0
    listas = {nome: {"com": 0, "sem": 0} for nome in m.LISTAS_160}
    tipos: Dict[str, int] = {}
    lados: Dict[str, int] = {}
    preenchido = {"exchange_order_id": 0, "depth_order_priority": 0, "prev_depth_price": 0}
    retrato = {"niveis": 0, "ordens": 0, "ids_vazios": 0, "ids_duplicados": 0, "lados": {}, "apos_o_fim": 0,
               "fim_visto": False, "recusado": False}
    ciclo = _Ciclo()
    negocios = {"quadros": 0, "retrato": 0, "sem_preco": 0, "agressor": {}, "id_passivo": 0,
                "passivo_visto": 0, "passivo_vivo": 0, "id_agressor": 0, "agressor_visto": 0,
                "agressor_vivo": 0}

    for arquivo, _ in arquivos:
        with arquivo.open("rb") as f:
            for linha in f:
                if len(linha) > LINHA_MAX_BYTES:
                    raise InspecaoError("linha_grande_demais")
                obj = json.loads(linha)
                template = str(obj["template_id"])
                _somar(quadros, template)
                try:
                    msg = m.decodificar(base64.b64decode(obj["protobuf_base64"], validate=True))
                except (m.MercadoError, ValueError):
                    _somar(invalidos, template)
                    continue
                c = msg.campos
                try:
                    m.conferir_identidade(c, manifesto["status"]["requested_symbol"], manifesto["status"]["exchange"])
                except (m.MercadoError, KeyError, TypeError):
                    raise InspecaoError("simbolo_ou_bolsa_diverge") from None
                if msg.template_id != obj["template_id"]:
                    raise InspecaoError("template_divergente")
                if "ssboe" in c:
                    _somar(carimbo, template)
                seq[template].ver(c.get("sequence_number"))
                if msg.template_id in (p.PROFUNDIDADE_ORDEM, p.PROFUNDIDADE_FIM):
                    seq["mercado"].ver(c.get("sequence_number"))

                if msg.template_id == p.RESP_RETRATO_PROFUNDIDADE:
                    if retrato["fim_visto"]:
                        retrato["apos_o_fim"] += 1
                        continue
                    ids = c.get("exchange_order_id", [])
                    if c.get("depth_price") is not None:
                        retrato["niveis"] += 1
                        _somar(retrato["lados"], _LADOS.get(c.get("depth_side"), "outro"))
                    retrato["ordens"] += len(c.get("depth_size", []))
                    retrato["ids_vazios"] += sum(1 for x in ids if not x)
                    retrato["ids_duplicados"] += ciclo.retrato(ids, c.get("depth_order_priority", []), c.get("depth_price"))
                    if c.get("rp_code"):
                        try:
                            p.sucesso(msg)
                        except p.ProtocolError:
                            retrato["recusado"] = True
                        else:
                            retrato["fim_visto"] = not retrato["recusado"]
                    continue

                if msg.template_id == p.ULTIMO_NEGOCIO:
                    negocios["quadros"] += 1
                    negocios["retrato"] += bool(c.get("is_snapshot"))
                    negocios["sem_preco"] += "trade_price" not in c
                    _somar(negocios["agressor"], _LADOS.get(c.get("aggressor"), "ausente"))
                    for nome, campo in (("passivo", "exchange_order_id"), ("agressor", "aggressor_exchange_order_id")):
                        ident = c.get(campo)
                        if ident:
                            negocios["id_" + nome] += 1
                            negocios[nome + "_visto"] += ident in ciclo.vistas
                            negocios[nome + "_vivo"] += ident in ciclo.vivas
                    continue

                if msg.template_id != p.PROFUNDIDADE_ORDEM:
                    continue
                n = len(c.get("update_type", []))
                entradas += n
                maior = max(maior, n)
                _somar(por_quadro, "0" if n == 0 else "1" if n == 1 else "2-5" if n <= 5 else "6+")
                tamanhos = set()
                for nome in m.LISTAS_160:
                    tamanho = len(c.get(nome, []))
                    listas[nome]["com" if tamanho else "sem"] += 1
                    if tamanho:
                        tamanhos.add(tamanho)
                if len(tamanhos | {n}) > 1:
                    desalinhados += 1
                preenchido["exchange_order_id"] += sum(1 for x in c.get("exchange_order_id", []) if x)
                preenchido["depth_order_priority"] += len(c.get("depth_order_priority", []))
                preenchido["prev_depth_price"] += len(c.get("prev_depth_price", []))
                fase = "depois_do_retrato" if retrato["fim_visto"] else "durante_retrato"
                for i in range(n):
                    tipo = c["update_type"][i]
                    _somar(tipos, _TIPOS.get(tipo, "outro"))
                    _somar(lados, _LADOS.get(_item(c.get("transaction_type"), i), "ausente"))
                    ciclo.atualizar(fase, tipo, _item(c.get("exchange_order_id"), i),
                                    _item(c.get("depth_order_priority"), i), _item(c.get("depth_price"), i))

    return {"esquema": ESQUEMA, "captura_id": pasta.name, "ambiente": relatorio_cat.get("ambiente"),
            "simbolo": relatorio_cat.get("simbolo"), "bolsa": relatorio_cat.get("bolsa"),
            "quadros": dict(sorted(quadros.items())), "invalidos": dict(sorted(invalidos.items())),
            "com_carimbo": dict(sorted(carimbo.items())),
            "sequencia": {k: s.relatorio() for k, s in seq.items()},
            "atualizacoes": {"entradas": entradas, "por_quadro": dict(sorted(por_quadro.items())), "maior_quadro": maior,
                             "quadros_desalinhados": desalinhados, "listas": listas, "tipos": dict(sorted(tipos.items())),
                             "lados": dict(sorted(lados.items())), "preenchidos": preenchido},
            "negocios": negocios, "retrato": retrato,
            "ciclo": {fase: dict(sorted(c.items())) for fase, c in ciclo.fases.items()},
            "ordens_vivas_no_fim": len(ciclo.vivas)}


def _pct(parte: int, todo: int) -> str:
    return f"{100 * parte / todo:.1f}%" if todo else "-"


def _imprimir(r: dict) -> None:
    a, rt = r["atualizacoes"], r["retrato"]
    print(f"captura={r['captura_id']} ambiente={r['ambiente']} {r['bolsa']}/{r['simbolo']}")
    print(f"  quadros={r['quadros']} invalidos={r['invalidos']} com_carimbo={r['com_carimbo']}")
    for nome, s in r["sequencia"].items():
        print(f"  sequencia {nome:<8} com={s['com']} sem={s['sem']} passos={s['passos']}")
    print(f"  160: entradas={a['entradas']} por_quadro={a['por_quadro']} maior={a['maior_quadro']} "
          f"desalinhados={a['quadros_desalinhados']}")
    print(f"  160: tipos={a['tipos']} lados={a['lados']}")
    e = a["entradas"]
    print(f"  160: id_preenchido={_pct(a['preenchidos']['exchange_order_id'], e)} "
          f"prioridade={_pct(a['preenchidos']['depth_order_priority'], e)} "
          f"preco_anterior={a['preenchidos']['prev_depth_price']} valores")
    print(f"  160: listas={ {k: v['com'] for k, v in a['listas'].items()} }")
    print(f"  retrato: niveis={rt['niveis']} ordens={rt['ordens']} lados={rt['lados']} ids_vazios={rt['ids_vazios']} "
          f"ids_duplicados={rt['ids_duplicados']} fim_visto={rt['fim_visto']} apos_o_fim={rt['apos_o_fim']}")
    ng = r["negocios"]
    print(f"  150: quadros={ng['quadros']} retrato={ng['retrato']} sem_preco={ng['sem_preco']} agressor={ng['agressor']}")
    print(f"  150: id_passivo={_pct(ng['id_passivo'], ng['quadros'])} visto={_pct(ng['passivo_visto'], ng['id_passivo'])} "
          f"vivo={_pct(ng['passivo_vivo'], ng['id_passivo'])} | id_agressor={_pct(ng['id_agressor'], ng['quadros'])} "
          f"visto={_pct(ng['agressor_visto'], ng['id_agressor'])} vivo={_pct(ng['agressor_vivo'], ng['id_agressor'])}")
    for fase, c in r["ciclo"].items():
        print(f"  ciclo {fase}: {c}")
    print(f"  ordens_vivas_no_fim={r['ordens_vivas_no_fim']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="qhos2.inspecao", description=__doc__.splitlines()[0])
    ap.add_argument("capturas", nargs="+", help="pasta(s) de captura; uma que falhe nao impede as outras")
    ap.add_argument("--json", action="store_true", help="imprime o relatorio completo em JSON")
    args = ap.parse_args(argv)
    recusadas = 0
    for caminho in args.capturas:
        if not Path(caminho).is_dir():
            continue  # o curinga do shell pode trazer arquivos soltos
        try:
            r = inspecionar(caminho)
        except InspecaoError as erro:
            recusadas += 1
            print(f"recusada: {Path(caminho).name}: {erro}")
            continue
        if args.json:
            print(json.dumps(r, indent=2, sort_keys=True))
        else:
            _imprimir(r)
    return 2 if recusadas else 0


if __name__ == "__main__":
    sys.exit(main())
