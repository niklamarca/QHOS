"""Campanha de correcao (lote l5a-captura): agendador diario.

IDs: QHOS-AUD-076, 121, 038 (parte de rodar_agenda), 042, 097, 135, 199, 200.
`src/qhos2` congelado (J0): defeitos confirmados em `xfail(strict=True)`, patch proposto em
`09_correcao/EVIDENCIAS/<ID>/patch_proposto.diff`. Servidor falso de `test_sessao`; nada conecta.
"""
import ast
import asyncio
import errno
import inspect
import json
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

from qhos2 import agenda, captura
from qhos2.config import Settings
from qhos2.sessao import ConfigSessao

from test_sessao import CRED, SENHA, Servidor  # noqa: E402


@pytest.fixture
def ajustes(tmp_path):
    return Settings(data_root=tmp_path / "dados")


@pytest.fixture
def ambiente(monkeypatch, tmp_path):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    return tmp_path


def config(ajustes):
    return ConfigSessao(template_version="5.42", symbol="ESZ6", exchange="CME", duracao_s=None,
                        resposta_timeout_s=1.0, retrato_timeout_s=1.0, settings=ajustes)


def calendario_curto(*janelas, margem=60.0):
    agora = datetime.now(timezone.utc)
    return {"schema": "qhos2_calendario_v1", "fonte": "teste", "valido_de": (agora - timedelta(seconds=margem)).isoformat(),
            "valido_ate": (agora + timedelta(seconds=margem)).isoformat(),
            "janelas": [{"inicio": a.isoformat(), "fim": b.isoformat()} for a, b in janelas]}


def janela(atraso_s, duracao_s):
    a = datetime.now(timezone.utc) + timedelta(seconds=atraso_s)
    return a, a + timedelta(seconds=duracao_s)


def rodar(cal, servidor_ou_fabrica, ajustes, tmp_path, *, registro=None, **kw):
    fabrica = servidor_ou_fabrica.fabrica() if isinstance(servidor_ou_fabrica, Servidor) else servidor_ou_fabrica
    registro = registro if registro is not None else agenda.Registro(tmp_path / "dados" / "agenda")
    base = dict(max_bytes=10 * 1024 ** 2, folga_s=0.5, atraso_max_s=30.0, duracao_minima_s=0.2,
                guarda_raiz=tmp_path / "operacao", chave_dia=lambda a: a.isoformat(), um_por_dia=False,
                saida=lambda _: None)
    base.update(kw)
    motivo = asyncio.run(agenda.rodar_agenda(cal, config(ajustes), fabrica, tmp_path / "capturas", CRED, registro, **base))
    return motivo, provas_legiveis(registro), registro


def provas_legiveis(registro):
    """Linhas de provas.jsonl que sao JSON; uma linha truncada plantada pelo teste fica de fora."""
    if not registro.provas.exists():
        return []
    provas = []
    for linha in registro.provas.read_text().splitlines():
        try:
            provas.append(json.loads(linha))
        except ValueError:
            continue
    return provas


def sem_conexao(*_):
    raise AssertionError("nao devia conectar")


so_linux = pytest.mark.skipif(sys.platform != "linux", reason="GuardaCaptura exige Linux (guarda_requer_linux)")


def estado_registrado(registro):
    return json.loads((registro.pasta / "estado.json").read_text())


# ==== QHOS-AUD-076 ====
# Entrada do horario de verao de NY em 2027 (14/03/2027): so a saida de 2026 estava testada.

