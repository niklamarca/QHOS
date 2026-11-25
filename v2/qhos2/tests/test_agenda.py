"""Agendador diario (opcao A): senha uma vez so na memoria, uma captura por janela, login recusado para tudo."""
import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from qhos2 import agenda, captura
from qhos2.config import Settings
from qhos2.sessao import ConfigSessao

from test_sessao import CRED, SENHA, Servidor  # noqa: E402


@pytest.fixture(autouse=True)
def guarda_isolada(tmp_path, monkeypatch):
    monkeypatch.setattr(captura, "estado_padrao", lambda: tmp_path / "operacao")
    monkeypatch.setattr(agenda, "estado_padrao", lambda: tmp_path / "operacao")


@pytest.fixture
def ajustes(tmp_path):
    return Settings(data_root=tmp_path / "dados")


def config(ajustes):
    return ConfigSessao(template_version="5.42", symbol="ESZ6", exchange="CME", duracao_s=None,
                        resposta_timeout_s=1.0, retrato_timeout_s=1.0, settings=ajustes)


class _Relogio:
    """Base dos calendarios curtos. Com a fixture `relogio_falso`, agendador e captura leem este relogio e as esperas o avancam."""
    falso = False
    agora = None


RELOGIO = _Relogio()


def agora():
    return RELOGIO.agora if RELOGIO.falso else datetime.now(timezone.utc)


@pytest.fixture
def relogio_falso(monkeypatch):
    # Janelas de 0,1-2 s com relogio real dependiam da velocidade da maquina (QHOS-AUD-051): aqui o agendador e a
    # captura leem `datetime.now()` deste relogio e `_esperar` o salta em vez de dormir. So a sessao leva tempo real.
    RELOGIO.falso, RELOGIO.agora = True, datetime.now(timezone.utc)

    class Agora(datetime):
        @classmethod
        def now(cls, tz=None):
            return RELOGIO.agora if tz is not None else RELOGIO.agora.replace(tzinfo=None)

    async def saltar(parar, segundos):
        RELOGIO.agora += timedelta(seconds=max(segundos, 0.0))
        await asyncio.sleep(0)
    monkeypatch.setattr(agenda, "datetime", Agora)
    monkeypatch.setattr(captura, "datetime", Agora)
    monkeypatch.setattr(agenda, "_esperar", saltar)
    yield RELOGIO
    RELOGIO.falso = False


def calendario_curto(*janelas, margem=60.0):
    base = agora()
    return {"schema": "qhos2_calendario_v1", "fonte": "teste", "valido_de": (base - timedelta(seconds=margem)).isoformat(),
            "valido_ate": (base + timedelta(seconds=margem)).isoformat(),
            "janelas": [{"inicio": a.isoformat(), "fim": b.isoformat()} for a, b in janelas]}


def janela(atraso_s, duracao_s):
    a = agora() + timedelta(seconds=atraso_s)
    return a, a + timedelta(seconds=duracao_s)


def rodar(cal, servidor_ou_fabrica, ajustes, tmp_path, **kw):
    fabrica = servidor_ou_fabrica.fabrica() if isinstance(servidor_ou_fabrica, Servidor) else servidor_ou_fabrica
    registro = agenda.Registro(tmp_path / "dados" / "agenda")
    base = dict(max_bytes=10 * 1024 ** 2, folga_s=0.5, atraso_max_s=30.0, duracao_minima_s=0.2,
                guarda_raiz=tmp_path / "operacao", chave_dia=lambda a: a.isoformat(), um_por_dia=False,
                saida=lambda _: None, agora=agora)
    base.update(kw)
    motivo = asyncio.run(agenda.rodar_agenda(cal, config(ajustes), fabrica, tmp_path / "capturas", CRED, registro, **base))
    provas = [json.loads(l) for l in registro.provas.read_text().splitlines()] if registro.provas.exists() else []
    return motivo, provas, registro


def logins(servidor):
    return sum(1 for con in servidor.conexoes for dado in con.enviados if SENHA.encode() in dado)


def sem_conexao(*_):
    raise AssertionError("nao devia conectar")


# ---- calendario -------------------------------------------------------------------------------------------

def test_calendario_so_dias_uteis_e_segue_o_horario_de_verao_de_ny():
    cal = agenda.gerar_calendario(date(2026, 10, 30), date(2026, 11, 3), "pagina oficial da CME")
    assert [j["dia"] for j in cal["janelas"]] == ["2026-10-30", "2026-11-02", "2026-11-03"]
    assert cal["janelas"][0]["inicio"] == "2026-10-30T13:00:00Z" and cal["janelas"][0]["fim"] == "2026-10-30T20:15:00Z"
    assert cal["janelas"][1]["inicio"] == "2026-11-02T14:00:00Z" and cal["janelas"][1]["fim"] == "2026-11-02T21:15:00Z"
    assert (cal["valido_de"], cal["valido_ate"]) == ("2026-10-30T04:00:00Z", "2026-11-04T05:00:00Z")
    assert len(agenda.janelas(cal)) == 3


