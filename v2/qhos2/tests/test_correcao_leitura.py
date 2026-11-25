"""Campanha de correcao, lote l5c-leitura: leitura ao vivo (QHOS-AUD-133, 194).

Xfail estrito so para a 083 (PermissionError em os.stat; patch em `EVIDENCIAS/QHOS-AUD-083/`). A 133 e a 194 sao
caracterizacao e pesquisa estatica/dinamica. `leitura.py` nao muda nesta janela.
"""
import os
import re
from pathlib import Path

import pytest

from qhos2 import leitura as le
from qhos2 import rprotocol as p
from qhos2 import sintetico as si
from qhos2.gravador import Gravador

RAIZ = Path(__file__).resolve().parents[1]
IDENTIDADE = {"system_name": si.SISTEMA, "requested_symbol": "ESZ6", "exchange": "CME"}
STATUS = {"estado": "FECHADA", "referencia": {"min_qprice_change": 0.25}}


def encerrado(tmp_path):
    pasta = si.gravar(si.gerar(1, 10), tmp_path)
    leit = le.Leitor(pasta, tick=0.25, simbolo="SINTETICO", bolsa="QHOS2")
    leit.avancar()
    assert (leit.estado, leit.codigo) == (le.ENCERRADA, None) and leit.livro_vale
    return leit


# ---- QHOS-AUD-133: `_falhar` nao guarda estados terminais ---------------------------------------------------------

def test_caracterizacao_falhar_chamado_direto_sobrescreve_encerrada(tmp_path):
    """QHOS-AUD-133, caracterizacao: `_falhar` (privado, leitura.py:196-199) nao tem a guarda de `avancar` (:71-72) e
    sobrescreve ENCERRADA. So por chamada direta: o unico chamador externo (coletor.py:560) nao alcanca leitor
    terminal (teste abaixo). Guardar e desenho (patch em EVIDENCIAS/QHOS-AUD-133)."""
    leit = encerrado(tmp_path)
    leit._falhar("arquivo_inacessivel")
    assert (leit.estado, leit.codigo, leit.livro_vale) == (le.FALHA, "arquivo_inacessivel", False)


def test_caracterizacao_falhar_chamado_direto_troca_o_codigo_da_primeira_falha(tmp_path):
    leit = le.Leitor(si.gravar(si.gerar(2, 20), tmp_path), tick=0.3, simbolo="SINTETICO", bolsa="QHOS2")
    leit.avancar()                                      # tick errado: preco_fora_da_grade:N
    assert leit.estado == le.FALHA and leit.codigo.startswith("preco_fora_da_grade:")
    leit._falhar("arquivo_inacessivel")
    assert leit.codigo == "arquivo_inacessivel"


def test_o_chamador_externo_nao_alcanca_um_leitor_terminal(tmp_path, monkeypatch):
    """coletor._seguir so chama `_falhar` quando `avancar` levanta OSError; depois de ENCERRADA/FALHA `avancar`
    devolve [] sem tocar no disco (leitura.py:71-72), logo o caminho real nao exerce o defeito."""
    leit = encerrado(tmp_path)

    def explode(*a, **k):
        raise OSError("nao deveria tocar no disco")
    monkeypatch.setattr(le.os, "stat", explode)
    monkeypatch.setattr(le.os, "fstat", explode)
    assert leit.avancar() == [] and (leit.estado, leit.codigo) == (le.ENCERRADA, None)


# ---- QHOS-AUD-194: constante morta CONFERIR_SIMBOLO_A_CADA --------------------------------------------------------

def test_ninguem_le_a_constante_no_codigo_de_producao():
    """Pesquisa estatica: em src/ e terminal/ a unica ocorrencia e a propria definicao (leitura.py:36)."""
    ocorrencias = []
    for raiz in (RAIZ / "src", RAIZ / "terminal"):
        for arquivo in raiz.rglob("*"):
            if arquivo.suffix in (".py", ".js", ".html") and "CONFERIR_SIMBOLO_A_CADA" in arquivo.read_text(encoding="utf-8", errors="replace"):
                ocorrencias.append(arquivo.relative_to(RAIZ).as_posix())
    assert set(ocorrencias) <= {"src/qhos2/leitura.py"}                   # (vazio depois de removida)
    fonte = (RAIZ / "src/qhos2/leitura.py").read_text(encoding="utf-8")
    assert len(re.findall(r"CONFERIR_SIMBOLO_A_CADA", fonte)) <= 1      # no maximo a atribuicao; nenhuma leitura


