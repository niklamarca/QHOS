"""Peca 4e: assinatura do ultimo negocio (100/101), quadros 150 gravados, lidos e ligados ao livro."""
import pytest

from qhos2 import captura, catalogo, inspecao
from qhos2 import mercado as m
from qhos2.gravador import Gravador
from qhos2.sessao import PROTOCOLO, ConfigSessao

# Reaproveita o servidor falso da sessao: o pytest poe esta pasta no caminho de importacao.
from test_sessao import SENHA, Servidor, ajustes, executar, falha, quadro150, quadro160  # noqa: F401
from test_inspecao import SSBOE, C, N, R, V, _dbl, _int, _txt, capturar, q116, q116_fim, q160

# Gerados pela biblioteca oficial do protobuf a partir dos .proto da Rithmic (fora do repositorio).
LT = bytes.fromhex("b1ea300000000040c2ba4080ec30c0c40790f53003da81310a31373631323436303030a2e1350445535a36aae13503434d45"
                   "c8e2350098d8360290e94809b2ef4803503737a0a549b0f6e9c706a8a549f40380b849b0f6e9c70688b8499003a0b849fbb518"
                   "c0c449b0f6e9c706e0c449e0a71298b64b96018ac14b03473838")
LT_RETRATO = bytes.fromhex("b1ea300000000000c1ba4090f53001a2e1350445535a36aae13503434d45c8e2350198d8360190e9480198b64b9601")


def test_ultimo_negocio_lido_igual_ao_oficial():
    c = m.decodificar(LT).campos
    assert (c["trade_price"], c["trade_size"], c["aggressor"]) == (6850.25, 3, m.VENDA) and c["is_snapshot"] is False
    assert (c["exchange_order_id"], c["aggressor_exchange_order_id"], c["volume"], c["trade_time"]) == (
        "P77", "G88", 123456, "1761246000")
    assert (c["presence_bits"], c["ssboe"], c["usecs"], c["source_nsecs"], c["jop_nsecs"]) == (
        9, 1761246000, 500, 400123, 300000)
    r = m.decodificar(LT_RETRATO).campos
    assert (r["aggressor"], r["trade_size"]) == (m.COMPRA, 1) and r["is_snapshot"] is True


# ---- sessao -----------------------------------------------------------------------------------------

def _templates_autenticada(servidor):
    return servidor.conexoes[1].templates()


def test_sessao_assina_e_cancela_os_negocios_na_ordem(ajustes):
    servidor = Servidor(apos_assinar=(quadro160(1),), apos_negocios=(quadro150(0, snapshot=True), quadro150(1), quadro150(2)))
    sessao, status, erro, coletor = executar(servidor, ajustes, negocios=True)
    assert erro is None and status["negocios_confirmados"] and status["negocios_cancelados"]
    assert status["quadros"]["150"] == 3 and [d for d, _, _ in coletor.itens] == servidor.mercado
    enviados = [t for t in _templates_autenticada(servidor) if t != 18]
    assert enviados == [10, 14, 117, 115, 100, 100, 117, 12]
    pedidos_100 = [d for d in servidor.conexoes[1].enviados if d.endswith(bytes.fromhex("98b64b64"))]
    assert [d[3] for d in pedidos_100] == [1, 2]  # assinar e depois cancelar


def test_sem_pedido_nao_ha_assinatura_de_negocios(ajustes):
    servidor = Servidor()
    _, status, erro, _ = executar(servidor, ajustes)
    assert erro is None and status["negocios_confirmados"] is False and status["negocios_cancelados"] is False
    assert 100 not in _templates_autenticada(servidor)


def test_negocios_recusados_derrubam_a_sessao(ajustes):
    falha(Servidor(rp_negocios="5"), ajustes, "resposta_recusada_101_codigo_5", PROTOCOLO, negocios=True)


def test_cancelamento_de_negocios_recusado(ajustes):
    sessao, _ = falha(Servidor(rp_negocios_cancelamento="3"), ajustes, "resposta_recusada_101_codigo_3", PROTOCOLO,
                      negocios=True)
    estado = sessao.status()
    assert estado["negocios_confirmados"] and not estado["negocios_cancelados"] and not estado["cancelamento_confirmado"]


