"""Campanha de correcao (lote l5a-captura): sessao do R|Protocol contra o servidor falso de `test_sessao`.

IDs: QHOS-AUD-077, 040, 103, 053, 079, 129, 130, 131.
`src/qhos2` congelado (J0): defeitos confirmados em `xfail(strict=True)`, patch proposto em
`09_correcao/EVIDENCIAS/<ID>/patch_proposto.diff`. Credenciais ficticias; nada conecta.
"""
import ast
import asyncio
import inspect
import json
import sys
from datetime import date, timedelta

import pytest

from qhos2 import captura, catalogo
from qhos2 import rprotocol as p
from qhos2 import sessao as modulo_sessao
from qhos2.config import Settings
from qhos2.operacao import GuardaCaptura
from qhos2.sessao import LOCAL, ConfigSessao, Credenciais, Sessao

from test_sessao import (CRED, SENHA, ConexaoFalsa, Servidor, _dbl, _msg, _rp, _txt, executar, falha,  # noqa: E402
                         quadro160, resp_assinatura)


@pytest.fixture
def ajustes(tmp_path):
    return Settings(data_root=tmp_path / "dados")


def _templates(dados):
    return {n: v for n, _, v in p._campos_brutos(dados)}


def servidor_com_conexao(classe_conexao, **kw):
    """Servidor falso cuja fabrica entrega uma subclasse de ConexaoFalsa (para quebrar enviar/fechar)."""
    class ServidorEspecial(Servidor):
        def fabrica(self):
            async def abrir():
                con = classe_conexao(self)
                self.conexoes.append(con)
                return con
            return abrir
    return ServidorEspecial(**kw)


def servidor_com_referencia(*campos_extra):
    """Servidor falso que responde a referencia (15) com os campos dados em vez dos padrao."""
    class Referencia(Servidor):
        def reagir(self, con, dados):
            if _templates(dados)[154467] == 14:
                self._dar(con, _msg(15, _rp(), _txt(110100, "ESZ6"), _txt(110101, "CME"), *campos_extra))
            else:
                super().reagir(con, dados)
    return Referencia()


# ==== QHOS-AUD-077 ====
# Quadros 160 que chegam durante o cancelamento (antes da resposta 118): _esperar aceita transito, sem prova.

def test_quadros_durante_o_cancelamento_sao_gravados(ajustes):
    extra = quadro160(99)

    class CancelamentoComQuadro(Servidor):
        def reagir(self, con, dados):
            campos = _templates(dados)
            if campos[154467] == 117 and campos[100000] != 1:   # pedido de cancelamento da assinatura
                self._dar(con, extra, mercado=True)              # o fio ainda empurra mercado...
                self._dar(con, resp_assinatura(self.rp_cancelamento))  # ...e so depois confirma
            else:
                super().reagir(con, dados)
    servidor = CancelamentoComQuadro(apos_assinar=(quadro160(1),))
    sessao, status, erro, coletor = executar(servidor, ajustes)
    assert erro is None and status["estado"] == "FECHADA" and status["cancelamento_confirmado"]
    gravados = [d for d, _, _ in coletor.itens]
    assert extra in gravados and gravados == servidor.mercado
    assert status["quadros"]["160"] == 2 and status["bytes"] == sum(len(d) for d in servidor.mercado)


# ==== QHOS-AUD-040 ====
# Sessao.executar:162-164 traduz excecoes inesperadas com `from None`: a causa some do traceback.

class ConexaoQueExplode(ConexaoFalsa):
    async def enviar(self, dados):
        raise AttributeError("boom (bug sintetico no transporte)")


def test_bug_inesperado_vira_falha_local_e_fecha_as_conexoes(ajustes):
    servidor = servidor_com_conexao(ConexaoQueExplode)
    falha(servidor, ajustes, "AttributeError", LOCAL)
    assert len(servidor.conexoes) == 1


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-040: Sessao.executar:162-164 relanca SessaoError(type(erro).__name__) "
                                        "com 'from None'; __cause__ fica None e o bug e irreproduzivel depois")
