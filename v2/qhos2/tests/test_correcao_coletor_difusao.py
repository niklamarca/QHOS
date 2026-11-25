"""Campanha de correcao, lote l5b-coletor: difusao, camada "coletor" do percurso, ambiente declarado, saida da CLI e
diagnostico da biblioteca websockets (QHOS-AUD-101, 150, 151, 171, 172, 173).

Nenhuma porta real: `_difundir` recebe sockets falsos; `main` roda com `rodar` substituido. `xfail(strict=True)` =
defeito confirmado em codigo congelado; patch minimo em `09_correcao/EVIDENCIAS/<ID>/patch_proposto.diff`.
"""
import asyncio
import errno
import os
import sys
import types

import pytest

from qhos2 import coletor as co, leitura as le, sintetico as si

from test_integridade_heranca import gravar, identificar, leitor as leitor_es

TOKEN = "A" * 43
LIVRO_VAZIO = {"type": "book", "subscriptionId": "s", "revision": "1", "instrumentId": co.ESZ6["id"],
               "time": 1, "timeNs": "1000000", "ready": False, "sequenceVerified": False, "bids": [], "asks": []}


class SocketFalso:
    def __init__(self, erro=None):
        self.erro, self.enviadas = erro, []

    async def send(self, texto):
        if self.erro is not None:
            raise self.erro
        self.enviadas.append(texto)


def coletor_sem_captura():
    return co.Coletor(token=TOKEN, porta=0, avisar=lambda _: None)


# ---- QHOS-AUD-150: _difundir engole qualquer Exception e conta "enviado" antes de enviar -----------------------------

def test_caracterizacao_enviados_cresce_sem_nenhum_terminal_e_com_envio_falhando():
    c = coletor_sem_captura()
    asyncio.run(c._difundir([LIVRO_VAZIO]))                          # zero clientes
    assert c.enviados["book"] == 1
    c.clientes = {SocketFalso(TypeError("objeto nao serializavel"))}
    asyncio.run(c._difundir([LIVRO_VAZIO]))                          # envio falhando por motivo que nao e conexao
    assert c.enviados["book"] == 2
    assert not any("falh" in k for k in list(c.contagem) + list(c.enviados))   # nenhum contador de envio falho


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-150: `self.enviados[type] += 1` acontece antes do laco de envio e "
                                       "`except Exception: pass` esconde TypeError/ValueError de serializacao")
def test_envio_falho_e_contado_e_nao_conta_como_enviado():
    c = coletor_sem_captura()
    c.clientes = {SocketFalso(TypeError("objeto nao serializavel"))}
    asyncio.run(c._difundir([LIVRO_VAZIO]))
    falhos = c.contagem.get("envios_falhos", 0) + getattr(c, "enviados_falhos", 0)
    assert falhos >= 1 and c.enviados["book"] == 0


def test_caracterizacao_conexao_caindo_continua_silenciosa_por_desenho():
    """O `except Exception` cobre o caso esperado (terminal fechou): o laco continua para os outros clientes."""
    from websockets.exceptions import ConnectionClosedOK
    c = coletor_sem_captura()
    bom = SocketFalso()
    c.clientes = {SocketFalso(ConnectionClosedOK(None, None)), bom}
    asyncio.run(c._difundir([LIVRO_VAZIO]))
    assert len(bom.enviadas) == 1


# ---- QHOS-AUD-151: camada "coletor" do percurso e sempre ok --------------------------------------------------------

def test_caracterizacao_camada_coletor_e_ok_sem_terminal_e_com_envios_falhando():
    c = coletor_sem_captura()
    assert c.clientes == set()
    ident, estado, motivo, campos = c._camadas()[-1]
    assert (ident, estado, motivo) == ("coletor", "ok", "Enviando ao terminal")
    assert ("Terminais conectados", 0) in campos
    c.clientes = {SocketFalso(TypeError("x"))}
    for _ in range(10):
        asyncio.run(c._difundir([LIVRO_VAZIO]))
    assert c._camadas()[-1][1] == "ok"


