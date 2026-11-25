"""Laco diario do coletor contra o servidor falso, com relogio real e janelas de segundos."""
import asyncio
import hashlib
import json
import threading
from datetime import date, datetime, timedelta, timezone

import pytest

from qhos2 import agenda, sintetico
from qhos2.agenda import Registro
from qhos2.config import Settings
from qhos2.operacao import GuardaCaptura

from coleta_multi import coletor, fechamento, fio
from coleta_multi.lista import QUOTE_BOARD
from coleta_multi.tests.test_sessao_multi import CRED, FRONTS_PRINT, REFS_PRINT, Servidor

GIB = 1024 ** 3
HOJE = datetime.now(agenda.NY).date().isoformat()
AVISOS = []


@pytest.fixture(autouse=True)
def _sem_discord(monkeypatch):
    AVISOS.clear()
    monkeypatch.setattr(coletor.aviso, "avisar", lambda *a, **k: pytest.fail("teste tentou o Discord de verdade"))


def _calendario(segundos=3.0, dias=1, comeca_em=0.5):
    agora = datetime.now(timezone.utc)
    janelas = []
    for i in range(dias):
        ini = agora + timedelta(days=i, seconds=comeca_em)
        janelas.append({"dia": (ini.astimezone(agenda.NY).date()).isoformat(), "inicio": agenda._iso(ini),
                        "fim": agenda._iso(ini + timedelta(seconds=segundos + 1))})
    return {"schema": "qhos2_calendario_v1", "fonte": "teste", "valido_de": agenda._iso(agora - timedelta(days=1)),
            "valido_ate": agenda._iso(agora + timedelta(days=dias + 1)), "janelas": janelas}


def _ambiente(tmp_path, estado_es="AGUARDANDO", com_prova=True):
    es = tmp_path / "es"
    (es / "agenda").mkdir(parents=True)
    (es / "agenda" / "estado.json").write_text(json.dumps({"estado": estado_es}))
    pasta = sintetico.gravar(sintetico.gerar(3, 100), es / "capturas", captura_id="es0001")
    if com_prova:
        (es / "agenda" / "provas.jsonl").write_text(json.dumps({"dia": HOJE, "resultado": "capturado",
                                                                 "captura": pasta.name}) + "\n")
    return es


def _rodar(tmp_path, servidor, calendario, es, pasta_registro="registro", **kw):
    registro = Registro(tmp_path / pasta_registro)
    opcoes = dict(settings=Settings(data_root=tmp_path / "dados"), staging=tmp_path / "staging",
                  destino=tmp_path / "DataSet", estado_es=es / "agenda" / "estado.json",
                  provas_es=es / "agenda" / "provas.jsonl", capturas_es=es / "capturas",
                  max_bytes_ativo=GIB, max_bytes_global=GIB, reserva_disco_bytes=0, guarda_raiz=tmp_path / "guarda",
                  atraso_entrada_s=0.0, folga_s=0.5, duracao_minima_s=0.1, vigia_es_s=0.05,
                  espera_apos_queda_s=0.2, fechamento_apos_fim_s=0.0, espera_prova_es_s=1.0, processos=2,
                  margem_troca_s=0.0, saida=lambda _: None, avisar=AVISOS.append)  # nunca o Discord nos testes
    opcoes.update(kw)
    motivo = asyncio.run(coletor.rodar_coletor(calendario, QUOTE_BOARD, servidor.fabrica(), CRED, registro, **opcoes))
    provas = [json.loads(l) for l in (registro.provas.read_text().splitlines() if registro.provas.exists() else [])]
    return motivo, provas, registro


def _resultados(provas):
    return [(p["dia"], p["resultado"]) for p in provas]


def _servidor(**kw):
    return Servidor(fronts=FRONTS_PRINT, refs=REFS_PRINT, **kw)


def _index(tmp_path, destino="DataSet"):
    return json.loads((tmp_path / destino / HOJE / "INDEX.json").read_text())


# ---- dia normal --------------------------------------------------------------------------------------------------

