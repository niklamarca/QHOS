"""Peca 4c: a captura grava tudo o que a sessao recebe, fecha o manifesto sempre e a CLI nunca conecta sem autorizacao."""
import asyncio
import json
import shutil
from collections import namedtuple

import pytest

from qhos2 import captura, catalogo
from qhos2.config import Settings
from qhos2.gravador import GravadorError
from qhos2.sessao import ConfigSessao

# Reaproveita o servidor falso da sessao: o pytest poe esta pasta no caminho de importacao.
from test_sessao import CRED, SENHA, Servidor, ate, quadro160  # noqa: E402


@pytest.fixture(autouse=True)
def guarda_isolada(tmp_path, monkeypatch):
    monkeypatch.setattr(captura, "estado_padrao", lambda: tmp_path / "operacao")


@pytest.fixture
def ajustes(tmp_path):
    return Settings(data_root=tmp_path / "dados")


def config(ajustes, **kw):
    base = dict(template_version="5.42", symbol="ESZ6", exchange="CME", duracao_s=0.45,
                resposta_timeout_s=1.0, retrato_timeout_s=1.0, settings=ajustes)
    base.update(kw)
    return ConfigSessao(**base)


def rodar(servidor, ajustes, tmp_path, **kw):
    cfg_kw = kw.pop("config", {})
    return asyncio.run(captura.capturar(config(ajustes, **cfg_kw), servidor.fabrica(), tmp_path / "capturas", CRED, **kw))


# ---- capturar -------------------------------------------------------------------------------------------

def test_captura_completa_e_verificada(ajustes, tmp_path):
    servidor = Servidor(durante_retrato=(quadro160(1),), apos_assinar=tuple(quadro160(i) for i in range(10, 14)))
    r = rodar(servidor, ajustes, tmp_path)
    assert r["erro"] is None and r["status"]["estado"] == "FECHADA"
    relatorio = catalogo.verificar_captura(r["pasta"])
    assert relatorio["verificada"] is True and relatorio["quadros"] == len(servidor.mercado)
    assert (relatorio["ambiente"], relatorio["simbolo"], relatorio["bolsa"], relatorio["fechamento_normal"]) == (
        "paper", "ESZ6", "CME", True)
    manifesto = json.loads(r["manifesto"].read_text())
    assert manifesto["status"]["sessao"]["logout_confirmado"] is True and manifesto["can_execute"] is False
    assert SENHA not in r["manifesto"].read_text() and "trader@exemplo.com" not in r["manifesto"].read_text()


def test_login_recusado_ainda_fecha_o_manifesto(ajustes, tmp_path):
    r = rodar(Servidor(rp_login="1"), ajustes, tmp_path)
    assert (r["erro"], r["tipo_de_falha"]) == ("resposta_recusada_11_codigo_1", "PROTOCOL")
    relatorio = catalogo.verificar_captura(r["pasta"])
    assert relatorio["verificada"] is True and relatorio["quadros"] == 0 and relatorio["fechamento_normal"] is False


def test_falha_de_gravacao_deixa_captura_parcial_valida(ajustes, tmp_path):
    r = rodar(Servidor(), ajustes, tmp_path, max_bytes=300)
    assert r["erro"] == "gravacao_falhou" and r["tipo_de_falha"] == "LOCAL_ERROR"
    relatorio = catalogo.verificar_captura(r["pasta"])
    assert relatorio["verificada"] is True and relatorio["fechamento_normal"] is False and relatorio["quadros"] >= 1


def test_cancelamento_fecha_o_manifesto_e_propaga(ajustes, tmp_path):
    servidor = Servidor()

    async def cena():
        sessoes = []
        tarefa = asyncio.create_task(captura.capturar(config(ajustes, duracao_s=5.0), servidor.fabrica(),
                                                      tmp_path / "capturas", CRED, captura_id="cancelada",
                                                      quando_pronta=sessoes.append))
        await ate(lambda: sessoes and sessoes[0].status()["retrato_completo"])   # ha quadros gravados (QHOS-AUD-085)
        tarefa.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarefa
    asyncio.run(cena())
    relatorio = catalogo.verificar_captura(tmp_path / "capturas" / "cancelada")
    assert relatorio["verificada"] is True and relatorio["fechamento_normal"] is False and relatorio["quadros"] > 0


