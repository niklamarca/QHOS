"""Peca 4b: a sessao do R|Protocol, do login ao logout, contra um servidor falso (sem rede, sem credenciais reais)."""
import asyncio
import struct
import time

import pytest

from qhos2 import rprotocol as p
from qhos2.config import Settings
from qhos2.sessao import (CANCELADA, LOCAL, LOGOUT_FORCADO, PROTOCOLO, REJEICAO, TRANSITORIA, ConexaoFechada,
                          ConfigSessao, Credenciais, Sessao, SessaoError)

SENHA = "SEGREDO-123"
CRED = Credenciais("trader@exemplo.com", SENHA)


# ---- mensagens do servidor falso ------------------------------------------------------------------

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


def resp_info(*sistemas, rp="0"):
    return _msg(17, _rp(rp), *[_txt(153628, s) for s in sistemas])


def resp_login(intervalo=0.2, rp="0"):
    return _msg(11, _rp(rp), _dbl(153633, intervalo))


def resp_logout():
    return _msg(13, _rp())


def resp_ref(simbolo, bolsa):
    return _msg(15, _rp(), _txt(110100, simbolo), _txt(110101, bolsa), _txt(100067, "20261218"),
                _dbl(154386, 0.25))


def resp_assinatura(rp="0"):
    return _msg(118, _rp(rp))


def resp_negocios(rp="0"):
    return _msg(101, _rp(rp))


def quadro150(i, snapshot=False):
    return _msg(150, _txt(110100, "ESZ6"), _txt(110101, "CME"), _int(110121, int(snapshot)), _dbl(100006, 5000.25),
                _int(100178, 1 + i), _int(112003, 1 + i % 2), _int(150100, 1_760_000_000 + i))


def linha_retrato(token, i):
    return _msg(116, _txt(132760, token), _txt(110100, "ESZ6"), _int(154406, 3 + i), _dbl(154405, 5000.25))


def final_retrato(token, rp="0"):
    return _msg(116, _txt(132760, token), _rp(rp))


def quadro160(i):
    return _msg(160, _txt(110100, "ESZ6"), _txt(110101, "CME"), _int(150100, 1_760_000_000 + i))


