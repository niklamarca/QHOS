"""Coleta multi-ativo contra um servidor falso (sem rede, sem credenciais reais).

Rodar no WSL, de qhos2/:  PYTHONPATH=src:. python -m pytest coleta_multi/tests -q
"""
import asyncio
import base64
import json
import struct
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from qhos2 import catalogo, mercado
from qhos2 import rprotocol as p
from qhos2.config import Settings
from qhos2.sessao import ConexaoFechada, Credenciais, SessaoError

from coleta_multi import fio, sonda
from coleta_multi.lista import EXPLICITO, FRONT, QUOTE_BOARD, Item
from coleta_multi.sessao_multi import SessaoMulti, falha_de_login

SENHA = "SEGREDO-XYZ-987"
CRED = Credenciais("trader@exemplo.com", SENHA)
DIA = date(2026, 10, 30)
GIB = 1024 ** 3


def _v(n):
    saida = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        saida.append(b | (0x80 if n else 0))
        if not n:
            return bytes(saida)


def _int(numero, valor):
    return _v(numero << 3) + _v(valor)


def _txt(numero, texto):
    bruto = texto.encode()
    return _v(numero << 3 | 2) + _v(len(bruto)) + bruto


def _dbl(numero, valor):
    return _v(numero << 3 | 1) + struct.pack("<d", valor)


def _msg(template, *campos):
    return _int(154467, template) + b"".join(campos)


def _rp(codigo="0"):
    return _txt(132766, codigo)


def _id(simbolo, bolsa):
    return _txt(110100, simbolo) + _txt(110101, bolsa)


def q160(simbolo, bolsa, i):
    return _msg(160, _id(simbolo, bolsa), _int(110121, 1), _int(153612, 1), _dbl(154405, 100.0 + i),
                _int(154406, 1 + i), _int(153613, i), _txt(149238, f"O{i}"), _int(150100, 1_793_300_000 + i))


def q150(simbolo, bolsa, i):
    return _msg(150, _id(simbolo, bolsa), _dbl(100006, 100.0 + i), _int(100178, 1), _int(112003, 1),
                _int(150100, 1_793_300_000 + i))


def q161(*pares):
    return _msg(161, *[_txt(110100, s) for s, _ in pares], *[_txt(110101, b) for _, b in pares],
                _int(150100, 1_793_300_000))


FECHAR = object()

# (raiz, bolsa) -> contrato devolvido pelo front month
FRONTS = {("CL", "NYMEX"): "CLZ6", ("ZN", "CBOT"): "ZNZ6", ("RF", "CME"): "RFZ6", ("NQ", "CME"): "NQZ6"}
# (contrato, bolsa) -> vencimento
REFS = {("CLZ6", "NYMEX"): "20261119", ("ZNZ6", "CBOT"): "20261221", ("RFZ6", "CME"): "20261214",
        ("NQZ6", "CME"): "20261218", ("GCZ6", "COMEX"): "20261229", ("GEZ2", "CME"): "20221219",
        ("BTCV6", "CME"): "20261030"}


class ConexaoFalsa:
    def __init__(self, servidor):
        self.servidor, self.fila, self.enviados, self.fechada = servidor, asyncio.Queue(), [], False
        self.mbo, self.negocios = set(), set()

    async def enviar(self, dados):
        if self.fechada:
            raise ConexaoFechada()
        self.enviados.append(dados)
        self.servidor.reagir(self, dados)

    async def receber(self):
        item = await self.fila.get()
        if item is FECHAR:
            raise ConexaoFechada()
        return item

    async def fechar(self):
        self.fechada = True

    def templates(self):
        return [fio.ler_rapido(d)[0] for d in self.enviados]