def test_parada_segura_encerra_com_logout(ajustes, tmp_path):
    servidor = Servidor()
    paradas = []

    def agendar(sessao):
        async def parar_em_streaming():
            try:
                await ate(lambda: sessao.status()["retrato_completo"])   # parada depois do retrato, nao apos 0,3 s
            finally:
                sessao.parar()          # sempre: sem isto, com duracao_s=None, uma espera que estoura penduraria a captura
        paradas.append(asyncio.ensure_future(parar_em_streaming()))
    r = rodar(servidor, ajustes, tmp_path, config={"duracao_s": None}, quando_pronta=agendar)
    assert len(paradas) == 1 and paradas[0].done() and paradas[0].exception() is None   # a parada veio do retrato completo
    assert r["erro"] is None and r["status"]["logout_confirmado"] and r["status"]["cancelamento_confirmado"]
    assert catalogo.verificar_captura(r["pasta"])["fechamento_normal"] is True


def test_nao_sobrescreve_captura_existente(ajustes, tmp_path):
    rodar(Servidor(), ajustes, tmp_path, captura_id="mesma")
    with pytest.raises(FileExistsError):
        rodar(Servidor(), ajustes, tmp_path, captura_id="mesma")


# ---- CLI --------------------------------------------------------------------------------------------------

@pytest.fixture
def ambiente(monkeypatch, tmp_path):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    return tmp_path


def sem_conexao(*_):
    raise AssertionError("nao devia conectar")


def test_sem_autorizacao_so_mostra_o_plano(ambiente, monkeypatch, capsys):
    monkeypatch.setattr(captura, "_criar_fabrica", sem_conexao)
    for extra in ([], ["--online"], ["--agreements-accepted"]):
        assert captura.main(["--symbol", "ESZ6", "--exchange", "CME", *extra],
                            entrada=sem_conexao, senha=sem_conexao) == 0
    saida = capsys.readouterr().out
    assert saida.count("nada foi conectado") == 3 and "Rithmic Paper Trading" in saida and "nila:QHOS" in saida
    assert not (ambiente / "dados").exists()


def test_cli_completa_com_servidor_falso(ambiente, monkeypatch, capsys):
    servidor = Servidor()
    monkeypatch.setattr(captura, "_criar_fabrica", lambda endpoint: servidor.fabrica())
    codigo = captura.main(["--symbol", "ESZ6", "--exchange", "CME", "--duration", "0.3", "--online",
                           "--agreements-accepted"], entrada=lambda _: "trader@exemplo.com", senha=lambda _: SENHA)
    saida = capsys.readouterr().out
    assert codigo == 0 and "verificada=True" in saida and "ambiente=paper" in saida
    assert "sessao: estado=FECHADA" in saida
    assert SENHA not in saida and "trader@exemplo.com" not in saida
    pastas = list((ambiente / "dados" / "capturas").iterdir())
    assert len(pastas) == 1 and (pastas[0] / "manifest.json").exists()


def test_cli_falha_da_sessao_devolve_2_e_ainda_verifica(ambiente, monkeypatch, capsys):
    servidor = Servidor(rp_login="1")
    monkeypatch.setattr(captura, "_criar_fabrica", lambda endpoint: servidor.fabrica())
    codigo = captura.main(["--symbol", "ESZ6", "--exchange", "CME", "--online", "--agreements-accepted"],
                          entrada=lambda _: "u", senha=lambda _: SENHA)
    saida = capsys.readouterr().out
    assert codigo == 2 and "erro=resposta_recusada_11_codigo_1" in saida and "verificada=True" in saida


def test_cli_usa_por_padrao_o_app_version_que_o_v1_ja_validou(ambiente, monkeypatch):
    servidor = Servidor()
    monkeypatch.setattr(captura, "_criar_fabrica", lambda endpoint: servidor.fabrica())
    captura.main(["--symbol", "ESZ6", "--exchange", "CME", "--duration", "0.2", "--online", "--agreements-accepted"],
                 entrada=lambda _: "u", senha=lambda _: SENHA)
    assert b"rithmic-session-1" in servidor.conexoes[1].enviados[0]
    servidor2 = Servidor()
    monkeypatch.setattr(captura, "_criar_fabrica", lambda endpoint: servidor2.fabrica())
    captura.main(["--symbol", "ESZ6", "--exchange", "CME", "--duration", "0.2", "--app-version", "outra-1", "--online",
                  "--agreements-accepted"], entrada=lambda _: "u", senha=lambda _: SENHA)
    assert b"outra-1" in servidor2.conexoes[1].enviados[0] and b"rithmic-session-1" not in servidor2.conexoes[1].enviados[0]


