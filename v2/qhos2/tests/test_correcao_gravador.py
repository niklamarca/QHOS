"""Campanha de correcao (lote l5a-captura): gravador e rastro em disco.

IDs: QHOS-AUD-080, 054, 099.
`src/qhos2` congelado (J0): defeitos confirmados em `xfail(strict=True)`, patch proposto em
`09_correcao/EVIDENCIAS/<ID>/patch_proposto.diff`. Processos filhos sao sempre os deste teste.
"""
import errno
import importlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from qhos2 import catalogo, gravador, operacao
from qhos2.gravador import Gravador, GravadorError
from qhos2.operacao import GuardaCaptura

from test_gravador import IDENT, STATUS, gravar, quadro  # noqa: E402


# ==== QHOS-AUD-080 ====
# SIGKILL durante a gravacao: o rastro em disco precisa ser consistente (manifesto ausente, linhas completas).

ROTEIRO_GRAVANDO = r'''
import sys, time
from qhos2.gravador import Gravador
from test_gravador import IDENT, quadro
g = Gravador(sys.argv[1], identidade=IDENT, captura_id="morta")
for i in range(5):
    dados, ns = quadro(i)
    g(dados, 160, ns)
print("pronto", flush=True)
time.sleep(60)
'''


@pytest.mark.skipif(sys.platform != "linux", reason="SIGKILL e guarda POSIX")
def test_sigkill_durante_a_gravacao_deixa_rastro_consistente(tmp_path):
    roteiro = tmp_path / "gravando.py"
    roteiro.write_text(ROTEIRO_GRAVANDO, encoding="utf-8")
    raiz = tmp_path / "dados"
    src = Path(gravador.__file__).resolve().parents[1]
    env = dict(os.environ, HOME=str(tmp_path / "home"), QHOS2_DATA_ROOT=str(raiz),
               PYTHONPATH=f"{src}{os.pathsep}{Path(__file__).resolve().parent}")
    (tmp_path / "home").mkdir()
    filho = subprocess.Popen([sys.executable, str(roteiro), str(raiz / "capturas")], env=env,
                             stdout=subprocess.PIPE, text=True)
    try:
        assert filho.stdout.readline().strip() == "pronto"
        filho.kill()  # SIGKILL no processo filho deste teste
        assert filho.wait(timeout=30) == -signal.SIGKILL
    finally:
        if filho.poll() is None:
            filho.kill()
    pasta = raiz / "capturas" / "morta"
    relatorio = catalogo.verificar_captura(pasta)
    assert relatorio["verificada"] is False and relatorio["divergencias"] == ["manifesto_ausente"]
    linhas = (pasta / "market_frames.jsonl").read_bytes().split(b"\n")
    assert linhas[-1] == b"" and len(linhas) == 6  # 5 linhas completas, descarregadas a cada quadro
    assert [json.loads(l)["index"] for l in linhas[:-1]] == [0, 1, 2, 3, 4]
    assert operacao.diagnosticar(raiz)["capturas_incompletas"] == ["morta"]
    assert operacao.ler_identidade_captura(pasta)["capture_id"] == "morta"  # cabecalho integro
    with pytest.raises(operacao.OperacaoError, match="captura_nao_verificada"):
        operacao.copiar_captura(pasta, tmp_path / "backup")  # nao se copia captura incompleta


# ==== QHOS-AUD-054 ====
# Gravador.__init__:73-75 traduz OSError/OperacaoError em identidade_de_captura_nao_persistida com `from None`.

@pytest.mark.xfail(strict=True, reason="QHOS-AUD-054: Gravador.__init__:73-75 relanca identidade_de_captura_nao_"
                                        "persistida com 'from None'; o errno (ENOSPC/EACCES/EIO) da causa se perde")