class Servidor:
    def __init__(self, *, rp_login="0", recusa_mbo=(), recusa_negocios=(), linhas_retrato=2, quadros=5,
                 sem_dado=(), cair_apos=None, forcar_logout_apos=None, logins_aceitos=None, fronts=None, refs=None,
                 desconhecido=False):
        self.rp_login, self.recusa_mbo, self.recusa_negocios = rp_login, set(recusa_mbo), set(recusa_negocios)
        self.linhas_retrato, self.quadros, self.sem_dado = linhas_retrato, quadros, set(sem_dado)
        self.cair_apos, self.forcar_logout_apos, self.logins_aceitos = cair_apos, forcar_logout_apos, logins_aceitos
        self.fronts, self.refs = fronts or FRONTS, refs or REFS
        self.desconhecido = desconhecido
        self.conexoes, self.logins = [], 0

    def fabrica(self):
        async def abrir():
            con = ConexaoFalsa(self)
            self.conexoes.append(con)
            return con
        return abrir

    def reagir(self, con, dados):
        campos = {}
        for numero, _, valor in p._campos_brutos(dados):
            campos.setdefault(numero, []).append(valor)
        t = fio.ler_rapido(dados)[0]
        texto = lambda n: campos[n][0].decode() if n in campos else None
        simbolo, bolsa = texto(110100), texto(110101)
        dar = con.fila.put_nowait
        if t == 16:
            dar(_msg(17, _rp(), _txt(153628, "Rithmic Paper Trading")))
        elif t == 10:
            self.logins += 1
            recusado = self.rp_login != "0" or (self.logins_aceitos is not None and self.logins > self.logins_aceitos)
            dar(_msg(11, _rp("13" if recusado and self.rp_login == "0" else self.rp_login), _dbl(153633, 0.4)))
        elif t == 113:
            contrato = self.fronts.get((simbolo, bolsa))
            dar(_msg(114, _rp("0"), _id(simbolo, bolsa), _txt(157095, contrato), _txt(157096, bolsa))
                if contrato else _msg(114, _rp("7")))
        elif t == 14:
            venc = self.refs.get((simbolo, bolsa))
            dar(_msg(15, _rp(), _id(simbolo, bolsa), _txt(100067, venc), _txt(100749, simbolo[:-2]))
                if venc else _msg(15, _rp("7")))
        elif t == 117:
            assinar = campos[100000][0] == 1
            if assinar and simbolo in self.recusa_mbo:
                dar(_msg(118, _rp("5")))
            else:
                (con.mbo.add if assinar else con.mbo.discard)((simbolo, bolsa))
                dar(_msg(118, _rp()))
        elif t == 115:
            token = texto(132760)
            for i in range(self.linhas_retrato):
                dar(_msg(116, _txt(132760, token), _id(simbolo, bolsa), _int(153612, 1), _dbl(154405, 99.0 - i),
                         _int(154406, 2)))
            dar(_msg(116, _txt(132760, token), _rp()))
        elif t == 100:
            assinar = campos[100000][0] == 1
            if assinar and simbolo in self.recusa_negocios:
                dar(_msg(101, _rp("5")))
                return
            (con.negocios.add if assinar else con.negocios.discard)((simbolo, bolsa))
            dar(_msg(101, _rp()))
            if assinar:
                self._empurrar(con, simbolo, bolsa)
        elif t == 18:
            agora = datetime.now(timezone.utc).timestamp()
            dar(_msg(19, _rp(), _int(150100, int(agora)), _int(150101, int((agora % 1) * 1e6))))
        elif t == 12:
            dar(_msg(13, _rp()))

    def _empurrar(self, con, simbolo, bolsa):
        if simbolo in self.sem_dado:
            return
        for i in range(self.quadros):
            if (simbolo, bolsa) in con.mbo:
                con.fila.put_nowait(q160(simbolo, bolsa, i))
            con.fila.put_nowait(q150(simbolo, bolsa, i))
        pares = sorted(con.mbo)
        if len(pares) >= 2:
            con.fila.put_nowait(q161(*pares[:2]))
        if self.desconhecido:
            con.fila.put_nowait(q160("ZZZ9", "CME", 0))
        enviados = sum(1 for d in con.enviados if fio.ler_rapido(d)[0] == 100)
        if self.cair_apos is not None and enviados >= self.cair_apos:
            con.fila.put_nowait(FECHAR)
        if self.forcar_logout_apos is not None and enviados >= self.forcar_logout_apos:
            con.fila.put_nowait(_msg(77))


