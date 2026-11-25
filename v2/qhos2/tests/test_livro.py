"""Peca 6b: o livro por ordem bate com o gabarito a cada passo, conta toda anomalia e so se qualifica quando confiavel."""
import pytest

from qhos2 import eventos as ev
from qhos2 import livro as lv
from qhos2 import sintetico as si
from qhos2.eventos import Evento
from qhos2.livro import Livro

C, V = ev.COMPRA, ev.VENDA


def eventos_do_cenario(cenario):
    saida = []
    for i, (template, dados) in enumerate(cenario.quadros):
        saida.extend(ev.normalizar(i, template, si.INICIO_NS + i * si.PASSO_NS, dados, cenario.tick))
    return saida


def novo(tipo, ordem, lado=C, preco=100, qtd=1, prio=1):
    return Evento(0, 0, 0, tipo, lado, preco, qtd, ordem, prio)


FIM = Evento(0, 0, 0, ev.RETRATO_FIM)


def montar(*eventos):
    livro = Livro()
    for e in eventos:
        livro.aplicar(e)
    return livro


# ---- contra o gabarito ------------------------------------------------------------------------------

@pytest.mark.parametrize("semente", [1, 2, 3, 4, 5])
def test_livro_bate_com_o_gabarito_depois_de_cada_passo(semente):
    cenario = si.gerar(semente, 500)
    eventos = eventos_do_cenario(cenario)
    livro, i = Livro(), 0
    for emitidos, verdade in cenario.verdades:
        while i < len(eventos) and eventos[i].indice < emitidos:
            livro.aplicar(eventos[i])
            i += 1
        assert livro.canonico() == verdade, f"diverge apos {emitidos} quadros"
        assert livro.qualificado and not livro.anomalias
    assert livro.negocios == sum(template == 150 for template, _ in cenario.quadros) > 0


def test_niveis_agregados_e_melhor_preco_batem_com_o_gabarito():
    cenario = si.gerar(8, 800)
    livro = montar(*eventos_do_cenario(cenario))
    verdade = cenario.livro_final()
    for lado, melhor_primeiro in ((C, True), (V, False)):
        esperado = [(p, sum(q for _, _, q in fila), len(fila))
                    for p, fila in sorted(verdade[lado].items(), reverse=melhor_primeiro)]
        assert livro.niveis(lado, 3) == esperado[:3] and livro.niveis(lado, 10 ** 6) == esperado
        assert livro.melhor(lado) == esperado[0][0]
    assert livro.resumo()["spread_ticks"] == livro.melhor(V) - livro.melhor(C) > 0


def test_replay_e_deterministico_e_igual_ao_da_captura_gravada(tmp_path):
    cenario = si.gerar(6, 400)
    ao_vivo = montar(*eventos_do_cenario(cenario))
    pasta = si.gravar(cenario, tmp_path)
    replay, de_novo = lv.livro_da_captura(pasta), lv.livro_da_captura(pasta)
    assert ao_vivo.assinatura() == replay.assinatura() == de_novo.assinatura()
    assert ao_vivo.resumo() == replay.resumo()


# ---- regras -----------------------------------------------------------------------------------------

def test_fases_e_qualificacao():
    livro = Livro()
    assert livro.fase == lv.VAZIO and not livro.qualificado
    livro.aplicar(novo(ev.RETRATO, "a"))
    assert livro.fase == lv.EM_RETRATO and not livro.qualificado
    livro.aplicar(FIM)
    assert livro.fase == lv.PRONTO and livro.qualificado
    vazio = montar(FIM)
    assert vazio.fase == lv.PRONTO and vazio.qualificado and vazio.resumo()["spread_ticks"] is None


def test_anomalias_durante_o_retrato_sao_toleradas_e_contadas_a_parte():
    livro = montar(novo(ev.RETRATO, "a"), novo(ev.REMOVE, "x"), novo(ev.ALTERA, "y"), novo(ev.NOVO, "a"), FIM)
    assert livro.qualificado and not livro.suspeito
    assert livro.anomalias == {"durante_retrato_remove_desconhecida": 1, "durante_retrato_altera_desconhecida": 1,
                               "durante_retrato_novo_ja_vivo": 1}
    assert set(livro.ordens) == {"a", "y"}


