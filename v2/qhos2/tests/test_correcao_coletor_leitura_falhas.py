"""Campanha de correcao, lote l5b-coletor: caminhos de falha da leitura vistos pelo coletor
(QHOS-AUD-155, 166, 167, 175, 189).

Capturas sinteticas em `tmp_path`, relogio injetado, nenhuma porta. `xfail(strict=True)` = defeito confirmado em
codigo congelado (`src/qhos2/coletor.py`, `src/qhos2/leitura.py`); patch minimo em `EVIDENCIAS/<ID>/`.
"""
import asyncio
import errno
import pathlib

import pytest

from qhos2 import coletor as co, eventos as ev, leitura as le, sintetico as si
from qhos2.catalogo import ARQUIVO_LEGADO
from qhos2.gravador import Gravador

TOKEN = "a" * 43
IDENTIDADE = {"system_name": si.SISTEMA, "requested_symbol": "SINTETICO", "exchange": "QHOS2"}


def leitor_sintetico(pasta):
    return le.Leitor(pasta, tick=0.25, simbolo="SINTETICO", bolsa="QHOS2")


def captura_aberta(raiz, semente=5, passos=30):
    gravador = Gravador(raiz, identidade=IDENTIDADE, captura_id="viva")
    for i, (template, dados) in enumerate(si.gerar(semente, passos).quadros):
        gravador(dados, template, si.INICIO_NS + i * si.PASSO_NS)
    return gravador


def seguir_ate_parar(coletor):
    """Roda `_seguir` ate a leitura falhar (ele retorna sozinho em FALHA)."""
    asyncio.run(asyncio.wait_for(coletor._seguir(), 5))


# ---- QHOS-AUD-166: OSError em avancar vira arquivo_inacessivel sem errno ---------------------------------------------

def _coletor_com_avancar_negado(tmp_path, excecao):
    leitor = leitor_sintetico(tmp_path)

    def negado(*_a, **_kw):
        raise excecao
    leitor.avancar = negado
    return co.Coletor(token=TOKEN, porta=0, leitor=leitor, avisar=lambda _: None, relogio=lambda: 1234.0)


def test_caracterizacao_oserror_em_avancar_vira_arquivo_inacessivel_sem_errno(tmp_path):
    c = _coletor_com_avancar_negado(tmp_path, PermissionError(errno.EACCES, "negado", "/caminho/privado"))
    seguir_ate_parar(c)
    assert c.leitor.estado == le.FALHA and c.leitor.codigo == "arquivo_inacessivel"
    assert "/caminho/privado" not in c.leitor.codigo                 # a privacidade do caminho e preservada


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-166: `_seguir` descarta o errno do OSError; a FALHA chega ao "
                                       "terminal e ao snapshot sem o porque (EACCES, EIO, ESTALE...)")
@pytest.mark.parametrize("excecao, nome", [(PermissionError(errno.EACCES, "negado"), "EACCES"),
                                           (OSError(errno.EIO, "io"), "EIO")])
def test_falha_de_leitura_por_oserror_traz_o_nome_do_errno(tmp_path, excecao, nome):
    c = _coletor_com_avancar_negado(tmp_path, excecao)
    seguir_ate_parar(c)
    assert c.leitor.codigo == f"arquivo_inacessivel:{nome}"


# ---- QHOS-AUD-167: _idade_do_arquivo_s devolve None em qualquer OSError; camada gravacao da motivo errado ------------

def _stat_negado_para_o_bruto(monkeypatch):
    original = pathlib.Path.stat

    def stat(self, *a, **kw):
        if self.name == ARQUIVO_LEGADO:
            raise PermissionError(errno.EACCES, "negado")
        return original(self, *a, **kw)
    monkeypatch.setattr(pathlib.Path, "stat", stat)


def _coletor_lendo_captura_aberta(tmp_path):
    gravador = captura_aberta(tmp_path)
    c = co.Coletor(token=TOKEN, porta=0, leitor=leitor_sintetico(gravador.pasta), avisar=lambda _: None)
    c.traduzir(c.leitor.avancar(), alcancado=True)
    assert c.leitor.estado == le.LENDO
    return c


def _camada(percurso, ident):
    return next(c for c in percurso["stages"] if c["id"] == ident)


def test_caracterizacao_stat_negado_vira_arquivo_parado_na_camada_gravacao(tmp_path, monkeypatch):
    c = _coletor_lendo_captura_aberta(tmp_path)
    _stat_negado_para_o_bruto(monkeypatch)
    assert c._idade_do_arquivo_s() is None
    m = c.percurso()
    assert _camada(m, "rithmic")["reason"] == "Arquivo da captura inacessível"
    assert _camada(m, "gravacao") == {**_camada(m, "gravacao"), "state": "atencao", "reason": "Arquivo parado e sem manifesto"}


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-167: `_idade_do_arquivo_s` devolve o mesmo None para 'sem stat' e "
                                       "'stat negado'; a camada gravacao diz 'parado' quando o arquivo esta inacessivel")
def test_stat_negado_e_dito_inacessivel_na_camada_gravacao(tmp_path, monkeypatch):
    c = _coletor_lendo_captura_aberta(tmp_path)
    _stat_negado_para_o_bruto(monkeypatch)
    gravacao = _camada(c.percurso(), "gravacao")
    assert "inacess" in gravacao["reason"].casefold() and "parado" not in gravacao["reason"].casefold()


