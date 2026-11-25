"""Lacunas de cobertura apontadas pelo code-simplifier (QHOS-AUD-204): asserções diretas, sem mudar o código.

(a) `operacao.prazo_calendario` chamado diretamente: validade, janela, `valido_ate` malformado e o retorno `float`;
    os caminhos de recusa da captura (`fora_da_janela_operacional` na CLI e em `capturar`) e o de expiração durante a
    sessão (`janela_operacional_expirada`, `captura.py:80`). A anotação e `float | None` e os chamadores so testam
    falsidade (`captura.py:47-48`, `operacao.py:191`): as asserções abaixo tambem.
(b) `max_gb_invalido` nas duas CLIs (`captura` e `agenda`): código 2 e a mensagem.
(c) FIFO e symlink no lugar de `manifest.json` para `operacao.copiar_captura` e `supervisao_coletor.selecionar`.
"""
import asyncio
import json
import os
import sys
from datetime import date, datetime, timezone

import pytest

from qhos2 import agenda, captura, catalogo, operacao
from qhos2 import supervisao_coletor as su
from qhos2.config import Settings
from qhos2.operacao import OperacaoError
from qhos2.sessao import ConfigSessao
from test_gravador import STATUS, gravar
from test_operacao import calendario
from test_sessao import CRED, Servidor, quadro160
from test_supervisao_coletor import AGORA, captura as captura_falsa

UTC = timezone.utc
SEM_SYMLINK = pytest.mark.skipif(sys.platform == "win32", reason="symlink exige privilegio no Windows")


def _em(hora, minuto=0, segundo=0):
    return datetime(2026, 10, 26, hora, minuto, segundo, tzinfo=UTC)


# ---- (a) prazo_calendario --------------------------------------------------------------------------------------

def test_prazo_e_o_fim_da_janela_como_float():
    prazo = operacao.prazo_calendario(calendario(), _em(10), 3600)
    assert isinstance(prazo, float) and prazo == _em(11).timestamp()
    assert operacao.prazo_calendario(calendario(), _em(10, 30), 60) == _em(11).timestamp()


def test_prazo_e_cortado_pelo_valido_ate():
    c = calendario()
    c["valido_ate"] = "2026-10-26T10:45:00Z"
    assert operacao.prazo_calendario(c, _em(10), 60) == _em(10, 45).timestamp()


def test_fora_da_janela_ou_da_validade_e_falso():
    # dentro da validade, mas em nenhuma janela
    assert not operacao.prazo_calendario(calendario(), _em(9), 30)
    assert not operacao.prazo_calendario(calendario(), _em(11), 30)
    # captura que atravessa o fim da janela
    assert not operacao.prazo_calendario(calendario(), _em(10), 3601)
    # fora da validade (antes de valido_de; alem de valido_ate)
    assert not operacao.prazo_calendario(calendario(), datetime(2026, 10, 25, 23, tzinfo=UTC), 30)
    assert not operacao.prazo_calendario(calendario(), _em(23, 59, 50), 3600)


@pytest.mark.parametrize("mudanca", [{"schema": "outro"}, {"fonte": ""}, {"valido_de": "2026-10-26T00:00:00"}])
def test_calendario_invalido_e_falso(mudanca):
    c = calendario()
    c.update(mudanca)
    assert not operacao.prazo_calendario(c, _em(10), 60)
    assert operacao.janela_permitida(c, _em(10), 60) is False


def test_duracao_invalida_ou_inicio_sem_fuso_e_falso():
    assert not operacao.prazo_calendario(calendario(), _em(10), float("nan"))
    assert not operacao.prazo_calendario(calendario(), _em(10), 0)
    assert not operacao.prazo_calendario(calendario(), datetime(2026, 10, 26, 10), 60)


def test_janela_invertida_e_falso():
    c = calendario()
    c["janelas"].append({"inicio": "2026-10-26T13:00:00Z", "fim": "2026-10-26T12:00:00Z"})
    assert not operacao.prazo_calendario(c, _em(10), 60)


def test_valido_ate_malformado_e_falso_antes_e_depois_de_valido_de():
    c = calendario()
    c["valido_ate"] = "nao e data"
    assert not operacao.prazo_calendario(c, datetime(2026, 10, 25, 23, tzinfo=UTC), 30)   # t < valido_de
    assert not operacao.prazo_calendario(c, _em(10), 30)                                  # t >= valido_de: le e falha
    assert operacao.janela_permitida(c, _em(10), 30) is False


def test_cli_recusa_calendario_fora_da_janela_antes_de_tudo(tmp_path, capsys):
    arquivo = tmp_path / "cal.json"
    arquivo.write_text(json.dumps(calendario()), encoding="utf-8")   # janela de 2026-10-26, ja passada
    with pytest.raises(SystemExit) as erro:
        captura.main(["--symbol", "ESZ6", "--exchange", "CME", "--calendario", str(arquivo),
                      "--raiz", str(tmp_path / "capturas")])
    assert erro.value.code == 2 and "destino_ou_calendario_invalido" in capsys.readouterr().err


def _config(tmp_path, duracao_s):
    ajustes = Settings(data_root=tmp_path / "dados")
    return ConfigSessao(template_version="5.42", symbol="ESZ6", exchange="CME", duracao_s=duracao_s,
                        resposta_timeout_s=1.0, retrato_timeout_s=1.0, settings=ajustes)