def test_dia_completo_chega_ao_dataset_com_o_es(tmp_path):
    es = _ambiente(tmp_path)
    motivo, provas, _ = _rodar(tmp_path, _servidor(), _calendario(), es)
    assert motivo == "calendario_esgotado"
    assert _resultados(provas) == [(f"{HOJE}#p1", "capturado"), (HOJE, "janela_encerrada"),
                                   (f"{HOJE}#fechamento", "fechado")]
    assert len(provas[0]["com_eventos"]) == 12 and provas[0]["faltando"] == []
    index = _index(tmp_path)
    assert index["resumo"]["publicados"] == 13  # 12 ativos + ES (o GEZ2 vencido fica fora, com motivo)
    por = {e["simbolo_pedido"]: e for e in index["ativos"]}
    assert por["ES"]["estado"] == "publicado" and por["GEZ2"]["motivo"] == "vencido_20221219_e_sem_front_month_GE"
    assert (tmp_path / "DataSet" / HOJE / "SHA256SUMS.txt").exists()
    assert json.loads((tmp_path / "guarda" / "estado.json").read_text())["estado"] == "LIVRE"


def test_fechamento_so_meia_hora_depois_do_fim(tmp_path):
    es = _ambiente(tmp_path)
    vistos = []
    motivo, provas, registro = _rodar(tmp_path, _servidor(), _calendario(), es, fechamento_apos_fim_s=2.0,
                                      saida=vistos.append)
    fechado = next(p for p in provas if p["resultado"] == "fechado")
    fim = datetime.fromisoformat(provas[0]["janela_fim"].replace("Z", "+00:00"))
    registrado = datetime.fromisoformat(fechado["registrado_utc"].replace("Z", "+00:00"))
    assert (registrado - fim).total_seconds() >= 1.5


def test_uma_captura_por_dia(tmp_path):
    es = _ambiente(tmp_path)
    calendario = _calendario()
    _rodar(tmp_path, _servidor(), calendario, es)
    servidor = _servidor()
    motivo, provas, _ = _rodar(tmp_path, servidor, calendario, es)
    assert servidor.logins == 0


def test_sem_prova_do_es_fecha_sem_o_es(tmp_path):
    es = _ambiente(tmp_path, com_prova=False)
    _rodar(tmp_path, _servidor(), _calendario(), es)
    es_linha = next(e for e in _index(tmp_path)["ativos"] if e["simbolo_pedido"] == "ES")
    assert es_linha["estado"] == "sem_captura" and _index(tmp_path)["resumo"]["publicados"] == 12


def test_fechamento_espera_o_es_sair_de_captura(tmp_path):
    es = _ambiente(tmp_path, estado_es="CAPTURANDO")
    # o ES segue CAPTURANDO depois da janela: o fechamento espera ate o limite e so entao fecha
    motivo, provas, _ = _rodar(tmp_path, _servidor(), _calendario(), es, espera_prova_es_s=0.0)
    assert [r for _, r in _resultados(provas)][-1] == "fechado"


def test_fechamento_que_quebra_nao_derruba_o_laco(tmp_path, monkeypatch):
    from concurrent.futures.process import BrokenProcessPool

    def quebra(*a, **k):
        raise BrokenProcessPool("teste")
    monkeypatch.setattr(coletor.fechamento, "fechar_dia", quebra)
    motivo, provas, _ = _rodar(tmp_path, _servidor(), _calendario(), _ambiente(tmp_path))
    assert motivo == "calendario_esgotado"
    assert [(r, p.get("motivo")) for p, (_, r) in zip(provas, _resultados(provas))][-1] == \
        ("fechamento_falhou", "BrokenProcessPool")
    assert sum(1 for _, r in _resultados(provas) if r == "fechamento_falhou") == 1


# ---- volta sozinho ----------------------------------------------------------------------------------------------