# ---- QHOS-AUD-175: Leitor.avancar so trata FileNotFoundError em os.stat -------------------------------------------

def test_caracterizacao_permission_error_em_os_stat_escapa_do_leitor(tmp_path, monkeypatch):
    gravador = captura_aberta(tmp_path)
    leitor = leitor_sintetico(gravador.pasta)
    assert leitor.avancar() and leitor.estado == le.LENDO
    monkeypatch.setattr(le.os, "stat", lambda *a, **kw: (_ for _ in ()).throw(PermissionError(errno.EACCES, "negado")))
    with pytest.raises(PermissionError):
        leitor.avancar()
    assert leitor.estado == le.LENDO                                 # a leitura nao virou FALHA: excecao em vez de estado


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-175: `Leitor.avancar` so trata FileNotFoundError em os.stat; outro "
                                       "OSError vira excecao, contra o contrato 'falha vira estado FALHA, nao excecao'")
def test_permission_error_em_os_stat_vira_falha_com_errno(tmp_path, monkeypatch):
    gravador = captura_aberta(tmp_path)
    leitor = leitor_sintetico(gravador.pasta)
    leitor.avancar()
    monkeypatch.setattr(le.os, "stat", lambda *a, **kw: (_ for _ in ()).throw(PermissionError(errno.EACCES, "negado")))
    assert leitor.avancar() == []
    assert leitor.estado == le.FALHA and leitor.codigo == "arquivo_inacessivel:EACCES"


# ---- QHOS-AUD-155: excecao nao-EventoError em normalizar escapa sem registrar o indice do quadro --------------------

def _normalizar_que_quebra_no_quadro(monkeypatch, alvo):
    original = le.ev.normalizar

    def normalizar(indice, *a, **kw):
        if indice == alvo:
            raise TypeError("tipo inesperado no decodificador")
        return original(indice, *a, **kw)
    monkeypatch.setattr(le.ev, "normalizar", normalizar)


def test_caracterizacao_type_error_em_normalizar_escapa_do_leitor(tmp_path, monkeypatch):
    pasta = si.gravar(si.gerar(5, 30), tmp_path)
    _normalizar_que_quebra_no_quadro(monkeypatch, 7)
    leitor = leitor_sintetico(pasta)
    with pytest.raises(TypeError):
        leitor.avancar()
    assert leitor.estado == le.LENDO and leitor.quadros == 7        # parou no quadro 7 sem registrar nada


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-155: `_quadro` so trata `EventoError`; outra excecao escapa sem o "
                                       "indice do quadro e vira loop de crash no supervisor")
def test_excecao_inesperada_em_normalizar_vira_falha_com_indice(tmp_path, monkeypatch):
    pasta = si.gravar(si.gerar(5, 30), tmp_path)
    _normalizar_que_quebra_no_quadro(monkeypatch, 7)
    leitor = leitor_sintetico(pasta)
    leitor.avancar()
    assert leitor.estado == le.FALHA and "7" in leitor.codigo        # fail-closed com o indice do quadro (nome do codigo livre)


# ---- QHOS-AUD-189: na FALHA, livro vazio e lacuna usam chegada local ou "agora" -------------------------------------

def _coletor_pronto_para_falhar(tmp_path):
    gravador = captura_aberta(tmp_path)
    relogio_t = 1_700_000_000.0
    c = co.Coletor(token=TOKEN, porta=0, leitor=leitor_sintetico(gravador.pasta), avisar=lambda _: None,
                   relogio=lambda: relogio_t)
    quadros = c.leitor.avancar()
    c.traduzir(quadros, alcancado=True)
    carimbos = [e.carimbo_ns for _, _, eventos in quadros for e in eventos if e.carimbo_ns is not None]
    S, R = max(carimbos), c.leitor.ultimo_recebido_ns
    assert c._tempo_ns == S and S // 10 ** 6 != R // 10 ** 6         # carimbo do servidor != chegada, em ms
    c.leitor._falhar("falha_sintetica")
    return c, S, R, relogio_t


def test_caracterizacao_na_falha_o_livro_vazio_usa_a_chegada_e_a_lacuna_usa_agora(tmp_path):
    c, S, R, T = _coletor_pronto_para_falhar(tmp_path)
    seguir_ate_parar(c)
    vazio, lacuna = c._ao_parar
    assert vazio["type"] == "book" and vazio["ready"] is False and vazio["time"] == R // 10 ** 6
    assert lacuna["type"] == "gap" and lacuna["time"] == int(T * 1000)


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-189: na FALHA `traduzir` troca `_tempo_ns` (carimbo do servidor) por "
                                       "`ultimo_recebido_ns` e a lacuna usa `relogio()`, contra a regra documentada")
def test_na_falha_o_livro_vazio_mantem_o_carimbo_do_servidor_e_a_lacuna_nao_usa_agora(tmp_path):
    c, S, R, T = _coletor_pronto_para_falhar(tmp_path)
    seguir_ate_parar(c)
    vazio, lacuna = c._ao_parar
    assert vazio["time"] == S // 10 ** 6
    assert lacuna["time"] != int(T * 1000)