ITENS = (Item("CL", FRONT, "NYMEX", "Front Month for CL"), Item("ZN", FRONT, "CBOT", "Front Month for ZN"),
         Item("GCZ6", EXPLICITO, "COMEX", "COMEX Gold Futures"))


def _sessao(servidor, tmp_path, itens=ITENS, **kw):
    opcoes = dict(settings=Settings(data_root=tmp_path / "dados"), raiz=tmp_path / "staging", dia=DIA,
                  max_bytes_ativo=GIB, max_bytes_global=4 * GIB, reserva_disco_bytes=0, duracao_s=0.3)
    opcoes.update(kw)
    return SessaoMulti(itens, servidor.fabrica(), **opcoes)


def _rodar(sessao):
    async def ir():
        try:
            await sessao.executar(CRED)
        except SessaoError:
            pass
        return sessao.fechar_gravadores()
    return asyncio.run(ir())


def _linhas(pasta):
    saida = []
    for linha in (Path(pasta) / "market_frames.jsonl").read_text().splitlines():
        reg = json.loads(linha)
        saida.append((reg["template_id"], base64.b64decode(reg["protobuf_base64"])))
    return saida


def _por(sessao, exibido):
    return next(a for a in sessao.ativos if a.item.exibido == exibido)


# ---- fio ------------------------------------------------------------------------------------------

def test_front_month_sem_atualizacoes_e_sem_pedido_de_ordem():
    dados = fio.codificar_front_month("CL", "NYMEX")
    campos = {n: v for n, _, v in p._campos_brutos(dados)}
    assert campos[154467] == 113 and campos[110100] == b"CL" and campos[110101] == b"NYMEX"
    assert campos[fio.F_NEED_UPDATES] == 0
    assert fio.PEDIDOS_EXTRAS == {113}
    assert not (fio.PEDIDOS_EXTRAS | p.PEDIDOS_PERMITIDOS) & set(range(300, 400))  # nenhuma ordem


def test_leitura_rapida_bate_com_o_decodificador_do_mercado():
    for dados in (q160("CLZ6", "NYMEX", 3), q150("GCZ6", "COMEX", 1), q161(("CLZ6", "NYMEX"), ("ZNZ6", "CBOT"))):
        t, simbolos, bolsas = fio.ler_rapido(dados)
        m = mercado.decodificar(dados)
        assert t == m.template_id
        esperado = m.campos["symbol"] if isinstance(m.campos["symbol"], list) else [m.campos["symbol"]]
        assert simbolos == esperado
    with pytest.raises(p.ProtocolError):
        fio.ler_rapido(q160("CLZ6", "NYMEX", 1)[:-3])


def test_quote_board_tem_o_cl_uma_vez_e_nao_tem_es_nem_zt():
    exibidos = [i.exibido for i in QUOTE_BOARD]
    assert exibidos.count("CL") == 1 and "ES" not in exibidos and "ZT" not in exibidos
    assert len(exibidos) == len(set(exibidos)) == 13


# ---- roteamento ------------------------------------------------------------------------------------

