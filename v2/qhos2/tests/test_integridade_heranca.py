"""Regressoes adversariais da heranca: bytes integros nao provam livro valido."""
import json

import pytest

from qhos2 import eventos as ev, historico as hi, inspecao, leitura as le, livro as lv
from qhos2 import mercado as m, rprotocol as p, sintetico as si
from qhos2.gravador import Gravador


IDENTIDADE = {"system_name": si.SISTEMA, "requested_symbol": "ESZ6", "exchange": "CME"}
STATUS = {"estado": "FECHADA", "retrato_completo": True, "referencia": {"min_qprice_change": 0.25}}


def identificar(dados, simbolo="ESZ6", bolsa="CME"):
    return dados + si._txt(p.F_SYMBOL, simbolo) + si._txt(p.F_EXCHANGE, bolsa)


def retrato(codigos=("0",)):
    return [(116, identificar(si.quadro_retrato("C", 100.0, [(1, 1, "c")]))),
            (116, identificar(si.quadro_retrato("V", 100.25, [(1, 2, "v")]))),
            (116, si._int(p.F_TEMPLATE, 116) + b"".join(si._txt(p.F_RP_CODE, c) for c in codigos))]


def gravar(raiz, quadros, *, status=STATUS, fechar=True, normal=True):
    g = Gravador(raiz, identidade=IDENTIDADE, captura_id="caso")
    for i, (t, dados) in enumerate(quadros):
        g(dados, t, si.INICIO_NS + i)
    if fechar:
        g.fechar(status, fechamento_normal=normal)
    return g


def leitor(pasta):
    return le.Leitor(pasta, tick=0.25, simbolo="ESZ6", bolsa="CME")


@pytest.mark.parametrize("codigos", [("4",), ("0", "4"), ("0", "0"), ("",)])
def test_retrato_recusado_nunca_qualifica_ao_vivo_ou_no_replay(tmp_path, codigos):
    g = gravar(tmp_path, retrato(codigos))
    l = leitor(g.pasta)
    l.avancar()
    assert l.estado == le.FALHA and not l.livro_vale and not l.livro.qualificado
    with pytest.raises(ev.EventoError, match="resposta_recusada_116"):
        lv.livro_da_captura(g.pasta)


def test_recusa_e_detectada_antes_do_manifesto_e_nao_envia_ready(tmp_path):
    from qhos2.coletor import Coletor
    g = gravar(tmp_path, retrato(("4",)), fechar=False)
    try:
        l = leitor(g.pasta)
        quadros = l.avancar()
        assert not (g.pasta / "manifest.json").exists()
        c = Coletor(token="A" * 43, leitor=l, avisar=lambda _: None)
        c.traduzir(quadros, True)
        livros = [x for x in c.retrato_atual() if x["type"] == "book"]
        assert livros and all(not x["ready"] for x in livros)
    finally:
        g.fechar({**STATUS, "estado": "FALHOU", "retrato_completo": False}, fechamento_normal=False)


@pytest.mark.parametrize("template,simbolo,bolsa", [
    (160, "NQZ6", "CME"), (160, "ESZ6", "NYMEX"), (150, "NQZ6", "CME"), (161, "NQZ6", "CME")])
def test_identidade_de_cada_quadro_inclusive_negocio_e_fim(tmp_path, template, simbolo, bolsa):
    dados = {160: si.quadro_livro([(1, "C", 99.75, 1, 3, "estranha")], si.INICIO_NS),
             150: si.quadro_negocio(100.0, 1, "C", "c", "a", si.INICIO_NS),
             161: si._int(p.F_TEMPLATE, 161)}[template]
    g = gravar(tmp_path, retrato() + [(template, identificar(dados, simbolo, bolsa))])
    l = leitor(g.pasta)
    l.avancar()
    assert l.estado == le.FALHA and l.codigo == "simbolo_ou_bolsa_diverge"
    assert "estranha" not in l.livro.ordens and not l.livro_vale
    with pytest.raises(ev.EventoError, match="simbolo_ou_bolsa_diverge:3"):
        list(ev.eventos_da_captura(g.pasta))


def test_todas_as_identidades_na_lista_do_161_sao_conferidas(tmp_path):
    dados = identificar(si._int(p.F_TEMPLATE, 161)) + si._txt(p.F_SYMBOL, "NQZ6")
    g = gravar(tmp_path, retrato() + [(161, dados)])
    with pytest.raises(ev.EventoError, match="simbolo_ou_bolsa_diverge"):
        lv.livro_da_captura(g.pasta)


@pytest.mark.parametrize("mudanca,normal", [
    ({"estado": "FALHOU"}, True), ({"retrato_completo": False}, True),
    ({"erro": "falha_sintetica"}, True), ({}, False),
    ({"anomalias": {"retrato_correlacao_divergente": 1}}, True)])