def test_calendario_atravessa_a_entrada_do_horario_de_verao_de_2027():
    cal = agenda.gerar_calendario(date(2027, 3, 12), date(2027, 3, 15), "pagina oficial da CME")
    assert [j["dia"] for j in cal["janelas"]] == ["2027-03-12", "2027-03-15"]  # 13 e 14 caem no fim de semana
    sexta, segunda = cal["janelas"]
    assert (sexta["inicio"], sexta["fim"]) == ("2027-03-12T14:00:00Z", "2027-03-12T21:15:00Z")      # EST (UTC-5)
    assert (segunda["inicio"], segunda["fim"]) == ("2027-03-15T13:00:00Z", "2027-03-15T20:15:00Z")  # EDT (UTC-4)
    assert (cal["valido_de"], cal["valido_ate"]) == ("2027-03-12T05:00:00Z", "2027-03-16T04:00:00Z")
    lista = agenda.janelas(cal)
    assert len(lista) == 2 and [agenda._dia_ny(a) for a, _ in lista] == ["2027-03-12", "2027-03-15"]
    # a virada acontece no domingo 14/03 as 02:00 de NY (07:00Z)
    assert agenda._dia_ny(datetime(2027, 3, 14, 6, 59, tzinfo=timezone.utc)) == "2027-03-14"
    assert datetime(2027, 3, 14, 7, 0, tzinfo=timezone.utc).astimezone(agenda.NY).utcoffset() == timedelta(hours=-4)
    assert datetime(2027, 3, 14, 6, 59, tzinfo=timezone.utc).astimezone(agenda.NY).utcoffset() == timedelta(hours=-5)


def test_duas_janelas_no_mesmo_dia_de_ny_na_virada_de_2027_sao_recusadas():
    cal = agenda.gerar_calendario(date(2027, 3, 12), date(2027, 3, 15), "CME")
    # 23:00Z de domingo 14/03 e 19:00 EDT do proprio 14/03 em NY: dia distinto, aceito;
    # 02:00Z de 16/03 e 22:00 EDT de 15/03: mesmo dia de NY da janela de segunda, recusado.
    cal["janelas"].insert(1, {"inicio": "2027-03-14T23:00:00Z", "fim": "2027-03-14T23:30:00Z"})
    assert [agenda._dia_ny(a) for a, _ in agenda.janelas(cal)] == ["2027-03-12", "2027-03-14", "2027-03-15"]
    cal["janelas"].append({"inicio": "2027-03-16T02:00:00Z", "fim": "2027-03-16T03:00:00Z"})
    with pytest.raises(agenda.AgendaError, match="mais_de_uma_janela_no_dia"):
        agenda.janelas(cal)


# ==== QHOS-AUD-121 ====
# agenda.main rodar com calendario todo no passado: retorno 2 e mensagem clara, sem teste.

def test_calendario_vencido_recusa_com_mensagem_clara(ambiente, monkeypatch, capsys):
    monkeypatch.setattr(captura, "_criar_fabrica", sem_conexao)
    arquivo = ambiente / "cal.json"
    arquivo.write_text(json.dumps(agenda.gerar_calendario(date(2026, 10, 1), date(2026, 10, 2), "CME")))
    codigo = agenda.main(["rodar", "--calendario", str(arquivo), "--symbol", "ESZ6", "--exchange", "CME",
                          "--online", "--agreements-accepted"], entrada=sem_conexao, senha=sem_conexao)
    saida = capsys.readouterr().out
    assert codigo == 2 and "nenhuma janela futura" in saida and "janelas restantes: 0" in saida
    assert not (ambiente / "dados").exists()  # nem registro nem lock foram criados
    assert agenda.main(["plano", "--calendario", str(arquivo)]) == 0
    assert "janelas restantes: 0" in capsys.readouterr().out


# ==== QHOS-AUD-038 ====
# rodar_agenda (agenda.py:203-214) so trata OperacaoError/GravadorError/FileExistsError/PermissionError:
# um OSError da captura sobe, estado.json fica em CAPTURANDO e o dia nao recebe prova.

@so_linux
@pytest.mark.xfail(strict=True, reason="QHOS-AUD-038: agenda.rodar_agenda:203-214 deixa OSError escapar; estado.json "
                                        "fica em CAPTURANDO com o processo morto e o dia fica sem prova")