def test_queda_no_meio_do_pregao_retoma_o_resto_sozinho(tmp_path):
    servidor = _servidor(cair_apos=12)  # a conexao cai depois de assinar os 12 ativos
    es = _ambiente(tmp_path)
    original = servidor.reagir

    def reagir(con, dados):  # so a primeira sessao cai
        if servidor.logins >= 2:
            servidor.cair_apos = None
        original(con, dados)
    servidor.reagir = reagir
    motivo, provas, _ = _rodar(tmp_path, servidor, _calendario(segundos=6), es)
    res = _resultados(provas)
    assert res[0] == (f"{HOJE}#p1", "interrompido") and res[1] == (f"{HOJE}#p2", "capturado")
    assert servidor.logins == 2 and (HOJE, "janela_encerrada") in res
    nomes = {p.name for p in (tmp_path / "DataSet" / HOJE).iterdir()}
    assert {"CME_NQZ6.tar.xz", "CME_NQZ6_p2.tar.xz"} <= nomes
    partes = {(e["simbolo_pedido"], e.get("parte")) for e in _index(tmp_path)["ativos"]}
    assert ("NQ", "p1") in partes and ("NQ", "p2") in partes
    guarda = json.loads((tmp_path / "guarda" / "estado.json").read_text())
    historico = list((tmp_path / "guarda").glob("liberacao-*.json"))
    assert guarda["estado"] == "LIVRE" and len(historico) == 1  # liberada sozinha depois da queda, e registrada
    assert json.loads(historico[0].read_text())["estado"] == "QUARENTENA"


def test_reinicio_no_meio_do_pregao_espera_e_captura_o_resto(tmp_path):
    es = _ambiente(tmp_path)
    # o processo anterior morreu capturando: guarda EM_ANDAMENTO e uma parte p1 sem .status
    with GuardaCaptura(tmp_path / "guarda") as guarda:
        guarda.iniciar()
    (tmp_path / "staging" / HOJE / "CME_NQZ6").mkdir(parents=True)
    servidor = _servidor()
    vistos = []
    motivo, provas, _ = _rodar(tmp_path, servidor, _calendario(segundos=4), es, saida=vistos.append)
    assert servidor.logins == 1 and _resultados(provas)[0] == (f"{HOJE}#p2", "capturado")
    assert any("esperando" in v for v in vistos) and any("liberada automaticamente" in v for v in vistos)


def test_login_recusado_para_e_nao_volta_nem_depois_de_reiniciar(tmp_path):
    es = _ambiente(tmp_path)
    servidor = _servidor(rp_login="1")
    motivo, provas, registro = _rodar(tmp_path, servidor, _calendario(dias=2), es)
    assert motivo == "login_recusado" and servidor.logins == 1
    assert json.loads((registro.pasta / "estado.json").read_text())["estado"] == "QUARENTENA"
    # o aviso ao Discord sai uma vez, dizendo o que aconteceu
    assert len(AVISOS) == 1 and "LOGIN RECUSADO" in AVISOS[0] and "coletor" in AVISOS[0] and "ES nao foi tocado" in AVISOS[0]
    assert any(p["resultado"] == "aviso_discord" for p in provas)
    # "reinicio": nova partida com a mesma pasta de registro nao loga nunca mais (ate liberacao manual)
    outro = _servidor()
    motivo2, _, _ = _rodar(tmp_path, outro, _calendario(), es)
    assert motivo2 == "login_recusado" and outro.logins == 0
    assert len(AVISOS) == 1  # o reinicio nao repete o aviso


def test_logout_forcado_avisa_com_o_nome_certo(tmp_path):
    _rodar(tmp_path, _servidor(forcar_logout_apos=1), _calendario(segundos=10), _ambiente(tmp_path))
    assert len(AVISOS) == 1 and "LOGOUT FORCADO" in AVISOS[0]


def test_logout_forcado_tambem_nao_volta(tmp_path):
    servidor = _servidor(forcar_logout_apos=1)
    motivo, _, _ = _rodar(tmp_path, servidor, _calendario(segundos=10), _ambiente(tmp_path))
    assert motivo == "logout_forcado" and servidor.logins == 1


def test_parada_do_operador_nao_retoma_nem_encerra_o_dia(tmp_path):
    es = _ambiente(tmp_path)
    parar = asyncio.Event()
    sessoes = []
    threading.Timer(2.0, lambda: (parar.set(), [s.parar() for s in sessoes])).start()
    servidor = _servidor()
    motivo, provas, _ = _rodar(tmp_path, servidor, _calendario(segundos=200), es, parar=parar,
                               quando_pronta=sessoes.append)
    assert motivo == "parado_pelo_operador" and servidor.logins == 1
    assert _resultados(provas) == [(f"{HOJE}#p1", "interrompido")]  # o dia nao conta como feito: volta no reinicio