@pytest.mark.parametrize("usuario,senha", [("", "x"), ("x", ""), ("  ", "x")])
def test_cli_sem_credenciais_nao_conecta(ambiente, monkeypatch, capsys, usuario, senha):
    monkeypatch.setattr(captura, "_criar_fabrica", sem_conexao)
    codigo = captura.main(["--symbol", "ESZ6", "--exchange", "CME", "--online", "--agreements-accepted"],
                          entrada=lambda _: usuario, senha=lambda _: senha)
    assert codigo == 2 and "obrigatorios" in capsys.readouterr().out
    assert not (ambiente / "dados").exists()


@pytest.mark.parametrize("args", [["--symbol", "ES;Z6", "--exchange", "CME"], ["--symbol", "ESZ6", "--exchange", ""],
                                  ["--symbol", "ESZ6", "--exchange", "CME", "--duration", "0"],
                                  ["--symbol", "ESZ6", "--exchange", "CME", "--template-version", ""], ["--symbol", "ESZ6"]])
def test_cli_configuracao_invalida(ambiente, args):
    with pytest.raises(SystemExit) as erro:
        captura.main([*args, "--online", "--agreements-accepted"], entrada=sem_conexao, senha=sem_conexao)
    assert erro.value.code == 2


def test_cli_recusa_endpoint_fora_da_rithmic(ambiente, monkeypatch):
    monkeypatch.setenv("QHOS2_ENDPOINT", "wss://evil.example.com:443")
    with pytest.raises(SystemExit) as erro:
        captura.main(["--symbol", "ESZ6", "--exchange", "CME", "--online", "--agreements-accepted"],
                     entrada=sem_conexao, senha=sem_conexao)
    assert erro.value.code == 2


def test_cli_disco_quase_cheio_recusa_antes_de_pedir_usuario_e_senha(ambiente, monkeypatch, capsys):
    Uso = namedtuple("Uso", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _: Uso(100, 100, 1))
    monkeypatch.setattr(captura, "_criar_fabrica", lambda endpoint: sem_conexao)
    codigo = captura.main(["--symbol", "ESZ6", "--exchange", "CME", "--online", "--agreements-accepted"],
                          entrada=sem_conexao, senha=sem_conexao)  # se pedisse usuario ou senha, o teste quebraria
    saida = capsys.readouterr().out
    assert codigo == 2 and "ESPACO INSUFICIENTE" in saida and "recusada antes das credenciais" in saida


def test_cli_teto_da_captura(ambiente, monkeypatch, capsys):
    servidor = Servidor(apos_assinar=tuple(quadro160(i) for i in range(50)))
    monkeypatch.setattr(captura, "_criar_fabrica", lambda endpoint: servidor.fabrica())
    codigo = captura.main(["--symbol", "ESZ6", "--exchange", "CME", "--max-gb", str(300 / 1024 ** 3), "--online",
                           "--agreements-accepted"], entrada=lambda _: "u", senha=lambda _: SENHA)
    saida = capsys.readouterr().out
    assert codigo == 2 and "erro=gravacao_falhou" in saida and "verificada=True" in saida and "GiB)" in saida


@pytest.mark.parametrize("valor", ["0", "-1", "101", "nan", "inf"])
def test_cli_teto_invalido(ambiente, valor):
    with pytest.raises(SystemExit) as erro:
        captura.main(["--symbol", "ESZ6", "--exchange", "CME", "--max-gb", valor, "--online", "--agreements-accepted"],
                     entrada=sem_conexao, senha=sem_conexao)
    assert erro.value.code == 2


def test_obter_credenciais_usa_as_funcoes_injetadas():
    c = captura.obter_credenciais(lambda pergunta: "  trader  ", lambda pergunta: "segredo")
    assert (c.user, c.password) == ("trader", "segredo")
    assert "segredo" not in repr(c) and "trader" not in repr(c)


# ---- espaco livre e runbook (auditoria de 27/10) -------------------------------------------------------

GIB = 1024 ** 3
Uso = namedtuple("Uso", "total used free")