def test_excecao_inesperada_na_captura_registra_estado_e_prova(ajustes, tmp_path, monkeypatch):
    async def falhar(*_, **__):
        raise OSError(errno.EIO, "erro de E/S sintetico")
    monkeypatch.setattr(captura, "capturar", falhar)
    a, b = janela(0.1, 2.0)
    registro = agenda.Registro(tmp_path / "dados" / "agenda")
    try:
        motivo = asyncio.run(agenda.rodar_agenda(calendario_curto((a, b)), config(ajustes), sem_conexao, tmp_path / "capturas",
                                                 CRED, registro, max_bytes=10 * 1024 ** 2, folga_s=0.5, atraso_max_s=30.0,
                                                 duracao_minima_s=0.2, guarda_raiz=tmp_path / "operacao",
                                                 chave_dia=lambda x: x.isoformat(), um_por_dia=False, saida=lambda _: None))
    except OSError:
        motivo = "propagado"  # propagar e aceitavel; sumir sem registro nao
    provas = [json.loads(l) for l in registro.provas.read_text().splitlines()] if registro.provas.exists() else []
    estado = estado_registrado(registro)
    assert estado["estado"] != "CAPTURANDO", (motivo, estado)
    assert provas and provas[0]["dia"] == a.isoformat() and provas[0]["resultado"].startswith("falhou"), provas
    assert SENHA not in registro.provas.read_text()


# ==== QHOS-AUD-042 ====
# Registro.dias_registrados:121-126 pula linhas corrompidas em silencio: o dia volta a ser elegivel.

@so_linux
@pytest.mark.xfail(strict=True, reason="QHOS-AUD-042: agenda.Registro.dias_registrados:124-125 pula a linha "
                                        "corrompida; o dia volta a ser elegivel e o agendador abre segunda captura")
def test_prova_corrompida_nao_libera_segunda_captura_no_dia(ajustes, tmp_path):
    a, b = janela(0.1, 1.5)
    registro = agenda.Registro(tmp_path / "dados" / "agenda")
    registro.provas.write_text('{"schema": "qhos2.agenda.prova.v1", "dia": "%s"' % a.isoformat())  # truncada antes do fsync
    chamadas = []

    async def fabrica():
        chamadas.append(1)
        raise AssertionError("nao devia conectar")
    motivo, provas, registro = rodar(calendario_curto((a, b)), fabrica, ajustes, tmp_path, registro=registro)
    assert chamadas == [] and not (tmp_path / "capturas").exists()
    assert motivo == "provas_corrompidas" or estado_registrado(registro)["estado"] == "FALHOU", motivo


@so_linux
def test_prova_integra_do_dia_continua_impedindo_segunda_captura(ajustes, tmp_path):
    a, b = janela(0.1, 1.5)
    registro = agenda.Registro(tmp_path / "dados" / "agenda")
    registro.provar(a.isoformat(), a, b, "interrompido")
    motivo, provas, _ = rodar(calendario_curto((a, b)), sem_conexao, ajustes, tmp_path, registro=registro)
    assert motivo == "calendario_esgotado" and len(provas) == 1 and not (tmp_path / "capturas").exists()


@so_linux
@pytest.mark.xfail(strict=True, reason="QHOS-AUD-042: com provas.jsonl corrompido, agenda.main captura o dia de novo "
                                        "e encerra com 0 (agenda.py:121-126 e :372); esperado: nao conectar e sair != 0")
def test_cli_do_agendador_com_provas_corrompidas_nao_conecta_e_nao_devolve_zero(ambiente, monkeypatch, capsys):
    hoje = agenda._dia_ny(datetime.now(timezone.utc))
    pasta = ambiente / "dados" / "agenda"
    pasta.mkdir(parents=True, mode=0o700)
    (pasta / "provas.jsonl").write_text('{"schema": "qhos2.agenda.prova.v1", "dia": "%s"' % hoje)  # truncada
    agora = datetime.now(timezone.utc)
    cal = ambiente / "cal.json"
    cal.write_text(json.dumps(calendario_curto((agora - timedelta(seconds=5), agora + timedelta(seconds=30)))))
    chamadas = []

    def fabrica(endpoint):
        async def abrir():
            chamadas.append(endpoint)
            raise AssertionError("nao devia conectar")
        return abrir
    monkeypatch.setattr(captura, "_criar_fabrica", fabrica)
    monkeypatch.setattr(agenda, "FOLGA_S", 0.5)
    monkeypatch.setattr(agenda, "DURACAO_MINIMA_S", 0.2)
    codigo = agenda.main(["rodar", "--calendario", str(cal), "--symbol", "ESZ6", "--exchange", "CME", "--online",
                          "--agreements-accepted"], entrada=lambda _: "trader@exemplo.com", senha=lambda _: SENHA)
    saida = capsys.readouterr().out
    assert chamadas == [], saida  # nenhuma conexao: o dia pode ja ter sido capturado
    assert codigo != 0, saida
    assert SENHA not in saida


