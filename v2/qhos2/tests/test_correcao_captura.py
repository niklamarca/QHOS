"""Campanha de correcao (lote l5a-captura): fluxo e CLI de captura.

IDs: QHOS-AUD-044, 045, 078, 038 (parte da CLI), 052, 060, 093, 094, 102, 198, 202.
`src/qhos2` esta congelado (janela J0): o que e defeito confirmado fica em `xfail(strict=True)` e o menor patch
proposto esta em `09_correcao/EVIDENCIAS/<ID>/patch_proposto.diff`. Servidor Rithmic falso de `test_sessao`;
credenciais ficticias; guarda e HOME isolados pelo conftest. Nada conecta fora do processo.
"""
import ast
import asyncio
import errno
import inspect
import json
import os
import signal
import subprocess
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from qhos2 import captura, catalogo
from qhos2 import rprotocol as p
from qhos2.config import Settings
from qhos2.gravador import Gravador
from qhos2.operacao import GuardaCaptura, OperacaoError
from qhos2.sessao import LOCAL, ConfigSessao

from test_sessao import CRED, SENHA, Servidor, resp_logout  # noqa: E402

so_linux = pytest.mark.skipif(sys.platform != "linux", reason="GuardaCaptura exige Linux (guarda_requer_linux)")
ARGS_CLI = ["--symbol", "ESZ6", "--exchange", "CME"]
ONLINE = ["--online", "--agreements-accepted"]


@pytest.fixture
def ajustes(tmp_path):
    return Settings(data_root=tmp_path / "dados")


@pytest.fixture
def ambiente(monkeypatch, tmp_path):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    return tmp_path


def config(ajustes, **kw):
    base = dict(template_version="5.42", symbol="ESZ6", exchange="CME", duracao_s=0.45,
                resposta_timeout_s=1.0, retrato_timeout_s=1.0, settings=ajustes)
    base.update(kw)
    return ConfigSessao(**base)


def sem_conexao(*_):
    raise AssertionError("nao devia conectar nem pedir credenciais")


def calendario_de(inicio: datetime, fim: datetime, *, margem=timedelta(hours=1), fonte="calendario sintetico de teste"):
    return {"schema": "qhos2_calendario_v1", "fonte": fonte,
            "valido_de": (inicio - margem).isoformat(), "valido_ate": (fim + margem).isoformat(),
            "janelas": [{"inicio": inicio.isoformat(), "fim": fim.isoformat()}]}


def janela_aberta(duracao=timedelta(hours=1)):
    agora = datetime.now(timezone.utc)
    return calendario_de(agora - timedelta(minutes=1), agora + duracao)


def _templates(dados):
    return {n: v for n, _, v in p._campos_brutos(dados)}


class ServidorLogoutLento(Servidor):
    """Responde ao logout (12) so depois de `atraso_s`: a sessao fica em SAINDO alem do prazo do calendario."""

    def __init__(self, atraso_s, **kw):
        super().__init__(**kw)
        self.atraso_s = atraso_s

    def reagir(self, con, dados):
        if _templates(dados)[154467] == 12:
            asyncio.get_running_loop().call_later(self.atraso_s, self._dar, con, resp_logout())
        else:
            super().reagir(con, dados)


def capturar_com_prazo_curto(ajustes, tmp_path):
    """Janela que termina 1,5 s depois do inicio; o logout demora 5 s: o prazo expira durante a sessao."""
    agora = datetime.now(timezone.utc)
    cal = calendario_de(agora - timedelta(seconds=60), agora + timedelta(seconds=1.5))
    servidor = ServidorLogoutLento(5.0)
    r = asyncio.run(captura.capturar(config(ajustes, duracao_s=0.3, resposta_timeout_s=10.0), servidor.fabrica(),
                                     tmp_path / "capturas", CRED, calendario=cal))
    return r, servidor


# ==== QHOS-AUD-044 ====
# Prazo do calendario expirando durante a sessao (captura.py:69-82): ramo sem nenhum teste.

