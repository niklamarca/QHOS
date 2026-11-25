"""Captura segmentada (`segments` no manifesto, `frames/00000.jsonl`...): quem le e quem recusa (QHOS-AUD-165).

Uma captura sintetica valida (`qhos2.sintetico.gravar`, arquivo unico) e reescrita no formato segmentado que o V1
produzia. Cada consumidor e exercitado uma vez; o resultado e a matriz de `docs/FORMATO_CAPTURA.md`. Nada aqui
implementa suporte a segmentos: so caracteriza o que o codigo faz hoje.
"""
import hashlib
import json
import shutil
import time
from pathlib import Path

import pytest

from qhos2 import atraso, eventos as ev, gravador, historico, inspecao, leitura, pesquisa, sintetico as si
from qhos2 import supervisao_coletor as su
from qhos2.catalogo import verificar_captura
from qhos2.operacao import OperacaoError, copiar_captura

TICK, SIMBOLO, BOLSA = 0.25, "SINTETICO", "QHOS2"


def _segmentar(pasta: Path, partes: int = 2, *, manter_arquivo_unico: bool = False) -> Path:
    """Reescreve uma captura de arquivo unico como segmentos; os totais do manifesto nao mudam."""
    linhas = (pasta / gravador.ARQUIVO).read_bytes().splitlines(keepends=True)
    assert len(linhas) >= partes
    (pasta / "frames").mkdir()
    segmentos, passo = [], (len(linhas) + partes - 1) // partes
    for k in range(partes):
        bloco = linhas[k * passo:(k + 1) * passo]
        nome = f"frames/{k:05d}.jsonl"
        (pasta / nome).write_bytes(b"".join(bloco))
        segmentos.append({"name": nome, "frames": len(bloco), "bytes": sum(map(len, bloco)),
                          "sha256": hashlib.sha256(b"".join(bloco)).hexdigest()})
    manifesto = json.loads((pasta / "manifest.json").read_text(encoding="utf-8"))
    manifesto["segments"] = segmentos
    (pasta / "manifest.json").write_text(json.dumps(manifesto, sort_keys=True), encoding="utf-8")
    if not manter_arquivo_unico:
        (pasta / gravador.ARQUIVO).unlink()
    return pasta


@pytest.fixture
def capturas(tmp_path):
    """(unica, segmentada): mesmo cenario sintetico; a segmentada nao tem `market_frames.jsonl`."""
    cenario = si.gerar(7, 40)
    unica = si.gravar(cenario, tmp_path / "unica", captura_id="unica")
    segmentada = si.gravar(cenario, tmp_path / "segmentada" / "capturas", captura_id="segmentada")
    return unica, _segmentar(segmentada)


def test_gravador_nunca_escreve_segmentos(capturas):
    unica, _ = capturas
    manifesto = json.loads((unica / "manifest.json").read_text(encoding="utf-8"))
    assert "segments" not in manifesto and (unica / gravador.ARQUIVO).is_file()
    assert gravador.ARQUIVO == "market_frames.jsonl" and not (unica / "frames").exists()


def test_catalogo_verifica_a_segmentada_e_a_unica_com_os_mesmos_totais(capturas):
    unica, segmentada = capturas
    a, b = verificar_captura(unica), verificar_captura(segmentada)
    assert a["verificada"] is True and b["verificada"] is True
    assert (a["quadros"], a["bytes"]) == (b["quadros"], b["bytes"])


def test_eventos_atraso_inspecao_e_historico_leem_segmentos_pelo_catalogo(capturas, tmp_path):
    unica, segmentada = capturas
    assert ev.assinatura(ev.eventos_da_captura(segmentada, tick=TICK)) == ev.assinatura(ev.eventos_da_captura(unica, tick=TICK))
    assert atraso.medir(segmentada)["quadros"] == atraso.medir(unica)["quadros"]
    assert inspecao.inspecionar(segmentada)["quadros"] == inspecao.inspecionar(unica)["quadros"]
    barras = historico.barras_da_captura(segmentada, tmp_path / "cache", tick=TICK, simbolo=SIMBOLO, bolsa=BOLSA)
    referencia = historico.barras_da_captura(unica, tmp_path / "cache_unica", tick=TICK, simbolo=SIMBOLO, bolsa=BOLSA)
    assert barras is not None and barras.minutos == referencia.minutos and barras.minutos
    assert (tmp_path / "cache" / f"{segmentada.name}.json").is_file()


def test_microestrutura_le_segmentos_pelos_eventos(capturas):
    from qhos2 import microestrutura as mi
    unica, segmentada = capturas
    a, b = list(mi.estados_da_captura(segmentada)), list(mi.estados_da_captura(unica))
    assert a and [e.recebido_ns for e, _ in a] == [e.recebido_ns for e, _ in b]
    assert [repr(m) for _, m in a] == [repr(m) for _, m in b]