def test_identidade_nao_persistida_preserva_o_errno(tmp_path, monkeypatch):
    def sem_espaco(*_):
        raise OSError(errno.ENOSPC, "sem espaco (sintetico)")
    monkeypatch.setattr(gravador, "_atomico", sem_espaco)
    with pytest.raises(GravadorError, match="identidade_de_captura_nao_persistida") as exc:
        Gravador(tmp_path, identidade=IDENT, captura_id="sem-espaco")
    causa = exc.value.__cause__
    assert isinstance(causa, OSError) and causa.errno == errno.ENOSPC, causa


def test_identidade_nao_persistida_nao_deixa_manifesto_nem_vaza_a_identidade(tmp_path, monkeypatch):
    def sem_espaco(*_):
        raise OSError(errno.ENOSPC, "sem espaco (sintetico)")
    monkeypatch.setattr(gravador, "_atomico", sem_espaco)
    with pytest.raises(GravadorError, match="identidade_de_captura_nao_persistida") as exc:
        Gravador(tmp_path, identidade={**IDENT, "password": "nunca"}, captura_id="sem-espaco")
    assert "nunca" not in str(exc.value) and "sintetico" not in str(exc.value)
    assert not (tmp_path / "sem-espaco" / "manifest.json").exists()


# ==== QHOS-AUD-099 ====
# Escrita parcial (ENOSPC) deixa linha truncada; o catalogo recusa a captura inteira. Recuperacao documentada no runbook.