@pytest.mark.parametrize("valor", [0, 10 ** 6, None])
def test_a_conferencia_de_simbolo_e_incondicional_seja_qual_for_o_valor(tmp_path, monkeypatch, valor):
    """Pesquisa dinamica: mudar a constante nao muda nada; o primeiro quadro identificado com outro simbolo para a leitura."""
    monkeypatch.setattr(le, "CONFERIR_SIMBOLO_A_CADA", valor, raising=False)
    g = Gravador(tmp_path, identidade=IDENTIDADE)
    retrato = si.quadro_retrato("C", 100.0, [(1, 1, "c")]) + si._txt(p.F_SYMBOL, "NQZ6") + si._txt(p.F_EXCHANGE, "CME")
    g(retrato, 116, si.INICIO_NS)
    leit = le.Leitor(g.pasta, tick=0.25, simbolo="ESZ6", bolsa="CME")
    assert leit.avancar() == []
    assert (leit.estado, leit.codigo, leit.quadros) == (le.FALHA, "simbolo_ou_bolsa_diverge", 0)


# ---- QHOS-AUD-083: leitura.main (CLI) sem teste; PermissionError em os.stat escapa do laco ---------------------------

ARGS = ["--tick", "0.25", "--simbolo", "SINTETICO", "--bolsa", "QHOS2"]


def _permissoes_posix():
    if not hasattr(os, "geteuid"):
        pytest.skip("permissoes POSIX: so no Linux")
    if os.geteuid() == 0:
        pytest.skip("permissoes nao se aplicam a root")


@pytest.fixture
def cli(monkeypatch, capsys):
    """`main()` sem mexer na prioridade do processo (os.nice) e com o laco limitado: a N-esima volta ociosa
    (`time.sleep`) levanta KeyboardInterrupt, que e o que o operador faria num Ctrl-C."""
    monkeypatch.setattr(le.os, "nice", lambda n: None, raising=False)

    def rodar(argv, voltas_ociosas=3):
        dormidas = []

        def dormir(s):
            dormidas.append(s)
            if len(dormidas) >= voltas_ociosas:
                raise KeyboardInterrupt
        monkeypatch.setattr(le.time, "sleep", dormir)
        try:
            codigo = le.main(argv)
        except KeyboardInterrupt:
            codigo = "interrompido"
        return codigo, capsys.readouterr().out, len(dormidas)
    return rodar


def test_cli_segue_uma_captura_encerrada_e_sai_com_0(tmp_path, cli):
    pasta = si.gravar(si.gerar(1, 20), tmp_path)
    codigo, saida, dormidas = cli(["--captura", str(pasta), *ARGS])
    assert codigo == 0 and dormidas == 0
    assert f"seguindo {pasta.name}" in saida and "estado=ENCERRADA" in saida and "codigo=" not in saida
    from qhos2 import livro as lv
    assert f"assinatura={lv.livro_da_captura(pasta).assinatura()[:16]}" in saida


def test_cli_com_captura_ausente_fica_aguardando_ate_o_operador_interromper(tmp_path, cli):
    codigo, saida, dormidas = cli(["--captura", str(tmp_path / "ainda_nao"), *ARGS])
    assert codigo == "interrompido" and dormidas == 3
    assert "estado=AGUARDANDO" in saida and "livro_vale=False" in saida and "ENCERRADA" not in saida


def test_cli_recusa_tick_invalido_e_raiz_sem_captura_com_2(tmp_path, cli, monkeypatch):
    codigo, saida, _ = cli(["--captura", str(tmp_path), "--tick", "0", "--simbolo", "SINTETICO", "--bolsa", "QHOS2"])
    assert codigo == 2 and "PARE: tick_invalido" in saida
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    (tmp_path / "dados" / "capturas").mkdir(parents=True)
    codigo, saida, _ = cli(["--em-andamento", *ARGS])
    assert codigo == 2 and "PARE: nenhuma_captura_em_andamento" in saida


def test_cli_em_andamento_acha_a_unica_captura_sem_manifesto(tmp_path, cli, monkeypatch):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    pasta = si.gravar(si.gerar(1, 20), tmp_path / "dados" / "capturas")
    (pasta / "manifest.json").unlink()
    codigo, saida, dormidas = cli(["--em-andamento", *ARGS])
    assert codigo == "interrompido" and dormidas == 3
    assert f"seguindo {pasta.name}" in saida and "estado=LENDO" in saida and "livro_vale=True" in saida