def test_cada_quadro_vai_ao_gravador_do_seu_ativo(tmp_path):
    servidor = Servidor(desconhecido=True)
    sessao = _sessao(servidor, tmp_path)
    fechados = _rodar(sessao)
    assert sessao.status()["estado"] == "FECHADA" and sessao.status()["logout_confirmado"]
    assert {a.captura_id for a, m, e in fechados if m} == {"NYMEX_CLZ6", "CBOT_ZNZ6", "COMEX_GCZ6"}
    for ativo, manifesto, erro in fechados:
        assert erro is None
        rel = catalogo.verificar_captura(ativo.gravador.pasta)
        assert rel["verificada"] and rel["fechamento_normal"] is True
        for template, dados in _linhas(ativo.gravador.pasta):
            _, simbolos, bolsas = fio.ler_rapido(dados)
            if template == 161:
                assert ativo.contrato in simbolos
            else:
                assert set(simbolos) <= {ativo.contrato} and set(bolsas) <= {ativo.bolsa}
        assert ativo.quadros[160] == 5 and ativo.quadros[150] == 5 and ativo.quadros[116] == 3
        m = json.loads(Path(manifesto).read_text())
        assert m["status"]["requested_symbol"] == ativo.contrato and m["status"]["exchange"] == ativo.bolsa
        assert m["status"]["sessao"]["ativo"]["exibido"] == ativo.item.exibido
        assert m["status"]["sessao"]["relogio_serie"]["amostras"] >= 0
    assert sessao.sem_dono == {"160": 3}  # o ZZZ9 de cada empurrao nao foi para ninguem
    assert sum(a.quadros[161] for a in sessao.ativos) >= 2


def test_bolsa_vem_do_servidor_com_tentativas_registradas(tmp_path):
    servidor = Servidor()
    sessao = _sessao(servidor, tmp_path, itens=(Item("RF", FRONT, "CBOT", "Front Month for RF"),))
    _rodar(sessao)
    rf = sessao.ativos[0]
    assert (rf.bolsa, rf.contrato, rf.estado) == ("CME", "RFZ6", "gravando")
    assert rf.tentativas == ["CBOT:front_month_7"]


def test_vencido_e_duplicado_ficam_fora_sem_assinatura(tmp_path):
    itens = ITENS + (Item("GEZ2", EXPLICITO, "CME", "Eurodollar"), Item("CL", FRONT, "NYMEX", "linha repetida"))
    servidor = Servidor()
    sessao = _sessao(servidor, tmp_path, itens=itens)
    _rodar(sessao)
    ge, cl2 = sessao.ativos[3], sessao.ativos[4]
    assert (ge.estado, ge.motivo) == ("excluido", "vencido_20221219") and ge.gravador is None
    assert (cl2.estado, cl2.motivo) == ("excluido", "duplicado_de_CL")
    assinados = [fio.ler_rapido(d)[1] for d in servidor.conexoes[1].enviados if fio.ler_rapido(d)[0] == 117]
    assert ["GEZ2"] not in assinados and assinados.count(["CLZ6"]) == 2  # assinar e cancelar, uma vez so


def test_explicito_vencido_passa_ao_front_month_da_raiz(tmp_path):
    servidor = Servidor(fronts={("GC", "COMEX"): "GCZ6"}, refs={("GCQ6", "COMEX"): "20260827", ("GCZ6", "COMEX"): "20261229"})
    sessao = _sessao(servidor, tmp_path, itens=(Item("GCQ6", EXPLICITO, "COMEX", "ouro antigo", raiz="GC"),))
    _rodar(sessao)
    gc = sessao.ativos[0]
    assert (gc.bolsa, gc.contrato, gc.estado) == ("COMEX", "GCZ6", "gravando") and gc.quadros[160] == 5
    assert gc.tentativas == ["COMEX:GCQ6_vencido_20260827_passa_ao_front_month_GC"]


def test_vence_hoje_continua_dentro(tmp_path):
    sessao = _sessao(Servidor(), tmp_path, itens=(Item("BTCV6", EXPLICITO, "CME", "Bitcoin"),))
    _rodar(sessao)
    assert sessao.ativos[0].estado == "gravando"


# BTC tem front month (BTCX6) como na Rithmic: o BTCV6 venceu em 30/10 e, nos testes que usam a data de hoje, o
# explicito vencido passa ao front month da raiz; sem ele o BTC sairia da lista depois de 30/10.
FRONTS_PRINT = {("YM", "CBOT"): "YMZ6", ("CL", "NYMEX"): "CLZ6", ("NQ", "CME"): "NQZ6", ("ZN", "CBOT"): "ZNZ6",
                ("NG", "NYMEX"): "NGZ6", ("RF", "CME"): "RFZ6", ("ZB", "CBOT"): "ZBZ6", ("ZF", "CBOT"): "ZFZ6",
                ("6B", "CME"): "6BZ6", ("6J", "CME"): "6JZ6", ("BTC", "CME"): "BTCX6"}
