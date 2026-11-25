"""Falhas de processo, gravacao, destino e relogio; nenhuma conexao externa."""
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from qhos2 import captura, gravador, operacao, rprotocol, sessao
from qhos2.config import ConfigError, Settings
from qhos2.operacao import GuardaCaptura, OperacaoError
from qhos2.sessao import ConfigSessao, Sessao
from test_gravador import IDENT, STATUS, gravar
from test_sessao import CRED, Servidor, resp_hb


def test_lock_impede_dois_processos_e_duas_raizes(tmp_path):
    raiz = tmp_path / "estado"
    with GuardaCaptura(raiz) as primeira:
        primeira.iniciar()
        code = "from qhos2.operacao import GuardaCaptura; GuardaCaptura(__import__('pathlib').Path(__import__('sys').argv[1])).__enter__()"
        p = subprocess.run([sys.executable, "-c", code, str(raiz)], capture_output=True, text=True)
        assert p.returncode != 0 and "captura_ja_em_execucao" in p.stderr
        primeira.finalizar(logout_confirmado=True, fechamento_normal=True)
    with GuardaCaptura(raiz) as nova:
        nova.conferir()


def test_queda_de_processo_deixa_quarentena_e_liberacao_nao_conecta(tmp_path):
    raiz = tmp_path / "estado"
    code = "from qhos2.operacao import GuardaCaptura; from pathlib import Path; import os,sys; g=GuardaCaptura(Path(sys.argv[1]));g.__enter__();g.iniciar();os._exit(0)"
    subprocess.run([sys.executable, "-c", code, str(raiz)], check=True)
    with GuardaCaptura(raiz) as nova:
        with pytest.raises(OperacaoError, match="quarentena"):
            nova.conferir()
        with pytest.raises(OperacaoError, match="confirmacao"):
            nova.liberar("incorreta")
        nova.liberar(nova.estado["geracao"])
        nova.conferir()
    assert len(list(raiz.glob("liberacao-*.json"))) == 1


@pytest.mark.parametrize("estado", ["not json", "{}", '{"schema":"qhos2_operacao_v1","estado":"EM_ANDAMENTO"}'])
def test_estado_corrompido_recusa_e_nao_recria(tmp_path, estado):
    raiz = tmp_path / "estado"
    raiz.mkdir(mode=0o700)
    (raiz / "estado.json").write_text(estado)
    with pytest.raises(OperacaoError, match="estado_operacional_invalido"):
        with GuardaCaptura(raiz):
            pass
    assert (raiz / "estado.json").read_text() == estado


def test_symlink_ou_permissao_publica_no_estado_recusa(tmp_path):
    fora = tmp_path / "fora"
    fora.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(fora, target_is_directory=True)
    with pytest.raises(OperacaoError, match="raiz_operacional"):
        with GuardaCaptura(link):
            pass
    fora.chmod(0o777)
    with pytest.raises(OperacaoError, match="permissoes"):
        with GuardaCaptura(fora):
            pass


def test_recusa_login_persiste_apos_troca_da_raiz_de_captura(tmp_path, monkeypatch):
    raiz_estado = tmp_path / "estado"
    monkeypatch.setattr(captura, "estado_padrao", lambda: raiz_estado)
    cfg = ConfigSessao("5.42", "ESZ6", "CME", duracao_s=0.1, settings=Settings(data_root=tmp_path / "dados"))
    r = asyncio.run(captura.capturar(cfg, Servidor(rp_login="1").fabrica(), tmp_path / "a", CRED))
    assert r["erro"] == "resposta_recusada_11_codigo_1"
    servidor = Servidor()
    with pytest.raises(OperacaoError, match="quarentena"):
        asyncio.run(captura.capturar(cfg, servidor.fabrica(), tmp_path / "b", CRED))
    assert not servidor.conexoes and not (tmp_path / "b").exists()
    texto = (raiz_estado / "estado.json").read_text()
    assert CRED.user not in texto and CRED.password not in texto
    assert json.loads(texto)["estado"] == "QUARENTENA"