# ---- protecao do ES -----------------------------------------------------------------------------------------------

def test_es_entrando_em_quarentena_para_so_o_coletor(tmp_path):
    es = _ambiente(tmp_path, estado_es="CAPTURANDO")
    arquivo = es / "agenda" / "estado.json"
    threading.Timer(2.0, lambda: arquivo.write_text(json.dumps({"estado": "QUARENTENA"}))).start()
    servidor = _servidor()
    motivo, provas, _ = _rodar(tmp_path, servidor, _calendario(segundos=20), es, espera_prova_es_s=0.0)
    assert provas[0]["resultado"] == "interrompido_pelo_es" and provas[0]["parada"] == "es_QUARENTENA"
    assert servidor.logins == 1  # nao retoma: pode ter sido por causa dele
    assert json.loads(arquivo.read_text()) == {"estado": "QUARENTENA"}  # o coletor nunca escreve no ES


def test_es_ja_em_quarentena_nao_para_o_coletor(tmp_path):
    motivo, provas, _ = _rodar(tmp_path, _servidor(), _calendario(), _ambiente(tmp_path, estado_es="QUARENTENA"))
    assert provas[0]["resultado"] == "capturado" and provas[0]["parada"] is None


# ---- pasta selada -------------------------------------------------------------------------------------------------

def test_janela_selada_guarda_o_es_na_pasta_selada_e_libera_na_data(tmp_path, monkeypatch):
    hoje = date.fromisoformat(HOJE)
    monkeypatch.setattr(coletor, "SELADO_DE", HOJE)
    monkeypatch.setattr(coletor, "SELADO_ATE", HOJE)
    monkeypatch.setattr(coletor, "SELADO_LIBERA_EM", hoje + timedelta(days=1))
    _rodar(tmp_path, _servidor(), _calendario(), _ambiente(tmp_path))
    pasta, selada = tmp_path / "DataSet" / HOJE, tmp_path / "DataSet" / coletor.SELADO_PASTA / HOJE
    es_linha = next(e for e in _index(tmp_path)["ativos"] if e["simbolo_pedido"] == "ES")
    assert es_linha["estado"] == "selado" and "quadros" not in es_linha and "sha256_xz" not in es_linha
    assert not (pasta / "QHOS2_SINTETICO.tar.xz").exists() and (selada / "QHOS2_SINTETICO.tar.xz").exists()
    assert json.loads((selada / "INDEX.json").read_text())["ativos"][0]["estado"] == "publicado"
    r = fechamento.liberar_selados(tmp_path / "DataSet", tmp_path / "DataSet" / coletor.SELADO_PASTA,
                                   hoje + timedelta(days=1), hoje + timedelta(days=1))
    assert r == [(HOJE, "liberado")] and not selada.exists()
    index = _index(tmp_path)
    linhas_es = [e for e in index["ativos"] if e["simbolo_pedido"] == "ES"]
    assert len(linhas_es) == 1 and linhas_es[0]["estado"] == "publicado" and index["resumo"]["publicados"] == 13
    for linha in (pasta / "SHA256SUMS.txt").read_text().splitlines():
        sha, nome = linha.split("  ")
        assert hashlib.sha256((pasta / nome).read_bytes()).hexdigest() == sha


def test_liberacao_retoma_depois_de_queda_e_nunca_sobrescreve(tmp_path, monkeypatch):
    hoje = date.fromisoformat(HOJE)
    monkeypatch.setattr(coletor, "SELADO_DE", HOJE)
    monkeypatch.setattr(coletor, "SELADO_ATE", HOJE)
    monkeypatch.setattr(coletor, "SELADO_LIBERA_EM", hoje + timedelta(days=30))
    _rodar(tmp_path, _servidor(), _calendario(), _ambiente(tmp_path))
    selada = tmp_path / "DataSet" / coletor.SELADO_PASTA
    (selada / HOJE / "QHOS2_SINTETICO.tar.xz").rename(tmp_path / "DataSet" / HOJE / "QHOS2_SINTETICO.tar.xz")
    assert fechamento.liberar_selados(tmp_path / "DataSet", selada, hoje, hoje) == [(HOJE, "liberado")]
    assert "QHOS2_SINTETICO.tar.xz" in (tmp_path / "DataSet" / HOJE / "SHA256SUMS.txt").read_text()