def test_bug_inesperado_preserva_a_causa_sem_vazar_segredo(ajustes):
    servidor = servidor_com_conexao(ConexaoQueExplode)
    sessao, status, erro, _ = executar(servidor, ajustes)
    assert (erro.codigo, erro.tipo) == ("AttributeError", LOCAL)
    assert isinstance(erro.__cause__, AttributeError), erro.__cause__
    assert SENHA not in repr(erro.__cause__) and SENHA not in str(erro.__cause__.args)


# ==== QHOS-AUD-103 ====
# sessao.py:204-214 aceita double nao finito na referencia; o manifesto so falha no fechamento (json allow_nan=False).

def config_captura(ajustes):
    return ConfigSessao(template_version="5.42", symbol="ESZ6", exchange="CME", duracao_s=0.3,
                        resposta_timeout_s=1.0, retrato_timeout_s=1.0, settings=ajustes)


@pytest.mark.skipif(sys.platform != "linux", reason="GuardaCaptura exige Linux (guarda_requer_linux)")
@pytest.mark.xfail(strict=True, reason="QHOS-AUD-103: Sessao._ler_referencia:212-214 copia min_qprice_change/"
                                        "single_point_value sem validar; NaN chega a gravador.fechar e derruba a escrita "
                                        "do manifesto (json allow_nan=False) no fim do dia")
def test_referencia_com_double_nao_finito_falha_cedo_e_manifesto_e_gravado(ajustes, tmp_path):
    servidor = servidor_com_referencia(_txt(100067, "20261218"), _dbl(154386, 0.25), _dbl(154389, float("nan")))
    r = asyncio.run(captura.capturar(config_captura(ajustes), servidor.fabrica(), tmp_path / "capturas", CRED))
    assert r["erro"] == "referencia_invalida" and r["status"]["estado"] == "FALHOU"
    assert r["status"]["assinatura_confirmada"] is False  # falhou antes de assinar
    assert catalogo.verificar_captura(r["pasta"])["verificada"] is True
    assert all(con.fechada for con in servidor.conexoes)


# ==== QHOS-AUD-053 ====
# Sessao._fechar:397-402 engole Exception do transporte sem contar anomalia.

class ConexaoQueNaoFecha(ConexaoFalsa):
    async def fechar(self):
        raise OSError("close do transporte falhou (sintetico)")


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-053: Sessao._fechar:399-402 engole a falha de con.fechar(); a sessao "
                                        "fica FECHADA com anomalias == {} e nada vai ao manifesto")
def test_falha_ao_fechar_transporte_vira_anomalia(ajustes):
    servidor = servidor_com_conexao(ConexaoQueNaoFecha)
    sessao, status, erro, _ = executar(servidor, ajustes)
    assert erro is None and status["estado"] == "FECHADA" and status["logout_confirmado"]
    assert status["anomalias"].get("fechamento_transporte_falhou") == 2  # descoberta e autenticada


def test_falha_ao_fechar_transporte_nao_derruba_a_sessao(ajustes):
    servidor = servidor_com_conexao(ConexaoQueNaoFecha)
    sessao, status, erro, _ = executar(servidor, ajustes)
    assert erro is None and status["estado"] == "FECHADA" and status["logout_confirmado"]


# ==== QHOS-AUD-079 ====
# Referencia com expiration_date no passado nao e recusada nem anotada.

@pytest.mark.xfail(strict=True, reason="QHOS-AUD-079: Sessao._ler_referencia:204-214 aceita expiration_date no "
                                        "passado sem recusar nem anotar anomalia; captura de instrumento vencido sem alerta")
def test_referencia_vencida_e_recusada_ou_anotada(ajustes):
    servidor = servidor_com_referencia(_txt(100067, "20200101"), _dbl(154386, 0.25))
    sessao, status, erro, _ = executar(servidor, ajustes)
    estado = sessao.status()
    assert estado["referencia"]["expiration_date"] == "20200101"
    assert erro is not None or estado["anomalias"].get("referencia_vencida"), estado["anomalias"]


# ==== QHOS-AUD-129 ====
# Credenciais("", "") e construivel; o invariante "nao vazio" vive em tres chamadores. Caracterizacao dos tres.