def test_caracterizacao_conflito_docstring_x_testes_na_camada_coletor():
    """QHOS-AUD-151: o docstring do coletor diz 'nenhuma camada fica verde por falta de informacao', mas quatro testes de
    `tests/test_coletor.py` (percurso sem cliente) e `tests/test_terminal.py:109-110` (inferido pela leitura; o teste de
    Node nao roda no WSL) fixam a camada `coletor` como 'ok' sem terminal. So o autor resolve o conflito; fica caracterizado."""
    assert "nenhuma camada fica verde por falta de informacao" in co.__doc__
    c = coletor_sem_captura()
    assert c.clientes == set() and c._camadas()[-1][1:3] == ("ok", "Enviando ao terminal")


# ---- QHOS-AUD-173: livro com um lado vazio: ready:false enquanto o percurso diz "Livro ok Qualificado" ---------------

def _leitor_com_livro_so_de_compras(tmp_path):
    from qhos2 import rprotocol as p
    quadros = [(116, identificar(si.quadro_retrato("C", 100.0, [(1, 1, "c")]))),
               (116, si._int(p.F_TEMPLATE, 116) + si._txt(p.F_RP_CODE, "0"))]
    g = gravar(tmp_path, quadros)
    l = leitor_es(g.pasta)
    l.avancar()
    assert l.estado == le.ENCERRADA and l.livro_vale
    assert l.livro.niveis("C", 40) and not l.livro.niveis("V", 40)
    return l


def test_caracterizacao_livro_de_um_lado_contradiz_mensagem_e_percurso(tmp_path):
    l = _leitor_com_livro_so_de_compras(tmp_path)
    c = co.Coletor(token=TOKEN, porta=0, leitor=l, avisar=lambda _: None)
    c.traduzir([], alcancado=True)
    c._tempo_ns = si.INICIO_NS
    mensagem = co.mensagem_livro(l.livro, c._base, c._tempo_ns, l.livro_vale, c.tick)
    assert mensagem["ready"] is False and mensagem["bids"] == [] and mensagem["asks"] == []
    livro = next(camada for camada in c._camadas() if camada[0] == "livro")
    assert livro[1:3] == ("ok", "Qualificado")


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-173: `mensagem_livro` exige os dois lados para `ready`, mas a camada "
                                       "`livro` do percurso so olha `livro_vale`: tela contraditoria")
def test_livro_de_um_lado_nao_e_ok_no_percurso(tmp_path):
    l = _leitor_com_livro_so_de_compras(tmp_path)
    c = co.Coletor(token=TOKEN, porta=0, leitor=l, avisar=lambda _: None)
    c._tempo_ns = si.INICIO_NS
    assert co.mensagem_livro(l.livro, c._base, c._tempo_ns, l.livro_vale, c.tick)["ready"] is False
    livro = next(camada for camada in c._camadas() if camada[0] == "livro")
    assert livro[1] == "atencao"


# ---- QHOS-AUD-171: ambiente_declarado transforma OSError/OperacaoError em "unknown" sem aviso ------------------------

def _captura_com_manifesto_ilegivel(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root ignora chmod 000; o cenario precisa de usuario comum")
    pasta = si.gravar(si.gerar(3, 20), tmp_path)
    (pasta / "manifest.json").chmod(0o000)
    return pasta


def test_caracterizacao_manifesto_ilegivel_nao_gera_aviso_no_ambiente_declarado(tmp_path):
    pasta = _captura_com_manifesto_ilegivel(tmp_path)
    avisos = []
    try:
        c = co.Coletor(token=TOKEN, porta=0, avisar=avisos.append,
                       leitor=le.Leitor(pasta, tick=0.25, simbolo="SINTETICO", bolsa="QHOS2"))
        ambiente = c.ambiente_declarado()
    finally:
        (pasta / "manifest.json").chmod(0o600)
    # o sidecar de identidade ainda responde "synthetic": o manifesto ilegivel fica invisivel para o operador
    assert ambiente == ("synthetic", si.SISTEMA) and avisos == []


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-171: `ambiente_declarado` engole OSError/OperacaoError na leitura do "
                                       "manifesto sem `_avisar`; o operador nao distingue erro de leitura de ausencia")