REFS_PRINT = {**{(c, b): "20261218" for (_, b), c in FRONTS_PRINT.items()},
              ("GCZ6", "COMEX"): "20261229", ("GEZ2", "CME"): "20221219", ("BTCV6", "CME"): "20261030"}


def test_lista_inteira_do_print_e_assinada_independente_do_front_end(tmp_path):
    from coleta_multi.lista import PRINT_1936, cobertura
    servidor = Servidor(fronts=FRONTS_PRINT, refs=REFS_PRINT)
    sessao = _sessao(servidor, tmp_path, itens=QUOTE_BOARD)
    fechados = _rodar(sessao)
    assert len(fechados) == 12  # 13 itens distintos fora o ES; o GEZ2 vencido nao grava
    assinados = {tuple(fio.ler_rapido(d)[1]) for d in servidor.conexoes[1].enviados if fio.ler_rapido(d)[0] == 117}
    assert len(assinados) == 12 and ("GEZ2",) not in assinados
    for ativo, manifesto, erro in fechados:
        assert ativo.quadros[160] == 5 and ativo.quadros[150] == 5
        assert catalogo.verificar_captura(ativo.gravador.pasta)["verificada"]
        for template, dados in _linhas(ativo.gravador.pasta):
            if template != 161:
                assert set(fio.ler_rapido(dados)[1]) <= {ativo.contrato}
    cob = cobertura(sessao.status_multi()["ativos"])
    assert cob["todos_cobertos"] and cob["faltando"] == [] and cob["gravando_sem_eventos"] == []
    assert cob["linhas_do_print"] == 15 and cob["itens_distintos"] == 14 and len(cob["com_eventos"]) == 12
    ge = next(l for l in cob["itens"] if l["exibido"] == "GEZ2")
    assert (ge["estado"], ge["motivo"]) == ("excluido", "vencido_20221219_e_sem_front_month_GE")
    assert set(PRINT_1936) == {l["exibido"] for l in cob["itens"]}


def test_parada_pedida_logo_depois_do_login_nao_assina_nada(tmp_path):
    # a vigia do coletor pode pedir parada enquanto a sessao ainda esta assinando (ES do agendador voltou)
    servidor = Servidor()
    sessao = _sessao(servidor, tmp_path, ao_logar=lambda: setattr(sessao, "parada", "es_do_agendador_capturando"))
    _rodar(sessao)
    pedidos = {fio.ler_rapido(d)[0] for con in servidor.conexoes for d in con.enviados}
    assert not pedidos & {117, 115, 100}  # nem MBO, nem retrato, nem negocios
    assert sessao.status()["logout_confirmado"]


def test_lista_diferente_do_print_e_recusada():
    from coleta_multi.lista import conferir_lista, cobertura
    conferir_lista(QUOTE_BOARD)
    with pytest.raises(ValueError, match="lista_diverge_do_print"):
        conferir_lista(QUOTE_BOARD[:3])  # so os 3 que um front end mostraria
    with pytest.raises(ValueError):
        conferir_lista(QUOTE_BOARD + (QUOTE_BOARD[1],))
    assert cobertura([])["faltando"] == [i.exibido for i in QUOTE_BOARD]


# ---- falhas por ativo ------------------------------------------------------------------------------

def test_ativo_recusado_nao_derruba_os_outros(tmp_path):
    servidor = Servidor(recusa_mbo={"ZNZ6", "GCZ6"}, recusa_negocios={"GCZ6"})
    sessao = _sessao(servidor, tmp_path)
    fechados = _rodar(sessao)
    zn, gc, cl = _por(sessao, "ZN"), _por(sessao, "GCZ6"), _por(sessao, "CL")
    assert (zn.mbo, zn.negocios, zn.estado) == ("recusada_5", "ok", "gravando")
    assert zn.quadros[150] == 5 and zn.quadros[160] == 0 and zn.retrato is None
    assert (gc.estado, gc.motivo) == ("excluido", "assinaturas_recusadas")
    assert cl.estado == "gravando" and cl.quadros[160] == 5
    assert all(catalogo.verificar_captura(a.gravador.pasta)["verificada"] for a, m, e in fechados)
    assert sessao.status()["logout_confirmado"]


