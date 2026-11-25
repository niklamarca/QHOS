"""Calendario sem prazo: a regra reproduz o calendario conferido a mao e os feriados publicados da bolsa."""
import json
from datetime import date, datetime

import pytest

from qhos2 import agenda

from coleta_multi import calendario_continuo as cc, servico

# Dias do calendario conferido a mao (calendario-20261028-20261125.json), de 30/10 em diante.
CONFERIDO = ["2026-10-30", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09",
             "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-16", "2026-11-17", "2026-11-18",
             "2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24", "2026-11-25"]


def test_reproduz_o_calendario_conferido():
    c = cc.gerar(date(2026, 10, 30))
    dias = [j["dia"] for j in c["janelas"]]
    assert dias[:len(CONFERIDO)] == CONFERIDO
    por_dia = {j["dia"]: j for j in c["janelas"]}
    assert (por_dia["2026-10-30"]["inicio"], por_dia["2026-10-30"]["fim"]) == ("2026-10-30T13:00:00Z", "2026-10-30T20:15:00Z")
    assert (por_dia["2026-11-02"]["inicio"], por_dia["2026-11-02"]["fim"]) == ("2026-11-02T14:00:00Z", "2026-11-02T21:15:00Z")
    assert "2026-11-26" not in por_dia and por_dia["2026-11-27"]["fim"] == "2026-11-27T18:30:00Z"
    assert c["contrato_es"] == "ESZ6" and c["proxima_rolagem_es"] == "2026-12-10" and dias[-1] == "2026-12-09"
    agenda.janelas(c)


@pytest.mark.parametrize("ano,esperado", [
    (2026, {"2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
            "2026-09-07", "2026-11-26", "2026-12-25"}),
    (2027, {"2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05",
            "2027-09-06", "2027-11-25", "2027-12-24"}),
])
def test_feriados_publicados(ano, esperado):
    assert {d.isoformat() for d in cc.feriados(ano)} == esperado


def test_ano_novo_no_sabado_nao_tem_folga_e_sessoes_curtas():
    assert date(2027, 12, 31) not in cc.feriados(2027) and date(2028, 1, 1) not in cc.feriados(2028)
    assert {d.isoformat() for d in cc.sessoes_curtas(2026)} == {"2026-11-27", "2026-12-24"}  # 3/7 e feriado
    assert {d.isoformat() for d in cc.sessoes_curtas(2025)} == {"2025-07-03", "2025-11-28", "2025-12-24"}


@pytest.mark.parametrize("dia,contrato,rolagem", [
    ("2026-10-30", "ESZ6", "2026-12-10"), ("2026-12-09", "ESZ6", "2026-12-10"), ("2026-12-10", "ESH7", "2027-03-11"),
    ("2027-06-10", "ESU7", "2027-09-09"), ("2029-12-31", "ESH0", "2030-03-07"),
])
def test_rolagem_do_es(dia, contrato, rolagem):
    assert cc.contrato_es(date.fromisoformat(dia)) == (contrato, date.fromisoformat(rolagem))


def test_calendario_seguinte_comeca_no_dia_da_rolagem_com_o_contrato_novo():
    c = cc.gerar(date(2026, 12, 10))
    assert c["contrato_es"] == "ESH7" and c["janelas"][0]["dia"] == "2026-12-10"
    assert "2026-12-25" not in [j["dia"] for j in c["janelas"]] and "2027-01-01" in c["dias_excluidos"]
    agenda.janelas(c)


def test_pregao_padrao_em_brasilia_antes_e_depois_do_horario_de_verao():
    """09:00-16:15 de NY: 10:00-17:15 de Brasilia ate 30/10 e 11:00-18:15 depois que os EUA saem do verao (1/11)."""
    from zoneinfo import ZoneInfo
    brt = ZoneInfo("America/Sao_Paulo")
    por = {j["dia"]: j for j in cc.gerar(date(2026, 10, 30))["janelas"]}
    em_brt = lambda iso: datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(brt).strftime("%H:%M")
    assert (em_brt(por["2026-10-30"]["inicio"]), em_brt(por["2026-10-30"]["fim"])) == ("10:00", "17:15")
    assert (em_brt(por["2026-11-02"]["inicio"]), em_brt(por["2026-11-02"]["fim"])) == ("11:00", "18:15")


def test_atualizar_escreve_atual_e_datado(tmp_path):
    caminho, c = cc.atualizar(tmp_path, date(2026, 10, 30))
    assert caminho.name == "calendario-atual.json" and json.loads(caminho.read_text()) == c
    assert (tmp_path / "calendario-20261030-20261210.json").exists()


@pytest.mark.parametrize("alvo,esperado", [
    ("agenda-continuo", ["rodar", "--symbol", "ESH7", "--exchange", "CME", "--online", "--agreements-accepted"]),
    ("coletor-continuo", ["--online", "--agreements-accepted"]),
])
def test_servico_continuo_monta_calendario_e_contrato(tmp_path, monkeypatch, alvo, esperado):
    from coleta_multi import coletor
    (tmp_path / "rithmic-paper").write_text("u\np\n")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))

    class Relogio(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 12, 15, 8, 0, tzinfo=tz)
    monkeypatch.setattr(servico, "datetime", Relogio)
    monkeypatch.setattr(servico, "_guarda_do_es_volta_sozinha", lambda *a, **k: "livre")  # nunca a guarda real
    visto = {}
    modulo = agenda if alvo.startswith("agenda") else coletor
    monkeypatch.setattr(modulo, "main", lambda argv, **k: visto.setdefault("argv", argv) and 0)
    assert servico.main([alvo]) == 0
    argv = visto["argv"]
    cal = argv[argv.index("--calendario") + 1]
    assert cal == str(tmp_path / "dados" / "coleta_multi" / "calendario" / "calendario-atual.json")  # o mesmo para os dois
    assert [a for i, a in enumerate(argv) if a != cal and argv[i - 1:i] != ["--calendario"] and a != "--calendario"] == esperado