# ==== QHOS-AUD-097 ====
# agenda.janelas:91-92 traduz KeyError em calendario_invalido sem dizer a chave.

@pytest.mark.xfail(strict=True, reason="QHOS-AUD-097: agenda.janelas:91-92 traduz KeyError('<chave>') em "
                                        "calendario_invalido sem dizer qual chave falta")
@pytest.mark.parametrize("chave", ["janelas", "valido_de", "valido_ate"])
def test_calendario_sem_chave_diz_qual_chave_falta(chave):
    cal = agenda.gerar_calendario(date(2026, 10, 28), date(2026, 10, 30), "CME")
    del cal[chave]
    with pytest.raises(agenda.AgendaError, match=chave):
        agenda.janelas(cal)


@pytest.mark.parametrize("chave", ["janelas", "valido_de", "valido_ate"])
def test_calendario_sem_chave_e_recusado_com_codigo_estatico(chave):
    cal = agenda.gerar_calendario(date(2026, 10, 28), date(2026, 10, 30), "CME")
    del cal[chave]
    with pytest.raises(agenda.AgendaError, match="^calendario_") as erro:
        agenda.janelas(cal)
    assert erro.value.__cause__ is None and "KeyError" not in str(erro.value)  # nada do dado externo vaza
    assert set(str(erro.value)) <= set("abcdefghijklmnopqrstuvwxyz_")  # codigo estatico, sem conteudo do arquivo


# ==== QHOS-AUD-135 ====
# agenda.Registro.estado/provar aceitam qualquer estado/resultado; o vocabulario so existe implicito em rodar_agenda.

ESTADOS_DO_AGENDADOR = {"AGUARDANDO", "CAPTURANDO", "QUARENTENA", "PARADO", "CALENDARIO_ESGOTADO"}
RESULTADOS_DAS_PROVAS = {"capturado", "interrompido", "pulado_partida_tardia", "pulado_sem_espaco", "pulado_guarda",
                         "pulado_janela_curta", "falhou_antes_do_login"}


def _literais(no):
    if isinstance(no, ast.Constant):
        return {no.value}
    if isinstance(no, ast.IfExp):
        return _literais(no.body) | _literais(no.orelse)
    return set()


# Vocabulario que os patches propostos de QHOS-AUD-038 (prova falhou_na_captura, estado FALHOU) e QHOS-AUD-042
# (estado FALHOU) acrescentariam: aceito aqui para o teste nao quebrar quando eles entrarem.
ESTADOS_FUTUROS = {"FALHOU"}
RESULTADOS_FUTUROS = {"falhou_na_captura"}


def vocabulario_do_codigo():
    """Literais passados a registro.estado(...) e registro.provar(..., resultado) em agenda.py."""
    estados, resultados = set(), set()
    for no in ast.walk(ast.parse(inspect.getsource(agenda))):
        if (isinstance(no, ast.Call) and isinstance(no.func, ast.Attribute) and isinstance(no.func.value, ast.Name)
                and no.func.value.id == "registro"):
            if no.func.attr == "estado" and no.args:
                estados |= _literais(no.args[0])
            elif no.func.attr == "provar" and len(no.args) >= 4:
                resultados |= _literais(no.args[3])
    return estados, resultados


