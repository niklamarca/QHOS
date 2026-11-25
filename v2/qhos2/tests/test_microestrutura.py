"""Aritmetica independente, censura temporal e preservacao do replay; sem rede."""
from fractions import Fraction

import pytest

from qhos2 import desequilibrio, eventos as ev, microestrutura as mi, sintetico as si


def evento(i, tipo, *, t=None, pos=0, lado=None, preco=None, quantidade=None, ordem=None, retrato=False):
    return ev.Evento(i, pos, i * 100 if t is None else t, tipo, lado, preco, quantidade, ordem, retrato=retrato)


def pronto(motor):
    motor.aplicar_quadro([evento(0, ev.RETRATO, lado=ev.COMPRA, preco=100, quantidade=3, ordem="b"),
                          evento(0, ev.RETRATO, pos=1, lado=ev.VENDA, preco=102, quantidade=1, ordem="a")])
    motor.aplicar_quadro([evento(1, ev.RETRATO_FIM)])
    return motor


def test_microprice_exato_com_pesos_do_lado_oposto():
    motor = pronto(mi.Microestrutura())
    assert mi.microprice(motor.livro) == Fraction(203, 2)
    a = motor.atributos()
    assert (a["microprice_num_ticks"], a["microprice_den"]) == (203, 2)
    assert a["ordens_ativas_censuradas"] == 2 and a["ordens_ativas_com_inicio_observado"] == 0
    motor.aplicar_quadro([evento(2, ev.ALTERA, lado=ev.COMPRA, preco=100, quantidade=1, ordem="b")])
    assert mi.microprice(motor.livro) == Fraction(101, 1)


def test_microprice_recusa_ausencia_cruzamento_e_suspeita():
    motor = mi.Microestrutura()
    assert mi.microprice(motor.livro) is None
    pronto(motor)
    motor.aplicar_quadro([evento(2, ev.ALTERA, lado=ev.COMPRA, preco=102, quantidade=3, ordem="b")])
    assert mi.microprice(motor.livro) is None
    motor.aplicar_quadro([evento(3, ev.ALTERA, lado=ev.COMPRA, preco=100, quantidade=3, ordem="b")])
    assert mi.microprice(motor.livro) == Fraction(203, 2)
    motor.aplicar_quadro([evento(4, ev.REMOVE, ordem="desconhecida")])
    assert mi.microprice(motor.livro) is None


def test_cvd_agressor_conhecido_desconhecido_e_retrato_separados():
    motor = pronto(mi.Microestrutura())
    for i, lado, q, retrato in [(2, ev.COMPRA, 7, False), (3, ev.VENDA, 2, False),
                                (4, None, 11, False), (5, ev.COMPRA, 1000, True)]:
        motor.aplicar_quadro([evento(i, ev.NEGOCIO, lado=lado, preco=101, quantidade=q, retrato=retrato)])
    a = motor.atributos()
    assert a["cvd_contratos"] == 5
    assert a["volume_compra_contratos"] == 7 and a["volume_venda_contratos"] == 2
    assert a["volume_agressor_desconhecido_contratos"] == 11


def test_vida_observada_preserva_new_apos_altera_e_nao_inventa_cancelamento():
    motor = pronto(mi.Microestrutura())
    motor.aplicar_quadro([evento(2, ev.NOVO, lado=ev.COMPRA, preco=99, quantidade=4, ordem="nova")])
    motor.aplicar_quadro([evento(3, ev.ALTERA, lado=ev.COMPRA, preco=98, quantidade=2, ordem="nova")])
    motor.aplicar_quadro([evento(4, ev.REMOVE, ordem="nova")])
    motor.aplicar_quadro([evento(5, ev.REMOVE, ordem="b")])
    a = motor.atributos()
    assert a["vida_observada_total_ns"] == 200
    assert a["remocoes_com_inicio_observado"] == 1 and a["remocoes_censuradas"] == 1
    assert a["ordens_ativas_censuradas"] == 1
    assert all("cancelamento" not in k for k in a)


