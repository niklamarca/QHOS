"""Etapa 3, passo 9 (minimo da etapa 5): silencio e atraso crescente com a sessao viva; livro relido no fechamento."""
import json
from datetime import datetime

import pytest

from qhos2 import gravador
from qhos2 import leitura as le
from qhos2 import mercado as m
from qhos2 import sintetico as si
from qhos2 import vigia as vg
from qhos2.gravador import Gravador
from qhos2.livro import livro_da_captura

S = 10 ** 9
MS = 10 ** 6
MIN = 60 * S
T0 = si.INICIO_NS                 # multiplo de 60 s: os minutos construidos aqui coincidem com os minutos do relogio
IDENTIDADE = {"system_name": si.SISTEMA, "requested_symbol": "SINTETICO", "exchange": "QHOS2"}
STATUS = {"estado": "FECHADA", "erro": None, "logout_confirmado": True, "heartbeats_confirmados": 12,
          "periodo_heartbeat_s": 10.0, "referencia": {"min_qprice_change": 0.25}, "anomalias": {}}
_FIM_NS = [T0]


@pytest.fixture(autouse=True)
def manifesto_logo_depois_do_ultimo_quadro(monkeypatch):
    """O gravador carimba o manifesto com 'agora'; aqui 'agora' e 1 s depois do ultimo quadro gravado, para que o
    fim da sessao nao apareca como silencio final (o teste que quer esse silencio move _FIM_NS antes de fechar)."""
    original = Gravador.__call__

    def chamar(self, dados, template, recebido_ns):
        original(self, dados, template, recebido_ns)
        _FIM_NS[0] = recebido_ns + S

    class _Agora(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(_FIM_NS[0] / 1e9, tz)

    monkeypatch.setattr(Gravador, "__call__", chamar)
    monkeypatch.setattr(gravador, "datetime", _Agora)
    _FIM_NS[0] = T0


def quadros_com_atraso(atrasos_ns, *, inicio=T0, por_minuto=10, especiais=None):
    """Retrato de um nivel por lado e, por minuto, `por_minuto` ordens novas de compra abaixo do melhor, cada uma com o
    carimbo do servidor `atraso` antes da chegada. `especiais={(minuto, j): entrada}` troca a entrada daquele quadro.
    Devolve (template, bytes, recebido_ns) na ordem."""
    saida = [(116, si.quadro_retrato("C", 100.0, [(1, 1, "C1")]), inicio),
             (116, si.quadro_retrato("V", 100.25, [(1, 2, "V1")]), inicio + 1),
             (116, si.quadro_retrato_fim(), inicio + 2)]
    passo = MIN // (por_minuto + 1) // 1000 * 1000       # alinhado ao microssegundo: o carimbo (ssboe + usecs) sai exato
    n = 3
    for k, atraso in enumerate(atrasos_ns):
        for j in range(por_minuto):
            recebido = inicio + k * MIN + (j + 1) * passo
            entrada = (especiais or {}).get((k, j), (m.NOVO, "C", 99.75, 1, 100 + n, f"N{n}"))
            saida.append((160, si.quadro_livro([entrada], recebido - atraso), recebido))
            n += 1
    return saida


ALTERA_DESCONHECIDA = (m.ALTERACAO, "C", 99.75, 1, 900, "X_nunca_vista")


def gravar(raiz, quadros, *, captura_id="dia", status=STATUS, fechar=True, fechamento_normal=True, fim_ns=None):
    grav = Gravador(raiz / "capturas", identidade=IDENTIDADE, captura_id=captura_id)
    for template, dados, recebido in quadros:
        grav(dados, template, recebido)
    if fim_ns is not None:
        _FIM_NS[0] = fim_ns
    if fechar:
        grav.fechar(status, fechamento_normal=fechamento_normal)
    return grav


def vigia_de(pasta, relogio, diario=None):
    leitor = le.Leitor(pasta, tick=0.25, simbolo="SINTETICO", bolsa="QHOS2", relogio=relogio)
    return vg.Vigia(leitor, diario=diario, relogio=relogio, avisar=lambda _: None)


def eventos(vigia):
    return [e["evento"] for e in vigia.eventos]


# ---- silencio -------------------------------------------------------------------------------------------

def test_silencio_com_a_sessao_viva_e_a_lacuna_quando_os_quadros_voltam(tmp_path):
    quadros = quadros_com_atraso([75 * MS] * 3)
    minuto0 = [q for q in quadros if q[2] < T0 + MIN]
    minuto2 = [q for q in quadros if q[2] >= T0 + 2 * MIN]
    ultimo0 = minuto0[-1][2]
    grav = gravar(tmp_path, minuto0, fechar=False)
    agora = [ultimo0 + S]
    vigia = vigia_de(grav.pasta, lambda: agora[0], diario=tmp_path / "dia.jsonl")
    vigia.passo()
    assert eventos(vigia) == ["livro_valido"] and vigia.silencio is None
    agora[0] = ultimo0 + vg.SILENCIO_NS + S                     # 61 s sem quadro, sem manifesto: silencio com a sessao viva
    vigia.passo()
    assert vigia.silencio == {"desde_ns": ultimo0, "detectado_ns": agora[0], "quadros": len(minuto0)}
    assert vigia.eventos[-1]["evento"] == "silencio"
    vigia.passo()
    assert eventos(vigia) == ["livro_valido", "silencio"]        # nao repete enquanto dura
    for template, dados, recebido in minuto2:
        grav(dados, template, recebido)
    agora[0] = minuto2[-1][2] + S
    vigia.passo()
    lacuna = vigia.eventos[-1]
    assert (lacuna["evento"], lacuna["desde_ns"], lacuna["ate_ns"], lacuna["vista_ao_vivo"]) == ("lacuna", ultimo0, minuto2[0][2], True)
    assert lacuna["duracao_s"] == round((minuto2[0][2] - ultimo0) / 1e9, 3) and vigia.silencio is None
    grav.fechar(STATUS, fechamento_normal=True)
    vigia.passo()
    r = vigia.fechamento
    assert r["verificada"] and r["achados"] == ["lacuna"] and r["igual_ao_vivo"] is True and r["silencio_final"] is None
    assert r["releitura"]["lacunas"] == [{"desde_ns": ultimo0, "ate_ns": minuto2[0][2], "quadro": len(minuto0),
                                          "duracao_s": lacuna["duracao_s"], "sessao_viva": True}]
    assert r["ao_vivo"]["lacunas_coincidem"] and r["ao_vivo"]["silencios"] == 1 and r["ao_vivo"]["lacunas"] == [lacuna]
    assert r["sessao"]["encerrada_normalmente"] and r["sessao"]["heartbeats_confirmados"] == 12
    diario = [json.loads(linha) for linha in (tmp_path / "dia.jsonl").read_text().splitlines()]
    assert [d["evento"] for d in diario] == ["livro_valido", "silencio", "lacuna", "captura_encerrada", "fechamento"]
    assert all(d["esquema"] == vg.ESQUEMA and d["captura_id"] == "dia" for d in diario)
    assert json.loads((tmp_path / "dia.fechamento.json").read_text()) == r


def test_silencio_nao_dispara_com_a_sessao_encerrada_nem_com_a_leitura_atrasada(tmp_path):
    quadros = quadros_com_atraso([75 * MS])
    ultimo = quadros[-1][2]
    pasta = gravar(tmp_path, quadros, captura_id="encerrada").pasta          # manifesto: a sessao ja terminou
    vigia = vigia_de(pasta, lambda: ultimo + 120 * S)
    vigia.passo()
    assert vigia.fechamento is not None and eventos(vigia) == ["livro_valido", "captura_encerrada", "fechamento"]
    assert vigia.fechamento["achados"] == [] and vigia.fechamento["ao_vivo"]["silencios"] == 0
    grav = gravar(tmp_path, quadros, captura_id="atrasada", fechar=False)   # sem manifesto, leitura atras da gravacao
    vigia = vigia_de(grav.pasta, lambda: ultimo + 120 * S)
    vigia.passo(max_bytes=200)
    assert vigia.leitor.resumo()["pendente_bytes"] > 0 and vigia.silencio is None
    while vigia.passo():
        pass
    assert vigia.silencio is not None and vigia.silencio["desde_ns"] == ultimo   # alcancou: agora o silencio e real


def test_silencio_do_arquivo_sem_lacuna_no_dado_e_a_gravacao_que_parou(tmp_path):
    quadros = quadros_com_atraso([75 * MS])
    grav = gravar(tmp_path, quadros[:8], fechar=False)
    agora = [quadros[7][2] + vg.SILENCIO_NS + S]
    vigia = vigia_de(grav.pasta, lambda: agora[0])
    vigia.passo()
    assert eventos(vigia) == ["livro_valido", "silencio"]
    for template, dados, recebido in quadros[8:]:            # o dado segue 5 s depois do anterior: nao ha lacuna nele
        grav(dados, template, recebido)
    agora[0] = quadros[-1][2] + S
    vigia.passo()
    assert eventos(vigia)[-1] == "silencio_sem_lacuna" and vigia.eventos[-1]["desde_ns"] == quadros[7][2]
    grav.fechar(STATUS, fechamento_normal=True)
    vigia.passo()
    r = vigia.fechamento
    assert r["achados"] == [] and r["ao_vivo"]["silencios"] == 1 and r["ao_vivo"]["lacunas"] == []
    assert r["ao_vivo"]["lacunas_coincidem"] is True


def test_lacuna_vista_depois_quando_a_vigia_chega_atrasada(tmp_path):
    quadros = [q for q in quadros_com_atraso([75 * MS] * 3) if not T0 + MIN <= q[2] < T0 + 2 * MIN]
    grav = gravar(tmp_path, quadros, fechar=False)
    vigia = vigia_de(grav.pasta, lambda: quadros[-1][2] + S)
    while vigia.passo(max_bytes=300):
        pass
    lacunas = [e for e in vigia.eventos if e["evento"] == "lacuna"]
    assert len(lacunas) == 1 and lacunas[0]["vista_ao_vivo"] is False and "silencio" not in eventos(vigia)


# ---- atraso crescente -----------------------------------------------------------------------------------

@pytest.mark.parametrize("atrasos_s, rampas", [
    ([0.075, 0.075, 0.6, 1.2, 1.8, 1.8], [[0.075, 0.6, 1.2, 1.8]]),   # tres degraus de >= 0,5 s, o ultimo acima de 1 s
    ([3.0] * 6, []),                                                   # desvio constante do relogio: nao sobe
    ([0.1, 0.3, 0.5, 0.7, 0.9, 1.1, 1.3], []),                         # sobe devagar: degraus abaixo de 0,5 s
    ([0.1, 0.6, 1.1], []),                                             # so duas subidas
    ([0.1, 0.6, 1.1, 1.6], [[0.1, 0.6, 1.1, 1.6]]),                    # exatamente tres degraus de 0,5 s
    ([0.0, 0.5, 1.0, 1.5, 0.2, 0.7, 1.2, 1.7], [[0.0, 0.5, 1.0, 1.5], [0.2, 0.7, 1.2, 1.7]]),   # duas rampas
])
def test_atraso_crescente_so_com_tres_degraus_de_meio_segundo_acima_de_1s(tmp_path, atrasos_s, rampas):
    quadros = quadros_com_atraso([int(a * S) for a in atrasos_s])
    r = vg.fechamento(gravar(tmp_path, quadros).pasta)
    rel = r["releitura"]
    assert [[mediana for _, mediana, _ in rampa] for rampa in rel["atraso_crescente"]] == [[a * 1000 for a in rampa] for rampa in rampas]
    assert [mediana for _, mediana, _ in rel["atraso_por_minuto_ms"]] == [a * 1000 for a in atrasos_s]
    assert all(n == 10 for _, _, n in rel["atraso_por_minuto_ms"])          # o retrato, sem carimbo, nao conta
    assert rel["atraso_maximo_ms"] == max(atrasos_s) * 1000
    assert ("atraso_crescente" in r["achados"]) == bool(rampas)


def test_atraso_crescente_ao_vivo_dispara_uma_vez_quando_o_minuto_fecha(tmp_path):
    quadros = quadros_com_atraso([int(a * S) for a in (0.075, 0.075, 0.6, 1.2, 1.8, 1.8)])
    grav = gravar(tmp_path, quadros, fechar=False)
    vigia = vigia_de(grav.pasta, lambda: quadros[-1][2] + S)
    while vigia.passo(max_bytes=300):
        pass
    rampas = [e for e in vigia.eventos if e["evento"] == "atraso_crescente"]
    assert len(rampas) == 1 and [mediana for _, mediana, _ in rampas[0]["minutos"]] == [75.0, 600.0, 1200.0, 1800.0]
    grav.fechar(STATUS, fechamento_normal=True)
    vigia.passo()
    r = vigia.fechamento
    assert r["achados"] == ["atraso_crescente"] and r["ao_vivo"]["atraso_crescente"] == 1
    assert r["releitura"]["atraso_crescente"] == [rampas[0]["minutos"]]


# ---- livro ----------------------------------------------------------------------------------------------

def test_livro_que_deixa_de_valer_e_registrado_no_instante_exato(tmp_path):
    quadros = quadros_com_atraso([75 * MS] * 2, especiais={(1, 3): ALTERA_DESCONHECIDA})
    estrago = [q for q in quadros if q[0] == 160][13][2]
    grav = gravar(tmp_path, quadros, fechar=False)
    vigia = vigia_de(grav.pasta, lambda: quadros[-1][2] + S)
    while vigia.passo(max_bytes=300):              # blocos pequenos: o instante nao depende do bloco lido
        pass
    valido, invalido = vigia.eventos
    assert (valido["evento"], valido["instante_ns"], valido["quadro"]) == ("livro_valido", quadros[2][2], 2)
    assert (invalido["evento"], invalido["instante_ns"], invalido["suspeito"], invalido["cruzado"]) == ("livro_invalido", estrago, True, False)
    assert invalido["anomalias"] == {"altera_desconhecida": 1}
    grav.fechar(STATUS, fechamento_normal=True)
    vigia.passo()
    r = vigia.fechamento
    rel = r["releitura"]
    assert r["achados"] == ["livro_invalido"] and r["igual_ao_vivo"] is True
    assert rel["primeiro_invalido_ns"] == rel["suspeito_desde_ns"] == estrago
    assert (rel["quadros_depois_do_retrato"], rel["quadros_qualificados"]) == (21, 14) and rel["fracao_qualificada"] == round(14 / 21, 4)
    assert (vigia.contagens["quadros_depois_do_retrato"], vigia.contagens["quadros_qualificados"]) == (21, 14)
    assert not rel["livro"]["qualificado"] and rel["livro"]["suspeito"]


def test_livro_cruzado_vai_e_volta_e_alem_do_teto_as_transicoes_so_contam(tmp_path, monkeypatch):
    monkeypatch.setattr(vg, "MAX_TRANSICOES", 1)
    cruza = {(0, 2): (m.NOVO, "C", 100.5, 1, 901, "CRUZA1"), (0, 3): (m.REMOCAO, "C", 100.5, 0, 901, "CRUZA1"),
             (0, 5): (m.NOVO, "C", 100.5, 1, 902, "CRUZA2"), (0, 6): (m.REMOCAO, "C", 100.5, 0, 902, "CRUZA2")}
    quadros = quadros_com_atraso([75 * MS], especiais=cruza)
    grav = gravar(tmp_path, quadros, fechar=False)
    vigia = vigia_de(grav.pasta, lambda: quadros[-1][2] + S)
    while vigia.passo(max_bytes=300):
        pass
    assert eventos(vigia) == ["livro_valido", "livro_invalido"]           # a segunda ida e volta so conta
    assert vigia.eventos[1]["cruzado"] is True and vigia.eventos[1]["suspeito"] is False
    assert (vigia.contagens["livro_valido"], vigia.contagens["livro_invalido"]) == (3, 2)
    grav.fechar(STATUS, fechamento_normal=True)
    vigia.passo()
    rel = vigia.fechamento["releitura"]
    assert rel["primeiro_invalido_ns"] == quadros[3 + 2][2] and rel["suspeito_desde_ns"] is None
    assert (rel["quadros_depois_do_retrato"], rel["quadros_qualificados"]) == (11, 9) and rel["livro"]["qualificado"]
    assert vigia.fechamento["achados"] == ["livro_invalido"] and vigia.fechamento["ao_vivo"]["livro_invalido"] == 2


# ---- fechamento -----------------------------------------------------------------------------------------

def test_fechamento_rele_do_zero_e_confere_o_livro_seguido_ao_vivo(tmp_path):
    cenario = si.gerar(7, 400)
    grav = Gravador(tmp_path / "capturas", identidade=IDENTIDADE, captura_id="viva")
    vigia = vigia_de(grav.pasta, lambda: T0 + len(cenario.quadros) * si.PASSO_NS)
    for i, (template, dados) in enumerate(cenario.quadros):
        grav(dados, template, T0 + i * si.PASSO_NS)
        if i % 7 == 0:
            vigia.passo(max_bytes=900)
    grav.fechar(STATUS, fechamento_normal=True)
    voltas = []
    r = vigia.seguir(dormir=lambda _: None, parar=lambda: voltas.append(1) or len(voltas) > 1000)
    assert r is vigia.fechamento and r["verificada"] and r["igual_ao_vivo"] is True and r["achados"] == []
    assinatura = livro_da_captura(grav.pasta).assinatura()
    assert r["releitura"]["assinatura"] == assinatura == vigia.livro.assinatura() == vigia.leitor.livro.assinatura()
    rel = r["releitura"]
    assert rel["lacunas"] == [] and rel["atraso_crescente"] == [] and rel["livro"]["qualificado"]
    assert rel["fracao_qualificada"] == 1.0 and rel["primeiro_invalido_ns"] is None and rel["atraso_mediana_ms"] == 75.0
    assert r["quadros"] == len(cenario.quadros) and r["ao_vivo"]["contagens"]["quadros"] == len(cenario.quadros)
    assert r["silencio_final"] is None and r["sessao"]["heartbeats_esperados_aprox"] == 0


def test_silencio_final_e_classificado_pela_sessao(tmp_path):
    quadros = quadros_com_atraso([75 * MS])
    fim = quadros[-1][2] + 120 * S
    viva = vg.fechamento(gravar(tmp_path, quadros, captura_id="viva", fim_ns=fim).pasta)
    assert viva["achados"] == ["silencio_final"] and viva["silencio_final"]["sessao_viva"] is True
    fim_gravado = fim // S * S                                   # o manifesto guarda o instante em segundos inteiros
    assert (viva["silencio_final"]["desde_ns"], viva["silencio_final"]["ate_ns"]) == (quadros[-1][2], fim_gravado)
    assert viva["silencio_final"]["duracao_s"] == round((fim_gravado - quadros[-1][2]) / 1e9, 3)
    caida = {**STATUS, "estado": "FALHOU", "erro": "heartbeat_sem_resposta", "tipo_de_falha": "TRANSIENT_TRANSPORT",
             "logout_confirmado": False}
    r = vg.fechamento(gravar(tmp_path, quadros, captura_id="caida", status=caida, fechamento_normal=False, fim_ns=fim).pasta)
    assert r["verificada"] and r["achados"] == ["sessao_caiu", "releitura_recusada", "silencio_final"]
    assert r["releitura"] == {"recusada": "captura_encerrada_com_falha"}
    assert r["sessao"]["encerrada_normalmente"] is False and r["sessao"]["erro"] == "heartbeat_sem_resposta"
    assert r["silencio_final"]["sessao_viva"] is False and "ao_vivo" not in r


def test_seguir_escreve_o_diario_ate_o_fechamento(tmp_path):
    quadros = quadros_com_atraso([75 * MS] * 2)
    grav = gravar(tmp_path, quadros[:5], fechar=False)
    restantes = list(quadros[5:])
    agora = [quadros[4][2] + S]

    def escrever(_):                 # a gravacao segue e o relogio acompanha o ultimo quadro, como ao vivo
        if restantes:
            template, dados, recebido = restantes.pop(0)
            grav(dados, template, recebido)
            agora[0] = recebido + S
        else:
            grav.fechar(STATUS, fechamento_normal=True)

    (tmp_path / "vigia").mkdir()
    diario = tmp_path / "vigia" / "dia.jsonl"
    vigia = vigia_de(grav.pasta, lambda: agora[0], diario=diario)
    r = vigia.seguir(dormir=escrever)
    assert r["achados"] == [] and r["igual_ao_vivo"] is True and not restantes
    linhas = [json.loads(linha) for linha in diario.read_text().splitlines()]
    assert [linha["evento"] for linha in linhas] == ["livro_valido", "captura_encerrada", "fechamento"]
    assert linhas[-1]["achados"] == [] and linhas[-1]["igual_ao_vivo"] is True
    assert json.loads((tmp_path / "vigia" / "dia.fechamento.json").read_text()) == r


def test_leitura_que_falha_registra_e_o_fechamento_e_recusado(tmp_path):
    quadros = quadros_com_atraso([75 * MS])
    grav = gravar(tmp_path, quadros, fechar=False)
    with open(grav.pasta / "market_frames.jsonl", "ab") as arquivo:
        arquivo.write(b'{"index": 13, "template_id": 160}\n')          # linha sem received_ns
    vigia = vigia_de(grav.pasta, lambda: quadros[-1][2] + S)
    while vigia.fechamento is None:
        vigia.passo()
    assert eventos(vigia) == ["livro_valido", "leitura_falhou", "fechamento"]
    assert vigia.eventos[1]["codigo"] == "linha_invalida" and vigia.eventos[1]["quadros"] == len(quadros)
    assert vigia.fechamento["achados"] == ["captura_nao_verificada"] and "manifesto_ausente" in vigia.fechamento["divergencias"]


# ---- CLI ------------------------------------------------------------------------------------------------

def test_cli_captura_encerrada_faz_o_fechamento_e_escreve_o_diario(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    pasta = gravar(tmp_path / "dados", quadros_com_atraso([75 * MS]), captura_id="cli").pasta
    assert vg.main(["--captura", str(pasta)]) == 0
    saida = capsys.readouterr().out
    assert "fechamento cli: verificada=True achados=nenhum" in saida and "livro relido: fase=PRONTO qualificado=True" in saida
    assert vg.main(["--captura", str(pasta), "--json"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["esquema"] == vg.ESQUEMA_FECHAMENTO and r["captura_id"] == "cli" and "ao_vivo" not in r
    diario = tmp_path / "dados" / "vigia"
    assert json.loads((diario / "cli.fechamento.json").read_text()) == r
    assert [json.loads(linha)["evento"] for linha in (diario / "cli.jsonl").read_text().splitlines()] == ["fechamento", "fechamento"]
    suja = gravar(tmp_path / "dados", quadros_com_atraso([75 * MS] * 2, especiais={(1, 0): ALTERA_DESCONHECIDA}),
                  captura_id="suja").pasta
    assert vg.main(["--captura", str(suja)]) == 1
    assert "achados=livro_invalido" in capsys.readouterr().out


def test_cli_recusa_captura_fora_da_raiz_e_nao_verificada(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    pasta = gravar(tmp_path / "dados", quadros_com_atraso([75 * MS]), captura_id="ruim").pasta
    manifesto = pasta / "manifest.json"
    manifesto.write_text(manifesto.read_text().replace('"frames": ', '"frames": 1'))
    assert vg.main(["--captura", str(pasta)]) == 2
    assert "achados=captura_nao_verificada" in capsys.readouterr().out
    (tmp_path / "solta").mkdir()
    assert vg.main(["--captura", str(tmp_path / "solta")]) == 2
    assert capsys.readouterr().out.startswith("PARE: captura_precisa_ficar_em_raiz_capturas")