def test_config_negocios_so_aceita_booleano(ajustes):
    with pytest.raises(ValueError, match="negocios_invalido"):
        ConfigSessao(template_version="5.42", symbol="ESZ6", exchange="CME", negocios="sim", settings=ajustes)


# ---- gravacao e inspecao ----------------------------------------------------------------------------

def test_gravador_aceita_negocio_e_o_catalogo_verifica(tmp_path):
    gravador = Gravador(tmp_path, identidade={"system_name": "Rithmic Paper Trading", "requested_symbol": "ESZ6",
                                              "exchange": "CME"})
    gravador(LT, 150, 1761246000 * 10 ** 9 + 75_000_000)
    gravador.fechar({"estado": "FECHADA"}, fechamento_normal=True)
    r = catalogo.verificar_captura(gravador.pasta)
    assert r["verificada"] is True and r["por_template"] == {"150": 1}


def _negocio(passivo, agressor, lado=V, snapshot=False, preco=True):
    dados = _int(154467, 150) + _int(110121, int(snapshot)) + _int(100178, 1) + _int(150100, SSBOE)
    if lado is not None:
        dados += _int(112003, lado)
    if preco:
        dados += _dbl(100006, 10.0)
    if passivo:
        dados += _txt(149238, passivo)
    if agressor:
        dados += _txt(154641, agressor)
    return dados


def test_inspecao_liga_negocio_a_ordem_do_livro(tmp_path):
    quadros = [q116(C, 10.0, [(5, 1, "P1"), (2, 2, "P2")]), q116_fim(),
               _negocio("", "", lado=C, snapshot=True),
               _negocio("P1", "G9"),            # passiva viva no livro
               q160([(R, C, 10.0, 0, 2, "P2")]),
               _negocio("P2", "G8"),            # passiva ja vista, mas removida antes do negocio chegar
               _negocio("Q7", "", preco=False),  # passiva nunca vista, negocio sem preco
               q160([(N, C, 9.0, 1, 3, "P3")]), q160([(R, C, 9.0, 0, 3, "P3")]),
               _negocio("P3", "", lado=None)]   # passiva vista so por atualizacao; negocio sem lado agressor
    r = inspecao.inspecionar(capturar(tmp_path, quadros))
    ng = r["negocios"]
    assert (ng["quadros"], ng["retrato"], ng["sem_preco"]) == (5, 1, 1)
    assert ng["agressor"] == {"ausente": 1, "compra": 1, "venda": 3}
    assert (ng["id_passivo"], ng["passivo_visto"], ng["passivo_vivo"]) == (4, 3, 1)
    assert (ng["id_agressor"], ng["agressor_visto"], ng["agressor_vivo"]) == (2, 0, 0)
    assert r["sequencia"]["150"] == {"com": 0, "sem": 5, "passos": {}}


def test_inspecao_do_negocio_nao_imprime_ids(tmp_path, capsys):
    quadros = [q116(C, 10.0, [(5, 1, "SEGREDOPASSIVO")]), q116_fim(), _negocio("SEGREDOPASSIVO", "SEGREDOAGRESSOR"),
               q160([(N, V, 11.0, 1, 3, "x")])]
    assert inspecao.main([str(capturar(tmp_path, quadros))]) == 0
    saida = capsys.readouterr().out
    assert "SEGREDO" not in saida and "150: quadros=1" in saida and "vivo=100.0%" in saida


# ---- CLI --------------------------------------------------------------------------------------------

@pytest.fixture
def ambiente(monkeypatch, tmp_path):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


@pytest.mark.parametrize("extra,assina", [([], True), (["--sem-negocios"], False)])
def test_cli_assina_negocios_por_padrao(ambiente, monkeypatch, capsys, extra, assina):
    servidor = Servidor(apos_negocios=(quadro150(1),))
    monkeypatch.setattr(captura, "_criar_fabrica", lambda endpoint: servidor.fabrica())
    codigo = captura.main(["--symbol", "ESZ6", "--exchange", "CME", "--duration", "0.2", *extra, "--online",
                           "--agreements-accepted"], entrada=lambda _: "u", senha=lambda _: SENHA)
    saida = capsys.readouterr().out
    assert codigo == 0 and (100 in _templates_autenticada(servidor)) is assina
    assert ("livro e negocios" in saida) is assina and f"negocios={assina}" in saida