def _livre(monkeypatch, livre_bytes):
    monkeypatch.setattr(shutil, "disk_usage", lambda p: Uso(livre_bytes * 2, 0, livre_bytes))


def test_teto_padrao_e_12_gib_e_a_folga_e_2x():
    assert captura.MAX_GB_PADRAO == 12.0 and captura.FOLGA_DISCO == 2


@pytest.mark.parametrize("livre_gib, esperado", [(100, "ok"), (24, "ok"), (23.9, "aviso"), (12.5, "aviso"),
                                                  (12.4, "recusa"), (0, "recusa")])
def test_espaco_livre_ok_aviso_ou_recusa(monkeypatch, tmp_path, livre_gib, esperado):
    _livre(monkeypatch, int(livre_gib * GIB))
    situacao, livre = captura.avaliar_espaco(tmp_path / "ainda_nao_existe" / "capturas", 12 * GIB)
    assert situacao == esperado and livre == int(livre_gib * GIB)


def test_textos_do_aviso_e_da_recusa_sao_claros(ambiente, monkeypatch, capsys):
    # pelo que o operador le na CLI, nao pela funcao privada que monta o texto (QHOS-AUD-115)
    monkeypatch.setattr(captura, "_criar_fabrica", sem_conexao)
    _livre(monkeypatch, 20 * GIB)
    assert captura.main(["--symbol", "ESZ6", "--exchange", "CME"], entrada=sem_conexao, senha=sem_conexao) == 0
    aviso = capsys.readouterr().out
    assert "AVISO" in aviso and "estourar" in aviso and "ESPACO INSUFICIENTE" not in aviso
    _livre(monkeypatch, 5 * GIB)
    assert captura.main(["--symbol", "ESZ6", "--exchange", "CME", "--online", "--agreements-accepted"],
                        entrada=sem_conexao, senha=sem_conexao) == 2
    recusa = capsys.readouterr().out
    assert "ESPACO INSUFICIENTE" in recusa and "AVISO" not in recusa


@pytest.mark.documentacao
def test_runbook_cita_as_regras_que_o_avaliador_aplica():
    from pathlib import Path
    from qhos2 import desequilibrio
    texto = (Path(__file__).parent.parent / "docs" / "RUNBOOK_CAPTURA.md").read_text(encoding="utf-8")
    # os valores vem do codigo, nao de prosa fixa: um limiar que mude sem o runbook acompanhar acusa aqui (QHOS-AUD-116)
    regras = ("mais_de_uma_captura_no_dia",                                      # avaliador: um pregao por dia
              f"{desequilibrio.LACUNA_MAX_NS // 10 ** 9} s",                       # lacuna de entrega que toque o pregao
              f"{captura.FOLGA_DISCO * captura.MAX_GB_PADRAO:g} GiB",              # livre desejado: FOLGA_DISCO x o teto
              f"--max-gb {captura.MAX_GB_PADRAO:g}")                               # teto padrao
    for trecho in regras:
        assert trecho in texto, trecho


def test_cli_mostra_o_teto_padrao_de_12_gib(ambiente, monkeypatch, capsys):
    monkeypatch.setattr(captura, "_criar_fabrica", sem_conexao)
    assert captura.main(["--symbol", "ESZ6", "--exchange", "CME"], entrada=sem_conexao, senha=sem_conexao) == 0
    assert "teto 12 GiB" in capsys.readouterr().out


def test_cli_avisa_quando_o_livre_e_menos_que_2x_o_teto(ambiente, monkeypatch, capsys):
    _livre(monkeypatch, 20 * GIB)
    monkeypatch.setattr(captura, "_criar_fabrica", sem_conexao)
    assert captura.main(["--symbol", "ESZ6", "--exchange", "CME"], entrada=sem_conexao, senha=sem_conexao) == 0
    saida = capsys.readouterr().out
    assert "AVISO" in saida and "estourar" in saida and "nada foi conectado" in saida


def test_cli_com_livre_suficiente_nao_avisa(ambiente, monkeypatch, capsys):
    _livre(monkeypatch, 100 * GIB)
    monkeypatch.setattr(captura, "_criar_fabrica", sem_conexao)
    assert captura.main(["--symbol", "ESZ6", "--exchange", "CME"], entrada=sem_conexao, senha=sem_conexao) == 0
    saida = capsys.readouterr().out
    assert "AVISO" not in saida and "ESPACO INSUFICIENTE" not in saida