def test_vocabulario_de_estados_e_resultados_do_agendador_e_fechado():
    estados, resultados = vocabulario_do_codigo()
    assert ESTADOS_DO_AGENDADOR <= estados <= ESTADOS_DO_AGENDADOR | ESTADOS_FUTUROS, estados
    assert RESULTADOS_DAS_PROVAS <= resultados <= RESULTADOS_DAS_PROVAS | RESULTADOS_FUTUROS, resultados
    assert all(isinstance(x, str) and x.isidentifier() for x in estados | resultados)  # codigos estaticos


def test_registro_grava_cada_estado_e_resultado_do_vocabulario(tmp_path):
    """Caracterizacao (QHOS-AUD-135): o vocabulario vive nos chamadores; Registro.estado/provar gravam o que recebem
    e nenhum contrato (ACHADOS campos 12/13) exige recusa na escrita. A validacao fica como sugestao em
    EVIDENCIAS/QHOS-AUD-135/patch_sugerido.diff."""
    registro = agenda.Registro(tmp_path / "agenda")
    a, b = janela(0, 60)
    for estado in sorted(ESTADOS_DO_AGENDADOR):
        registro.estado(estado, dia="2026-10-28")
        gravado = estado_registrado(registro)
        assert (gravado["schema"], gravado["estado"], gravado["dia"]) == ("qhos2.agenda.estado.v1", estado, "2026-10-28")
    for resultado in sorted(RESULTADOS_DAS_PROVAS):
        prova = registro.provar("2026-10-28", a, b, resultado)
        assert prova["schema"] == agenda.PROVA_ESQUEMA and prova["resultado"] == resultado
    assert [p["resultado"] for p in provas_legiveis(registro)] == sorted(RESULTADOS_DAS_PROVAS)
    assert "2026-10-28" in registro.dias_registrados()


# ==== QHOS-AUD-199 ====
# Registro.dias_registrados:121 segue symlink em provas.jsonl, enquanto provar:135 usa O_NOFOLLOW.

@pytest.mark.xfail(strict=True, reason="QHOS-AUD-199: Registro.dias_registrados:121 le provas.jsonl seguindo um "
                                        "symlink, enquanto provar:135 recusa (O_NOFOLLOW)")
def test_provas_atras_de_link_simbolico_nao_contam_como_registro(tmp_path):
    fora = tmp_path / "fora.jsonl"
    fora.write_text(json.dumps({"schema": agenda.PROVA_ESQUEMA, "dia": "2026-10-28", "resultado": "capturado"}) + "\n")
    registro = agenda.Registro(tmp_path / "agenda")
    registro.provas.symlink_to(fora)
    a, b = janela(0, 60)
    with pytest.raises(OSError):  # a escrita ja recusa o link (ELOOP)
        registro.provar("2026-10-29", a, b, "interrompido")
    try:
        dias = registro.dias_registrados()
    except agenda.AgendaError:
        dias = set()
    assert "2026-10-28" not in dias  # a leitura deve ser tao estrita quanto a escrita


# ==== QHOS-AUD-200 ====
# agenda.py:197-199: pulado_janela_curta e o unico dia pulado que nao chama saida().

@so_linux
@pytest.mark.xfail(strict=True, reason="QHOS-AUD-200: agenda.rodar_agenda:197-199 registra pulado_janela_curta sem "
                                        "chamar saida(); e o unico dia pulado mudo")
@so_linux
def test_janela_curta_e_anunciada_na_saida(ajustes, tmp_path):
    mensagens = []
    motivo, provas, _ = rodar(calendario_curto(janela(0.1, 1.0)), sem_conexao, ajustes, tmp_path,
                              duracao_minima_s=5.0, saida=mensagens.append)
    assert motivo == "calendario_esgotado" and [p["resultado"] for p in provas] == ["pulado_janela_curta"]
    assert any("pulado" in m and "janela curta" in m for m in mensagens), mensagens


@so_linux
def test_os_outros_dias_pulados_sao_anunciados_na_saida(ajustes, tmp_path):
    mensagens = []
    rodar(calendario_curto(janela(-20.0, 40.0)), sem_conexao, ajustes, tmp_path, atraso_max_s=5.0, saida=mensagens.append)
    assert any("pulado (partida tardia)" in m for m in mensagens), mensagens