@pytest.mark.skipif(sys.platform != "linux", reason="GuardaCaptura exige Linux (guarda_requer_linux)")
def test_credenciais_vazias_sao_construiveis_mas_nenhuma_entrada_as_aceita(ajustes, tmp_path, monkeypatch, capsys):
    from qhos2 import agenda
    vazias = Credenciais("", "")
    assert "''" not in repr(vazias)  # repr=False: nem o vazio aparece
    servidor = Servidor()
    _, status, erro, _ = executar(servidor, ajustes, credenciais=vazias)
    assert status is None and erro.codigo == "credenciais_ausentes" and servidor.conexoes == []  # sessao.py:143
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    monkeypatch.setattr(captura, "_criar_fabrica", lambda endpoint: servidor.fabrica())
    assert captura.main(["--symbol", "ESZ6", "--exchange", "CME", "--online", "--agreements-accepted"],
                        entrada=lambda _: "", senha=lambda _: "") == 2                            # captura.py:209
    cal = tmp_path / "cal.json"
    cal.write_text(json.dumps(agenda.gerar_calendario(date.today(), date.today() + timedelta(days=3), "CME")))
    assert agenda.main(["rodar", "--calendario", str(cal), "--symbol", "ESZ6", "--exchange", "CME", "--online",
                        "--agreements-accepted"], entrada=lambda _: "", senha=lambda _: "") == 2  # agenda.py:326
    saida = capsys.readouterr().out
    assert saida.count("usuario e senha sao obrigatorios") == 2 and servidor.conexoes == []
    with GuardaCaptura(captura.estado_padrao()) as guarda:
        assert guarda.estado["estado"] == "LIVRE"  # nada foi iniciado


# ==== QHOS-AUD-130 ====
# ConfigSessao so valida app_version/template_version no login: um NUL vira falha PROTOCOL, nao de configuracao.

@pytest.mark.xfail(strict=True, reason="QHOS-AUD-130: ConfigSessao.__post_init__:71-83 nao passa app_version/"
                                        "template_version por _texto_valido; o NUL so e recusado no login, como PROTOCOL")
@pytest.mark.parametrize("campo", ["app_version", "template_version"])
def test_versao_com_nul_e_recusada_na_configuracao(ajustes, campo):
    with pytest.raises((ValueError, p.ProtocolError)):
        ConfigSessao(**{"template_version": "5.42", "symbol": "ESZ6", "exchange": "CME", "settings": ajustes,
                        campo: "a\x00b"})


# ==== QHOS-AUD-131 ====
# Sessao: 13 estados como strings; status() e um dict livre. Caracterizacao: status() e copia e o conjunto e fechado.

ESTADOS_DA_SESSAO = {"NOVA", "DESCOBRINDO", "CONECTANDO", "AUTENTICADA", "REFERENCIA", "ASSINANDO", "RETRATO",
                     "NEGOCIOS", "STREAMING", "CANCELANDO", "SAINDO", "FECHADA", "FALHOU"}


def _atribuicoes_de_estado(arvore):
    literais = set()
    for no in ast.walk(arvore):
        if not isinstance(no, ast.Assign):
            continue
        for alvo in no.targets:
            alvos = alvo.elts if isinstance(alvo, ast.Tuple) else [alvo]
            valores = no.value.elts if isinstance(no.value, ast.Tuple) else [no.value]
            for a, v in zip(alvos, valores):
                if isinstance(a, ast.Attribute) and a.attr == "_estado" and isinstance(v, ast.Constant):
                    literais.add(v.value)
    return literais


def test_status_e_copia_e_o_conjunto_de_estados_da_sessao_e_fechado(ajustes):
    s = Sessao(ConfigSessao("5.42", "ESZ6", "CME", settings=ajustes), None, ao_receber=lambda *_: None)
    visao = s.status()
    visao["estado"], visao["geracao_conexao"] = "INVENTADO", "x"      # captura.py:86 muta o dict devolvido...
    assert s.status()["estado"] == "NOVA" and "geracao_conexao" not in s.status()  # ...sem tocar a sessao
    assert _atribuicoes_de_estado(ast.parse(inspect.getsource(modulo_sessao))) == ESTADOS_DA_SESSAO