# ---- reserva do ES (decisao do autor, 30/10) -----------------------------------------------------------------------

def _servidor_com_es(**kw):
    return Servidor(fronts={**FRONTS_PRINT, ("ES", "CME"): "ESZ6"}, refs={**REFS_PRINT, ("ESZ6", "CME"): "20261218"},
                    **kw)


def _prova_do_es(es, resultado):
    (es / "agenda" / "provas.jsonl").write_text(json.dumps({"dia": HOJE, "resultado": resultado,
                                                             "captura": "es0001"}) + "\n")


def _assinaturas_do_es(servidor):
    """Em quantas sessoes (conexoes) o ES foi assinado: pedido 117 de MBO com o ESZ6 (assinar e cancelar contam 1)."""
    return sum(1 for con in servidor.conexoes
               if any(fio.ler_rapido(d)[0] == 117 and "ESZ6" in fio.ler_rapido(d)[1] for d in con.enviados))


INICIO = datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc)
DIA_R = "2026-11-02"


@pytest.mark.parametrize("estado, prova, minutos, esperado", [
    ("CAPTURANDO", None, 60, None),                              # o ES oficial esta gravando: nunca
    ("CAPTURANDO", "interrompido", 60, None),
    ("AGUARDANDO", "interrompido", 60, "es_interrompido"),       # caiu no meio do pregao
    ("AGUARDANDO", "pulado_partida_tardia", 60, "es_pulado_partida_tardia"),  # reinicio no meio do pregao
    ("AGUARDANDO", "pulado_guarda", 60, "es_pulado_guarda"),     # guarda em quarentena nao liberada
    ("AGUARDANDO", "falhou_antes_do_login", 60, "es_falhou_antes_do_login"),
    ("AGUARDANDO", "capturado", 60, None),
    ("AGUARDANDO", "pulado_sem_espaco", 60, None),               # nao disputa disco com o ES
    ("QUARENTENA", "interrompido", 60, None),                    # login recusado ou logout forcado: para tudo
    ("AGUARDANDO", None, 10, None),                              # o agendador ainda pode comecar o dia
    ("AGUARDANDO", None, 27, "es_sem_partida"),                  # passou do atraso maximo: o dia nao comeca mais
    ("PARADO", None, 27, "es_sem_partida"),
])
def test_quando_a_reserva_do_es_entra(tmp_path, estado, prova, minutos, esperado):
    estado_es, provas = tmp_path / "estado.json", tmp_path / "provas.jsonl"
    campos = {"estado": estado, **({"motivo": "login_recusado"} if estado == "QUARENTENA" else {})}
    estado_es.write_text(json.dumps(campos))
    if prova:
        provas.write_text(json.dumps({"dia": "2026-10-30", "resultado": "interrompido"}) + "\n" +
                          json.dumps({"dia": DIA_R, "resultado": prova}) + "\n")
    agora = INICIO + timedelta(minutes=minutos)
    assert coletor.reserva_do_es(estado_es, provas, DIA_R, INICIO, agora) == esperado


@pytest.mark.parametrize("registrado, esperado", [
    ("2026-11-02T16:00:00Z", "es_capturado_parcial"),  # parado de forma ordenada no meio do pregao
    ("2026-11-02T21:13:30Z", None),                    # fim normal: logout 120 s antes do fim, mais a verificacao
])
def test_capturado_so_vale_como_dia_inteiro_se_registrado_no_fim(tmp_path, registrado, esperado):
    estado_es, provas = tmp_path / "estado.json", tmp_path / "provas.jsonl"
    estado_es.write_text(json.dumps({"estado": "PARADO"}))
    provas.write_text(json.dumps({"dia": DIA_R, "resultado": "capturado", "janela_fim": "2026-11-02T21:15:00Z",
                                  "registrado_utc": registrado}) + "\n")
    assert coletor.reserva_do_es(estado_es, provas, DIA_R, INICIO, INICIO + timedelta(hours=3)) == esperado