@pytest.mark.parametrize("evento,anomalia", [
    (novo(ev.REMOVE, "x"), "remove_desconhecida"),
    (novo(ev.ALTERA, "y"), "altera_desconhecida"),
    (novo(ev.NOVO, "a"), "novo_ja_vivo"),
    (novo(ev.NOVO, None), "sem_id"),
    (novo(ev.NOVO, "b", qtd=0), "quantidade_invalida"),
    (novo(ev.RETRATO, "c"), "retrato_apos_o_fim"),
    (Evento(0, 0, 0, "OUTRO"), "evento_desconhecido"),
])
def test_anomalia_depois_do_retrato_deixa_o_livro_suspeito(evento, anomalia):
    livro = montar(novo(ev.RETRATO, "a"), FIM, evento)
    assert livro.suspeito and not livro.qualificado and livro.anomalias[anomalia] == 1


def test_atualizacao_que_chega_antes_do_fim_do_retrato_e_aplicada_depois():
    # a remocao chega antes da mensagem do retrato que ainda traz a ordem: aplicada na hora, sobraria um fantasma
    livro = montar(novo(ev.REMOVE, "x"), novo(ev.RETRATO, "x", C, 100), novo(ev.RETRATO, "v", V, 101))
    assert set(livro.ordens) == {"x", "v"} and livro.fase == lv.EM_RETRATO and livro.resumo()["pendentes"] == 1
    livro.aplicar(FIM)
    assert set(livro.ordens) == {"v"} and livro.qualificado and not livro.anomalias
    assert livro.resumo()["pendentes"] == 0
    # a conta do defeito: aplicada na hora, "x" teria ficado viva; e a ordem fantasma que a etapa 2 corrige
    assert livro.reaplicadas == {"aplicadas": 1, "fantasmas": 1, "defasadas": 0}


def test_atualizacoes_guardadas_sao_aplicadas_na_ordem_recebida():
    livro = montar(novo(ev.RETRATO, "c", C, 100, qtd=9), novo(ev.ALTERA, "c", C, 100, qtd=5),
                   novo(ev.NOVO, "n", C, 99, qtd=1), novo(ev.ALTERA, "n", C, 99, qtd=3),
                   novo(ev.NOVO, "m", V, 101), novo(ev.REMOVE, "m", V, 101), FIM)
    assert {i: o.quantidade for i, o in livro.ordens.items()} == {"c": 5, "n": 3}
    assert livro.qualificado and not livro.anomalias
    assert livro.reaplicadas == {"aplicadas": 5, "fantasmas": 0, "defasadas": 0}  # na hora daria o mesmo livro


def test_retrato_que_ja_reflete_a_atualizacao_so_conta_a_parte():
    livro = montar(novo(ev.REMOVE, "x"), novo(ev.RETRATO, "v", V, 101), novo(ev.NOVO, "n", qtd=2),
                   novo(ev.RETRATO, "n", qtd=2), FIM)
    assert set(livro.ordens) == {"v", "n"} and livro.qualificado and not livro.suspeito
    assert livro.anomalias == {"durante_retrato_remove_desconhecida": 1, "durante_retrato_novo_ja_vivo": 1}
    assert livro.reaplicadas == {"aplicadas": 2, "fantasmas": 0, "defasadas": 0}  # "x" nunca existiu: nao e fantasma


def test_remocao_que_chega_com_a_ordem_ja_no_livro_nao_e_fantasma():
    livro = montar(novo(ev.RETRATO, "x"), novo(ev.REMOVE, "x"), FIM)
    assert not livro.ordens and livro.qualificado and not livro.anomalias
    assert livro.reaplicadas == {"aplicadas": 1, "fantasmas": 0, "defasadas": 0}


def test_alteracao_que_chega_antes_do_retrato_da_ordem_ficaria_defasada():
    # na hora: ALTERA cria "c" com 5 e o retrato a sobrescreve com 9 (estado anterior a alteracao)
    livro = montar(novo(ev.ALTERA, "c", qtd=5), novo(ev.RETRATO, "c", qtd=9), FIM)
    assert livro.ordens["c"].quantidade == 5 and livro.qualificado
    assert livro.anomalias == {} and livro.reaplicadas == {"aplicadas": 1, "fantasmas": 0, "defasadas": 1}


