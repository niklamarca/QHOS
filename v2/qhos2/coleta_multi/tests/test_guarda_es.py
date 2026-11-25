"""O ES volta sozinho depois de queda ou reinicio; nunca depois de login recusado; nunca com captura viva."""
import json
import time

import pytest

from qhos2 import agenda
from qhos2.operacao import GuardaCaptura

from coleta_multi import aviso, guarda_es, servico

HORA_NS = 3600 * 10 ** 9


def _guarda(tmp_path, *, fim_ha_s=None, em_andamento=False):
    raiz = tmp_path / "guarda_es"
    with GuardaCaptura(raiz) as g:
        g.iniciar()
        if not em_andamento:
            g.finalizar(logout_confirmado=False, fechamento_normal=False)  # queda: QUARENTENA
    if fim_ha_s is not None:
        estado = json.loads((raiz / "estado.json").read_text())
        estado["fim_ns" if not em_andamento else "inicio_ns"] = time.time_ns() - int(fim_ha_s * 1e9)
        (raiz / "estado.json").write_text(json.dumps(estado))
    return raiz


def _agenda(tmp_path, **estado):
    arquivo = tmp_path / "agenda_estado.json"
    arquivo.write_text(json.dumps(estado or {"estado": "AGUARDANDO"}))
    return arquivo


def test_queda_antiga_libera_e_registra(tmp_path):
    raiz = _guarda(tmp_path, fim_ha_s=900)
    assert guarda_es.liberar_se_queda(raiz, _agenda(tmp_path)) == "liberada"
    assert json.loads((raiz / "estado.json").read_text())["estado"] == "LIVRE"
    assert len(list(raiz.glob("liberacao-*.json"))) == 1  # o historico do codigo congelado


def test_queda_recente_espera_o_servidor_descartar_a_sessao(tmp_path):
    raiz = _guarda(tmp_path, fim_ha_s=10)
    assert guarda_es.liberar_se_queda(raiz, _agenda(tmp_path)) == "aguardando_a_espera"
    assert json.loads((raiz / "estado.json").read_text())["estado"] == "QUARENTENA"


def test_reinicio_no_meio_da_captura_libera(tmp_path):
    raiz = _guarda(tmp_path, fim_ha_s=3600, em_andamento=True)
    assert guarda_es.liberar_se_queda(raiz, _agenda(tmp_path)) == "liberada"


@pytest.mark.parametrize("motivo", ["login_recusado", "logout_forcado"])
def test_login_recusado_nunca_libera(tmp_path, motivo):
    raiz = _guarda(tmp_path, fim_ha_s=86400)
    assert guarda_es.liberar_se_queda(raiz, _agenda(tmp_path, estado="QUARENTENA", motivo=motivo)) == \
        f"quarentena_por_{motivo}"
    assert json.loads((raiz / "estado.json").read_text())["estado"] == "QUARENTENA"


def test_captura_viva_nunca_e_tocada(tmp_path):
    raiz = _guarda(tmp_path, fim_ha_s=86400)
    with GuardaCaptura(raiz):  # o lock da captura viva
        assert guarda_es.liberar_se_queda(raiz, _agenda(tmp_path)) == "captura_ja_em_execucao"
    assert json.loads((raiz / "estado.json").read_text())["estado"] == "QUARENTENA"


def test_guarda_livre_nao_muda_nada(tmp_path):
    raiz = tmp_path / "g"
    with GuardaCaptura(raiz):
        pass
    assert guarda_es.liberar_se_queda(raiz, _agenda(tmp_path)) == "livre"


# ---- aviso ao Discord -------------------------------------------------------------------------------------------

def test_aviso_usa_o_script_do_windows_e_nunca_levanta():
    visto = {}

    class R:
        returncode = 0

    def executar(comando, **k):
        visto["comando"] = comando
        return R()
    assert aviso.avisar("teste", executar=executar) is True
    assert visto["comando"][0].endswith("powershell.exe") and aviso.SCRIPT in visto["comando"]
    assert visto["comando"][-1] == "-Chamar" and "teste" in visto["comando"]

    def quebra(*a, **k):
        raise OSError("sem interop")
    assert aviso.avisar("x", executar=quebra) is False


def test_texto_do_aviso_diz_o_acontecido():
    t = aviso.login_recusado("agendador do ES", "login_recusado", "2026-10-30", "O ES parou.")
    assert t.startswith("QHOS: LOGIN RECUSADO") and "agendador do ES" in t and "2026-10-30" in t
    assert "LOGOUT FORCADO" in aviso.login_recusado("coletor", "logout_forcado", "d", "c")


# ---- lancador do agendador ------------------------------------------------------------------------------------------

def _credencial(tmp_path, monkeypatch):
    (tmp_path / "rithmic-paper").write_text("u\np\n")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))


def test_lancador_avisa_o_discord_quando_o_es_sai_com_login_recusado(tmp_path, monkeypatch):
    _credencial(tmp_path, monkeypatch)
    (tmp_path / "dados" / "agenda").mkdir(parents=True)
    (tmp_path / "dados" / "agenda" / "estado.json").write_text(
        json.dumps({"estado": "QUARENTENA", "motivo": "login_recusado", "dia": "2026-10-30"}))
    monkeypatch.setattr(servico, "_guarda_do_es_volta_sozinha", lambda: "livre")
    monkeypatch.setattr(agenda, "main", lambda argv, **k: 3)
    enviados = []
    monkeypatch.setattr(aviso, "avisar", lambda m, **k: enviados.append(m) or True)
    assert servico.main(["agenda", "rodar"]) == 3
    assert len(enviados) == 1 and "LOGIN RECUSADO" in enviados[0] and "agendador do ES" in enviados[0]
    assert "2026-10-30" in enviados[0]


def test_lancador_nao_avisa_em_saida_normal(tmp_path, monkeypatch):
    _credencial(tmp_path, monkeypatch)
    monkeypatch.setattr(servico, "_guarda_do_es_volta_sozinha", lambda: "livre")
    monkeypatch.setattr(agenda, "main", lambda argv, **k: 0)
    monkeypatch.setattr(aviso, "avisar", lambda *a, **k: pytest.fail("nao devia avisar"))
    assert servico.main(["agenda", "rodar"]) == 0


def test_lancador_libera_a_guarda_do_es_depois_de_reinicio(tmp_path, monkeypatch):
    from qhos2 import operacao
    raiz = _guarda(tmp_path, fim_ha_s=3600, em_andamento=True)
    monkeypatch.setattr(operacao, "estado_padrao", lambda: raiz)
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    dormiu = []
    assert servico._guarda_do_es_volta_sozinha(dormir=dormiu.append) == "liberada"
    assert dormiu == [guarda_es.ESPERA_S]  # esperou o servidor descartar a sessao antes de liberar
    assert json.loads((raiz / "estado.json").read_text())["estado"] == "LIVRE"
