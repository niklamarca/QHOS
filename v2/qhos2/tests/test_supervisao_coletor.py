"""Selecao por metadados e ciclo de filhos artificiais, sem login ou portas reais."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from qhos2 import supervisao_coletor as su
from qhos2.config import ConfigError

AGORA = 1_800_000_000


def captura(raiz, nome, *, criada=None, mtime=AGORA, estado="FECHADA", simbolo="ESZ6"):
    pasta = raiz / "capturas" / nome
    pasta.mkdir(parents=True)
    (pasta / "market_frames.jsonl").write_bytes(b"NAO LER DADO DE MERCADO\n")
    os.utime(pasta / "market_frames.jsonl", (mtime, mtime))
    info = (pasta / "market_frames.jsonl").stat()
    identidade = {"schema": "qhos2.capture_identity.v1", "capture_id": nome,
                  "system_name": "Rithmic Paper Trading", "requested_symbol": simbolo, "exchange": "CME",
                  "raw_dev": info.st_dev, "raw_ino": info.st_ino}
    (pasta / "capture_identity.json").write_text(json.dumps(identidade))
    (pasta / "capture_identity.json").chmod(0o600)
    if criada is not None:
        manifesto = {"schema": su.SCHEMA_MANIFESTO, "status": {"requested_symbol": simbolo,
            "exchange": "CME", "criado_em": datetime.fromtimestamp(criada, timezone.utc).isoformat(),
            "sessao": {"estado": estado}}}
        (pasta / "manifest.json").write_text(json.dumps(manifesto))
    return pasta


def test_vazia_serve_inativo_e_comando_nao_tem_login(tmp_path):
    escolha = su.selecionar(tmp_path, agora=AGORA)
    assert escolha.pasta is None and escolha.codigo == "sem_captura"
    assert su.comando_coletor(tmp_path, escolha) == [sys.executable, "-m", "qhos2.coletor", "--raiz", str(tmp_path)]


def test_unica_ativa_vence_encerrada_sem_ler_raw(tmp_path, monkeypatch):
    captura(tmp_path, "ontem", criada=AGORA-100)
    ativa = captura(tmp_path, "agora")
    abrir = os.open
    def seguro(caminho, *a, **kw):
        assert Path(caminho).name != "market_frames.jsonl"
        return abrir(caminho, *a, **kw)
    monkeypatch.setattr(su.os, "open", seguro)
    escolha = su.selecionar(tmp_path, agora=AGORA)
    assert escolha.pasta == ativa and escolha.codigo == "captura_em_curso"
    chave = escolha.chave
    os.utime(ativa / "market_frames.jsonl", (AGORA+1, AGORA+1))
    assert su.selecionar(tmp_path, agora=AGORA+1).chave == chave


def test_duas_ativas_recusa_sem_escolher_pelo_nome(tmp_path):
    captura(tmp_path, "a")
    captura(tmp_path, "z")
    assert su.selecionar(tmp_path, agora=AGORA).codigo == "mais_de_uma_captura_em_curso"
    assert su.selecionar(tmp_path, agora=AGORA).pasta is None


def test_ativa_precisa_identidade_positiva_vinculada_ao_arquivo(tmp_path):
    pasta = captura(tmp_path, "x")
    lado = pasta / "capture_identity.json"
    conteudo = json.loads(lado.read_text())
    lado.unlink()
    assert su.selecionar(tmp_path, agora=AGORA).codigo == "identidade_da_captura_ativa_invalida"
    conteudo["raw_ino"] += 1
    lado.write_text(json.dumps(conteudo))
    assert su.selecionar(tmp_path, agora=AGORA).codigo == "identidade_da_captura_ativa_invalida"


def test_ativa_de_outro_instrumento_nao_vai_ao_terminal_fixo(tmp_path):
    captura(tmp_path, "nq", simbolo="NQZ6")
    assert su.selecionar(tmp_path, agora=AGORA).codigo == "captura_ativa_de_outro_instrumento"


def test_encerrada_usa_data_e_nao_esconde_sessao_falha(tmp_path):
    captura(tmp_path, "z", criada=AGORA-100)
    mais_nova = captura(tmp_path, "a", criada=AGORA-10, estado="FALHOU")
    escolha = su.selecionar(tmp_path, agora=AGORA)
    assert escolha.pasta == mais_nova and escolha.codigo == "captura_encerrada"
    captura(tmp_path, "empate", criada=AGORA-10)
    assert su.selecionar(tmp_path, agora=AGORA).codigo == "encerradas_com_horario_ambiguo"


def test_incompleta_antiga_nao_se_declara_ativa_e_outro_contrato_nao_vai_terminal(tmp_path):
    captura(tmp_path, "morta", mtime=AGORA-100)
    captura(tmp_path, "nq", criada=AGORA-10, simbolo="NQZ6")
    assert su.selecionar(tmp_path, agora=AGORA).codigo == "captura_incompleta_nao_resolvida"


def test_incompleta_mais_nova_nao_e_escondida_por_replay_antigo(tmp_path):
    encerrada = captura(tmp_path, "encerrada", criada=AGORA-300)
    morta = captura(tmp_path, "morta", mtime=AGORA-100)
    assert su.selecionar(tmp_path, agora=AGORA).codigo == "captura_incompleta_nao_resolvida"
    os.utime(morta / "market_frames.jsonl", (AGORA-400, AGORA-400))
    assert su.selecionar(tmp_path, agora=AGORA).pasta == encerrada
    (tmp_path / "capturas" / "sem_raw").mkdir()
    assert su.selecionar(tmp_path, agora=AGORA).codigo == "captura_incompleta_nao_resolvida"


@pytest.mark.posix
@pytest.mark.parametrize("alvo", ["manifest.json", "market_frames.jsonl"])
def test_fifo_recusado_sem_abrir_ou_bloquear(tmp_path, alvo):
    pasta = captura(tmp_path, "x")
    caminho = pasta / alvo
    caminho.unlink(missing_ok=True)
    os.mkfifo(caminho)
    assert su.selecionar(tmp_path, agora=AGORA).pasta is None


@pytest.mark.posix
def test_link_recusado_e_limites_de_metadados(tmp_path, monkeypatch):
    pasta = captura(tmp_path, "x", criada=AGORA-10)
    (tmp_path / "capturas" / "alias").symlink_to(pasta, target_is_directory=True)
    assert su.selecionar(tmp_path, agora=AGORA).codigo == "captura_com_link"
    (tmp_path / "capturas" / "alias").unlink()
    monkeypatch.setattr(su, "MAX_MANIFESTO", 10)
    assert su.selecionar(tmp_path, agora=AGORA).codigo == "metadados_de_captura_invalidos"


def test_limites_de_quantidade_e_horario_futuro(tmp_path, monkeypatch):
    captura(tmp_path, "x", mtime=AGORA+100)
    assert su.selecionar(tmp_path, agora=AGORA).codigo == "arquivo_com_horario_futuro"
    monkeypatch.setattr(su, "MAX_ENTRADAS", 0)
    assert su.selecionar(tmp_path, agora=AGORA).codigo == "limite_de_capturas_excedido"


def test_raiz_git_e_selecao_fora_da_raiz_recusadas(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: algum-worktree")
    with pytest.raises(ConfigError):
        su.selecionar(repo)
    with pytest.raises(ValueError, match="fora_da_raiz"):
        su.comando_coletor(tmp_path, su.Selecao(tmp_path / "fora", "fixture"))


@pytest.mark.parametrize("valor", [0.0, -1.0, float("nan"), float("inf"), 1e300, True])
def test_intervalo_invalido_nao_cria_processos(tmp_path, valor):
    with pytest.raises(ValueError, match="limites_de_supervisao_invalidos"):
        su.Supervisor(tmp_path, intervalo=valor)


class Filho:
    def __init__(self):
        self.codigo = None
        self.terminacoes = 0
    def poll(self):
        return self.codigo
    def terminate(self):
        self.terminacoes += 1
        self.codigo = -15
    def wait(self, timeout):
        return self.codigo


def test_troca_de_geracao_fecha_filho_anterior_e_reinicia_com_argv_fixo(tmp_path):
    filhos, comandos = [], []
    def fabrica(argv, **kwargs):
        assert kwargs == {"stdin": subprocess.DEVNULL}
        filhos.append(Filho())
        comandos.append(argv)
        return filhos[-1]
    supervisor = su.Supervisor(tmp_path, popen=fabrica, avisar=lambda _: None)
    a = su.Selecao(tmp_path / "capturas/a", "captura_em_curso", (1, 1))
    b = su.Selecao(tmp_path / "capturas/b", "captura_em_curso", (1, 2))
    supervisor.passo(a)
    supervisor.passo(a)
    assert len(filhos) == 1
    supervisor.passo(b)
    assert len(filhos) == 2 and filhos[0].terminacoes == 1
    assert comandos[1][-1] == str(b.pasta)
    supervisor.encerrar()
    assert filhos[1].terminacoes == 1


def test_falha_de_filho_tem_backoff_e_teto_de_partidas(tmp_path):
    agora, filhos = [0.0], []
    def fabrica(*a, **kw):
        filhos.append(Filho())
        return filhos[-1]
    s = su.Supervisor(tmp_path, max_partidas=2, janela_s=60, popen=fabrica,
                      relogio=lambda: agora[0], avisar=lambda _: None)
    escolha = su.Selecao(None, "sem_captura")
    s.passo(escolha)
    filhos[0].codigo = 1
    s.passo(escolha)
    for t in (0.1, 1.0, 1.9):
        agora[0] = t
        s.passo(escolha)
    assert len(filhos) == 1
    agora[0] = 2
    s.passo(escolha)
    assert len(filhos) == 2
    filhos[-1].codigo = 1
    s.passo(escolha)
    agora[0] = 6
    s.passo(escolha)
    assert len(filhos) == 2
    agora[0] = 61
    s.passo(escolha)
    assert len(filhos) == 3
    s.encerrar()


# Integracao real (processos filhos de verdade, SIGTERM): declarada `lento`/`posix`; a logica do supervisor ja esta
# provada acima com o `Filho` falso (QHOS-AUD-088). Mantidos: sao a prova de que o filho real morre.
@pytest.mark.lento
def test_subprocesso_real_proprio_termina_na_troca_e_saida(tmp_path):
    filhos = []
    def fabrica(argv, **kwargs):
        assert argv[1:3] == ["-m", "qhos2.coletor"]
        filho = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"], **kwargs)
        filhos.append(filho)
        return filho
    s = su.Supervisor(tmp_path, popen=fabrica, espera_saida_s=1, avisar=lambda _: None)
    try:
        s.passo(su.Selecao(None, "sem_captura"))
        s.passo(su.Selecao(None, "mais_de_uma_captura_em_curso"))
        assert filhos[0].poll() is not None and filhos[1].poll() is None
    finally:
        s.encerrar()
    assert all(f.poll() is not None for f in filhos)


@pytest.mark.lento
@pytest.mark.posix
def test_sigterm_do_supervisor_encerra_seu_filho_real(tmp_path):
    pidfile = tmp_path / "pid-fixture"
    codigo = '''
import sys,subprocess
from pathlib import Path
from qhos2 import supervisao_coletor as s
Base=s.Supervisor
class Fixture(Base):
 def __init__(self,*a,**kw):
  def fabrica(argv,**params):
   p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'],**params)
   Path(sys.argv[2]).write_text(str(p.pid))
   return p
  super().__init__(*a,popen=fabrica,espera_saida_s=1,**kw)
s.Supervisor=Fixture
raise SystemExit(s.main(['--raiz',sys.argv[1],'--intervalo','0.05']))
'''
    pai = subprocess.Popen([sys.executable, "-c", codigo, str(tmp_path), str(pidfile)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        prazo = time.monotonic() + 30                   # teto so contra travamento: importar o pacote num filho e lento sob carga
        while not pidfile.exists() and time.monotonic() < prazo:
            time.sleep(0.02)
        assert pidfile.exists()
        filho = int(pidfile.read_text())
        pai.send_signal(signal.SIGTERM)
        out, err = pai.communicate(timeout=30)
        assert pai.returncode == 0, (out, err)
        with pytest.raises(ProcessLookupError):
            os.kill(filho, 0)
    finally:
        if pai.poll() is None:
            pai.kill()
            pai.wait(timeout=3)