def test_estado_do_es_ilegivel_so_conta_com_a_prova(tmp_path):
    provas = tmp_path / "provas.jsonl"
    assert coletor.reserva_do_es(tmp_path / "nao_existe.json", provas, DIA_R, INICIO,
                                 INICIO + timedelta(minutes=5)) is None
    provas.write_text(json.dumps({"dia": DIA_R, "resultado": "interrompido"}) + "\n")
    assert coletor.reserva_do_es(tmp_path / "nao_existe.json", provas, DIA_R, INICIO,
                                 INICIO + timedelta(minutes=5)) == "es_interrompido"


def test_dia_normal_nunca_assina_o_es(tmp_path):
    servidor = _servidor_com_es()
    _rodar(tmp_path, servidor, _calendario(), _ambiente(tmp_path, estado_es="CAPTURANDO"), espera_prova_es_s=0.0)
    assert _assinaturas_do_es(servidor) == 0


def test_es_do_agendador_capturando_vence_ate_prova_de_queda(tmp_path):
    es = _ambiente(tmp_path, estado_es="CAPTURANDO")
    _prova_do_es(es, "interrompido")  # prova de queda, mas o agendador diz que esta gravando: nunca assina
    servidor = _servidor_com_es()
    _rodar(tmp_path, servidor, _calendario(), es, espera_prova_es_s=0.0)
    assert _assinaturas_do_es(servidor) == 0


def test_es_que_cai_perto_do_fim_nao_corta_os_outros_ativos(tmp_path):
    es = _ambiente(tmp_path, estado_es="CAPTURANDO", com_prova=False)

    def es_cai():
        _prova_do_es(es, "interrompido")
        (es / "agenda" / "estado.json").write_text(json.dumps({"estado": "AGUARDANDO"}))
    threading.Timer(4.5, es_cai).start()
    servidor = _servidor_com_es()
    # janela de 9 s, parte minima de 5 s: aos 4,5 s ja nao cabe outra parte, entao a troca nao acontece
    motivo, provas, _ = _rodar(tmp_path, servidor, _calendario(segundos=8), es, duracao_minima_s=5.0)
    assert _resultados(provas)[0] == (f"{HOJE}#p1", "capturado") and provas[0]["parada"] is None
    assert _assinaturas_do_es(servidor) == 0


def test_es_ja_caido_entra_como_reserva_e_vai_ao_dataset_com_nome_proprio(tmp_path):
    es = _ambiente(tmp_path)
    _prova_do_es(es, "interrompido")
    servidor = _servidor_com_es()
    motivo, provas, _ = _rodar(tmp_path, servidor, _calendario(), es)
    assert motivo == "calendario_esgotado" and _assinaturas_do_es(servidor) == 1
    assert provas[0]["resultado"] == "capturado" and provas[0]["reserva_es"] == "es_interrompido"
    assert len(provas[0]["com_eventos"]) == 13
    nomes = {p.name for p in (tmp_path / "DataSet" / HOJE).iterdir()}
    assert {"QHOS2_SINTETICO.tar.xz", "CME_ESZ6_reserva.tar.xz"} <= nomes  # a copia do oficial e a reserva
    assert "CME_ESZ6.tar.xz" not in nomes  # nunca com o nome do ES do agendador
    por_origem = {e.get("origem"): e for e in _index(tmp_path)["ativos"] if e["simbolo_pedido"] == "ES"}
    reserva = por_origem["reserva_do_coletor"]
    assert reserva["estado"] == "publicado" and reserva["captura_id"] == "CME_ESZ6"
    assert reserva["contrato_resolvido"] == "ESZ6" and reserva["arquivo"] == "CME_ESZ6_reserva.tar.xz"
    assert por_origem["copia_da_captura_do_agendador"]["estado"].startswith("publicado")
    assert _index(tmp_path)["resumo"]["publicados"] == 14


