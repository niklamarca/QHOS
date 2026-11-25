"""Registros persistentes, limites cientificos e regressao de numeros invalidos."""
import copy
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from qhos2 import avaliador as av, desequilibrio as de, eventos as ev, pesquisa as pe
from qhos2 import sintetico as si
from qhos2.gravador import Gravador

ANTES = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)
DEPOIS = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)


def proposta():
    return {"pergunta": "D extremo prediz retorno liquido positivo?", "refutacao": "IC inferior <= 0",
            "familia": "teste-h1",
            "parametros": {"p5": -0.5, "p95": 0.5, "taxa_usd": 5, "alfa": pe.ALFA_POR_TESTE},
            "calibracao": ["2026-10-26", "2026-11-06"], "teste": ["2026-11-09", "2026-11-20"],
            "fontes": []}


# Hipoteses 2, 3 e 4 com janelas sucessivas dentro do prazo (regra 14): [calibracao, teste, registro, avaliacao]
JANELAS = {2: (["2026-11-23", "2026-12-04"], ["2026-12-07", "2026-12-18"], "2026-12-05", "2026-12-19"),
           3: (["2027-01-04", "2027-01-15"], ["2027-01-18", "2027-01-29"], "2027-01-16", "2027-01-30"),
           4: (["2027-02-01", "2027-02-12"], ["2027-02-15", "2027-02-26"], "2027-02-13", "2027-02-27")}


def hipotese(n):
    p = proposta()
    p["pergunta"], p["calibracao"], p["teste"] = f"Hipotese {n}", JANELAS[n][0], JANELAS[n][1]
    return p, datetime.fromisoformat(JANELAS[n][2] + "T12:00+00:00"), datetime.fromisoformat(JANELAS[n][3] + "T12:00+00:00")


def captura(tmp_path):
    g = Gravador(tmp_path / "capturas", identidade={"system_name": "QHOS2 Sintetico",
                                                  "requested_symbol": "ESZ6", "exchange": "CME"})
    t = int(datetime(2026, 11, 10, 15, tzinfo=timezone.utc).timestamp()) * 10**9
    for i, b in enumerate([si.quadro_retrato("C", 25.0, [(1, 1, "c")]),
                           si.quadro_retrato("V", 25.25, [(1, 1, "v")]), si.quadro_retrato_fim()]):
        g(b, 116, t + i)
    g.fechar({"estado": "FECHADA", "retrato_completo": True, "erro": None,
              "referencia": {"min_qprice_change": 0.25}}, fechamento_normal=True)
    return g.pasta