def test_capturar_fora_da_janela_nao_abre_sessao_nem_guarda(tmp_path):
    servidor = Servidor()
    with pytest.raises(OperacaoError, match="fora_da_janela_operacional"):
        asyncio.run(captura.capturar(_config(tmp_path, 0.45), servidor.fabrica(), tmp_path / "capturas", CRED,
                                     calendario=calendario()))
    assert not (tmp_path / "capturas").exists()
    guarda = captura.estado_padrao()
    assert not guarda.exists() or not any(guarda.iterdir())


def test_janela_que_expira_durante_a_sessao_fecha_a_captura_com_codigo(tmp_path, monkeypatch):
    """`captura.py:72-80`: o prazo vence no meio da sessao -> `janela_operacional_expirada`, manifesto escrito,
    captura verificavel, guarda liberada. O prazo e injetado (calendario valido com fim a 50 ms)."""
    monkeypatch.setattr(captura, "prazo_calendario", lambda cal, inicio, d: datetime.now(UTC).timestamp() + 0.05)
    servidor = Servidor(apos_assinar=tuple(quadro160(i) for i in range(5)))
    r = asyncio.run(captura.capturar(_config(tmp_path, 2.0), servidor.fabrica(), tmp_path / "capturas", CRED,
                                     calendario=calendario()))
    assert (r["erro"], r["tipo_de_falha"]) == ("janela_operacional_expirada", "PROTOCOL") or \
        r["erro"] == "janela_operacional_expirada"
    assert r["status"]["estado"] != "FECHADA" or not r["status"]["logout_confirmado"]
    assert catalogo.verificar_captura(r["pasta"])["verificada"] is True
    manifesto = json.loads(r["manifesto"].read_text(encoding="utf-8"))
    assert manifesto["transport_closed_normally"] is False
    with operacao.GuardaCaptura(captura.estado_padrao()) as guarda:
        with pytest.raises(OperacaoError, match="quarentena"):
            guarda.conferir()


# ---- (b) max_gb_invalido -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("valor", ["0", "-1", "101", "nan", "inf"])
def test_cli_da_captura_explica_o_teto_invalido(valor, capsys):
    with pytest.raises(SystemExit) as erro:
        captura.main(["--symbol", "ESZ6", "--exchange", "CME", "--max-gb", valor])
    assert erro.value.code == 2 and "max_gb_invalido" in capsys.readouterr().err


@pytest.mark.parametrize("valor", ["0", "101", "nan"])
def test_cli_da_agenda_explica_o_teto_invalido(tmp_path, valor, capsys):
    cal = agenda.gerar_calendario(date(2030, 1, 7), date(2030, 1, 8), "calendario sintetico de teste")
    arquivo = tmp_path / "cal.json"
    arquivo.write_text(json.dumps(cal), encoding="utf-8")
    with pytest.raises(SystemExit) as erro:
        agenda.main(["rodar", "--calendario", str(arquivo), "--symbol", "ESZ6", "--exchange", "CME", "--max-gb", valor])
    assert erro.value.code == 2 and "max_gb_invalido" in capsys.readouterr().err


def test_teto_valido_na_borda_e_aceito_sem_conectar(tmp_path, capsys):
    assert captura.main(["--symbol", "ESZ6", "--exchange", "CME", "--max-gb", "100",
                         "--raiz", str(tmp_path / "capturas")]) == 0
    assert "nada foi conectado" in capsys.readouterr().out


# ---- (c) FIFO e symlink no lugar de manifest.json ---------------------------------------------------------------

def _captura_fechada(tmp_path):
    g = gravar(tmp_path / "origem")
    g.fechar(STATUS, fechamento_normal=True)
    return g.pasta


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO so no POSIX")
def test_copia_recusa_manifesto_fifo_sem_bloquear(tmp_path):
    pasta = _captura_fechada(tmp_path)
    (pasta / "manifest.json").unlink()
    os.mkfifo(pasta / "manifest.json")
    with pytest.raises(OperacaoError, match="captura_nao_verificada"):
        operacao.copiar_captura(pasta, tmp_path / "copia")
    assert not (tmp_path / "copia").exists()


@SEM_SYMLINK
def test_copia_recusa_manifesto_symlink_mesmo_para_alvo_valido(tmp_path):
    pasta = _captura_fechada(tmp_path)
    alvo = tmp_path / "manifesto_fora.json"
    alvo.write_bytes((pasta / "manifest.json").read_bytes())
    (pasta / "manifest.json").unlink()
    (pasta / "manifest.json").symlink_to(alvo)
    with pytest.raises(OperacaoError, match="captura_nao_verificada"):
        operacao.copiar_captura(pasta, tmp_path / "copia")
    assert not (tmp_path / "copia").exists()


@SEM_SYMLINK
def test_supervisor_recusa_manifesto_symlink(tmp_path):
    pasta = captura_falsa(tmp_path, "x", criada=AGORA - 10)
    alvo = tmp_path / "manifesto_fora.json"
    alvo.write_bytes((pasta / "manifest.json").read_bytes())
    (pasta / "manifest.json").unlink()
    (pasta / "manifest.json").symlink_to(alvo)
    escolha = su.selecionar(tmp_path, agora=AGORA)
    assert escolha.pasta is None and escolha.codigo == "metadados_de_captura_invalidos"
