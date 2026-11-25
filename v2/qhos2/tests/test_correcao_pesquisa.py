"""Correcao 1, lote l5d: `qhos2.pesquisa` (QHOS-AUD-253: o hash registrado da captura e os `segments` do manifesto).

Corpus sintetico: uma captura do `Gravador` (ESZ6/CME, fechada) e convertida a mao numa captura segmentada: os bytes
vao para `frames/00000.jsonl`, o manifesto ganha `segments` e `market_frames.jsonl` passa a ser uma isca que ninguem
le. Nenhum hash, versao ou esquema de manifesto e alterado no codigo (063/136/137 excluidos): caracterizacao.
"""
import hashlib
import json

from qhos2 import catalogo as ca
from qhos2 import eventos as ev
from qhos2 import pesquisa as pe
from test_pesquisa import captura


def segmentar(pasta, isca: bytes) -> bytes:
    """Move os quadros para um segmento declarado no manifesto e deixa `isca` em market_frames.jsonl."""
    manifesto = json.loads((pasta / "manifest.json").read_text(encoding="utf-8"))
    bruto = (pasta / "market_frames.jsonl").read_bytes()
    (pasta / ca.PASTA_SEGMENTOS).mkdir()
    (pasta / ca.PASTA_SEGMENTOS / "00000.jsonl").write_bytes(bruto)
    manifesto["segments"] = [{"name": f"{ca.PASTA_SEGMENTOS}/00000.jsonl", "frames": manifesto["frames"],
                              "bytes": manifesto["bytes"], "sha256": manifesto["sha256"]}]
    (pasta / "manifest.json").write_text(json.dumps(manifesto, sort_keys=True), encoding="utf-8")
    (pasta / "market_frames.jsonl").write_bytes(isca)
    return bruto


def test_captura_segmentada_e_verificada_e_lida_pelos_segmentos(tmp_path):
    pasta = captura(tmp_path)
    antes = ev.assinatura(ev.eventos_da_captura(pasta))
    bruto = segmentar(pasta, b'{"isca": true}\n')
    assert ca.verificar_captura(pasta)["verificada"] is True
    assert ev.assinatura(ev.eventos_da_captura(pasta)) == antes          # os eventos vem do segmento, nao da isca
    assert (pasta / "market_frames.jsonl").read_bytes() != bruto


def test_caracterizacao_quadros_sha256_e_o_da_isca_e_nao_dos_bytes_avaliados(tmp_path):
    """Hoje: `_identificar_capturas` faz o hash de `market_frames.jsonl` (pesquisa.py:233,257); com `segments` esse
    arquivo nao e o que `eventos_da_captura` le. O `manifesto_sha256` cobre os segmentos so por transitividade
    (o manifesto lista o sha256 de cada segmento e o catalogo o confere antes da leitura)."""
    pasta = captura(tmp_path)
    isca = b'{"isca": true}\n'
    bruto = segmentar(pasta, isca)
    linha, = pe._identificar_capturas([pasta])
    assert linha["quadros_sha256"] == hashlib.sha256(isca).hexdigest()
    assert linha["quadros_sha256"] != hashlib.sha256(bruto).hexdigest()
    assert linha["manifesto_sha256"] == hashlib.sha256((pasta / "manifest.json").read_bytes()).hexdigest()
    assert hashlib.sha256(bruto).hexdigest() in (pasta / "manifest.json").read_text(encoding="utf-8")


def test_caracterizacao_duplicata_por_segmento_nao_e_detectada_e_isca_igual_e_falsa_duplicata(tmp_path):
    a, b = captura(tmp_path / "a"), captura(tmp_path / "b")
    bruto_a, bruto_b = segmentar(a, b"isca A\n"), segmentar(b, b"isca B\n")
    assert bruto_a == bruto_b                                                  # os mesmos bytes avaliados
    assert ev.assinatura(ev.eventos_da_captura(a)) == ev.assinatura(ev.eventos_da_captura(b))
    linhas = pe._identificar_capturas([a, b])
    assert len(linhas) == 2 and len({l["quadros_sha256"] for l in linhas}) == 2     # hoje: nao e `captura_duplicada`
    (b / "market_frames.jsonl").write_bytes(b"isca A\n")
    try:
        pe._identificar_capturas([a, b])
    except pe.PesquisaError as erro:
        assert str(erro) == "captura_duplicada"                                # a mesma isca: duplicata pela isca
    else:
        raise AssertionError("esperava captura_duplicada pela isca igual")


def test_captura_segmentada_sem_o_arquivo_legado_e_recusada_pela_pesquisa(tmp_path):
    """Fail-closed de hoje: sem `market_frames.jsonl` a pesquisa recusa a captura, embora o catalogo a verifique."""
    pasta = captura(tmp_path)
    segmentar(pasta, b"")
    (pasta / "market_frames.jsonl").unlink()
    assert ca.verificar_captura(pasta)["verificada"] is True
    try:
        pe._identificar_capturas([pasta])
    except pe.PesquisaError as erro:
        assert str(erro) == "captura_inacessivel_ou_invalida"
    else:
        raise AssertionError("esperava captura_inacessivel_ou_invalida")
