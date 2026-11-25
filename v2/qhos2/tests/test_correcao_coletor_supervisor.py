"""Campanha de correcao, lote l5b-coletor: supervisor do coletor (QHOS-AUD-122, 145, 153, 154, 174).

Filhos artificiais e relogio falso; nenhum processo real, nenhuma porta. Os `xfail(strict=True)` demonstram defeitos
confirmados em `src/qhos2/supervisao_coletor.py` (congelado em J0); o patch minimo proposto fica em
`09_correcao/EVIDENCIAS/<ID>/patch_proposto.diff`.
"""
import json

import pytest

from qhos2 import supervisao_coletor as su
from qhos2.config import ConfigError

from test_supervisao_coletor import AGORA, Filho, captura


def _fabrica(filhos, codigo_de_saida=None):
    def fabrica(*_a, **_kw):
        filho = Filho()
        if codigo_de_saida is not None:
            filho.codigo_programado = codigo_de_saida
        filhos.append(filho)
        return filho
    return fabrica


# ---- QHOS-AUD-122: executar() encerra o filho quando selecionar() lanca ----------------------------------------

def test_executar_do_supervisor_encerra_filho_quando_selecionar_lanca(tmp_path, monkeypatch):
    """Lacuna de teste (campo 13 do achado): um ConfigError tardio em `selecionar` nao pode deixar filho orfao."""
    filhos, chamadas = [], [0]

    def selecionar_que_falha_na_segunda(raiz, **_):
        chamadas[0] += 1
        if chamadas[0] == 1:
            return su.Selecao(None, "sem_captura")
        raise ConfigError("data_root_invalido")
    monkeypatch.setattr(su, "selecionar", selecionar_que_falha_na_segunda)
    s = su.Supervisor(tmp_path, popen=_fabrica(filhos), avisar=lambda _: None)
    with pytest.raises(ConfigError, match="data_root_invalido"):
        s.executar(dormir=lambda _: None)
    assert len(filhos) == 1 and filhos[0].terminacoes == 1 and filhos[0].poll() is not None
    assert s._filho is None


def test_executar_do_supervisor_encerra_filho_quando_parar_pede(tmp_path):
    filhos, voltas = [], [0]

    def parar():
        voltas[0] += 1
        return voltas[0] > 2
    s = su.Supervisor(tmp_path, popen=_fabrica(filhos), avisar=lambda _: None)
    s.executar(parar=parar, dormir=lambda _: None)
    assert len(filhos) == 1 and filhos[0].terminacoes == 1


# ---- QHOS-AUD-145: loop de crash: um aviso por saida, com codigo; backoff volta apos filho estavel ----------------

def _supervisor_com_relogio(tmp_path, filhos, agora, avisos, **extra):
    return su.Supervisor(tmp_path, popen=_fabrica(filhos), relogio=lambda: agora[0],
                         avisar=lambda linha: avisos.append(json.loads(linha)), **extra)


def _loop_de_crash(tmp_path, segundos=300, passo=5.0):
    """Filho que sai sempre com codigo 1; o supervisor e chamado a cada `passo` s por `segundos` s."""
    agora, filhos, avisos = [0.0], [], []
    s = _supervisor_com_relogio(tmp_path, filhos, agora, avisos)
    escolha = su.Selecao(None, "sem_captura")
    saidas = 0
    while agora[0] <= segundos:
        if filhos and filhos[-1].codigo is None:
            filhos[-1].codigo = 1          # o filho morreu antes desta volta
            saidas += 1
        s.passo(escolha)
        agora[0] += passo
    s.encerrar()
    return filhos, avisos, saidas


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-145: `_aviso` deduplica por codigo; um loop de crash gera um unico "
                                       "`coletor_encerrou_backoff` no journal")
def test_loop_de_crash_gera_um_aviso_por_saida_do_filho(tmp_path):
    filhos, avisos, saidas = _loop_de_crash(tmp_path)
    encerrou = [a for a in avisos if a["estado"] == "coletor_encerrou_backoff"]
    assert saidas >= 5 and len(filhos) >= 5            # o cenario reproduz um loop, nao uma queda isolada
    assert len(encerrou) == saidas


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-145: o aviso de saida do filho nao traz o `returncode` (poll() e "
                                       "usado so como booleano)")
def test_aviso_de_saida_do_filho_traz_o_codigo_de_saida(tmp_path):
    _, avisos, _ = _loop_de_crash(tmp_path, segundos=10)
    encerrou = [a for a in avisos if a["estado"] == "coletor_encerrou_backoff"]
    assert encerrou and all(a.get("returncode") == 1 for a in encerrou)


