"""Peca 4d: a inspecao conta o que a captura traz, segue o ciclo de vida das ordens e nunca imprime dado de mercado."""
import json
import struct

import pytest

from qhos2 import inspecao
from qhos2 import mercado as m
from qhos2.gravador import Gravador

IDENT = {"system_name": "Rithmic Paper Trading", "requested_symbol": "ESZ6", "exchange": "CME"}
STATUS = {"estado": "FECHADA", "logout_confirmado": True}
SSBOE = 1_761_246_000


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


def _dbl(numero, valor):
    return _v(numero << 3 | 1) + struct.pack("<d", valor)


def _txt(numero, valor):
    bruto = valor.encode()
    return _v(numero << 3 | 2) + _v(len(bruto)) + bruto


def q160(entradas, seq=None, carimbo=True, sem_prioridade=False):
    """entradas: (tipo, lado, preco, tamanho, prioridade, id); id vazio = campo vazio."""
    dados = _int(154467, 160)
    if seq is not None:
        dados += _int(112002, seq)
    for tipo, lado, preco, tamanho, prioridade, ident in entradas:
        dados += _int(110121, tipo) + _int(153612, lado) + _dbl(154405, preco) + _int(154406, tamanho)
        if not sem_prioridade:
            dados += _int(153613, prioridade)
        dados += _txt(149238, ident)
    if carimbo:
        dados += _int(150100, SSBOE) + _int(150101, 1)
    return dados


def q116(lado, preco, ordens, seq=None):
    dados = _int(154467, 116) + _int(153612, lado) + _dbl(154405, preco)
    if seq is not None:
        dados += _int(112002, seq)
    for tamanho, prioridade, ident in ordens:
        dados += _int(154406, tamanho) + _int(153613, prioridade) + _txt(149238, ident)
    return dados


def q116_fim():
    return _int(154467, 116) + _txt(132766, "0")


def q161(seq):
    return _int(154467, 161) + _int(112002, seq) + _int(150100, SSBOE)


def capturar(pasta, quadros, *, fechar=True):
    gravador = Gravador(pasta, identidade=IDENT)
    for i, dados in enumerate(quadros):
        gravador(dados, dados_template(dados), SSBOE * 10 ** 9 + i)
    if fechar:
        gravador.fechar(STATUS, fechamento_normal=True)
    return gravador.pasta


def dados_template(dados):
    return m.decodificar(dados).template_id if dados[:3] == _v(154467 << 3) else 160


N, A, R = m.NOVO, m.ALTERACAO, m.REMOCAO
C, V = m.COMPRA, m.VENDA

CENA = [
    q116(C, 100.0, [(1, 1, "B1"), (2, 2, "B2")], seq=1),
    q160([(N, C, 99.5, 1, 9, "D1")], seq=10),  # chega antes do fim do retrato
    q116(V, 100.5, [(3, 1, "S1")], seq=1),
    q116_fim(),
    q160([(N, C, 99.75, 4, 5, "N1"), (A, C, 100.0, 9, 1, "B1"), (R, V, 100.5, 0, 1, "S1")], seq=11),
    q161(12),
    q160([(A, C, 99.75, 2, 7, "B2")], seq=13),
    q160([(R, V, 101.0, 0, 3, "ZZ"), (A, C, 98.0, 1, 4, "YY"), (N, C, 99.0, 1, 6, "N1"), (N, C, 99.0, 1, 6, "")],
         seq=13),
    q161(20),
    q160([(N, V, 101.0, 1, 2, "E1")], seq=19, carimbo=False),
]


def test_relatorio_da_cena_completa(tmp_path):
    r = inspecao.inspecionar(capturar(tmp_path, CENA))
    assert r["quadros"] == {"116": 3, "160": 5, "161": 2} and r["invalidos"] == {}
    assert r["com_carimbo"] == {"160": 4, "161": 2}
    a = r["atualizacoes"]
    assert a["entradas"] == 10 and a["maior_quadro"] == 4 and a["quadros_desalinhados"] == 0
    assert a["por_quadro"] == {"1": 3, "2-5": 2}
    assert a["tipos"] == {"CHANGE": 3, "DELETE": 2, "NEW": 5} and a["lados"] == {"compra": 7, "venda": 3}
    assert a["preenchidos"] == {"exchange_order_id": 9, "depth_order_priority": 10, "prev_depth_price": 0}
    rt = r["retrato"]
    assert (rt["niveis"], rt["ordens"], rt["ids_vazios"], rt["ids_duplicados"]) == (2, 3, 0, 0)
    assert rt["lados"] == {"compra": 1, "venda": 1} and rt["fim_visto"] is True and rt["apos_o_fim"] == 0


def test_ciclo_de_vida_separa_durante_e_depois_do_retrato(tmp_path):
    r = inspecao.inspecionar(capturar(tmp_path, CENA))
    assert r["ciclo"]["durante_retrato"] == {"novo": 1}
    assert r["ciclo"]["depois_do_retrato"] == {
        "alteracao": 2, "alteracao_desconhecida": 1, "alteracao_mantem_prioridade": 1,
        "alteracao_muda_prioridade": 1, "alteracao_muda_preco": 1, "novo": 2, "novo_ja_viva": 1,
        "remocao": 1, "remocao_desconhecida": 1, "sem_id": 1}
    # vivas: B1, B2, D1, N1, YY, E1 (S1 removida, ZZ nunca existiu)
    assert r["ordens_vivas_no_fim"] == 6