def test_calendario_exige_fonte_e_so_exclui_o_que_for_declarado():
    with pytest.raises(agenda.AgendaError):
        agenda.gerar_calendario(date(2026, 11, 23), date(2026, 11, 27), "  ")
    cal = agenda.gerar_calendario(date(2026, 11, 23), date(2026, 11, 27), "CME", excluir=[date(2026, 11, 26)])
    assert [j["dia"] for j in cal["janelas"]] == ["2026-11-23", "2026-11-24", "2026-11-25", "2026-11-27"]
    assert cal["dias_excluidos"] == ["2026-11-26"]


@pytest.mark.parametrize("estrago", ["sem_fonte", "sobreposta", "fora_da_validade", "duas_no_dia", "sem_janelas"])
def test_calendario_ambiguo_e_recusado(estrago):
    cal = agenda.gerar_calendario(date(2026, 10, 28), date(2026, 10, 30), "CME")
    if estrago == "sem_fonte":
        cal["fonte"] = ""
    elif estrago == "sobreposta":
        cal["janelas"][1]["inicio"] = cal["janelas"][0]["inicio"]
    elif estrago == "fora_da_validade":
        cal["valido_ate"] = cal["janelas"][1]["fim"]
    elif estrago == "duas_no_dia":
        cal["janelas"].append({"inicio": "2026-10-30T20:30:00Z", "fim": "2026-10-30T21:00:00Z"})
    else:
        cal["janelas"] = []
    with pytest.raises(agenda.AgendaError):
        agenda.janelas(cal)


# ---- agendador --------------------------------------------------------------------------------------------

def test_cada_janela_vira_uma_captura_com_a_mesma_credencial(ajustes, tmp_path, relogio_falso):
    a1, b1 = janela(0.3, 2.0)
    a2, b2 = b1 + timedelta(seconds=0.2), b1 + timedelta(seconds=2.2)
    servidor = Servidor()
    motivo, provas, registro = rodar(calendario_curto((a1, b1), (a2, b2)), servidor, ajustes, tmp_path)
    assert motivo == "calendario_esgotado"
    assert [p["resultado"] for p in provas] == ["capturado", "capturado"]
    assert all(p["verificada"] and p["fechamento_normal"] and p["quadros"] > 0 for p in provas)
    assert all(p["schema"] == agenda.PROVA_ESQUEMA and p["primeiro_ns"] <= p["ultimo_ns"] for p in provas)
    assert logins(servidor) == 2
    estado = json.loads((registro.pasta / "estado.json").read_text())
    assert estado["estado"] == "CALENDARIO_ESGOTADO"
    for arquivo in (registro.pasta / "estado.json", registro.provas):
        assert SENHA not in arquivo.read_text() and "trader@exemplo.com" not in arquivo.read_text()


def test_login_recusado_para_tudo_sem_nova_tentativa(ajustes, tmp_path, relogio_falso):
    servidor = Servidor(rp_login="1")
    motivo, provas, registro = rodar(calendario_curto(janela(0.2, 1.5), janela(2.0, 1.5)), servidor, ajustes, tmp_path)
    assert motivo == "login_recusado" and logins(servidor) == 1
    assert [p["resultado"] for p in provas] == ["interrompido"]
    assert provas[0]["erro"] == "resposta_recusada_11_codigo_1"
    assert json.loads((registro.pasta / "estado.json").read_text())["estado"] == "QUARENTENA"


def test_guarda_em_quarentena_pula_o_dia_sem_conectar(ajustes, tmp_path, relogio_falso):
    guarda = tmp_path / "operacao"
    guarda.mkdir(mode=0o700)
    (guarda / "estado.json").write_text(json.dumps({"schema": "qhos2_operacao_v1", "estado": "QUARENTENA",
                                                    "geracao": "a" * 32}))
    motivo, provas, _ = rodar(calendario_curto(janela(0.1, 1.5)), sem_conexao, ajustes, tmp_path)
    assert motivo == "calendario_esgotado"
    assert [(p["resultado"], p["motivo"]) for p in provas] == [("pulado_guarda", "captura_em_quarentena")]


def test_partida_tardia_pula_o_dia(ajustes, tmp_path, relogio_falso):
    motivo, provas, _ = rodar(calendario_curto(janela(-20.0, 40.0)), sem_conexao, ajustes, tmp_path, atraso_max_s=5.0)
    assert motivo == "calendario_esgotado" and [p["resultado"] for p in provas] == ["pulado_partida_tardia"]


def test_dia_ja_registrado_nao_e_capturado_de_novo(ajustes, tmp_path, relogio_falso):
    a, b = janela(0.1, 1.5)
    registro = agenda.Registro(tmp_path / "dados" / "agenda")
    registro.provar(a.isoformat(), a, b, "interrompido")
    motivo, provas, _ = rodar(calendario_curto((a, b)), sem_conexao, ajustes, tmp_path)
    assert motivo == "calendario_esgotado" and len(provas) == 1


