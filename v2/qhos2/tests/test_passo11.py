"""Etapa 3, passo 11: o roteiro de registro (operacao/wsl/passo11.py) em dado sintetico, sem nenhuma captura real.

As pastas de captura sao falsas (manifesto e a primeira linha de quadros, que bastam a regra de selecao) e os estados do
livro vem de `avaliador_sintetico.gerar_estados` por troca de `desequilibrio.estados_da_captura`; os processos filhos
do roteiro herdam a troca pelo fork, por isso os testes que medem so rodam no Linux. O hash congelado e trocado pelo do
codigo sob teste, para provar a logica; outro teste confere que o pino do roteiro e o hash da prova do passo 10.
"""
import importlib.util
import json
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest

from qhos2 import avaliador_sintetico as si
from qhos2 import desequilibrio as de
from qhos2 import eventos as ev
from qhos2 import pesquisa as pq

PACOTE = Path(__file__).resolve().parents[1]
CAMINHO = PACOTE / "operacao" / "wsl" / "passo11.py"
SINT = ("QHOS2 Sintetico", "SINTETICO", "QHOS2")
ENSAIO = ["--ensaio", "--identidade", *SINT]
S = 10 ** 9
linux = pytest.mark.skipif(sys.platform != "linux", reason="os filhos herdam a troca de estados_da_captura so com fork")


def carregar():
    spec = importlib.util.spec_from_file_location("passo11_sob_teste", CAMINHO)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture
def p11(monkeypatch):
    modulo = carregar()
    monkeypatch.setattr(modulo, "HASH_CONGELADO", pq.identidade_codigo()["sha256"])
    monkeypatch.setattr(modulo, "CORRIDAS", 10)
    return modulo