def test_manifesto_de_falha_nao_e_validade_de_retrato(tmp_path, mudanca, normal):
    g = gravar(tmp_path, retrato(), fechar=False)
    l = leitor(g.pasta)
    l.avancar()
    assert l.livro_vale
    g.fechar({**STATUS, **mudanca}, fechamento_normal=normal)
    l.avancar()
    assert l.estado == le.FALHA and not l.livro_vale and not l.livro.qualificado
    with pytest.raises(ev.EventoError):
        lv.livro_da_captura(g.pasta)


def test_controle_sucesso_igual_no_replay_e_leitor(tmp_path):
    g = gravar(tmp_path, retrato())
    l = leitor(g.pasta)
    l.avancar()
    assert l.estado == le.ENCERRADA and l.livro_vale
    assert l.livro.assinatura() == lv.livro_da_captura(g.pasta).assinatura()


def test_inspecao_nao_confunde_recusa_com_fim_bem_sucedido(tmp_path):
    g = gravar(tmp_path, retrato(("4",)))
    r = inspecao.inspecionar(g.pasta)["retrato"]
    assert r["recusado"] and not r["fim_visto"]


@pytest.mark.parametrize("ordem,quantidade", [(None, 1), ("c", 0), ("c", -1), ("c", True)])
def test_dado_invalido_no_retrato_nao_se_torna_qualificado(ordem, quantidade):
    l = lv.Livro()
    l.aplicar(ev.Evento(0, 0, 0, ev.RETRATO, ev.COMPRA, 400, quantidade, ordem, 1))
    l.aplicar(ev.Evento(1, 0, 0, ev.RETRATO_FIM))
    assert not l.qualificado and l.suspeito


def test_cache_nao_oculta_corrupcao_dos_bytes(tmp_path):
    g = gravar(tmp_path / "capturas", retrato())
    cache = tmp_path / "cache"
    hi.barras_da_captura(g.pasta, cache, tick=0.25, simbolo="ESZ6", bolsa="CME")
    bruto = g.pasta / "market_frames.jsonl"
    bruto.write_bytes(bruto.read_bytes() + b"\n")
    with pytest.raises(ev.EventoError, match="captura_nao_verificada"):
        hi.barras_da_captura(g.pasta, cache, tick=0.25, simbolo="ESZ6", bolsa="CME")


def test_cache_nao_oculta_manifesto_de_falha(tmp_path):
    g = gravar(tmp_path / "capturas", retrato())
    cache = tmp_path / "cache"
    hi.barras_da_captura(g.pasta, cache, tick=0.25, simbolo="ESZ6", bolsa="CME")
    caminho = g.pasta / "manifest.json"
    manifesto = json.loads(caminho.read_text())
    manifesto["status"]["sessao"]["retrato_completo"] = False
    caminho.write_text(json.dumps(manifesto))
    with pytest.raises(ev.EventoError, match="captura_encerrada_com_falha"):
        hi.barras_da_captura(g.pasta, cache, tick=0.25, simbolo="ESZ6", bolsa="CME")


def test_cache_v1_de_captura_recusada_nao_e_reutilizado(tmp_path):
    g = gravar(tmp_path / "capturas", retrato(("4",)))
    cache = tmp_path / "cache"
    cache.mkdir()
    manifesto = json.loads((g.pasta / "manifest.json").read_text())
    (cache / "caso.json").write_text(json.dumps({"esquema": "qhos2.candles_1m.v1", "captura": "caso",
        "sha256": manifesto["sha256"], "tick": 0.25, "barras": [[600, 400, 400, 400, 400, 1]]}))
    with pytest.raises(ev.EventoError, match="resposta_recusada_116"):
        hi.barras_da_captura(g.pasta, cache, tick=0.25, simbolo="ESZ6", bolsa="CME")


def test_cache_v1_de_identidade_misturada_nao_e_reutilizado(tmp_path):
    estrangeiro = identificar(si.quadro_negocio(100.0, 1, "C", "c", "a", si.INICIO_NS), "NQZ6")
    g = gravar(tmp_path / "capturas", retrato() + [(150, estrangeiro)])
    cache = tmp_path / "cache"
    cache.mkdir()
    manifesto = json.loads((g.pasta / "manifest.json").read_text())
    (cache / "caso.json").write_text(json.dumps({"esquema": "qhos2.candles_1m.v1", "captura": "caso",
        "sha256": manifesto["sha256"], "tick": 0.25, "barras": []}))
    with pytest.raises(ev.EventoError, match="simbolo_ou_bolsa_diverge"):
        hi.barras_da_captura(g.pasta, cache, tick=0.25, simbolo="ESZ6", bolsa="CME")


def test_ticks_invalidos_e_overflow_sao_recusados_com_codigo():
    for tick in (0.0, -0.25, float("nan"), float("inf"), True):
        with pytest.raises(ev.EventoError, match="tick_invalido"):
            ev.ticks(100.0, tick)
    with pytest.raises(ev.EventoError, match="preco_fora_da_grade"):
        ev.ticks(1e308, 1e-308)