def test_caracterizacao_loop_de_crash_nunca_alterna_para_limite_de_partidas(tmp_path):
    """Com backoff 2,4,...,60 s e intervalo 5 s, ha 3-5 partidas por 60 s (< 6): o codigo nunca alterna, logo a
    deduplicacao por `_ultimo_aviso` suprime todas as saidas seguintes (mecanismo do achado)."""
    filhos, avisos, saidas = _loop_de_crash(tmp_path)
    codigos = [a["estado"] for a in avisos]
    assert "limite_de_partidas_aguardando" not in codigos
    assert codigos.count("coletor_encerrou_backoff") == 1 and saidas > 1


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-145: `_falhas` nunca e zerado; depois de seis saidas na vida do "
                                       "processo toda reinicializacao espera 60 s, mesmo apos filho estavel")
def test_backoff_volta_a_2_s_depois_de_filho_estavel_por_10_min(tmp_path):
    agora, filhos, avisos = [0.0], [], []
    s = _supervisor_com_relogio(tmp_path, filhos, agora, avisos, max_partidas=100, janela_s=60)
    escolha = su.Selecao(None, "sem_captura")
    for _ in range(6):                                  # seis falhas rapidas
        s.passo(escolha)
        filhos[-1].codigo = 1
        s.passo(escolha)
        agora[0] += 61
    s.passo(escolha)                                    # filho estavel por 10 minutos
    estavel = filhos[-1]
    assert estavel.codigo is None
    agora[0] += 600
    s.passo(escolha)
    assert filhos[-1] is estavel
    estavel.codigo = 1                                  # sai uma vez; a proxima partida deve vir em ate 2 s
    s.passo(escolha)
    agora[0] += 2
    s.passo(escolha)
    try:
        assert filhos[-1] is not estavel, f"nenhuma partida em 2 s; _falhas={s._falhas}, _proxima={s._proxima}"
    finally:
        s.encerrar()


# ---- QHOS-AUD-153: pasta sem market_frames.jsonl bloqueia toda selecao de encerrada, sem nomear a pasta ----------

def test_caracterizacao_pasta_vazia_bloqueia_selecao_de_encerrada_sem_nomear(tmp_path):
    boa = captura(tmp_path, "boa", criada=AGORA - 100)
    (tmp_path / "capturas" / "vazia").mkdir()
    escolha = su.selecionar(tmp_path, agora=AGORA)
    assert escolha.pasta is None and escolha.codigo == "captura_incompleta_nao_resolvida"
    assert "vazia" not in json.dumps(escolha.chave)
    (tmp_path / "capturas" / "vazia").rmdir()
    assert su.selecionar(tmp_path, agora=AGORA).pasta == boa     # so a pasta vazia impedia


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-153: a recusa `captura_incompleta_nao_resolvida` nao diz qual pasta "
                                       "(`incompletas.append(None)` descarta o nome)")
def test_recusa_por_pasta_sem_raw_nomeia_a_pasta(tmp_path):
    captura(tmp_path, "boa", criada=AGORA - 100)
    (tmp_path / "capturas" / "vazia").mkdir()
    escolha = su.selecionar(tmp_path, agora=AGORA)
    assert escolha.pasta is None
    assert "vazia" in json.dumps(escolha.chave) or "vazia" in str(getattr(escolha, "detalhe", ""))


# ---- QHOS-AUD-154: filho vivo mas travado e "saudavel" --------------------------------------------------------------

def test_caracterizacao_filho_vivo_nunca_e_substituido_por_falta_de_sonda(tmp_path):
    """`poll() is None` e o unico criterio de saude: um filho travado por horas nunca e terminado."""
    agora, filhos, avisos = [0.0], [], []
    s = _supervisor_com_relogio(tmp_path, filhos, agora, avisos)
    escolha = su.Selecao(None, "sem_captura")
    for _ in range(720):                                # 1 hora, a cada 5 s, filho sempre vivo
        s.passo(escolha)
        agora[0] += 5
    assert len(filhos) == 1 and filhos[0].terminacoes == 0
    assert not hasattr(s, "sonda") and not hasattr(s, "_sonda")
    s.encerrar()


# ---- QHOS-AUD-174: supervisao_recusada sem motivo -------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="QHOS-AUD-174: `main` imprime `supervisao_recusada` sem o codigo do ValueError")
def test_intervalo_zero_imprime_o_motivo_da_recusa(tmp_path, capsys):
    import sys
    if sys.platform != "linux":
        pytest.skip("o supervisor so roda no Linux")
    assert su.main(["--raiz", str(tmp_path), "--intervalo", "0"]) == 2
    assert "supervisao_recusada:limites_de_supervisao_invalidos" in capsys.readouterr().out


def test_caracterizacao_intervalo_zero_e_recusado_sem_motivo(tmp_path, capsys):
    import sys
    if sys.platform != "linux":
        pytest.skip("o supervisor so roda no Linux")
    assert su.main(["--raiz", str(tmp_path), "--intervalo", "0"]) == 2
    saida = capsys.readouterr().out
    assert saida.strip() == "supervisao_recusada"