def test_ativo_sem_dado_fecha_normal_so_com_o_retrato(tmp_path):
    sessao = _sessao(Servidor(sem_dado={"ZNZ6"}), tmp_path)
    fechados = _rodar(sessao)
    zn = _por(sessao, "ZN")
    assert zn.quadros == {116: 3} and zn.estado == "gravando"
    rel = catalogo.verificar_captura(zn.gravador.pasta)
    assert rel["verificada"] and rel["quadros"] == 3 and rel["fechamento_normal"] is True


def test_teto_do_ativo_fecha_so_ele(tmp_path):
    sessao = _sessao(Servidor(quadros=40), tmp_path, max_bytes_ativo=2500)
    _rodar(sessao)
    assert {a.estado for a in sessao.ativos} == {"teto_do_ativo"}
    assert sessao.status()["logout_confirmado"]  # logout limpo mesmo com todos no teto
    for a in sessao.ativos:
        rel = catalogo.verificar_captura(a.gravador.pasta)
        assert rel["verificada"] and rel["fechamento_normal"] is False


def test_teto_global_pede_logout_limpo(tmp_path):
    sessao = _sessao(Servidor(quadros=40), tmp_path, max_bytes_global=6000, duracao_s=30)
    _rodar(sessao)
    assert sessao.parada == "teto_global"
    assert sessao.status()["estado"] == "FECHADA" and sessao.status()["logout_confirmado"]


def test_disco_abaixo_da_reserva_pede_logout_limpo(tmp_path, monkeypatch):
    from coleta_multi import sessao_multi
    monkeypatch.setattr(sessao_multi, "CHECAR_DISCO_A_CADA", 1)
    monkeypatch.setattr(sessao_multi.disco, "livre_bytes", lambda _: 10)
    sessao = _sessao(Servidor(), tmp_path, reserva_disco_bytes=1000, duracao_s=30, min_livre_bytes=0)
    _rodar(sessao)
    assert sessao.parada == "disco_abaixo_da_reserva" and sessao.status()["logout_confirmado"]


# ---- falhas da sessao ------------------------------------------------------------------------------

def test_queda_no_meio_fecha_tudo_como_dia_interrompido(tmp_path):
    sessao = _sessao(Servidor(cair_apos=2), tmp_path, duracao_s=30)
    fechados = _rodar(sessao)
    st = sessao.status()
    assert (st["estado"], st["erro"], st["tipo_de_falha"]) == ("FALHOU", "conexao_perdida", "TRANSIENT_TRANSPORT")
    assert falha_de_login(sessao) is None
    assert fechados
    for a, manifesto, erro in fechados:
        rel = catalogo.verificar_captura(a.gravador.pasta)
        assert rel["verificada"] and rel["fechamento_normal"] is False


def test_login_recusado_nao_tenta_de_novo(tmp_path):
    servidor = Servidor(rp_login="1")
    sessao = _sessao(servidor, tmp_path)
    fechados = _rodar(sessao)
    assert falha_de_login(sessao) == "login_recusado" and servidor.logins == 1 and fechados == []
    assert not (tmp_path / "staging").exists() or not any((tmp_path / "staging").iterdir())


def test_logout_forcado(tmp_path):
    sessao = _sessao(Servidor(forcar_logout_apos=1), tmp_path, duracao_s=30)
    _rodar(sessao)
    assert falha_de_login(sessao) == "logout_forcado"


# ---- sonda ------------------------------------------------------------------------------------------