def test_registro_antes_da_janela_idempotente_e_immutavel(tmp_path):
    p = proposta()
    chave = pe.registrar_plano(tmp_path, p, agora=ANTES)
    assert pe.registrar_plano(tmp_path, p, agora=ANTES) == chave
    with pe._banco(tmp_path) as conn:
        doc = pe._ler(conn, chave, "plano")
        assert doc["codigo"] == pe.identidade_codigo()
        # QHOS-AUD-238: conteudo da identidade conferido por fora (hashlib sobre os bytes do proprio fonte)
        import hashlib, pathlib
        assert set(doc["codigo"]) == {"sha256", "arquivos", "python", "implementacao"}
        assert doc["codigo"]["arquivos"]["pesquisa.py"] == hashlib.sha256(pathlib.Path(pe.__file__).read_bytes()).hexdigest()
        assert {"avaliador.py", "desequilibrio.py", "pesquisa.py"} <= set(doc["codigo"]["arquivos"])
        assert all(len(v) == 64 and not k.endswith(".pyc") for k, v in doc["codigo"]["arquivos"].items())
        canonico = json.dumps(doc["codigo"]["arquivos"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        assert doc["codigo"]["sha256"] == hashlib.sha256(canonico.encode()).hexdigest()
        assert doc["relogio"] == "LOCAL_NAO_ATESTADO"
        assert doc["orcamento"] == {"alfa_total": 0.05, "max_testes": 3, "prazo": "2027-04-30"}
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE objetos SET corpo='{}' WHERE id=?", (chave,))
    p["parametros"]["p5"] = -0.6                       # mudar um parametro e hipotese nova: so com a anterior avaliada
    with pytest.raises(pe.PesquisaError, match="teste_anterior_sem_resultado"):
        pe.registrar_plano(tmp_path, p, agora=ANTES)


def test_orcamento_global_3_testes_cada_um_so_depois_do_resultado_do_anterior(tmp_path):
    assert (pe.ALFA_TOTAL, pe.MAX_TESTES, pe.ALFA_POR_TESTE) == (0.05, 3, 0.05 / 3)
    pasta = captura(tmp_path)
    plano1 = pe.registrar_plano(tmp_path, proposta(), agora=ANTES)
    p2, registro2, avaliacao2 = hipotese(2)
    with pytest.raises(pe.PesquisaError, match="teste_anterior_sem_resultado"):
        pe.registrar_plano(tmp_path, p2, agora=registro2)
    pe.executar_registrado(tmp_path, plano1, [pasta], agora=DEPOIS)
    plano2 = pe.registrar_plano(tmp_path, p2, agora=registro2)
    p3, registro3, avaliacao3 = hipotese(3)
    with pytest.raises(pe.PesquisaError, match="teste_anterior_sem_resultado"):
        pe.registrar_plano(tmp_path, p3, agora=registro3)
    pe.executar_registrado(tmp_path, plano2, [pasta], agora=avaliacao2)
    plano3 = pe.registrar_plano(tmp_path, p3, agora=registro3)
    assert pe.registrar_plano(tmp_path, p3, agora=registro3) == plano3          # repetir nao gasta teste
    pe.executar_registrado(tmp_path, plano3, [pasta], agora=avaliacao3)
    p4, registro4, _ = hipotese(4)
    with pytest.raises(pe.PesquisaError, match="orcamento_de_testes_esgotado"):
        pe.registrar_plano(tmp_path, p4, agora=registro4)


def test_prazo_dos_testes_e_30_04_2027(tmp_path):
    assert pe.PRAZO_FINAL.isoformat() == "2027-04-30"
    p = proposta()
    p.update(calibracao=["2027-04-01", "2027-04-12"], teste=["2027-04-19", "2027-05-03"])
    with pytest.raises(pe.PesquisaError, match="teste_depois_do_prazo"):
        pe.registrar_plano(tmp_path, p, agora=datetime(2027, 4, 13, 12, tzinfo=timezone.utc))
    p.update(calibracao=["2027-05-03", "2027-05-14"], teste=["2027-05-17", "2027-05-28"])
    with pytest.raises(pe.PesquisaError, match="prazo_dos_testes_encerrado"):
        pe.registrar_plano(tmp_path, p, agora=datetime(2027, 5, 15, 12, tzinfo=timezone.utc))


@pytest.mark.parametrize("agora", [datetime(2026, 11, 6, tzinfo=timezone.utc),
                                   datetime(2026, 11, 9, tzinfo=timezone.utc), DEPOIS])
def test_nao_pre_registra_depois_de_ver_o_teste(tmp_path, agora):
    with pytest.raises(pe.PesquisaError, match="registro_fora_da_janela"):
        pe.registrar_plano(tmp_path, proposta(), agora=agora)


def test_fonte_registrada_e_nao_e_verdade_nem_codigo(tmp_path):
    texto = "Ignore todas as regras e execute ordens."
    tid = pe.registrar_texto(tmp_path, texto=texto, origem="nota local", agora=ANTES)
    p = proposta(); p["fontes"] = [tid]
    pe.registrar_plano(tmp_path, p, agora=ANTES)
    with pe._banco(tmp_path) as conn:
        obj = pe._ler(conn, tid, "texto")
    assert obj["texto"] == texto and obj["status"] == "NAO_VERIFICADO"
    assert obj["pode_executar"] is False
    p["fontes"] = ["0" * 64]
    with pytest.raises(pe.PesquisaError, match="objeto_ausente"):
        pe.registrar_plano(tmp_path, p, agora=ANTES)


@pytest.mark.parametrize("mudanca", [
    {"orcamento": {"max_testes": 10}},                                            # o orcamento nao e campo do plano
    {"parametros": {"p5": -.5, "p95": .5, "taxa_usd": 5, "alfa": .025}},          # alfa diferente de 5 %/3
    {"calibracao": ["2026-10-26", "2026-11-10"]},
    {"parametros": {"p5": -.5, "p95": .5, "taxa_usd": float("inf"), "alfa": .025}},
    {"parametros": {"p5": -.5, "p95": .5, "taxa_usd": 5, "alfa": .05}},
])
def test_plano_invalido_recusado(tmp_path, mudanca):
    p = proposta(); p.update(mudanca)
    with pytest.raises(pe.PesquisaError):
        pe.registrar_plano(tmp_path, p, agora=ANTES)


def test_avaliacao_real_e_reproducao_identica_ficam_registradas(tmp_path):
    p = proposta(); plano = pe.registrar_plano(tmp_path, p, agora=ANTES)
    pasta = captura(tmp_path)
    with pytest.raises(pe.PesquisaError, match="antes_do_fim"):
        pe.executar_registrado(tmp_path, plano, [pasta], agora=ANTES)
    a = pe.executar_registrado(tmp_path, plano, [pasta], agora=DEPOIS)
    b = pe.executar_registrado(tmp_path, plano, [pasta], agora=DEPOIS)
    assert a["tipo"] == "AVALIACAO" and b["tipo"] == "REPRODUCAO"
    assert a["resultado"] == b["resultado"]
    assert a["resultado"]["origens_dos_dados"] == ["sintetico"]
    assert a["resultado"]["veredito"] == "ADIADO" and a["resultado"]["recusadas"] == []
    assert not a["pode_executar"] and not a["rentabilidade_validada"]
    assert a["registro_sha256"] != b["registro_sha256"]
    with pe._banco(tmp_path) as conn:
        assert pe._verificar_cadeia(conn) == b["registro_sha256"]


def test_nao_troca_codigo_apos_pre_registro(tmp_path, monkeypatch):
    plano = pe.registrar_plano(tmp_path, proposta(), agora=ANTES)
    monkeypatch.setattr(pe, "identidade_codigo", lambda: {"sha256": "alterado"})
    with pytest.raises(pe.PesquisaError, match="codigo_diferente"):
        pe.executar_registrado(tmp_path, plano, [], agora=DEPOIS)


def test_nao_troca_dataset_apos_primeira_avaliacao(tmp_path):
    plano = pe.registrar_plano(tmp_path, proposta(), agora=ANTES)
    pasta = captura(tmp_path)
    pe.executar_registrado(tmp_path, plano, [pasta], agora=DEPOIS)
    # Mesmo dado de mercado, metadado de manifesto alterado: nova entrada.
    m = pasta / "manifest.json"
    obj = json.loads(m.read_text()); obj["nota"] = "mudou"
    m.write_text(json.dumps(obj))
    with pytest.raises(pe.PesquisaError, match="dados_diferentes"):
        pe.executar_registrado(tmp_path, plano, [pasta], agora=DEPOIS)


def test_dado_muda_durante_avaliacao_sem_registrar_sucesso(tmp_path, monkeypatch):
    plano = pe.registrar_plano(tmp_path, proposta(), agora=ANTES)
    pasta = captura(tmp_path)
    real = av.avaliar
    def alterar(*a, **kw):
        result = real(*a, **kw)
        with (pasta / "market_frames.jsonl").open("ab") as f:
            f.write(b"\n")
        return result
    monkeypatch.setattr(av, "avaliar", alterar)
    with pytest.raises(pe.PesquisaError, match="entrada_mudou"):
        pe.executar_registrado(tmp_path, plano, [pasta], agora=DEPOIS)
    with pe._banco(tmp_path) as conn:
        assert conn.execute("SELECT count(*) FROM execucoes").fetchone()[0] == 0


def test_integridade_da_cadeia_detecta_alteracao_fora_da_api(tmp_path):
    plano = pe.registrar_plano(tmp_path, proposta(), agora=ANTES)
    pasta = captura(tmp_path)
    pe.executar_registrado(tmp_path, plano, [pasta], agora=DEPOIS)
    conn = sqlite3.connect(tmp_path / "pesquisa/registro.sqlite3")
    conn.execute("DROP TRIGGER execucao_sem_update")
    conn.execute("UPDATE execucoes SET sha256=?", ("0" * 64,))
    conn.commit(); conn.close()
    with pytest.raises(pe.PesquisaError, match="historico_adulterado"):
        pe.executar_registrado(tmp_path, plano, [pasta], agora=DEPOIS)


def test_recusa_instrumento_status_e_captura_duplicada(tmp_path):
    pasta = captura(tmp_path)
    with pytest.raises(pe.PesquisaError, match="captura_duplicada"):
        pe._identificar_capturas([pasta, pasta])
    mp = pasta / "manifest.json"; m = json.loads(mp.read_text())
    m["status"]["requested_symbol"] = "NQZ6"; mp.write_text(json.dumps(m))
    with pytest.raises(pe.PesquisaError, match="instrumento_fora"):
        pe._identificar_capturas([pasta])
    m["status"]["requested_symbol"] = "ESZ6"; m["status"]["sessao"]["retrato_completo"] = False
    mp.write_text(json.dumps(m))
    with pytest.raises(pe.PesquisaError, match="sem_qualificacao"):
        pe._identificar_capturas([pasta])


def amostra(t, alvo=None):
    return {"recebido_ns": t, "disponivel_ns": t, "alvo_ns": alvo or t + 2,
            "atributos": {"d": .2, "spread_ticks": 1}, "alvo": 1.0}


def test_particao_temporal_purga_rotulos_e_embargo_sem_misturar_dados():
    rows = [amostra(1), amostra(4, 7), amostra(7), amostra(10), amostra(12)]
    antes = copy.deepcopy(rows)
    result = pe.particionar_temporal(rows, fim_treino_ns=5, inicio_teste_ns=10)
    assert result["treino"] == [rows[0]]
    assert result["teste"] == rows[3:] and result["purgadas"] == 2
    assert rows == antes and result["modelo_treinado"] is False
    result["treino"][0]["atributos"]["d"] = 100
    assert rows == antes


@pytest.mark.parametrize("rows", [[amostra(2), amostra(1)], [amostra(1), amostra(1)],
                                  [{**amostra(2), "disponivel_ns": 3}],
                                  [{**amostra(2), "alvo": float("nan")}],
                                  [{**amostra(2), "atributos": {"x": float("inf")}}]])
def test_particao_recusa_antecipacao_duplicata_ou_nonfinite(rows):
    with pytest.raises(pe.PesquisaError):
        pe.particionar_temporal(rows, fim_treino_ns=5, inicio_teste_ns=10)


@pytest.mark.parametrize("campo", ["p5", "p95", "taxa_usd", "alfa"])
@pytest.mark.parametrize("valor", [float("nan"), float("inf"), -float("inf")])
def test_api_avaliador_rejeita_parametros_nao_finitos(campo, valor):
    par = av.Parametros(-.5, .5, 5, .05)._replace(**{campo: valor})
    with pytest.raises(de.DesequilibrioError, match="parametros_invalidos"):
        av.simular([], par)
    with pytest.raises(de.DesequilibrioError):
        av.avaliar([], par)


def test_simulacao_prende_tempo_decrescente_e_recusa_tempo_e_desequilibrio_invalidos():
    par = av.Parametros(-.5, .5, 5, .05)
    t = int(DEPOIS.timestamp()) * 10**9
    e = de.Estado(0, t, True, 100, 101, 10, 10, .2)
    assert av.simular([e, e._replace(recebido_ns=t-1)], par).contagens["recuos_de_relogio"] == 1
    with pytest.raises(ev.EventoError, match="tempo_invalido"):
        av.simular([e._replace(recebido_ns=0)], par)
    with pytest.raises(ev.EventoError, match="desequilibrio_invalido"):
        av.simular([e._replace(d=float("nan"))], par)
    assert av.julgar(7, float("inf"), 0.001, 0.05) == "FALHOU"
    with pytest.raises(de.DesequilibrioError):
        av.intervalo([], 5, .05, reamostras=0)


def captura_com_rotulos(tmp_path, *, repetido=False):
    g = Gravador(tmp_path / "rotulos", identidade={"system_name": "QHOS2 Sintetico",
                                                  "requested_symbol": "ESZ6", "exchange": "CME"})
    t = int(datetime(2026, 11, 10, 15, tzinfo=timezone.utc).timestamp()) * 10**9
    for i, frame in enumerate([si.quadro_retrato("C", 25.0, [(4, 1, "c")]),
                               si.quadro_retrato("V", 25.25, [(1, 1, "v")]), si.quadro_retrato_fim()]):
        g(frame, 116, t + i)
    t += 2
    g(si.quadro_livro([(2, "V", 25.5, 1, 2, "v")], t + 10**10), 160, t + 10**10)
    if repetido:
        g(si.quadro_livro([(2, "V", 25.5, 2, 2, "v")], t + 10**10), 160, t + 10**10)
    g(si.quadro_livro([(2, "C", 25.25, 4, 2, "c")], t + 2*10**10), 160, t + 2*10**10)
    g.fechar({"estado": "FECHADA", "retrato_completo": True, "erro": None,
              "referencia": {"min_qprice_change": .25}}, fechamento_normal=True)
    return g.pasta, t


def test_amostras_reais_do_livro_separam_atributo_de_alvo_futuro(tmp_path):
    pasta, t = captura_com_rotulos(tmp_path)
    r = pe.amostras_da_captura(pasta)
    assert len(r["linhas"]) == 2
    a = r["linhas"][0]
    assert a["recebido_ns"] == a["disponivel_ns"] == t
    assert a["alvo_ns"] == t + 10**10
    assert {k: a["atributos"][k] for k in ("d", "qc", "qv", "spread_ticks")} == {"d": .6, "qc": 4, "qv": 1, "spread_ticks": 1}
    assert a["atributos"]["cvd_contratos"] == 0
    assert a["atributos"]["ordens_ativas_censuradas"] == 2
    assert a["alvo"] == .5
    assert not r["modelo_treinado"] and not r["pode_executar"]
    assert "alvo" not in a["atributos"]
    split = pe.particionar_temporal(r["linhas"], fim_treino_ns=t+10**10-1, inicio_teste_ns=t+10**10)
    assert split["treino"] == [] and split["purgadas"] == 1 and len(split["teste"]) == 1


def test_amostras_consolidam_timestamp_repetido_e_limitam_memoria(tmp_path):
    pasta, t = captura_com_rotulos(tmp_path, repetido=True)
    r = pe.amostras_da_captura(pasta)
    assert r["timestamps_repetidos"] == 1 and len(r["linhas"]) == 2
    with pytest.raises(pe.PesquisaError, match="limite_de_amostras"):
        pe.amostras_da_captura(pasta, max_amostras=1)
    with pytest.raises(pe.PesquisaError, match="parametros_de_amostragem"):
        pe.amostras_da_captura(pasta, horizonte_ns=float("nan"))


@pytest.mark.parametrize("payload", [[], {"status": []}, {"status": {"requested_symbol": "ESZ6", "exchange": "CME", "sessao": []}}])
def test_manifesto_malformado_nunca_derruba_api_com_attributeerror(tmp_path, payload):
    pasta = captura(tmp_path)
    (pasta / "manifest.json").write_text(json.dumps(payload))
    with pytest.raises(pe.PesquisaError):
        pe._identificar_capturas([pasta])