def test_cli_arquivo_sem_permissao_de_leitura_e_falha_com_codigo_e_sai_com_1(tmp_path, cli):
    _permissoes_posix()
    pasta = si.gravar(si.gerar(1, 20), tmp_path)
    arquivo = pasta / "market_frames.jsonl"
    arquivo.chmod(0o000)
    try:
        codigo, saida, _ = cli(["--captura", str(pasta), *ARGS])
    finally:
        arquivo.chmod(0o600)
    assert codigo == 1 and "estado=FALHA codigo=arquivo_nao_regular_ou_inacessivel" in saida


def _depois_da_primeira_volta(monkeypatch, acao):
    """Chama `acao(leitor)` depois do primeiro `avancar` bem-sucedido: o que muda entre uma volta e a seguinte.
    A primeira volta le um bloco curto (4 KiB), como numa captura maior que um bloco ou ainda sendo gravada."""
    original = le.Leitor.avancar
    voltas = []

    def avancar(self, max_bytes=le.BLOCO_BYTES):
        saida = original(self, 4096 if not voltas else max_bytes)
        voltas.append(self.estado)
        if len(voltas) == 1:
            acao(self)
        return saida
    monkeypatch.setattr(le.Leitor, "avancar", avancar)
    return voltas


def test_cli_arquivo_removido_entre_duas_voltas_e_falha_arquivo_sumiu_com_1(tmp_path, cli, monkeypatch):
    pasta = si.gravar(si.gerar(1, 300), tmp_path)
    voltas = _depois_da_primeira_volta(monkeypatch, lambda leitor: (pasta / "market_frames.jsonl").unlink())
    codigo, saida, _ = cli(["--captura", str(pasta), *ARGS])
    assert voltas[0] == le.LENDO and codigo == 1
    assert "estado=FALHA codigo=arquivo_sumiu" in saida           # indisponibilidade, nao violacao de conteudo


def test_cli_conteudo_invalido_continua_sendo_falha_de_conteudo(tmp_path, cli):
    pasta = si.gravar(si.gerar(1, 20), tmp_path)
    linhas = (pasta / "market_frames.jsonl").read_bytes().splitlines(keepends=True)
    (pasta / "market_frames.jsonl").write_bytes(b"".join(linhas[:2] + [b"{nao e json\n"] + linhas[3:]))
    codigo, saida, _ = cli(["--captura", str(pasta), *ARGS])
    assert codigo == 1 and "estado=FALHA codigo=linha_invalida" in saida


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-083: PermissionError em os.stat (leitura.py:100) nao e FileNotFoundError: "
                                       "escapa de avancar() e do laco de main() em traceback; leitura.py:41 diz que 'a falha de uma "
                                       "captura vira estado FALHA, nao excecao'")
def test_cli_pasta_que_perde_a_permissao_vira_falha_com_codigo_e_nao_traceback(tmp_path, cli, monkeypatch):
    _permissoes_posix()
    pasta = si.gravar(si.gerar(1, 300), tmp_path)
    _depois_da_primeira_volta(monkeypatch, lambda leitor: pasta.chmod(0o600))      # sem `x`: stat dentro dela falha
    try:
        try:
            codigo, saida, _ = cli(["--captura", str(pasta), *ARGS])
        except PermissionError as erro:
            pytest.fail(f"PermissionError escapou de leitura.main: {erro}")
    finally:
        pasta.chmod(0o700)
    assert codigo == 1 and "estado=FALHA codigo=" in saida and "linha_invalida" not in saida


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-083: depois do PermissionError em os.stat o leitor fica LENDO com livro_vale True, "
                                       "um estado enganoso; o contrato e FALHA com codigo (leitura.py:11-13 e :41)")
def test_leitor_sem_acesso_a_pasta_entra_em_falha_em_vez_de_levantar(tmp_path):
    _permissoes_posix()
    pasta = si.gravar(si.gerar(1, 300), tmp_path)
    leit = le.Leitor(pasta, tick=0.25, simbolo="SINTETICO", bolsa="QHOS2")
    leit.avancar(4096)
    assert leit.estado == le.LENDO
    pasta.chmod(0o600)
    try:
        try:
            assert leit.avancar() == []
        except PermissionError as erro:
            pytest.fail(f"avancar levantou em vez de falhar: {erro}; estado={leit.estado} livro_vale={leit.livro_vale}")
    finally:
        pasta.chmod(0o700)
    assert leit.estado == le.FALHA and leit.codigo and not leit.livro_vale