def test_es_que_cai_durante_a_parte_troca_para_a_reserva_sem_esperar(tmp_path):
    es = _ambiente(tmp_path, estado_es="CAPTURANDO", com_prova=False)

    def es_cai():
        _prova_do_es(es, "interrompido")
        (es / "agenda" / "estado.json").write_text(json.dumps({"estado": "AGUARDANDO"}))
    threading.Timer(1.5, es_cai).start()
    servidor = _servidor_com_es()
    # espera de queda longa: se a troca esperasse por ela, a janela acabaria antes da parte com o ES
    motivo, provas, _ = _rodar(tmp_path, servidor, _calendario(segundos=8), es, espera_apos_queda_s=30.0)
    res = _resultados(provas)
    assert res[0] == (f"{HOJE}#p1", "encerrada_para_a_reserva_do_es") and provas[0]["parada"] == "es_reserva"
    assert res[1] == (f"{HOJE}#p2", "capturado") and provas[1]["reserva_es"] == "es_interrompido"
    assert provas[0].get("reserva_es") is None and _assinaturas_do_es(servidor) == 1
    nomes = {p.name for p in (tmp_path / "DataSet" / HOJE).iterdir()}
    assert {"CME_ESZ6_reserva_p2.tar.xz", "CME_NQZ6.tar.xz", "CME_NQZ6_p2.tar.xz"} <= nomes


def test_reserva_sai_na_hora_se_o_es_do_agendador_voltar_a_gravar(tmp_path):
    es = _ambiente(tmp_path)
    _prova_do_es(es, "interrompido")
    threading.Timer(1.5, lambda: (es / "agenda" / "estado.json").write_text(json.dumps({"estado": "CAPTURANDO"}))).start()
    servidor = _servidor_com_es()
    motivo, provas, _ = _rodar(tmp_path, servidor, _calendario(segundos=8), es, espera_apos_queda_s=30.0,
                               espera_prova_es_s=0.0)
    res = _resultados(provas)
    assert res[0] == (f"{HOJE}#p1", "encerrada_porque_o_es_do_agendador_voltou")
    assert provas[0]["parada"] == "es_do_agendador_capturando" and provas[0]["reserva_es"] == "es_interrompido"
    assert res[1] == (f"{HOJE}#p2", "capturado") and provas[1].get("reserva_es") is None
    assert _assinaturas_do_es(servidor) == 1  # so na primeira parte


def test_login_recusado_com_a_reserva_avisa_que_o_es_estava_na_parte(tmp_path):
    es = _ambiente(tmp_path)
    _prova_do_es(es, "interrompido")
    motivo, _, _ = _rodar(tmp_path, _servidor_com_es(rp_login="1"), _calendario(), es)
    assert motivo == "login_recusado" and len(AVISOS) == 1
    assert "ES nao foi tocado" not in AVISOS[0] and "reserva" in AVISOS[0]


def test_login_recusado_do_es_nao_aciona_a_reserva(tmp_path):
    es = _ambiente(tmp_path, estado_es="QUARENTENA")
    (es / "agenda" / "estado.json").write_text(json.dumps({"estado": "QUARENTENA", "motivo": "login_recusado"}))
    _prova_do_es(es, "interrompido")
    servidor = _servidor_com_es()
    _rodar(tmp_path, servidor, _calendario(), es)
    assert _assinaturas_do_es(servidor) == 0