def test_new_concorrente_ao_retrato_permanece_censurado():
    motor = mi.Microestrutura()
    motor.aplicar_quadro([evento(0, ev.NOVO, lado=ev.COMPRA, preco=99, quantidade=4, ordem="pendente")])
    motor.aplicar_quadro([evento(1, ev.RETRATO, lado=ev.COMPRA, preco=100, quantidade=3, ordem="b"),
                          evento(1, ev.RETRATO, pos=1, lado=ev.VENDA, preco=102, quantidade=1, ordem="a")])
    motor.aplicar_quadro([evento(2, ev.RETRATO_FIM)])
    motor.aplicar_quadro([evento(3, ev.REMOVE, ordem="pendente")])
    assert motor.atributos()["remocoes_censuradas"] == 1
    assert motor.atributos()["vida_observada_total_ns"] == 0


def test_dois_streams_reinicio_nao_carrega_cvd_nem_nascimento():
    motor = pronto(mi.Microestrutura())
    motor.aplicar_quadro([evento(2, ev.NOVO, lado=ev.COMPRA, preco=99, quantidade=1, ordem="x")])
    motor.aplicar_quadro([evento(3, ev.NEGOCIO, lado=ev.COMPRA, preco=100, quantidade=50)])
    assert motor.atributos()["ordens_ativas_com_inicio_observado"] == 1
    motor.reiniciar()
    pronto(motor)  # nova origem temporal, inclusive com timestamps menores que a captura anterior
    assert motor.geracao == 1
    assert motor.atributos()["cvd_contratos"] == 0
    assert motor.atributos()["ordens_ativas_com_inicio_observado"] == 0
    assert motor.atributos()["vida_observada_total_ns"] == 0


@pytest.mark.parametrize("ruim", [evento(1, ev.FIM_EVENTO), evento(2, ev.FIM_EVENTO, t=50),
                                  evento(2, ev.FIM_EVENTO, t=True), evento(-1, ev.FIM_EVENTO)])
def test_tempo_ou_indice_incoerente_exige_reinicio(ruim):
    motor = pronto(mi.Microestrutura())
    with pytest.raises(mi.MicroestruturaError, match="fora_de_ordem"):
        motor.aplicar_quadro([ruim])
    with pytest.raises(mi.MicroestruturaError, match="requer_reinicio"):
        motor.atributos()
    motor.reiniciar()
    pronto(motor)
    assert motor.livro.qualificado


def test_mesmo_timestamp_e_multiplas_mudancas_atomicas_no_quadro():
    motor = pronto(mi.Microestrutura())
    motor.aplicar_quadro([evento(2, ev.NOVO, t=100, lado=ev.COMPRA, preco=103, quantidade=1, ordem="x"),
                          evento(2, ev.REMOVE, t=100, pos=1, ordem="x")])
    assert motor.livro.qualificado
    assert motor.atributos()["vida_observada_total_ns"] == 0
    assert motor.atributos()["remocoes_com_inicio_observado"] == 1


@pytest.mark.parametrize("quantidade,lado", [(0, ev.COMPRA), (-1, ev.COMPRA), (True, ev.COMPRA), (2, "unknown")])
def test_negocio_invalido_nunca_vira_volume(quantidade, lado):
    motor = pronto(mi.Microestrutura())
    with pytest.raises(mi.MicroestruturaError, match="negocio_invalido"):
        motor.aplicar_quadro([evento(2, ev.NEGOCIO, lado=lado, quantidade=quantidade, preco=101)])


def test_limite_memoria_recusa_e_reinicio_limpa():
    motor = pronto(mi.Microestrutura(limite_ordens=2))
    with pytest.raises(mi.MicroestruturaError, match="limite_de_ordens_excedido"):
        motor.aplicar_quadro([evento(2, ev.NOVO, lado=ev.COMPRA, preco=99, quantidade=1, ordem="x")])
    motor.reiniciar()
    assert motor.atributos()["ordens_ativas_censuradas"] == 0


def test_iterador_preserva_estado_original_e_nao_reescreve_passado(tmp_path):
    pasta = si.gravar(si.gerar(7, 300), tmp_path)
    original = list(desequilibrio.estados_da_captura(pasta))
    com_features = list(mi.estados_da_captura(pasta))
    assert [e for e, _ in com_features] == original
    eventos = list(ev.eventos_da_captura(pasta))
    corte = original[len(original) // 2].indice
    prefixo = list(mi.estados(e for e in eventos if e.indice <= corte))
    assert prefixo == com_features[:len(prefixo)]
    assert mi.METODOLOGIA["experimental"] is True
    assert mi.METODOLOGIA["utc_qualificado"] is False
    assert mi.METODOLOGIA["unidade_temporal"] == "quadro_recebido"