def test_caminho_de_pesquisa_e_hipoteses_recusa_a_segmentada(capturas, tmp_path):
    """`pesquisa.amostras_da_captura` (:385), `executar_registrado` (:298) e `hipoteses.barras_da_captura` (:197-200)
    chamam `_identificar_capturas` antes de ler: a segmentada e recusada mesmo com a leitura por eventos possivel."""
    from qhos2 import hipoteses
    _, segmentada = capturas
    _como_esz6_qualificada(segmentada)
    with pytest.raises(pesquisa.PesquisaError, match="captura_inacessivel_ou_invalida"):
        pesquisa.amostras_da_captura(segmentada)
    with pytest.raises(pesquisa.PesquisaError, match="captura_inacessivel_ou_invalida"):
        hipoteses.barras_da_captura(segmentada)
    # executar_registrado so chega a _identificar_capturas depois de um plano registrado e do fim do teste
    # (pesquisa.py:286-298); o ponto comum e a mesma funcao, exercitada acima.
    fonte = (Path(pesquisa.__file__)).read_text(encoding="utf-8")
    assert fonte.count("_identificar_capturas(") >= 3


def test_leitor_sem_market_frames_fica_aguardando(capturas):
    """Sem `market_frames.jsonl` o Leitor nao abre nada: uma volta devolve [] e o estado continua AGUARDANDO.
    A espera indefinida com manifesto presente e o QHOS-AUD-015 (fora deste lote); aqui so se registra uma volta."""
    _, segmentada = capturas
    leitor = leitura.Leitor(segmentada, tick=TICK, simbolo=SIMBOLO, bolsa=BOLSA)
    assert leitor.avancar() == [] and leitor.estado == leitura.AGUARDANDO and leitor.codigo is None


def test_leitor_com_arquivo_unico_e_segments_no_manifesto_falha_ao_conferir_o_fim(tmp_path):
    pasta = si.gravar(si.gerar(7, 40), tmp_path / "ambos", captura_id="ambos")
    _segmentar(pasta, manter_arquivo_unico=True)
    leitor = leitura.Leitor(pasta, tick=TICK, simbolo=SIMBOLO, bolsa=BOLSA)
    for _ in range(50):
        leitor.avancar()
        if leitor.estado in (leitura.ENCERRADA, leitura.FALHA):
            break
    assert (leitor.estado, leitor.codigo) == (leitura.FALHA, "segmentos_nao_suportados")


def test_supervisor_trata_a_segmentada_como_incompleta(capturas):
    _, segmentada = capturas
    raiz = segmentada.parents[1]
    assert su.selecionar(raiz, agora=time.time()).codigo == "captura_incompleta_nao_resolvida"


def _como_esz6_qualificada(pasta: Path) -> None:
    """So metadados: o manifesto passa a declarar o instrumento e a qualificacao que `pesquisa` exige."""
    manifesto = json.loads((pasta / "manifest.json").read_text(encoding="utf-8"))
    manifesto["transport_closed_normally"] = True
    manifesto["status"].update({"requested_symbol": "ESZ6", "exchange": "CME",
                                "sessao": {"estado": "FECHADA", "retrato_completo": True, "erro": None,
                                           "referencia": {"min_qprice_change": 0.25}}})
    (pasta / "manifest.json").write_text(json.dumps(manifesto, sort_keys=True), encoding="utf-8")


def test_pesquisa_recusa_a_segmentada(capturas):
    """`pesquisa._identificar_capturas` faz SHA-256 de `market_frames.jsonl`; sem ele, recusa."""
    _, segmentada = capturas
    with pytest.raises(pesquisa.PesquisaError, match="instrumento_fora_do_protocolo"):
        pesquisa._identificar_capturas([segmentada])         # a sintetica e recusada antes, pelo instrumento
    _como_esz6_qualificada(segmentada)
    with pytest.raises(pesquisa.PesquisaError, match="captura_inacessivel_ou_invalida"):
        pesquisa._identificar_capturas([segmentada])


def _sem_identidade(pasta: Path) -> None:
    """Captura ao estilo V1: sem `capture_identity.json` e sem `capture_identity_sha256` no manifesto."""
    manifesto = json.loads((pasta / "manifest.json").read_text(encoding="utf-8"))
    manifesto.pop("capture_identity_sha256", None)
    (pasta / "manifest.json").write_text(json.dumps(manifesto, sort_keys=True), encoding="utf-8")
    (pasta / "capture_identity.json").unlink()


@pytest.mark.parametrize("estilo", ["com_identidade", "sem_identidade_como_o_v1"])
def test_copia_recusa_a_segmentada_com_codigo(capturas, tmp_path, estilo):
    """`copiar_captura` copia so `market_frames.jsonl` (+ identidade) e `manifest.json`: sem o arquivo unico, recusa."""
    _, segmentada = capturas
    if estilo == "sem_identidade_como_o_v1":
        _sem_identidade(segmentada)
    assert verificar_captura(segmentada)["verificada"] is True
    with pytest.raises(OperacaoError, match="arquivo_regular_esperado"):
        copiar_captura(segmentada, tmp_path / "copia")
    assert not (tmp_path / "copia").exists()


def test_copia_da_unica_segue_funcionando(capturas, tmp_path):
    unica, _ = capturas
    assert copiar_captura(unica, tmp_path / "copia")["copiada"] is True
    shutil.rmtree(tmp_path / "copia")