@so_linux
def test_prazo_do_calendario_expira_durante_a_sessao(ajustes, tmp_path):
    r, servidor = capturar_com_prazo_curto(ajustes, tmp_path)
    assert r["erro"] == "janela_operacional_expirada"
    assert r["status"]["estado"] == "FALHOU" and r["status"]["logout_confirmado"] is False
    assert r["status"]["login_confirmado"] and r["status"]["assinatura_confirmada"]  # expirou ja em sessao
    relatorio = catalogo.verificar_captura(r["pasta"])
    assert relatorio["verificada"] is True and relatorio["fechamento_normal"] is False
    manifesto = json.loads(r["manifesto"].read_text())
    assert manifesto["status"]["sessao"]["erro"] == "janela_operacional_expirada"
    assert len(servidor.conexoes) == 2 and all(con.fechada for con in servidor.conexoes)
    with GuardaCaptura(captura.estado_padrao()) as guarda:  # o conftest aponta estado_padrao para o tmp_path
        assert guarda.estado["estado"] == "QUARENTENA" and guarda.estado["motivo"] == "encerramento_nao_confirmado"
        with pytest.raises(OperacaoError, match="captura_em_quarentena"):
            guarda.conferir()
    assert SENHA not in r["manifesto"].read_text()


@so_linux
def test_prazo_ja_vencido_na_partida_recusa_antes_de_conectar(ajustes, tmp_path):
    agora = datetime.now(timezone.utc)
    cal = calendario_de(agora - timedelta(hours=2), agora - timedelta(hours=1))
    servidor = Servidor()
    with pytest.raises(OperacaoError, match="fora_da_janela_operacional"):
        asyncio.run(captura.capturar(config(ajustes), servidor.fabrica(), tmp_path / "capturas", CRED, calendario=cal))
    assert servidor.conexoes == [] and not (tmp_path / "capturas").exists()


# ==== QHOS-AUD-094 ====
# captura.py:80 constroi SessaoError("janela_operacional_expirada") com o tipo padrao PROTOCOL para um prazo local.

@so_linux
@pytest.mark.xfail(strict=True, reason="QHOS-AUD-094: captura.py:80 usa o tipo padrao PROTOCOL de SessaoError para "
                                        "janela_operacional_expirada, um prazo local; esperado LOCAL_ERROR")
def test_prazo_expirado_e_falha_local_no_resultado_e_no_manifesto(ajustes, tmp_path):
    r, _ = capturar_com_prazo_curto(ajustes, tmp_path)
    assert r["erro"] == "janela_operacional_expirada"
    assert r["tipo_de_falha"] == LOCAL
    manifesto = json.loads(r["manifesto"].read_text())
    assert manifesto["status"]["sessao"]["tipo_de_falha"] == LOCAL


# ==== QHOS-AUD-045 ====
# CLI com calendario fora da janela ou invalido (captura.py:179-185): recusa antes das credenciais, sem teste.

@pytest.mark.parametrize("estrago", ["janela_passada", "json_invalido", "arquivo_inexistente"])
def test_cli_calendario_fora_da_janela_recusa_antes_das_credenciais(ambiente, monkeypatch, capsys, estrago):
    monkeypatch.setattr(captura, "_criar_fabrica", sem_conexao)
    arquivo = ambiente / "cal.json"
    if estrago == "janela_passada":
        agora = datetime.now(timezone.utc)
        arquivo.write_text(json.dumps(calendario_de(agora - timedelta(hours=2), agora - timedelta(hours=1))))
    elif estrago == "json_invalido":
        arquivo.write_text("{")
    with pytest.raises(SystemExit) as erro:
        captura.main([*ARGS_CLI, "--calendario", str(arquivo), *ONLINE], entrada=sem_conexao, senha=sem_conexao)
    assert erro.value.code == 2
    assert "destino_ou_calendario_invalido" in capsys.readouterr().err
    assert not (ambiente / "dados").exists()


def test_cli_calendario_com_janela_aberta_segue_ate_o_plano(ambiente, monkeypatch, capsys):
    monkeypatch.setattr(captura, "_criar_fabrica", sem_conexao)
    arquivo = ambiente / "cal.json"
    arquivo.write_text(json.dumps(janela_aberta()))
    assert captura.main([*ARGS_CLI, "--calendario", str(arquivo)], entrada=sem_conexao, senha=sem_conexao) == 0
    assert "nada foi conectado" in capsys.readouterr().out