def test_manifesto_ilegivel_avisa_o_operador(tmp_path):
    pasta = _captura_com_manifesto_ilegivel(tmp_path)
    avisos = []
    try:
        c = co.Coletor(token=TOKEN, porta=0, avisar=avisos.append,
                       leitor=le.Leitor(pasta, tick=0.25, simbolo="SINTETICO", bolsa="QHOS2"))
        c.ambiente_declarado()
    finally:
        (pasta / "manifest.json").chmod(0o600)
    assert any("manifesto_inacessivel" in a for a in avisos)


# ---- QHOS-AUD-172: qualquer OSError em asyncio.run(rodar()) e impresso como porta_indisponivel --------------------

def _main_com_rodar_que_lanca(tmp_path, monkeypatch, excecao, capsys):
    async def rodar(self, pronto=None):
        raise excecao
    monkeypatch.setattr(co.Coletor, "rodar", rodar)
    codigo = co.main(["--porta", "0", "--token", str(tmp_path / "t"), "--raiz", str(tmp_path)])
    return codigo, capsys.readouterr().out


def test_caracterizacao_qualquer_oserror_na_execucao_vira_porta_indisponivel(tmp_path, monkeypatch, capsys):
    codigo, saida = _main_com_rodar_que_lanca(tmp_path, monkeypatch, OSError(errno.EIO, "erro de leitura"), capsys)
    assert codigo == 2 and "PARE: porta_indisponivel (5)" in saida
    assert "erro de leitura" not in saida                            # a mensagem do SO nao vai para a tela (bom)


# estrito so no Linux: no Windows o token nao fica 0600, `main` para antes em `token_com_permissao_aberta` e o
# defeito nao e alcancado (passaria como XPASS(strict) sem relacao com a correcao)
@pytest.mark.xfail(sys.platform == "linux", strict=True,
                   reason="QHOS-AUD-172: `main` rotula todo OSError de `rodar()` como "
                          "`porta_indisponivel`, mesmo EIO/ENOSPC que nada tem a ver com o bind")
def test_oserror_que_nao_e_do_bind_nao_e_rotulado_porta_indisponivel(tmp_path, monkeypatch, capsys):
    codigo, saida = _main_com_rodar_que_lanca(tmp_path, monkeypatch, OSError(errno.EIO, "erro de leitura"), capsys)
    assert codigo == 2 and "PARE:" in saida and "porta_indisponivel" not in saida


# ---- QHOS-AUD-101: biblioteca_websockets_ausente tambem e usado para versao incompativel ---------------------------

def _sem_websockets_asyncio(monkeypatch):
    """websockets importavel (versao < 13: sem `websockets.asyncio.client`)."""
    antigo = types.ModuleType("websockets")
    antigo.__version__ = "10.4"
    monkeypatch.setitem(sys.modules, "websockets", antigo)
    for nome in ("websockets.asyncio", "websockets.asyncio.client"):
        monkeypatch.delitem(sys.modules, nome, raising=False)


def _codigo_da_fabrica(tmp_path):
    from qhos2 import conector, sessao
    with pytest.raises(sessao.SessaoError) as erro:
        asyncio.run(conector.fabrica_wss("wss://exemplo.invalid:443")())
    return erro.value.codigo


def test_caracterizacao_websockets_antigo_recebe_o_codigo_de_ausente(tmp_path, monkeypatch):
    _sem_websockets_asyncio(monkeypatch)
    assert _codigo_da_fabrica(tmp_path) == "biblioteca_websockets_ausente"


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-101: `except ImportError` em `fabrica_wss` usa um codigo so para "
                                       "'nao instalada' e 'instalada mas sem websockets.asyncio.client'")
def test_websockets_antigo_recebe_codigo_de_incompativel(tmp_path, monkeypatch):
    _sem_websockets_asyncio(monkeypatch)
    codigo = _codigo_da_fabrica(tmp_path)
    assert codigo.startswith("biblioteca_websockets") and codigo != "biblioteca_websockets_ausente"