def test_sem_pendentes_a_conta_do_defeito_e_zero_e_so_existe_depois_do_fim():
    livro = montar(novo(ev.RETRATO, "a"))
    assert livro.resumo()["reaplicadas"] == {}
    livro.aplicar(FIM)
    assert livro.resumo()["reaplicadas"] == {"aplicadas": 0, "fantasmas": 0, "defasadas": 0}
    livro.aplicar(novo(ev.REMOVE, "a"))  # depois do fim nada e emulado: a remocao vale na hora e a conta nao muda
    assert livro.reaplicadas == {"aplicadas": 0, "fantasmas": 0, "defasadas": 0} and "a" not in livro.ordens


def test_atualizacoes_demais_antes_do_fim_deixam_o_livro_suspeito():
    livro = Livro(limite_pendentes=2)
    for i in range(3):
        livro.aplicar(novo(ev.NOVO, f"n{i}"))
    livro.aplicar(FIM)
    assert livro.suspeito and not livro.qualificado and livro.anomalias == {"retrato_pendentes_demais": 1}
    assert set(livro.ordens) == {"n0", "n1"}


def test_id_repetido_no_retrato_e_contado_e_a_ultima_versao_vale():
    livro = montar(novo(ev.RETRATO, "a", qtd=1), novo(ev.RETRATO, "a", qtd=4), FIM)
    assert livro.anomalias == {"durante_retrato_novo_ja_vivo": 1} and livro.ordens["a"].quantidade == 4
    assert livro.niveis(C, 5) == [(100, 4, 1)] and livro.qualificado


def test_alteracao_desconhecida_entra_no_livro_e_novo_repetido_substitui():
    livro = montar(novo(ev.RETRATO, "a", qtd=5), FIM, novo(ev.ALTERA, "y", qtd=2, preco=99), novo(ev.NOVO, "a", qtd=7))
    assert livro.ordens["y"].quantidade == 2 and livro.ordens["a"].quantidade == 7
    assert livro.niveis(C, 5) == [(100, 7, 1), (99, 2, 1)]


def test_quantidade_zero_numa_alteracao_tira_a_ordem():
    livro = montar(novo(ev.RETRATO, "a", qtd=5), novo(ev.RETRATO, "b", qtd=1), FIM, novo(ev.ALTERA, "a", qtd=0))
    assert set(livro.ordens) == {"b"} and livro.niveis(C, 5) == [(100, 1, 1)]


def test_cruzado_tira_a_qualificacao_so_enquanto_dura():
    livro = montar(novo(ev.RETRATO, "c", C, 100), novo(ev.RETRATO, "v", V, 101), FIM)
    livro.aplicar(novo(ev.NOVO, "x", C, 101))
    assert livro.cruzado and not livro.qualificado and livro.anomalias["cruzado"] == 1
    livro.aplicar(novo(ev.REMOVE, "x", C, 101))
    assert not livro.cruzado and livro.qualificado and not livro.suspeito


def test_cruzado_so_no_meio_do_quadro_nao_tira_a_qualificacao():
    livro = montar(novo(ev.RETRATO, "c", C, 100), novo(ev.RETRATO, "v", V, 101), FIM)
    livro.aplicar_quadro([novo(ev.NOVO, "x", C, 101), novo(ev.REMOVE, "v", V, 101)])  # agressor entra antes da baixa
    assert livro.qualificado and not livro.cruzado
    assert livro.anomalias == {"cruzado_dentro_do_quadro": 1}
    livro.aplicar_quadro([novo(ev.NOVO, "w", V, 100), novo(ev.NOVO, "y", V, 102)])  # termina cruzado
    assert livro.cruzado and not livro.qualificado
    assert livro.anomalias == {"cruzado_dentro_do_quadro": 2, "cruzado": 1}


def test_captura_e_aplicada_quadro_a_quadro(tmp_path):
    from qhos2.gravador import Gravador
    quadros = [(116, si.quadro_retrato("C", 25.0, [(1, 1, "c")])), (116, si.quadro_retrato("V", 25.25, [(1, 2, "v")])),
               (116, si.quadro_retrato_fim()),
               (160, si.quadro_livro([(1, "C", 25.25, 1, 3, "x"), (3, "V", 25.25, 0, 2, "v")], si.INICIO_NS)),
               (160, si.quadro_livro([(1, "V", 25.0, 1, 4, "w")], si.INICIO_NS))]
    gravador = Gravador(tmp_path, identidade={"system_name": si.SISTEMA, "requested_symbol": "X", "exchange": "Y"})
    for i, (template, dados) in enumerate(quadros):
        gravador(dados, template, si.INICIO_NS + i)
    gravador.fechar({"referencia": {"min_qprice_change": 0.25}}, fechamento_normal=True)
    livro = lv.livro_da_captura(gravador.pasta)
    assert livro.anomalias == {"cruzado_dentro_do_quadro": 1, "cruzado": 1} and livro.cruzado
    assert livro.eventos == 6