def test_sequencia_por_fluxo_e_no_mercado(tmp_path):
    s = inspecao.inspecionar(capturar(tmp_path, CENA))["sequencia"]
    assert s["116"] == {"com": 2, "sem": 1, "passos": {"0": 1}}
    assert s["160"] == {"com": 5, "sem": 0, "passos": {"+1": 1, "0": 1, "salto": 2}}
    assert s["161"] == {"com": 2, "sem": 0, "passos": {"salto": 1}}
    assert s["mercado"] == {"com": 7, "sem": 0, "passos": {"+1": 3, "0": 1, "salto": 1, "volta": 1}}


def test_listas_desalinhadas_e_ausentes_sao_contadas(tmp_path):
    torto = q160([(N, C, 1.0, 1, 1, "a"), (N, C, 2.0, 1, 2, "b")]) + _int(153613, 3)
    sem_prioridade = q160([(N, C, 1.0, 1, 1, "c")], sem_prioridade=True)
    a = inspecao.inspecionar(capturar(tmp_path, [torto, sem_prioridade]))["atualizacoes"]
    assert a["quadros_desalinhados"] == 1
    assert a["listas"]["depth_order_priority"] == {"com": 1, "sem": 1}
    assert a["listas"]["exchange_order_id"] == {"com": 2, "sem": 0}


def test_quadro_com_listas_mas_sem_tipo_e_desalinhado(tmp_path):
    sem_tipo = _int(154467, 160) + _int(153613, 1) + _txt(149238, "a")
    a = inspecao.inspecionar(capturar(tmp_path, [sem_tipo]))["atualizacoes"]
    assert a["entradas"] == 0 and a["por_quadro"] == {"0": 1} and a["quadros_desalinhados"] == 1


def test_alteracao_compara_prioridade_e_preco_com_o_estado_anterior(tmp_path):
    quadros = [q116(C, 10.0, [(1, 1, "a"), (1, 2, "b")]), q116_fim(),
               q160([(A, C, 10.0, 2, 1, "a")]), q160([(A, C, 10.0, 3, 1, "a")]), q160([(A, C, 9.0, 1, 5, "b")])]
    c = inspecao.inspecionar(capturar(tmp_path, quadros))["ciclo"]["depois_do_retrato"]
    assert (c["alteracao_mantem_prioridade"], c["alteracao_muda_prioridade"], c["alteracao_muda_preco"]) == (2, 1, 1)


def test_quadro_ilegivel_e_contado_sem_derrubar(tmp_path):
    r = inspecao.inspecionar(capturar(tmp_path, [b"\xff", q161(1)]))
    assert r["invalidos"] == {"160": 1} and r["quadros"] == {"160": 1, "161": 1}


def test_retrato_sem_fim_deixa_tudo_como_durante(tmp_path):
    r = inspecao.inspecionar(capturar(tmp_path, [q116(C, 1.0, [(1, 1, "a")]), q160([(R, C, 1.0, 0, 1, "a")])]))
    assert r["retrato"]["fim_visto"] is False
    assert r["ciclo"] == {"durante_retrato": {"remocao": 1}, "depois_do_retrato": {}}


def test_retrato_repetido_depois_do_fim_e_contado_a_parte(tmp_path):
    r = inspecao.inspecionar(capturar(tmp_path, [q116(C, 1.0, [(1, 1, "a"), (1, 2, "a")]), q116_fim(),
                                                 q116(C, 1.0, [(1, 1, "b")])]))
    assert r["retrato"]["ids_duplicados"] == 1 and r["retrato"]["apos_o_fim"] == 1 and r["ordens_vivas_no_fim"] == 1


def test_recusa_captura_nao_verificada(tmp_path):
    pasta = capturar(tmp_path, CENA, fechar=False)
    with pytest.raises(inspecao.InspecaoError, match="captura_nao_verificada:manifesto_ausente"):
        inspecao.inspecionar(pasta)


def test_cli_nao_imprime_preco_tamanho_nem_id(tmp_path, capsys):
    quadros = [q116(C, 4321.75, [(77, 1, "ORDEMSECRETA1")]), q116_fim(),
               q160([(A, C, 4321.5, 66, 1, "ORDEMSECRETA1"), (N, V, 4322.25, 55, 2, "ORDEMSECRETA2")], seq=1)]
    pasta = capturar(tmp_path, quadros)
    assert inspecao.main([str(pasta), str(pasta / "manifest.json")]) == 0
    saida = capsys.readouterr().out
    assert "ORDEMSECRETA" not in saida and "4321" not in saida and "4322" not in saida
    assert "id_preenchido=100.0%" in saida and "fim_visto=True" in saida and "alteracao_muda_preco" in saida


def test_cli_json_e_recusa(tmp_path, capsys):
    boa = capturar(tmp_path / "a", CENA)
    ruim = capturar(tmp_path / "b", CENA, fechar=False)
    assert inspecao.main(["--json", str(boa)]) == 0
    assert json.loads(capsys.readouterr().out)["esquema"] == "qhos2.inspecao.v1"
    assert inspecao.main([str(ruim), str(boa)]) == 2
    saida = capsys.readouterr().out
    assert "recusada:" in saida and "captura=" in saida