# ==== QHOS-AUD-052 ====
# captura.py:184-185: ConfigError, OSError, ValueError e OperacaoError viram o mesmo destino_ou_calendario_invalido.

@pytest.mark.xfail(strict=True, reason="QHOS-AUD-052: captura.main:184-185 colapsa calendario ausente (OSError), "
                                        "malformado (ValueError), fora da janela (OperacaoError) e raiz em git "
                                        "(ConfigError) em destino_ou_calendario_invalido")
def test_cli_distingue_calendario_ausente_malformado_fora_da_janela_e_raiz_em_git(ambiente, monkeypatch, capsys):
    monkeypatch.setattr(captura, "_criar_fabrica", sem_conexao)
    agora = datetime.now(timezone.utc)
    passado = ambiente / "passado.json"
    passado.write_text(json.dumps(calendario_de(agora - timedelta(hours=2), agora - timedelta(hours=1))))
    malformado = ambiente / "malformado.json"
    malformado.write_text("{")
    aberto = ambiente / "aberto.json"
    aberto.write_text(json.dumps(janela_aberta()))
    repo = ambiente / "repo"
    (repo / ".git").mkdir(parents=True)
    casos = {"ausente": ["--calendario", str(ambiente / "nao_existe.json")],
             "malformado": ["--calendario", str(malformado)],
             "fora_da_janela": ["--calendario", str(passado)],
             "raiz_em_git": ["--calendario", str(aberto), "--raiz", str(repo / "dados")]}
    codigos = {}
    for nome, extra in casos.items():
        with pytest.raises(SystemExit) as erro:
            captura.main([*ARGS_CLI, *extra, *ONLINE], entrada=sem_conexao, senha=sem_conexao)
        assert erro.value.code == 2
        codigos[nome] = capsys.readouterr().err.strip().splitlines()[-1].split("error:")[-1].strip()
    assert len(set(codigos.values())) == 4, codigos
    assert codigos["fora_da_janela"] == "fora_da_janela_operacional"
    assert codigos["raiz_em_git"] == "data_root_dentro_de_repositorio_git"


# ==== QHOS-AUD-078 ====
# Parada segura por SIGINT (_parada_segura, captura.py:97-110; agenda.py:344-355) e segundo SIGINT, sem teste.
# Processo descartavel proprio: o sinal vai so para o filho que este teste cria.

ROTEIRO_SIGINT = r'''
import asyncio, json, os, signal, sys
from datetime import datetime, timedelta, timezone
from qhos2 import agenda, captura, rprotocol as p
from test_sessao import SENHA, Servidor, resp_assinatura

modo = sys.argv[1]


class CancelamentoLento(Servidor):
    """Confirma o cancelamento da assinatura so depois de 5 s: a sessao fica em CANCELANDO."""
    def reagir(self, con, dados):
        campos = {n: v for n, _, v in p._campos_brutos(dados)}
        if campos[154467] == 117 and campos[100000] != 1:
            asyncio.get_running_loop().call_later(5.0, self._dar, con, resp_assinatura())
        else:
            super().reagir(con, dados)


servidor = CancelamentoLento() if modo.endswith("dois") else Servidor()
captura._criar_fabrica = lambda endpoint: servidor.fabrica()

# Marcadores sincronizam o pai: ele so manda SIGINT depois de "handler instalado N" e o segundo so depois de
# "handler removido" (quando o default_int_handler ja esta de volta).
_add, _remove = asyncio.SelectorEventLoop.add_signal_handler, asyncio.SelectorEventLoop.remove_signal_handler
instalados = [0]


def add_marcado(self, sig, callback, *args):
    _add(self, sig, callback, *args)
    if sig == signal.SIGINT:
        instalados[0] += 1
        print(f"handler instalado {instalados[0]}", flush=True)


def remove_marcado(self, sig):
    removido = _remove(self, sig)
    if sig == signal.SIGINT:
        print("handler removido", flush=True)
    return removido


asyncio.SelectorEventLoop.add_signal_handler = add_marcado
asyncio.SelectorEventLoop.remove_signal_handler = remove_marcado
if modo.startswith("captura"):
    codigo = captura.main(["--symbol", "ESZ6", "--exchange", "CME", "--duration", "30", "--max-gb", "0.01",
                           "--online", "--agreements-accepted"], entrada=lambda _: "u", senha=lambda _: SENHA)
else:
    agora = datetime.now(timezone.utc)
    cal = {"schema": "qhos2_calendario_v1", "fonte": "teste", "valido_de": (agora - timedelta(hours=1)).isoformat(),
           "valido_ate": (agora + timedelta(hours=1)).isoformat(),
           "janelas": [{"inicio": (agora - timedelta(seconds=5)).isoformat(),
                        "fim": (agora + timedelta(seconds=60)).isoformat()}]}
    os.makedirs(os.environ["QHOS2_DATA_ROOT"], exist_ok=True)
    arquivo = os.path.join(os.environ["QHOS2_DATA_ROOT"], "cal.json")
    with open(arquivo, "w") as f:
        json.dump(cal, f)
    agenda.FOLGA_S, agenda.DURACAO_MINIMA_S = 0.5, 0.2
    codigo = agenda.main(["rodar", "--calendario", arquivo, "--symbol", "ESZ6", "--exchange", "CME", "--max-gb", "0.01",
                          "--online", "--agreements-accepted"], entrada=lambda _: "u", senha=lambda _: SENHA)
print("codigo_de_saida", codigo, flush=True)
sys.exit(codigo)
'''