def test_negocio_nao_muda_o_livro_e_passiva_fora_so_e_contada():
    livro = montar(novo(ev.RETRATO, "a"), FIM)
    antes = livro.assinatura()
    for ordem, retrato in (("a", False), ("z", False), ("z", True), (None, False)):
        livro.aplicar(Evento(0, 0, 0, ev.NEGOCIO, V, 100, 1, ordem, retrato=retrato))
    livro.aplicar(Evento(0, 0, 0, ev.FIM_EVENTO))
    assert livro.assinatura() == antes and livro.negocios == 4 and livro.qualificado
    assert livro.anomalias == {"negocio_com_passiva_fora_do_livro": 1}


def test_nivel_some_quando_a_ultima_ordem_sai():
    livro = montar(novo(ev.RETRATO, "a", preco=100), novo(ev.RETRATO, "b", preco=98), FIM)
    livro.aplicar(novo(ev.REMOVE, "a"))
    assert livro.niveis(C, 5) == [(98, 1, 1)] and livro.melhor(C) == 98
    livro.aplicar(novo(ev.REMOVE, "b"))
    assert livro.niveis(C, 5) == [] and livro.melhor(C) is None


# ---- comparacao de livros ---------------------------------------------------------------------------

def test_diferencas_entre_livros():
    a = montar(novo(ev.RETRATO, "a", qtd=1, prio=1), novo(ev.RETRATO, "b", qtd=2, prio=2),
               novo(ev.RETRATO, "c", preco=100, prio=3), novo(ev.RETRATO, "d", prio=4), FIM).canonico()
    b = montar(novo(ev.RETRATO, "a", qtd=1, prio=1), novo(ev.RETRATO, "b", qtd=3, prio=2),
               novo(ev.RETRATO, "c", preco=99, prio=3), novo(ev.RETRATO, "d", prio=9), novo(ev.RETRATO, "e"), FIM).canonico()
    assert lv.diferencas(a, a) == {"so_no_primeiro": 0, "so_no_segundo": 0, "lado_ou_preco_diferente": 0,
                                   "prioridade_diferente": 0, "quantidade_diferente": 0}
    assert lv.diferencas(a, b) == {"so_no_primeiro": 0, "so_no_segundo": 1, "lado_ou_preco_diferente": 1,
                                   "prioridade_diferente": 1, "quantidade_diferente": 1}


def test_assinatura_muda_com_qualquer_ordem():
    a = montar(novo(ev.RETRATO, "a"), FIM)
    b = montar(novo(ev.RETRATO, "a", qtd=2), FIM)
    assert a.assinatura() != b.assinatura() and a.assinatura() == montar(novo(ev.RETRATO, "a"), FIM).assinatura()


# ---- CLI --------------------------------------------------------------------------------------------

def test_cli_resume_sem_preco_nem_id(tmp_path, capsys):
    pasta = si.gravar(si.gerar(2, 300, base=43210), tmp_path, captura_id="sint")
    assert lv.main([str(pasta), str(pasta / "manifest.json")]) == 0
    saida = capsys.readouterr().out
    assert "captura=sint fase=PRONTO qualificado=True" in saida and "anomalias={}" in saida
    assert "reaplicadas={'aplicadas': 0, 'defasadas': 0, 'fantasmas': 0}" in saida
    assert "4321" not in saida and "S1" not in saida and "O1" not in saida


def test_cli_recusa_captura_nao_verificada(tmp_path, capsys):
    pasta = si.gravar(si.gerar(2, 10), tmp_path, captura_id="ruim")
    (pasta / "manifest.json").unlink()
    assert lv.main([str(pasta)]) == 2
    assert "recusada: ruim: captura_nao_verificada" in capsys.readouterr().out