def test_tick_explicito_nao_reinterpreta_contrato_conhecido(tmp_path):
    g = gravar(tmp_path, retrato())
    with pytest.raises(ev.EventoError, match="tick_diverge"):
        list(ev.eventos_da_captura(g.pasta, tick=0.5))


@pytest.mark.parametrize("template,quadro", [
    (160, si.quadro_livro([(1, "C", 99.75, 1, 3, "x")], si.INICIO_NS)),
    (150, si.quadro_negocio(100.0, 1, "C", "c", "a", si.INICIO_NS)),
    (116, si.quadro_retrato("C", 100.0, [(1, 3, "b")])),
])
def test_identidade_singular_repetida_nao_esconde_contrato(template, quadro):
    dados = identificar(quadro, "NQZ6") + si._txt(p.F_SYMBOL, "ESZ6")
    with pytest.raises(ev.EventoError, match="identidade_repetida_divergente"):
        ev.normalizar(3, template, si.INICIO_NS, dados, 0.25, simbolo="ESZ6", bolsa="CME")
    dados = identificar(quadro) + si._txt(p.F_SYMBOL, "ESZ6")
    assert ev.normalizar(3, template, si.INICIO_NS, dados, 0.25, simbolo="ESZ6", bolsa="CME")


def test_negocio_zero_ou_negativo_recusado():
    for q in (0, -1):
        dados = identificar(si.quadro_negocio(100.0, q & ((1 << 64) - 1), "C", "c", "a", si.INICIO_NS))
        with pytest.raises(ev.EventoError, match="quantidade_do_negocio_invalida"):
            ev.normalizar(3, 150, si.INICIO_NS, dados, 0.25)


@pytest.mark.posix
@pytest.mark.lento
def test_leitor_nao_bloqueia_em_fifo(tmp_path):
    import os, subprocess, sys
    os.mkfifo(tmp_path / "market_frames.jsonl")
    codigo = "from qhos2.leitura import Leitor;import sys;l=Leitor(sys.argv[1],tick=.25,simbolo='ESZ6',bolsa='CME');l.avancar();assert l.estado=='FALHA'"
    # o teto e so contra o travamento no FIFO; 2 s nao cabia importar o pacote num filho sob carga (QHOS-AUD-252)
    r = subprocess.run([sys.executable, "-c", codigo, str(tmp_path)], timeout=30, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("consumidor", ["replay", "leitor", "cache"])
def test_identidade_persistida_corrompida_nao_qualifica(tmp_path, consumidor):
    from qhos2 import historico
    pasta = gravar(tmp_path / "capturas", si.gerar(14, 30).quadros).pasta
    cache = tmp_path / "derivados"
    if consumidor == "cache":
        assert historico.barras_da_captura(pasta, cache, tick=0.25, simbolo="ESZ6", bolsa="CME") is not None
    controle = leitor(pasta)
    controle.avancar()
    assert controle.estado == le.ENCERRADA and controle.livro_vale
    identidade = pasta / "capture_identity.json"
    identidade.write_bytes(identidade.read_bytes() + b" ")  # mesmo JSON, bytes diferentes do hash fixado
    if consumidor == "leitor":
        l = leitor(pasta)
        l.avancar()
        assert l.estado == le.FALHA and not l.livro_vale
    else:
        with pytest.raises(ev.EventoError, match="identidade_de_captura_invalida"):
            if consumidor == "replay":
                list(ev.eventos_da_captura(pasta))
            else:
                historico.barras_da_captura(pasta, cache, tick=0.25, simbolo="ESZ6", bolsa="CME")


def test_identidade_encerrada_permite_copia_em_outro_inode_e_nome(tmp_path):
    from qhos2.operacao import copiar_captura
    pasta = si.gravar(si.gerar(14, 30), tmp_path / "capturas")
    copia = tmp_path / "restaurada"
    copiar_captura(pasta, copia)
    assert ev.assinatura(ev.eventos_da_captura(copia)) == ev.assinatura(ev.eventos_da_captura(pasta))


@pytest.mark.parametrize("campo,valor", [("requested_symbol", "NQZ6"), ("raw_ino", 0)])
def test_leitor_ativo_confere_sidecar_antes_de_quadro(campo, valor, tmp_path):
    g = gravar(tmp_path, si.gerar(14, 30).quadros, fechar=False)
    try:
        controle = leitor(g.pasta)
        controle.avancar()
        assert controle.livro_vale
        controle._fechar()
        caminho = g.pasta / "capture_identity.json"
        identidade = json.loads(caminho.read_text())
        identidade[campo] = valor
        caminho.write_text(json.dumps(identidade))
        l = leitor(g.pasta)
        assert l.avancar() == [] and l.estado == le.FALHA and not l.livro_vale
    finally:
        from qhos2.gravador import GravadorError
        with pytest.raises(GravadorError, match="identidade_de_captura_diverge"):
            g.fechar(STATUS, fechamento_normal=False)