def rodar_cli_com_sigint(tmp_path, modo, *, timeout=60):
    """Roda captura.main/agenda.main num filho proprio e manda SIGINT (um ou dois) sincronizado pelas linhas que o
    filho imprime: o 1o depois de "handler instalado N" (N=1 na captura; N=2 no agendador, cujo 1o handler e o
    parar.set de instalar()), o 2o depois de "handler removido". Devolve (returncode, stdout, stderr)."""
    roteiro = tmp_path / "roteiro_sigint.py"
    roteiro.write_text(ROTEIRO_SIGINT, encoding="utf-8")
    src = Path(captura.__file__).resolve().parents[1]
    env = dict(os.environ, HOME=str(tmp_path / "home"), USERPROFILE=str(tmp_path / "home"),
               QHOS2_DATA_ROOT=str(tmp_path / "dados"), PYTHONPATH=f"{src}{os.pathsep}{Path(__file__).resolve().parent}")
    (tmp_path / "home").mkdir()
    filho = subprocess.Popen([sys.executable, str(roteiro), modo], env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    linhas = []

    def ate(marca):
        for linha in filho.stdout:
            linhas.append(linha)
            if marca in linha:
                return True
        return False
    try:
        assert ate("handler instalado 2" if modo.startswith("agenda") else "handler instalado 1"), "".join(linhas)
        os.kill(filho.pid, signal.SIGINT)
        if modo.endswith("dois"):
            assert ate("handler removido"), "".join(linhas)
            os.kill(filho.pid, signal.SIGINT)
        resto, erros = filho.communicate(timeout=timeout)
    finally:
        if filho.poll() is None:
            filho.kill()
    return types.SimpleNamespace(returncode=filho.returncode, stdout="".join(linhas) + resto, stderr=erros)


@pytest.mark.skipif(sys.platform != "linux", reason="guarda e sinais POSIX")
def test_sigint_pede_parada_segura_e_a_captura_fecha_com_logout(tmp_path):
    resultado = rodar_cli_com_sigint(tmp_path, "captura_um")
    saida = resultado.stdout
    assert resultado.returncode == 0, (saida, resultado.stderr)
    assert "Parando com seguranca" in saida and "sessao: estado=FECHADA" in saida
    assert "cancelamento=True logout=True" in saida and "verificada=True" in saida
    assert "abortado" not in saida and SENHA not in saida
    pastas = list((tmp_path / "dados" / "capturas").iterdir())
    assert len(pastas) == 1 and catalogo.verificar_captura(pastas[0])["fechamento_normal"] is True
    with GuardaCaptura(tmp_path / "home" / ".local" / "state" / "qhos2" / "operacao") as guarda:
        assert guarda.estado["estado"] == "LIVRE"


@pytest.mark.skipif(sys.platform != "linux", reason="guarda e sinais POSIX")
def test_segundo_sigint_aborta_a_captura_com_codigo_2(tmp_path):
    resultado = rodar_cli_com_sigint(tmp_path, "captura_dois")
    saida = resultado.stdout
    assert resultado.returncode == 2, (saida, resultado.stderr)
    assert "Parando com seguranca" in saida and "abortado" in saida and SENHA not in saida
    with GuardaCaptura(tmp_path / "home" / ".local" / "state" / "qhos2" / "operacao") as guarda:
        assert guarda.estado["estado"] == "QUARENTENA"  # sem logout confirmado: exige liberacao manual


@pytest.mark.skipif(sys.platform != "linux", reason="guarda e sinais POSIX")
def test_sigint_no_agendador_para_com_seguranca_depois_da_captura_do_dia(tmp_path):
    resultado = rodar_cli_com_sigint(tmp_path, "agenda_um")
    saida = resultado.stdout
    assert resultado.returncode == 0, (saida, resultado.stderr)
    assert "Parando com seguranca" in saida and "agendador encerrado: parado_pelo_operador" in saida
    provas = [json.loads(l) for l in (tmp_path / "dados" / "agenda" / "provas.jsonl").read_text().splitlines()]
    assert [p_["resultado"] for p_ in provas] == ["capturado"] and provas[0]["fechamento_normal"] is True
    estado = json.loads((tmp_path / "dados" / "agenda" / "estado.json").read_text())
    assert estado["estado"] == "PARADO" and estado["motivo"] == "pedido_do_operador"
    assert SENHA not in saida and SENHA not in (tmp_path / "dados" / "agenda" / "provas.jsonl").read_text()


# ==== QHOS-AUD-038 ====
# Excecoes fora das listas escapam de captura.main (:216-224) e o abort do agendador devolve 0 (agenda.py:372).
# A parte de rodar_agenda (estado.json em CAPTURANDO) esta em test_correcao_agenda.py.

@so_linux
@pytest.mark.xfail(strict=True, reason="QHOS-AUD-038: captura.main:216-224 so trata OperacaoError, GravadorError, "
                                        "FileExistsError, PermissionError e KeyboardInterrupt; um OSError da captura "
                                        "sobe como traceback em vez de codigo 2 com mensagem")
def test_cli_erro_de_sistema_na_captura_devolve_2_com_mensagem(ambiente, monkeypatch, capsys):
    async def falhar(*_, **__):
        raise OSError(errno.EIO, "erro de E/S sintetico")
    monkeypatch.setattr(captura, "capturar", falhar)
    monkeypatch.setattr(captura, "_criar_fabrica", lambda endpoint: sem_conexao)
    codigo = captura.main([*ARGS_CLI, *ONLINE], entrada=lambda _: "u", senha=lambda _: SENHA)
    saida = capsys.readouterr().out
    assert codigo == 2 and "captura nao concluida" in saida and SENHA not in saida


@pytest.mark.skipif(sys.platform != "linux", reason="guarda e sinais POSIX")
@pytest.mark.xfail(strict=True, reason="QHOS-AUD-038: agenda.main:365-372 devolve 0 para motivo 'abortado' "
                                        "(segundo Ctrl+C), escondendo o abort de quem chamou")
def test_cli_do_agendador_abortado_nao_devolve_zero(tmp_path):
    resultado = rodar_cli_com_sigint(tmp_path, "agenda_dois")
    saida = resultado.stdout
    assert "agendador encerrado: abortado" in saida, (saida, resultado.stderr)
    assert resultado.returncode != 0


# ==== QHOS-AUD-060 ====
# captura.main:222-224 afirma "a captura parcial foi fechada" sem verificar que o manifesto existe.

@so_linux
@pytest.mark.xfail(strict=True, reason="QHOS-AUD-060: captura.main:223 afirma 'a captura parcial foi fechada' mesmo "
                                        "quando o KeyboardInterrupt caiu dentro de gravador.fechar e nao ha manifest.json")
def test_abort_durante_fechar_nao_afirma_captura_fechada(ambiente, monkeypatch, capsys):
    servidor = Servidor()
    monkeypatch.setattr(captura, "_criar_fabrica", lambda endpoint: servidor.fabrica())

    def interromper(self, *_, **__):
        raise KeyboardInterrupt
    monkeypatch.setattr(Gravador, "fechar", interromper)
    codigo = captura.main([*ARGS_CLI, "--duration", "0.2", *ONLINE], entrada=lambda _: "u", senha=lambda _: SENHA)
    saida = capsys.readouterr().out
    pastas = list((ambiente / "dados" / "capturas").iterdir())
    assert codigo == 2 and "abortado" in saida
    assert len(pastas) == 1 and not (pastas[0] / "manifest.json").exists()
    assert "foi fechada" not in saida


# ==== QHOS-AUD-093 ====
# captura._parada_segura:106-110 engole NotImplementedError/RuntimeError de add_signal_handler sem avisar.

@so_linux
@pytest.mark.xfail(strict=True, reason="QHOS-AUD-093: captura._parada_segura:109-110 engole a falha de "
                                        "add_signal_handler; o operador nao sabe que Ctrl+C vira abort duro (quarentena)")
def test_parada_segura_indisponivel_e_avisada_ao_operador(ambiente, monkeypatch, capsys):
    servidor = Servidor()
    monkeypatch.setattr(captura, "_criar_fabrica", lambda endpoint: servidor.fabrica())

    def indisponivel(self, *_):
        raise RuntimeError("loop sem suporte a sinais (sintetico)")
    monkeypatch.setattr(asyncio.SelectorEventLoop, "add_signal_handler", indisponivel)
    codigo = captura.main([*ARGS_CLI, "--duration", "0.2", *ONLINE], entrada=lambda _: "u", senha=lambda _: SENHA)
    saida = capsys.readouterr().out
    assert codigo == 0 and "sessao: estado=FECHADA" in saida  # a captura segue sem a parada segura...
    assert "parada segura" in saida.lower() and "indisponivel" in saida.lower()  # ...e o operador e avisado


@so_linux
def test_parada_segura_indisponivel_nao_impede_a_captura(ambiente, monkeypatch, capsys):
    servidor = Servidor()
    monkeypatch.setattr(captura, "_criar_fabrica", lambda endpoint: servidor.fabrica())

    def indisponivel(self, *_):
        raise RuntimeError("loop sem suporte a sinais (sintetico)")
    monkeypatch.setattr(asyncio.SelectorEventLoop, "add_signal_handler", indisponivel)
    codigo = captura.main([*ARGS_CLI, "--duration", "0.2", *ONLINE], entrada=lambda _: "u", senha=lambda _: SENHA)
    assert codigo == 0 and "sessao: estado=FECHADA" in capsys.readouterr().out


# ==== QHOS-AUD-102 ====
# captura.py:68, 84-92: se guarda.iniciar() falha, o finally ainda fecha o gravador e grava um manifesto.
# Caracterizacao: o contrato nao diz o que deve existir nesse caso (ver FICHA; pergunta ao autor).

@so_linux
def test_falha_ao_iniciar_a_guarda_deixa_manifesto_de_sessao_que_nao_existiu(ajustes, tmp_path, monkeypatch):
    def recusar(self):
        raise OSError(errno.EACCES, "estado.json sem permissao (sintetico)")
    monkeypatch.setattr(GuardaCaptura, "iniciar", recusar)
    servidor = Servidor()
    with pytest.raises(OSError):
        asyncio.run(captura.capturar(config(ajustes), servidor.fabrica(), tmp_path / "capturas", CRED,
                                     captura_id="nunca-existiu"))
    assert servidor.conexoes == []  # nada conectou
    pasta = tmp_path / "capturas" / "nunca-existiu"
    relatorio = catalogo.verificar_captura(pasta)
    manifesto = json.loads((pasta / "manifest.json").read_text())
    assert relatorio["verificada"] is True and relatorio["quadros"] == 0 and relatorio["fechamento_normal"] is False
    assert manifesto["status"]["sessao"]["estado"] == "NOVA" and manifesto["status"]["sessao"]["erro"] is None
    with GuardaCaptura(captura.estado_padrao()) as guarda:
        assert guarda.estado["estado"] == "LIVRE"  # a guarda nunca chegou a EM_ANDAMENTO


# ==== QHOS-AUD-198 ====
# captura.py:181 le o calendario com read_text() sem encoding (agenda.py:286 usa utf-8).

def _calendario_utf8(tmp_path):
    arquivo = tmp_path / "cal.json"
    arquivo.write_text(json.dumps(janela_aberta(), ensure_ascii=False), encoding="utf-8")
    return arquivo


def _rodar_em_locale_ascii(tmp_path, codigo_python, *args):
    src = Path(captura.__file__).resolve().parents[1]
    env = dict(os.environ, HOME=str(tmp_path), QHOS2_DATA_ROOT=str(tmp_path / "dados"), PYTHONPATH=str(src),
               LC_ALL="C", LANG="C", PYTHONCOERCECLOCALE="0", PYTHONUTF8="0", PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-c", codigo_python, *args], env=env, capture_output=True, text=True, timeout=60)


@pytest.mark.skipif(sys.platform != "linux", reason="locale C sem coercao so e reproduzivel no Linux")
@pytest.mark.xfail(strict=True, reason="QHOS-AUD-198: captura.main:181 usa read_text() sem encoding; num locale "
                                        "nao UTF-8 um calendario UTF-8 e recusado como destino_ou_calendario_invalido")
def test_calendario_utf8_e_lido_pela_cli_de_captura_em_locale_ascii(tmp_path):
    arquivo = _calendario_utf8(tmp_path)
    janela_aberta_fonte = json.loads(arquivo.read_text(encoding="utf-8"))
    janela_aberta_fonte["fonte"] = "pregão regular da CME"
    arquivo.write_text(json.dumps(janela_aberta_fonte, ensure_ascii=False), encoding="utf-8")
    codigo = "import sys; from qhos2 import captura; sys.exit(captura.main(['--symbol', 'ESZ6', '--exchange', 'CME', '--calendario', sys.argv[1]]))"
    r = _rodar_em_locale_ascii(tmp_path, codigo, str(arquivo))
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "plano:" in r.stdout and "nada foi conectado" in r.stdout


@pytest.mark.skipif(sys.platform != "linux", reason="locale C sem coercao so e reproduzivel no Linux")
def test_calendario_utf8_e_lido_pela_cli_da_agenda_em_locale_ascii(tmp_path):
    arquivo = _calendario_utf8(tmp_path)
    cal = json.loads(arquivo.read_text(encoding="utf-8"))
    cal["fonte"] = "pregão regular da CME"
    arquivo.write_text(json.dumps(cal, ensure_ascii=False), encoding="utf-8")
    codigo = "import sys; from qhos2 import agenda; sys.exit(agenda.main(['plano', '--calendario', sys.argv[1]]))"
    r = _rodar_em_locale_ascii(tmp_path, codigo, str(arquivo))
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "janelas restantes: 1" in r.stdout


# ==== QHOS-AUD-202 ====
# captura.py:81 chama o privado sessao._falhar a partir de outro modulo.

PRIVADOS_DA_SESSAO_CONHECIDOS = {"sessao._falhar"}  # captura.py:81; expor Sessao.encerrar_com e a sugestao (patch_sugerido.diff)


def test_captura_so_usa_os_membros_privados_da_sessao_ja_conhecidos():
    """Caracterizacao (QHOS-AUD-202): o acoplamento privado entre modulos e uma observacao de desenho, nao um
    defeito contratual; este teste impede que apareca outro sem registro."""
    arvore = ast.parse(inspect.getsource(captura))
    privados = {f"{n.value.id}.{n.attr}" for n in ast.walk(arvore)
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                and n.value.id == "sessao" and n.attr.startswith("_")}
    assert privados <= PRIVADOS_DA_SESSAO_CONHECIDOS, privados