def resp_hb(ns):
    return _msg(19, _rp(), _int(150100, ns // 10 ** 9), _int(150101, (ns % 10 ** 9) // 1000))


FORCADO = _msg(77)
REJEITADO = _msg(75, _rp("9"))
FECHAR = None


class ConexaoFalsa:
    def __init__(self, servidor):
        self.servidor, self.fila, self.enviados, self.fechada = servidor, asyncio.Queue(), [], False

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
        return [dict((n, v) for n, _, v in p._campos_brutos(d))[154467] for d in self.enviados]


class Servidor:
    def __init__(self, *, sistemas=("Rithmic Paper Trading",), rp_login="0", intervalo=0.2, simbolo="ESZ6", bolsa="CME",
                 rp_assinatura="0", linhas=3, durante_retrato=(), retrato_final=True, token_errado=False,
                 apos_assinar=(), apos_retrato=(), responde_heartbeat=True, desvio_ns=0, resposta_info=None,
                 resposta_logout=None, rp_cancelamento="0", rp_retrato="0", atraso_hb_s=0.0, hb_roteiro=None,
                 rp_negocios="0", rp_negocios_cancelamento="0", apos_negocios=()):
        self.__dict__.update(locals())
        self.conexoes, self.mercado, self.hb_n = [], [], 0

    def fabrica(self):
        async def abrir():
            con = ConexaoFalsa(self)
            self.conexoes.append(con)
            return con
        return abrir

    def _dar(self, con, dados, mercado=False):
        con.fila.put_nowait(dados)
        if mercado and dados is not FECHAR:
            self.mercado.append(dados)

    def reagir(self, con, dados):
        campos = {n: v for n, _, v in p._campos_brutos(dados)}
        template = campos[154467]
        if template == 16:
            self._dar(con, self.resposta_info or resp_info(*self.sistemas))
        elif template == 10:
            self._dar(con, resp_login(self.intervalo, self.rp_login))
        elif template == 14:
            self._dar(con, resp_ref(self.simbolo, self.bolsa))
        elif template == 117 and campos[100000] == 1:
            self._dar(con, resp_assinatura(self.rp_assinatura))
            for item in self.apos_assinar:
                self._dar(con, item, mercado=item not in (FORCADO, REJEITADO, FECHAR))
        elif template == 117:
            self._dar(con, resp_assinatura(self.rp_cancelamento))
        elif template == 100 and campos[100000] == 1:
            self._dar(con, resp_negocios(self.rp_negocios))
            for item in self.apos_negocios:
                self._dar(con, item, mercado=item not in (FORCADO, REJEITADO, FECHAR))
        elif template == 100:
            self._dar(con, resp_negocios(self.rp_negocios_cancelamento))
        elif template == 115:
            token = "outro" if self.token_errado else campos[132760].decode()
            for i in range(self.linhas):
                self._dar(con, linha_retrato(token, i), mercado=True)
                if i == 0:
                    for extra in self.durante_retrato:
                        self._dar(con, extra, mercado=True)
            if self.retrato_final:
                self._dar(con, final_retrato(token, self.rp_retrato), mercado=True)
                for extra in self.apos_retrato:
                    self._dar(con, extra, mercado=True)
        elif template == 18:
            if self.responde_heartbeat:
                # por padrao o carimbo do servidor cai no ponto medio do tempo de ida e volta simulado;
                # com `hb_roteiro` cada heartbeat tem (atraso, deslocamento do carimbo) proprios
                if self.hb_roteiro:
                    atraso, deslocamento = self.hb_roteiro[min(self.hb_n, len(self.hb_roteiro) - 1)]
                else:
                    atraso, deslocamento = self.atraso_hb_s, self.atraso_hb_s / 2
                self.hb_n += 1
                resposta = resp_hb(time.time_ns() + self.desvio_ns + int(deslocamento * 1e9))
                if atraso:
                    asyncio.get_running_loop().call_later(atraso, con.fila.put_nowait, resposta)
                else:
                    self._dar(con, resposta)
        elif template == 12:
            self._dar(con, self.resposta_logout or resp_logout())


class Coletor:
    def __init__(self, falhar=False):
        self.itens, self.falhar = [], falhar

    def __call__(self, dados, template, recebido_ns):
        if self.falhar:
            raise RuntimeError("disco cheio")
        self.itens.append((dados, template, recebido_ns))


@pytest.fixture
def ajustes(tmp_path):
    return Settings(data_root=tmp_path / "dados")


def executar(servidor, ajustes, *, coletor=None, credenciais=CRED, **config):
    coletor = coletor if coletor is not None else Coletor()
    padrao = dict(template_version="5.42", symbol="ESZ6", exchange="CME", duracao_s=0.45,
                  resposta_timeout_s=1.0, retrato_timeout_s=1.0, settings=ajustes)
    padrao.update(config)

    async def cena():
        sessao = Sessao(ConfigSessao(**padrao), servidor.fabrica(), ao_receber=coletor)
        try:
            return sessao, await sessao.executar(credenciais), None
        except SessaoError as erro:
            return sessao, None, erro
    sessao, status, erro = asyncio.run(cena())
    return sessao, status, erro, coletor


async def ate(condicao, limite_s=5.0):
    """Espera uma condicao observavel da sessao (status()) em vez de dormir um tempo fixo (QHOS-AUD-085)."""
    prazo = asyncio.get_running_loop().time() + limite_s
    while not condicao():
        assert asyncio.get_running_loop().time() < prazo, "condicao nao atingida no prazo"
        await asyncio.sleep(0.005)


def falha(servidor, ajustes, codigo, tipo, **kw):
    sessao, status, erro, coletor = executar(servidor, ajustes, **kw)
    assert status is None and erro is not None, "a sessao deveria ter falhado"
    assert (erro.codigo, erro.tipo) == (codigo, tipo)
    estado = sessao.status()
    assert (estado["estado"], estado["erro"], estado["tipo_de_falha"]) == ("FALHOU", codigo, tipo)
    assert all(con.fechada for con in servidor.conexoes)
    return sessao, coletor


# ---- fluxo completo ---------------------------------------------------------------------------------

def test_fluxo_completo(ajustes):
    servidor = Servidor(durante_retrato=(quadro160(1), quadro160(2)), apos_assinar=tuple(quadro160(i) for i in range(10, 15)))
    sessao, status, erro, coletor = executar(servidor, ajustes)
    assert erro is None
    assert status["estado"] == "FECHADA" and status["erro"] is None
    assert all(status[k] for k in ("login_confirmado", "assinatura_confirmada", "retrato_completo",
                                   "cancelamento_confirmado", "logout_confirmado"))
    assert status["retrato_quadros"] == 4
    assert status["quadros"] == {"116": 4, "160": 7}
    assert status["periodo_heartbeat_s"] == 0.1 and status["heartbeats_confirmados"] >= 3
    assert status["referencia"]["expiration_date"] == "20261218" and status["referencia"]["symbol"] == "ESZ6"
    assert status["anomalias"] == {}
    # tudo o que o servidor mandou como mercado chegou ao gravador, na mesma ordem e com relogio crescente
    assert [d for d, _, _ in coletor.itens] == servidor.mercado
    horas = [ns for _, _, ns in coletor.itens]
    assert horas == sorted(horas) and all(isinstance(ns, int) for ns in horas)
    assert status["bytes"] == sum(len(d) for d in servidor.mercado)
    # pedidos, em ordem, nas duas conexoes
    descoberta, autenticada = servidor.conexoes
    assert descoberta.templates() == [16]
    modelos = autenticada.templates()
    assert modelos[:4] == [10, 14, 117, 115] and modelos[-2:] == [117, 12]
    assert set(modelos[4:-2]) == {18} and len(modelos[4:-2]) >= 3
    assert descoberta.fechada and autenticada.fechada


def test_status_nunca_traz_a_senha(ajustes):
    servidor = Servidor(rp_login="1")
    sessao, coletor = falha(servidor, ajustes, "resposta_recusada_11_codigo_1", PROTOCOLO)
    assert SENHA not in str(sessao.status()) and SENHA not in repr(CRED) and "trader@exemplo.com" not in repr(CRED)
    assert SENHA.encode() in servidor.conexoes[1].enviados[0]  # so o pedido de login leva a senha


# ---- relogio ----------------------------------------------------------------------------------------

def test_desvio_de_relogio_e_medido(ajustes):
    _, status, _, _ = executar(Servidor(desvio_ns=500_000_000), ajustes)
    assert 450 < status["relogio"]["desvio_ms"] < 550 and status["relogio"]["amostras"] >= 3
    assert status["relogio_alerta"] is False


def test_desvio_usa_o_ponto_medio_do_tempo_de_ida_e_volta(ajustes):
    _, status, _, _ = executar(Servidor(atraso_hb_s=0.12), ajustes, duracao_s=0.7)
    assert abs(status["relogio"]["desvio_ms"]) < 25 and 100 < status["relogio"]["rtt_ms"] < 200


def test_desvio_vem_da_amostra_de_menor_rtt(ajustes):
    # 1o heartbeat rapido e exato; os seguintes lentos e com carimbo assimetrico (erro de ~ -75 ms)
    servidor = Servidor(hb_roteiro=[(0.0, 0.0), (0.15, 0.0)])
    _, status, _, _ = executar(servidor, ajustes, duracao_s=0.9)
    assert status["relogio"]["amostras"] >= 3
    assert abs(status["relogio"]["desvio_ms"]) < 25 and status["relogio"]["rtt_ms"] < 50


def test_desvio_grande_num_heartbeat_aciona_o_alerta_pela_sessao(ajustes):
    # pelo servidor falso e pelo status(), sem escrever _hb_pendente nem chamar _heartbeat_recebido (QHOS-AUD-070):
    # 1o heartbeat exato; os seguintes com o carimbo 3 s a frente e o mesmo rtt -> alerta, ultimo desvio ~ +3000 ms
    servidor = Servidor(hb_roteiro=[(0.0, 0.0), (0.0, 3.0)])
    _, status, erro, _ = executar(servidor, ajustes)
    assert erro is None and status["relogio_alerta"] is True and status["relogio"]["amostras"] >= 2
    assert 2900 < status["relogio"]["ultimo_desvio_ms"] < 3100 and status["anomalias"] == {}


def test_periodo_de_heartbeat_respeita_o_teto_das_configuracoes(tmp_path):
    ajustes = Settings(data_root=tmp_path / "dados", heartbeat_seconds=1)
    _, status, _, _ = executar(Servidor(intervalo=60.0), ajustes, duracao_s=0.2)
    assert status["periodo_heartbeat_s"] == 1.0


def test_desvio_de_relogio_acima_de_1_s_gera_alerta(ajustes):
    _, status, _, _ = executar(Servidor(desvio_ns=3_000_000_000), ajustes)
    assert status["relogio_alerta"] is True and status["relogio"]["desvio_ms"] > 2900


def test_heartbeat_sem_carimbo_e_anomalia(ajustes):
    class SemCarimbo(Servidor):
        def reagir(self, con, dados):
            if dict((n, v) for n, _, v in p._campos_brutos(dados))[154467] == 18:
                self._dar(con, _msg(19, _rp()))
            else:
                super().reagir(con, dados)
    _, status, _, _ = executar(SemCarimbo(), ajustes)
    assert status["relogio"] is None and status["anomalias"]["heartbeat_sem_carimbo"] >= 3


def test_heartbeat_nao_solicitado_e_anomalia(ajustes):
    servidor = Servidor(apos_assinar=(resp_hb(time.time_ns()),))
    _, status, _, _ = executar(servidor, ajustes)
    assert status["anomalias"]["heartbeat_nao_solicitado"] == 1


def test_heartbeat_tardio_chega_durante_o_logout(ajustes):
    # a resposta do heartbeat so e entregue junto com a do logout: chega em SAINDO, nao em STREAMING (QHOS-AUD-119);
    # por construcao, nao por sorte de tempo
    class RespostaTardia(Servidor):
        def __init__(self):
            super().__init__()
            self.guardadas = []

        def reagir(self, con, dados):
            template = dict((n, v) for n, _, v in p._campos_brutos(dados))[154467]
            if template == 18:
                self.guardadas.append(resp_hb(time.time_ns()))
            else:
                if template == 12:
                    for resposta in self.guardadas:
                        self._dar(con, resposta)
                    self.guardadas = []
                super().reagir(con, dados)
    servidor = RespostaTardia()
    _, status, erro, _ = executar(servidor, ajustes)
    assert erro is None and status["estado"] == "FECHADA"
    assert status["cancelamento_confirmado"] and status["logout_confirmado"]
    assert status["heartbeats_confirmados"] == 1 and status["relogio"]["amostras"] == 1 and status["anomalias"] == {}
    assert servidor.conexoes[1].templates()[-2:] == [117, 12] and not servidor.guardadas


# ---- falhas de protocolo -----------------------------------------------------------------------------

def test_sistema_nao_anunciado(ajustes):
    servidor = Servidor(sistemas=("Rithmic Test",))
    falha(servidor, ajustes, "sistema_nao_anunciado", PROTOCOLO)
    assert len(servidor.conexoes) == 1  # nao chegou a abrir a conexao autenticada


def test_descoberta_recusada(ajustes):
    falha(Servidor(resposta_info=resp_info("Rithmic Paper Trading", rp="3")), ajustes,
          "resposta_recusada_17_codigo_3", PROTOCOLO)


def test_login_recusado(ajustes):
    falha(Servidor(rp_login="1"), ajustes, "resposta_recusada_11_codigo_1", PROTOCOLO)


def test_intervalo_de_heartbeat_invalido_no_login(ajustes):
    falha(Servidor(intervalo=0.01), ajustes, "intervalo_de_heartbeat_invalido", PROTOCOLO)


def test_referencia_de_outro_instrumento(ajustes):
    falha(Servidor(simbolo="GCZ6", bolsa="COMEX"), ajustes, "referencia_de_outro_instrumento", PROTOCOLO)


def test_assinatura_recusada(ajustes):
    falha(Servidor(rp_assinatura="5"), ajustes, "resposta_recusada_118_codigo_5", PROTOCOLO)


def test_retrato_final_recusado(ajustes):
    falha(Servidor(rp_retrato="5"), ajustes, "resposta_recusada_116_codigo_5", PROTOCOLO)


def test_cancelamento_recusado(ajustes):
    falha(Servidor(rp_cancelamento="4"), ajustes, "resposta_recusada_118_codigo_4", PROTOCOLO)


def test_logout_recusado(ajustes):
    falha(Servidor(resposta_logout=_msg(13, _rp("6"))), ajustes, "resposta_recusada_13_codigo_6", PROTOCOLO)


def test_resposta_inesperada_para_o_estado(ajustes):
    falha(Servidor(resposta_info=resp_logout()), ajustes, "resposta_inesperada_para_o_estado", PROTOCOLO)


def test_quadro_corrompido_derruba_a_sessao(ajustes):
    falha(Servidor(apos_assinar=(b"\x80",)), ajustes, "varint_invalido", PROTOCOLO)


def test_template_desconhecido_derruba_a_sessao(ajustes):
    falha(Servidor(apos_assinar=(_msg(312),)), ajustes, "template_de_entrada_inesperado", PROTOCOLO)


def test_rejeicao_do_servidor(ajustes):
    sessao, coletor = falha(Servidor(apos_assinar=(REJEITADO,)), ajustes, "rejeicao_do_servidor", REJEICAO)


def test_logout_forcado_nao_tenta_logout(ajustes):
    servidor = Servidor(apos_assinar=(quadro160(1), quadro160(2), FORCADO))
    sessao, coletor = falha(servidor, ajustes, "logout_forcado", LOGOUT_FORCADO)
    assert 12 not in servidor.conexoes[1].templates() and len(coletor.itens) == 2  # o retrato nem chegou a ser lido


# ---- retrato ----------------------------------------------------------------------------------------

def test_retrato_sem_resposta_final(ajustes):
    falha(Servidor(retrato_final=False), ajustes, "retrato_sem_resposta", TRANSITORIA, retrato_timeout_s=0.2)


def test_correlacao_divergente_do_retrato_aborta_antes_de_gravar(ajustes):
    sessao, _, erro, coletor = executar(Servidor(token_errado=True), ajustes)
    assert erro.codigo == "retrato_correlacao_divergente"
    assert sessao.status()["anomalias"]["retrato_correlacao_divergente"] == 1
    assert not coletor.itens


def test_retrato_fora_de_hora_e_recusado_antes_de_gravar(ajustes):
    extra = linha_retrato("x", 0)
    servidor = Servidor(apos_retrato=(extra,))
    sessao, _, erro, coletor = executar(servidor, ajustes)
    assert erro.codigo == "retrato_fora_de_hora"
    assert sessao.status()["anomalias"]["retrato_fora_de_hora"] == 1
    assert extra not in [d for d, _, _ in coletor.itens]


# ---- transporte e gravador ---------------------------------------------------------------------------

def test_heartbeat_sem_resposta(ajustes):
    falha(Servidor(responde_heartbeat=False), ajustes, "heartbeat_sem_resposta", TRANSITORIA,
          duracao_s=2.0, resposta_timeout_s=0.15)


def test_conexao_cai_durante_a_transmissao(ajustes):
    falha(Servidor(apos_assinar=(quadro160(1), FECHAR)), ajustes, "conexao_perdida", TRANSITORIA)


def test_falha_do_gravador_derruba_a_sessao(ajustes):
    falha(Servidor(), ajustes, "gravacao_falhou", LOCAL, coletor=Coletor(falhar=True))


def test_cancelamento_fecha_as_conexoes(ajustes):
    servidor = Servidor()

    async def cena():
        sessao = Sessao(ConfigSessao(template_version="5.42", symbol="ESZ6", exchange="CME", duracao_s=5.0,
                                     settings=ajustes), servidor.fabrica(), ao_receber=Coletor())
        tarefa = asyncio.create_task(sessao.executar(CRED))
        await ate(lambda: sessao.status()["retrato_completo"])      # cancelada em STREAMING, com as duas conexoes abertas
        tarefa.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarefa
        return sessao
    sessao = asyncio.run(cena())
    assert sessao.status()["estado"] == "FALHOU" and sessao.status()["tipo_de_falha"] == CANCELADA
    assert all(con.fechada for con in servidor.conexoes)


# ---- encerramento e uso ------------------------------------------------------------------------------

def test_parar_encerra_de_forma_graciosa(ajustes):
    servidor = Servidor()

    async def cena():
        sessao = Sessao(ConfigSessao(template_version="5.42", symbol="ESZ6", exchange="CME", settings=ajustes,
                                     resposta_timeout_s=1.0), servidor.fabrica(), ao_receber=Coletor())
        tarefa = asyncio.create_task(sessao.executar(CRED))
        await ate(lambda: sessao.status()["retrato_completo"])      # parada pedida em STREAMING, nao por tempo
        sessao.parar()
        return await tarefa
    status = asyncio.run(cena())
    assert status["estado"] == "FECHADA" and status["cancelamento_confirmado"] and status["logout_confirmado"]
    assert servidor.conexoes[1].templates()[-2:] == [117, 12]


def test_sessao_nao_e_reutilizavel(ajustes):
    servidor = Servidor()

    async def cena():
        sessao = Sessao(ConfigSessao(template_version="5.42", symbol="ESZ6", exchange="CME", duracao_s=0.2,
                                     settings=ajustes), servidor.fabrica(), ao_receber=Coletor())
        await sessao.executar(CRED)
        with pytest.raises(SessaoError) as erro:
            await sessao.executar(CRED)
        return erro.value
    erro = asyncio.run(cena())
    assert (erro.codigo, erro.tipo) == ("sessao_nao_reutilizavel", LOCAL)


@pytest.mark.parametrize("credenciais", [Credenciais("", "x"), Credenciais("x", "")])
def test_credenciais_ausentes_nao_abrem_conexao(ajustes, credenciais):
    servidor = Servidor()
    _, status, erro, _ = executar(servidor, ajustes, credenciais=credenciais)
    assert status is None and erro.codigo == "credenciais_ausentes" and servidor.conexoes == []


@pytest.mark.parametrize("campos", [
    {"template_version": ""}, {"template_version": "x" * 33}, {"symbol": "ES;Z6"}, {"exchange": ""},
    {"duracao_s": 0}, {"duracao_s": -1.0}, {"duracao_s": float("inf")}, {"duracao_s": 8 * 86400},
    {"resposta_timeout_s": 0}, {"resposta_timeout_s": 601}, {"retrato_timeout_s": float("nan")},
])
def test_configuracao_invalida(ajustes, campos):
    base = dict(template_version="5.42", symbol="ESZ6", exchange="CME", settings=ajustes)
    base.update(campos)
    with pytest.raises((ValueError, p.ProtocolError)):
        ConfigSessao(**base)