def test_api_e_cli_nao_gravam_em_git_nem_solicitam_senha(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: elsewhere")
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    def nunca(*_):
        raise AssertionError("credenciais/rede nao deveriam ser chamadas")
    with pytest.raises(SystemExit) as erro:
        captura.main(["--symbol", "ESZ6", "--exchange", "CME", "--raiz", str(repo / "dados"),
                      "--online", "--agreements-accepted"], entrada=nunca, senha=nunca)
    assert erro.value.code == 2
    with pytest.raises(ConfigError, match="repositorio_git"):
        gravador.Gravador(repo / "capturas", identidade=IDENT)
    assert not (repo / "capturas").exists()


def calendario():
    return {"schema": "qhos2_calendario_v1", "fonte": "calendario sintético de teste",
            "valido_de": "2026-10-26T00:00:00Z", "valido_ate": "2026-10-27T00:00:00Z",
            "janelas": [{"inicio": "2026-10-26T10:00:00Z", "fim": "2026-10-26T11:00:00Z"}]}


@pytest.mark.parametrize("hora,duracao,esperado", [(10, 3600, True), (10, 3601, False), (9, 30, False),
                                                   (11, 30, False), (10, float("nan"), False)])
def test_calendario_exige_captura_inteira_em_janela(hora, duracao, esperado):
    assert operacao.janela_permitida(calendario(), datetime(2026, 10, 26, hora, tzinfo=timezone.utc), duracao) is esperado


def test_calendario_sem_fuso_ou_fonte_recusa():
    c = calendario()
    c["janelas"][0]["inicio"] = "2026-10-26T10:00:00"
    assert not operacao.janela_permitida(c, datetime(2026, 10, 26, 10, tzinfo=timezone.utc), 5)
    c = calendario()
    del c["fonte"]
    assert not operacao.janela_permitida(c, datetime(2026, 10, 26, 10, tzinfo=timezone.utc), 5)


def test_backup_e_restore_verificam_bytes_sem_promover_livro(tmp_path):
    g = gravar(tmp_path / "origem")
    g.fechar(STATUS, fechamento_normal=True)
    r = operacao.copiar_captura(g.pasta, tmp_path / "backup")
    r2 = operacao.copiar_captura(tmp_path / "backup", tmp_path / "restaurada")
    assert r == r2 and r["livro_qualificado"] == "nao_avaliado"
    with pytest.raises(FileExistsError):
        operacao.copiar_captura(g.pasta, tmp_path / "backup")
    for nome in ("market_frames.jsonl", "manifest.json"):
        assert (g.pasta / nome).read_bytes() == (tmp_path / "restaurada" / nome).read_bytes()


def test_backup_recusa_parcial_corrupcao_e_link(tmp_path):
    g = gravar(tmp_path / "origem")
    with pytest.raises(OperacaoError):
        operacao.copiar_captura(g.pasta, tmp_path / "backup")
    g.fechar(STATUS, fechamento_normal=True)
    (g.pasta / "market_frames.jsonl").write_bytes(b"alterado\n")
    with pytest.raises(OperacaoError, match="captura_nao_verificada"):
        operacao.copiar_captura(g.pasta, tmp_path / "backup")
    link = tmp_path / "link"
    link.symlink_to(g.pasta, target_is_directory=True)
    with pytest.raises(OperacaoError, match="origem_com_link"):
        operacao.copiar_captura(link, tmp_path / "backup")


def test_backup_detecta_fonte_mutando_durante_copia(tmp_path, monkeypatch):
    g = gravar(tmp_path / "origem")
    g.fechar(STATUS, fechamento_normal=True)
    original = operacao.shutil.copyfileobj
    def mudar(entrada, saida, length):
        original(entrada, saida, length)
        with open(entrada.name, "ab") as f:
            f.write(b"\n")
    monkeypatch.setattr(operacao.shutil, "copyfileobj", mudar)
    with pytest.raises(OperacaoError, match="copia_divergente"):
        operacao.copiar_captura(g.pasta, tmp_path / "backup")
    assert not (tmp_path / "backup" / "manifest.json").exists()


def test_falha_fsync_nao_publica_manifesto_nem_falso_sucesso_no_retry(tmp_path, monkeypatch):
    g = gravar(tmp_path / "origem")
    def erro(_):
        raise OSError("falha sintética")
    monkeypatch.setattr(gravador.os, "fsync", erro)
    with pytest.raises(OSError):
        g.fechar(STATUS, fechamento_normal=True)
    assert g._arquivo.closed and not (g.pasta / "manifest.json").exists()
    with pytest.raises(gravador.GravadorError, match="fechamento_anterior_falhou"):
        g.fechar(STATUS, fechamento_normal=True)


@pytest.mark.parametrize("max_bytes,min_livre", [(0, 1), (True, 1), (float("inf"), 1), (1, -1), (1, float("nan"))])
def test_limites_invalidos_recusados_antes_de_criar_pasta(tmp_path, max_bytes, min_livre):
    with pytest.raises(gravador.GravadorError, match="limites_invalidos"):
        gravador.Gravador(tmp_path / "destino", identidade=IDENT, max_bytes=max_bytes, min_livre_bytes=min_livre)
    assert not (tmp_path / "destino").exists()


# QHOS-AUD-070: `_amostra` escreve `_hb_pendente`, remenda `time.monotonic` e chama `_heartbeat_recebido` porque a sessao
# nao recebe um relogio monotonico injetavel. O alerta por desvio ja e provado pelo caminho publico em
# test_sessao.test_desvio_grande_num_heartbeat_aciona_o_alerta_pela_sessao; o que fica aqui (janela de amostras e
# `fresco`) so se prova com `relogio_mono` em Sessao: patch proposto em EVIDENCIAS/QHOS-AUD-070/patch_proposto.diff.
def _amostra(s, monkeypatch, agora, desvio_ms, rtt_ms=10):
    monkeypatch.setattr(sessao.time, "monotonic", lambda: agora)
    recebido_ns = 1_800_000_000_000_000_000 + int(agora * 1e9)
    enviado_ns = recebido_ns - int(rtt_ms * 1e6)
    s._hb_pendente = (agora - rtt_ms / 1000, enviado_ns)
    resposta = rprotocol.decodificar(resp_hb((enviado_ns + recebido_ns) // 2 + int(desvio_ms * 1e6)))
    s._heartbeat_recebido(resposta, recebido_ns)


def test_desvio_novo_com_rtt_igual_ou_pior_aciona_alerta(tmp_path, monkeypatch):
    s = Sessao(ConfigSessao("5.42", "ESZ6", "CME", settings=Settings(data_root=tmp_path)), None, ao_receber=lambda *_: None)
    _amostra(s, monkeypatch, 100, 0)
    assert not s.status()["relogio_alerta"]
    _amostra(s, monkeypatch, 101, 3000, rtt_ms=10)
    assert s.status()["relogio_alerta"] and s.status()["relogio"]["ultimo_desvio_ms"] == 3000
    _amostra(s, monkeypatch, 102, -3000, rtt_ms=50)
    assert s.status()["relogio_alerta"] and s.status()["relogio"]["ultimo_desvio_ms"] == -3000
    _amostra(s, monkeypatch, 140, 50, rtt_ms=50)
    assert s.status()["relogio"]["amostras_na_janela"] == 1 and s.status()["relogio"]["desvio_ms"] == 50
    monkeypatch.setattr(sessao.time, "monotonic", lambda: 200)
    assert s.status()["relogio_alerta"] and not s.status()["relogio"]["fresco"]


def test_recuo_do_relogio_local_nao_vira_melhor_rtt(tmp_path, monkeypatch):
    s = Sessao(ConfigSessao("5.42", "ESZ6", "CME", settings=Settings(data_root=tmp_path)), None, ao_receber=lambda *_: None)
    monkeypatch.setattr(sessao.time, "monotonic", lambda: 100)
    s._hb_pendente = (99.99, 1_800_000_000_000_000_000)
    recebido = 1_799_999_999_000_000_000
    s._heartbeat_recebido(rprotocol.decodificar(resp_hb(recebido)), recebido)
    assert s.status()["relogio"] is None and s.status()["relogio_alerta"]
    assert s.status()["anomalias"]["relogio_salto_local"] == 1


@pytest.mark.parametrize("capid", [1, True, [], {}, b"abc", "../outside", "", "x" * 65])
def test_gravador_recusa_tipo_id_antes_de_criar_pasta(tmp_path, capid):
    from qhos2.gravador import Gravador, GravadorError
    with pytest.raises(GravadorError, match="captura_id_invalido"):
        Gravador(tmp_path / "captures", identidade={"system_name":"QHOS2 Sintetico","requested_symbol":"ESZ6","exchange":"CME"}, captura_id=capid)
    assert not (tmp_path / "captures").exists()


@pytest.mark.parametrize("valor", [True, "5", [], float("nan"), float("inf"), 0, -1])
def test_sessao_recusa_tipos_temporais_antes_de_rede(valor):
    from qhos2.sessao import ConfigSessao
    for nome in ("duracao_s", "resposta_timeout_s", "retrato_timeout_s"):
        with pytest.raises(ValueError, match="invalida|invalido"):
            ConfigSessao("5.42", "ESZ6", "CME", **{nome:valor})