def test_janela_que_ja_terminou_e_ignorada(ajustes, tmp_path, relogio_falso):
    motivo, provas, _ = rodar(calendario_curto(janela(-30.0, 10.0)), sem_conexao, ajustes, tmp_path)
    assert motivo == "calendario_esgotado" and provas == []


def test_parar_durante_a_espera_nao_conecta(ajustes, tmp_path):
    # relogio real de proposito: a espera de verdade (`_esperar`) e que deve acordar com o `parar`; a janela esta a 30 s
    async def cena():
        parar = asyncio.Event()
        asyncio.get_running_loop().call_later(0.2, parar.set)
        registro = agenda.Registro(tmp_path / "dados" / "agenda")
        return await agenda.rodar_agenda(calendario_curto(janela(30.0, 5.0)), config(ajustes), sem_conexao,
                                         tmp_path / "capturas", CRED, registro, max_bytes=10 * 1024 ** 2,
                                         parar=parar, guarda_raiz=tmp_path / "operacao", um_por_dia=False,
                                         chave_dia=lambda a: a.isoformat(), saida=lambda _: None), registro
    motivo, registro = asyncio.run(cena())
    assert motivo == "parado_pelo_operador"
    assert json.loads((registro.pasta / "estado.json").read_text())["estado"] == "PARADO"


# ---- CLI --------------------------------------------------------------------------------------------------

@pytest.fixture
def ambiente(monkeypatch, tmp_path):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    return tmp_path


def test_cli_gera_calendario_sem_sobrescrever(ambiente, capsys):
    destino = ambiente / "cal.json"
    argv = ["calendario", "--de", "2026-10-28", "--ate", "2026-10-30", "--fonte", "CME", "--saida", str(destino)]
    assert agenda.main(argv) == 0
    assert len(json.loads(destino.read_text())["janelas"]) == 3
    with pytest.raises(FileExistsError):
        agenda.main(argv)


def test_cli_sem_autorizacao_nao_pede_senha(ambiente, monkeypatch, capsys):
    monkeypatch.setattr(captura, "_criar_fabrica", sem_conexao)
    cal = ambiente / "cal.json"
    cal.write_text(json.dumps(calendario_curto(janela(5.0, 30.0))))
    assert agenda.main(["rodar", "--calendario", str(cal), "--symbol", "ESZ6", "--exchange", "CME"],
                       entrada=sem_conexao, senha=sem_conexao) == 0
    assert "nada foi conectado" in capsys.readouterr().out
    assert not (ambiente / "dados" / "agenda").exists()


def test_cli_pede_a_senha_uma_vez_e_captura(ambiente, monkeypatch, capsys, relogio_falso):
    servidor = Servidor()
    monkeypatch.setattr(captura, "_criar_fabrica", lambda endpoint: servidor.fabrica())
    monkeypatch.setattr(agenda, "FOLGA_S", 0.5)
    monkeypatch.setattr(agenda, "DURACAO_MINIMA_S", 0.2)
    pedidos = []
    cal = ambiente / "cal.json"
    cal.write_text(json.dumps(calendario_curto(janela(0.5, 2.0))))
    codigo = agenda.main(["rodar", "--calendario", str(cal), "--symbol", "ESZ6", "--exchange", "CME", "--online",
                          "--agreements-accepted"],
                         entrada=lambda _: pedidos.append("u") or "trader@exemplo.com",
                         senha=lambda _: pedidos.append("s") or SENHA)
    saida = capsys.readouterr().out
    assert codigo == 0 and pedidos == ["u", "s"] and logins(servidor) == 1
    assert "agendador encerrado: calendario_esgotado" in saida and SENHA not in saida
    provas = (ambiente / "dados" / "agenda" / "provas.jsonl").read_text().splitlines()
    assert [json.loads(l)["resultado"] for l in provas] == ["capturado"]


def test_cli_login_recusado_sai_com_codigo_3(ambiente, monkeypatch, capsys, relogio_falso):
    servidor = Servidor(rp_login="1")
    monkeypatch.setattr(captura, "_criar_fabrica", lambda endpoint: servidor.fabrica())
    monkeypatch.setattr(agenda, "FOLGA_S", 0.5)
    monkeypatch.setattr(agenda, "DURACAO_MINIMA_S", 0.2)
    cal = ambiente / "cal.json"
    cal.write_text(json.dumps(calendario_curto(janela(0.3, 1.5))))
    codigo = agenda.main(["rodar", "--calendario", str(cal), "--symbol", "ESZ6", "--exchange", "CME", "--online",
                          "--agreements-accepted"], entrada=lambda _: "trader@exemplo.com", senha=lambda _: SENHA)
    assert codigo == 3 and logins(servidor) == 1
    assert "agendador encerrado: login_recusado" in capsys.readouterr().out