@pytest.fixture
def dados(tmp_path, monkeypatch):
    raiz = tmp_path / "dados"
    (raiz / "capturas").mkdir(parents=True)
    raiz.chmod(0o700)
    dias = {}

    def captura(nome, dia, identidade=SINT, manifesto=True):
        pasta = raiz / "capturas" / nome
        pasta.mkdir()
        if manifesto:
            status = dict(zip(("system_name", "requested_symbol", "exchange"), identidade))
            (pasta / "manifest.json").write_text(json.dumps({"status": status}))
        (pasta / "market_frames.jsonl").write_text(json.dumps({"received_ns": si.instante(dia, time(9, 20))}) + "\n")
        dias[str(pasta)] = dia

    def estados(pasta, niveis=3):
        if Path(pasta).name == "estragada":
            raise ev.EventoError("linha_invalida:7")
        dia = dias[str(pasta)]
        return iter(si.gerar_estados(dia.toordinal(), dia, passo_ns=S // 4, prob_passo=1.0))

    for k, dia in enumerate((date(2026, 10, 26), date(2026, 10, 28), date(2026, 11, 2))):
        captura(f"dia{k}", dia)
    captura("antiga", date(2026, 10, 23))
    captura("outra", date(2026, 10, 29), identidade=("Rithmic Test", "ESZ6", "CME"))
    captura("aberta", date(2026, 10, 30), manifesto=False)
    monkeypatch.setattr(de, "estados_da_captura", estados)
    return raiz, captura


def medir(p11, raiz, saida, *extra):
    return p11.main(["medir", "--dados", str(raiz), "--saida", str(saida), *extra])


def ler(pasta, nome):
    return json.loads((pasta / nome).read_text(encoding="utf-8"))


def test_pino_do_roteiro_e_o_hash_congelado_do_passo_10():
    prova = json.loads((PACOTE / "docs" / "provas" / "etapa3_20261029" / "identidade_codigo.json").read_text())
    assert carregar().HASH_CONGELADO == prova["sha256"]


def test_opcoes_de_ensaio_so_valem_com_ensaio(p11, tmp_path):
    for argv in (["medir", "--dados", str(tmp_path), "--taxa-usd", "0"],
                 ["medir", "--dados", str(tmp_path), "--identidade", *SINT],
                 ["registrar", "--dados", str(tmp_path), "--rodada", str(tmp_path), "--proposta-sha256", "0",
                  "--agora", "2026-11-07T15:00:00+00:00"]):
        with pytest.raises(SystemExit) as saida:
            p11.main(argv)
        assert saida.value.code == 2


def test_codigo_que_nao_e_o_congelado_para_antes_de_ler(p11, dados, monkeypatch):
    raiz, _ = dados
    monkeypatch.setattr(p11, "HASH_CONGELADO", "0" * 64)
    with pytest.raises(SystemExit, match="codigo_diferente_do_congelado"):
        medir(p11, raiz, raiz / "r", "--ensaio")
    assert not (raiz / "r").exists()


@linux
def test_producao_antes_do_fim_da_calibracao_nao_le_nada(p11, dados, capsys, monkeypatch):
    # O fim da janela (6/11/2026) ja passou; sem fixar o relogio este teste passava a falhar sozinho depois dessa data.
    # Empurra o fim da calibracao para o futuro para continuar provando a regra "antes do fim, nada e lido".
    monkeypatch.setattr(p11, "FIM_CALIBRACAO_UTC", datetime.now(timezone.utc) + timedelta(days=1))
    raiz, _ = dados
    assert p11.main(["medir", "--dados", str(raiz)]) == 2
    assert "janela de calibracao so fecha" in capsys.readouterr().out and not (raiz / "etapa3_registro").exists()


@linux
def test_selecao_pela_identidade_do_protocolo(p11, dados):
    raiz, _ = dados
    assert medir(p11, raiz, raiz / "r", "--ensaio") == 3                  # nenhuma captura falsa e da Rithmic Paper
    assert ler(raiz / "r", "resumo.json")["veredito"] == "PARE_SEM_CAPTURAS"
    motivos = {c["captura"]: c["motivo"] for c in ler(raiz / "r", "capturas.json")["excluidas"]}
    assert motivos == {"aberta": "sem_manifesto", "antiga": "outra_identidade", "dia0": "outra_identidade",
                       "dia1": "outra_identidade", "dia2": "outra_identidade", "outra": "outra_identidade"}


@linux
def test_teto_leva_ao_abandono_sem_proposta(p11, dados):
    raiz, _ = dados
    assert medir(p11, raiz, raiz / "r", *ENSAIO) == 4
    resumo = ler(raiz / "r", "resumo.json")
    assert resumo["veredito"] == "ABANDONO_MICROESTRUTURA" and resumo["taxa_usd"] == p11.TAXA_TETO_USD == 12.5
    assert resumo["checklist1"]["acerto_necessario"] > 0.65 and not (raiz / "r" / "proposta.json").exists()
    incluidas = [c["captura"] for c in ler(raiz / "r", "capturas.json")["incluidas"]]
    assert incluidas == ["dia0", "dia1", "dia2"]
    por_dia = ler(raiz / "r", "calibracao.json")["por_dia"]
    assert por_dia["2026-10-28"] < por_dia["2026-10-26"] == por_dia["2026-11-02"]   # faixa do FOMC fora


@linux
def test_captura_recusada_para_tudo(p11, dados):
    raiz, captura = dados
    captura("estragada", date(2026, 10, 30))
    assert medir(p11, raiz, raiz / "r", *ENSAIO, "--taxa-usd", "0") == 3
    resumo = ler(raiz / "r", "resumo.json")
    assert resumo["veredito"] == "PARE_CAPTURA_RECUSADA"
    assert resumo["recusadas"]["calibracao"] == ["estragada: linha_invalida:7"]
    assert not (raiz / "r" / "checklist1.json").exists()


@linux
def test_proposta_aprovada_e_o_que_se_registra(p11, dados, capsys):
    raiz, _ = dados
    rodada = raiz / "r"
    assert medir(p11, raiz, rodada, *ENSAIO, "--taxa-usd", "0") == 0
    resumo, proposta = ler(rodada, "resumo.json"), ler(rodada, "proposta.json")
    assert resumo["veredito"] == "SEGUE_PROPOSTA_PRONTA" and resumo["planos_ja_registrados"] == 0
    assert resumo["checklist1"]["veredito"] == resumo["checklist2"]["veredito"] == "SEGUE"
    cal = ler(rodada, "calibracao.json")
    assert proposta["parametros"] == {"p5": cal["p5"], "p95": cal["p95"], "taxa_usd": 0.0, "alfa": pq.ALFA_POR_TESTE,
                                      "fomc": ["2026-10-28", "2026-12-09"]}
    assert (proposta["calibracao"], proposta["teste"]) == (["2026-10-26", "2026-11-06"], ["2026-11-09", "2026-11-20"])
    assert f"P95 = {cal['p95']}" in proposta["pergunta"] and "0.833%" in proposta["refutacao"]
    sha = resumo["proposta_sha256"]
    registrar = ["registrar", "--dados", str(raiz), "--rodada", str(rodada), "--ensaio"]
    assert p11.main(registrar + ["--proposta-sha256", "0" * 64, "--agora", "2026-11-07T15:00:00+00:00"]) == 2
    assert p11.main(registrar + ["--proposta-sha256", sha, "--agora", "2026-11-07T15:00:00+00:00"]) == 0
    plano = ler(rodada, "registro.json")["plano_id"]
    assert p11.main(registrar + ["--proposta-sha256", sha, "--agora", "2026-11-08T12:00:00+00:00"]) == 0
    assert ler(rodada, "registro.json")["plano_id"] == plano                     # repetir nao gasta outro teste
    capsys.readouterr()
    assert p11.main(registrar + ["--proposta-sha256", sha, "--agora", "2026-11-09T00:30:00+00:00"]) == 2
    assert "registro_fora_da_janela_pre_teste" in capsys.readouterr().out       # 8/11 as 21h30 de Brasilia
    assert p11.main(["registrar", "--dados", str(raiz), "--rodada", str(rodada), "--proposta-sha256", sha]) == 2
    assert p11.planos_registrados(raiz) == 1