def test_janela_selada_leva_a_reserva_do_es_junto_e_libera_as_duas(tmp_path, monkeypatch):
    hoje = date.fromisoformat(HOJE)
    monkeypatch.setattr(coletor, "SELADO_DE", HOJE)
    monkeypatch.setattr(coletor, "SELADO_ATE", HOJE)
    monkeypatch.setattr(coletor, "SELADO_LIBERA_EM", hoje + timedelta(days=1))
    es = _ambiente(tmp_path)
    _prova_do_es(es, "interrompido")
    _rodar(tmp_path, _servidor_com_es(), _calendario(), es)
    pasta, selada = tmp_path / "DataSet" / HOJE, tmp_path / "DataSet" / coletor.SELADO_PASTA / HOJE
    assert not (pasta / "CME_ESZ6_reserva.tar.xz").exists() and (selada / "CME_ESZ6_reserva.tar.xz").exists()
    assert (selada / "QHOS2_SINTETICO.tar.xz").exists()
    selado = json.loads((selada / "INDEX.json").read_text())
    assert sorted(e["origem"] for e in selado["ativos"]) == ["copia_da_captura_do_agendador", "reserva_do_coletor"]
    apontadores = [e for e in _index(tmp_path)["ativos"] if e["simbolo_pedido"] == "ES"]
    assert len(apontadores) == 2 and all(e["estado"] == "selado" and "sha256_xz" not in e for e in apontadores)
    r = fechamento.liberar_selados(tmp_path / "DataSet", tmp_path / "DataSet" / coletor.SELADO_PASTA,
                                   hoje + timedelta(days=1), hoje + timedelta(days=1))
    assert r == [(HOJE, "liberado")] and not selada.exists()
    linhas_es = [e for e in _index(tmp_path)["ativos"] if e["simbolo_pedido"] == "ES"]
    assert sorted(e["estado"][:9] for e in linhas_es) == ["publicado", "publicado"]
    somas = (pasta / "SHA256SUMS.txt").read_text()
    assert "CME_ESZ6_reserva.tar.xz" in somas and "QHOS2_SINTETICO.tar.xz" in somas
    for linha in somas.splitlines():
        sha, nome = linha.split("  ")
        assert hashlib.sha256((pasta / nome).read_bytes()).hexdigest() == sha


def _dia_selado_com_reserva(tmp_path, monkeypatch):
    hoje = date.fromisoformat(HOJE)
    monkeypatch.setattr(coletor, "SELADO_DE", HOJE)
    monkeypatch.setattr(coletor, "SELADO_ATE", HOJE)
    monkeypatch.setattr(coletor, "SELADO_LIBERA_EM", hoje + timedelta(days=30))
    es = _ambiente(tmp_path)
    _prova_do_es(es, "interrompido")
    _rodar(tmp_path, _servidor_com_es(), _calendario(), es)
    return hoje, tmp_path / "DataSet" / coletor.SELADO_PASTA


def test_liberacao_selada_confere_tudo_antes_de_mover_qualquer_arquivo(tmp_path, monkeypatch):
    hoje, selada = _dia_selado_com_reserva(tmp_path, monkeypatch)
    alvo = selada / HOJE / "QHOS2_SINTETICO.tar.xz"  # o ES do agendador estragado; a reserva esta boa
    dados = bytearray(alvo.read_bytes())
    dados[len(dados) // 2] ^= 0xFF
    alvo.write_bytes(bytes(dados))
    assert fechamento.liberar_selados(tmp_path / "DataSet", selada, hoje, hoje) == [(HOJE, "xz_selado_diverge")]
    assert (selada / HOJE / "CME_ESZ6_reserva.tar.xz").exists()  # nada saiu da pasta selada
    assert not (tmp_path / "DataSet" / HOJE / "CME_ESZ6_reserva.tar.xz").exists()


def test_liberacao_selada_recusa_xz_fora_do_index_e_nao_apaga_nada(tmp_path, monkeypatch):
    hoje, selada = _dia_selado_com_reserva(tmp_path, monkeypatch)
    intruso = selada / HOJE / "CME_ESZ6_reserva_p9.tar.xz"
    intruso.write_bytes(b"dado que nao esta no INDEX")
    assert fechamento.liberar_selados(tmp_path / "DataSet", selada, hoje, hoje) == \
        [(HOJE, "arquivo_nao_listado_na_pasta_selada")]
    assert intruso.exists() and (selada / HOJE / "CME_ESZ6_reserva.tar.xz").exists()


def test_pasta_selada_fica_antes_da_data(tmp_path):
    selada = tmp_path / "DataSet" / "SELADO_ate_2026-11-21" / "2026-11-10"
    selada.mkdir(parents=True)
    (selada / "x").write_text("y")
    assert fechamento.liberar_selados(tmp_path / "DataSet", selada.parent, date(2026, 11, 20), date(2026, 11, 21)) == []
    assert (selada / "x").exists()