def _sonda(servidor, tmp_path, **kw):
    return asyncio.run(sonda.executar_sonda(ITENS, servidor.fabrica(), CRED,
                                            settings=Settings(data_root=tmp_path / "dados"),
                                            raiz=tmp_path / "sonda", duracao_s=0.3, intervalo_substituto_s=0.05,
                                            saida=lambda _: None, **kw))


def test_sonda_com_substituto_mede_e_verifica(tmp_path):
    servidor = Servidor()
    relatorio = _sonda(servidor, tmp_path, substituto=True)
    assert relatorio["resultado"] == "ok" and servidor.logins == 2
    assert relatorio["substituto_status"]["logout_confirmado"]
    assert all(v["verificada"] for v in relatorio["verificacoes"].values()) and len(relatorio["verificacoes"]) == 3
    assert SENHA not in json.dumps(relatorio, default=str)


def test_terceiro_login_recusado_para_sem_tentar_de_novo(tmp_path):
    servidor = Servidor(logins_aceitos=1)
    relatorio = _sonda(servidor, tmp_path, substituto=True)
    assert relatorio["resultado"] == "login_recusado" and servidor.logins == 2
    assert relatorio["substituto_status"]["logout_confirmado"]  # o substituto sai limpo


def test_substituto_recusado_nem_inicia_o_coletor(tmp_path):
    servidor = Servidor(rp_login="1")
    relatorio = _sonda(servidor, tmp_path, substituto=True)
    assert relatorio["resultado"] == "login_recusado" and servidor.logins == 1 and "status" not in relatorio


def test_sonda_confere_a_janela_de_novo_depois_da_senha(tmp_path, monkeypatch):
    cal = tmp_path / "cal.json"
    cal.write_text("{}")
    respostas = iter([None, "perto_da_janela_do_es_2026-10-30"])
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    monkeypatch.setattr(sonda, "conferir_janela", lambda *a: next(respostas))
    monkeypatch.setattr(sonda, "conferir_es", lambda *a: None)
    monkeypatch.setattr(sonda.disco, "livre_bytes", lambda _: 500 * GIB)
    monkeypatch.setattr(sonda, "guarda_propria", lambda: tmp_path / "guarda")
    monkeypatch.setattr(sonda.captura, "obter_credenciais", lambda: CRED)

    def proibida(_):
        raise AssertionError("nao pode conectar")
    monkeypatch.setattr(sonda.captura, "_criar_fabrica", proibida)
    assert sonda.main(["--online", "--agreements-accepted", "--calendario", str(cal)]) == 2


def test_janela_do_es_e_estado_do_agendador(tmp_path):
    cal = {"schema": "qhos2_calendario_v1", "fonte": "teste", "valido_de": "2026-10-30T04:00:00Z",
           "valido_ate": "2026-10-31T04:00:00Z",
           "janelas": [{"dia": "2026-10-30", "inicio": "2026-10-30T13:00:00Z", "fim": "2026-10-30T20:15:00Z"}]}
    t = lambda h, m: datetime(2026, 10, 30, h, m, tzinfo=timezone.utc)
    assert sonda.conferir_janela(cal, t(12, 0), 600) is None
    assert sonda.conferir_janela(cal, t(12, 35), 600) == "perto_da_janela_do_es_2026-10-30"
    assert sonda.conferir_janela(cal, t(15, 0), 600) is not None
    assert sonda.conferir_janela(cal, t(20, 20), 600) is not None
    assert sonda.conferir_janela(cal, t(20, 30), 600) is None
    agenda_estado, guarda = tmp_path / "a.json", tmp_path / "g.json"
    guarda.write_text('{"estado": "LIVRE"}')
    agenda_estado.write_text('{"estado": "CAPTURANDO"}')
    assert sonda.conferir_es(agenda_estado, guarda) == "agendador_em_CAPTURANDO"
    agenda_estado.write_text('{"estado": "AGUARDANDO"}')
    assert sonda.conferir_es(agenda_estado, guarda) is None
    guarda.write_text('{"estado": "QUARENTENA"}')
    assert sonda.conferir_es(agenda_estado, guarda) == "guarda_do_es_em_QUARENTENA"