class EscritaParcial:
    """Envolve o arquivo do gravador: a proxima escrita grava metade da linha e falha com ENOSPC."""

    def __init__(self, real):
        self._real, self.falhou = real, False

    def write(self, dados):
        if not self.falhou:
            self.falhou = True
            self._real.write(dados[:len(dados) // 2])
            self._real.flush()
            raise OSError(errno.ENOSPC, "disco cheio (sintetico)")
        return self._real.write(dados)

    def __getattr__(self, nome):
        return getattr(self._real, nome)


def test_linha_truncada_por_escrita_parcial_e_recuperavel_truncando_no_bytes_do_manifesto(tmp_path):
    g = gravar(tmp_path, quantos=3)
    g._arquivo = EscritaParcial(g._arquivo)  # o unico ponto de injecao da falha de disco e o proprio arquivo
    dados, ns = quadro(3)
    with pytest.raises(OSError) as exc:
        g(dados, 160, ns)
    assert exc.value.errno == errno.ENOSPC and g.quadros == 3
    manifesto = json.loads(g.fechar(STATUS, fechamento_normal=False).read_text())
    assert manifesto["frames"] == 3  # frames, bytes e sha256 contam so as linhas completas
    relatorio = catalogo.verificar_captura(g.pasta)
    assert relatorio["verificada"] is False and relatorio["divergencias"] == ["linha_truncada"]
    assert (g.pasta / "market_frames.jsonl").stat().st_size > manifesto["bytes"]
    # procedimento do runbook: truncar market_frames.jsonl no `bytes` do manifesto e verificar de novo
    with open(g.pasta / "market_frames.jsonl", "r+b") as arquivo:
        arquivo.truncate(manifesto["bytes"])
    recuperado = catalogo.verificar_captura(g.pasta)
    assert recuperado["verificada"] is True and recuperado["quadros"] == 3 and recuperado["fechamento_normal"] is False


RUNBOOK = Path(__file__).parent.parent / "docs" / "RUNBOOK_CAPTURA.md"
SECAO_099 = "## Captura recusada pelo catalogo por `linha_truncada`"
COMANDO = re.compile(r"^ {4,}python -m (qhos2\.\w+)(?: (.*))?$")
TRUNCATE = re.compile(r"^ {4,}truncate -s (\S+) (\S+)$")


def passos_do_runbook():
    """Os comandos em bloco (linhas indentadas) da secao de QHOS-AUD-099, na ordem em que aparecem."""
    texto = RUNBOOK.read_text(encoding="utf-8")
    inicio = texto.index(SECAO_099)
    fim = texto.find("\n## ", inicio + 1)
    passos = []
    for linha in texto[inicio:fim if fim != -1 else None].splitlines():
        if (m := COMANDO.match(linha)):
            passos.append(("modulo", m.group(1), m.group(2) or ""))
        elif (m := TRUNCATE.match(linha)):
            passos.append(("truncate", m.group(1), m.group(2)))
    return passos


def test_runbook_lista_os_passos_da_recuperacao_na_ordem():
    resumo = [(tipo, nome, (args.split() or [""])[0]) if tipo == "modulo" else (tipo,) for tipo, nome, args in passos_do_runbook()]
    assert resumo == [("modulo", "qhos2.catalogo", "<data_root>/capturas"), ("truncate",),
                      ("modulo", "qhos2.catalogo", "<data_root>/capturas"), ("modulo", "qhos2.operacao", "estado"),
                      ("modulo", "qhos2.operacao", "liberar"), ("modulo", "qhos2.operacao", "diagnosticar")]
    liberar = next(args for tipo, nome, args in passos_do_runbook() if args.startswith("liberar"))
    assert "--geracao <geracao>" in liberar and "--confirmo-sessoes-encerradas" in liberar  # operacao.py:330-331


@pytest.mark.skipif(sys.platform != "linux", reason="GuardaCaptura exige Linux (guarda_requer_linux)")
def test_procedimento_do_runbook_recupera_a_captura_com_linha_truncada(tmp_path, monkeypatch, capsys):
    """Executa, na ordem, os comandos da secao do runbook contra uma captura com linha truncada e a guarda em
    quarentena, substituindo so os marcadores <data_root>, <captura_id>, <bytes> e <geracao>."""
    data_root = tmp_path / "dados"
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(data_root))
    g = gravar(data_root / "capturas", quantos=3)
    g._arquivo = EscritaParcial(g._arquivo)
    dados, ns = quadro(3)
    with pytest.raises(OSError):
        g(dados, 160, ns)
    manifesto = json.loads(g.fechar(STATUS, fechamento_normal=False).read_text())
    with GuardaCaptura(operacao.estado_padrao()) as guarda:  # como depois de uma captura sem logout confirmado
        geracao = guarda.iniciar()
        guarda.finalizar(logout_confirmado=False, fechamento_normal=False)
    valores = {"<data_root>": str(data_root), "<captura_id>": g.id, "<bytes>": str(manifesto["bytes"])}

    def preencher(texto):
        for marcador, valor in valores.items():
            texto = texto.replace(marcador, valor)
        assert "<" not in texto, texto  # nenhum marcador sem valor
        return texto
    saidas = []
    for tipo, a, b in passos_do_runbook():
        if tipo == "truncate":
            os.truncate(preencher(b), int(preencher(a)))
            continue
        codigo = importlib.import_module(a).main(shlex.split(preencher(b)))
        saida = capsys.readouterr().out
        saidas.append((a, b.split()[0], codigo, saida))
        if b.startswith("estado"):
            valores["<geracao>"] = json.loads(saida)["geracao"]
    (_, _, c1, s1), (_, _, c2, s2), (_, _, c3, s3), (_, _, c4, s4), (_, _, c5, s5) = saidas
    linha = next(l for l in s1.splitlines() if l.startswith(g.id))
    assert c1 == 2 and "verificada=False" in linha and "divergencias=['linha_truncada']" in linha
    linha = next(l for l in s2.splitlines() if l.startswith(g.id))
    assert c2 == 0 and "verificada=True" in linha and f"quadros={manifesto['frames']}" in linha
    assert c3 == 0 and json.loads(s3)["estado"] == "QUARENTENA" and valores["<geracao>"] == geracao
    assert c4 == 0 and json.loads(s4)["estado"] == "LIVRE"
    assert c5 == 0 and json.loads(s5)["capturas_incompletas"] == []
    relatorio = catalogo.verificar_captura(g.pasta)
    assert relatorio["verificada"] is True and relatorio["fechamento_normal"] is False
