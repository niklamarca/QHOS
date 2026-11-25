"""Fechamento diario contra capturas do servidor falso e uma captura sintetica no papel do ES."""
import hashlib
import json
import tarfile
from pathlib import Path

import pytest

from qhos2 import sintetico

from coleta_multi import fechamento
from coleta_multi.tests.test_sessao_multi import ITENS, Item, Servidor, _rodar, _sessao
from coleta_multi.lista import EXPLICITO

DIA = "2026-10-30"


def _dia(tmp_path, **kw):
    itens = ITENS + (Item("GEZ2", EXPLICITO, "CME", "Eurodollar"),)
    duracao = kw.pop("duracao", 0.3)
    sessao = _sessao(Servidor(**kw), tmp_path, itens=itens, duracao_s=duracao)
    _rodar(sessao)
    return sessao


def _es(tmp_path):
    return sintetico.gravar(sintetico.gerar(7, 300), tmp_path / "capturas_es", captura_id="es0001")


def _hashes(pasta):
    return {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in sorted(Path(pasta).iterdir())}


def test_dia_completo_publica_tudo_e_nao_toca_no_es(tmp_path):
    sessao = _dia(tmp_path)
    es = _es(tmp_path)
    antes = _hashes(es)
    destino = tmp_path / "DataSet"
    index = fechamento.fechar_dia(DIA, tmp_path / "staging", destino, ativos=sessao.status_multi()["ativos"],
                                  captura_es=es, prova_es={"resultado": "capturado"}, processos=2)
    assert _hashes(es) == antes and sorted(p.name for p in es.iterdir()) == sorted(antes)
    pasta = destino / DIA
    nomes = sorted(p.name for p in pasta.iterdir())
    assert nomes == ["CBOT_ZNZ6.tar.xz", "COMEX_GCZ6.tar.xz", "INDEX.json", "NYMEX_CLZ6.tar.xz",
                     "QHOS2_SINTETICO.tar.xz", "SHA256SUMS.txt"]
    por = {e["simbolo_pedido"]: e for e in index["ativos"]}
    assert por["GEZ2"]["estado"] == "excluido" and por["GEZ2"]["motivo"] == "vencido_20221219"
    assert por["ES"]["origem"] == "copia_da_captura_do_agendador" and por["ES"]["estado"] == "publicado"
    for chave in ("CL", "ZN", "GCZ6"):
        e = por[chave]
        assert e["estado"] == "publicado" and e["profundidade"] == "mbo_e_negocios"
        ativo = next(a for a in sessao.ativos if a.item.exibido == chave)
        assert e["quadros"] == ativo.gravador.quadros >= 13 and e["bytes_brutos"] == ativo.gravador.bytes
        assert e["bytes_xz"] > 0 and len(e["sha256_manifesto"]) == 64
        assert "desvio_ms" in e["relogio"] and e["relogio"]["utc_qualificado"] is False
    for linha in (pasta / "SHA256SUMS.txt").read_text().splitlines():
        sha, nome = linha.split("  ")
        assert hashlib.sha256((pasta / nome).read_bytes()).hexdigest() == sha
    cl = sessao.ativos[0].gravador.pasta
    with tarfile.open(pasta / "NYMEX_CLZ6.tar.xz") as tar:
        assert tar.getnames() == ["NYMEX_CLZ6/manifest.json", "NYMEX_CLZ6/capture_identity.json",
                                  "NYMEX_CLZ6/market_frames.jsonl"]
        for nome in fechamento.ARQUIVOS:
            assert tar.extractfile(f"NYMEX_CLZ6/{nome}").read() == (cl / nome).read_bytes()
    assert (cl / "market_frames.jsonl").exists()  # o original fica
    with pytest.raises(fechamento.FechamentoError, match="dia_ja_fechado"):
        fechamento.fechar_dia(DIA, tmp_path / "staging", destino, ativos=[], captura_es=None, prova_es=None)


def test_captura_adulterada_nao_e_publicada(tmp_path):
    sessao = _dia(tmp_path)
    cl = sessao.ativos[0].gravador.pasta
    with open(cl / "market_frames.jsonl", "ab") as f:
        # carimbo realista: com received_ns=1 o catalogo varreria cada segundo desde 1970 no histograma
        f.write(b'{"index":99,"template_id":160,"received_ns":1793300000000000000,"protobuf_base64":""}\n')
    index = fechamento.fechar_dia(DIA, tmp_path / "staging", tmp_path / "DataSet",
                                  ativos=sessao.status_multi()["ativos"], captura_es=None,
                                  prova_es={"resultado": "interrompido"}, processos=1)
    por = {e["simbolo_pedido"]: e for e in index["ativos"]}
    assert por["CL"]["estado"] == "nao_publicado" and por["CL"]["motivo"].startswith("nao_verificada")
    assert not (tmp_path / "DataSet" / DIA / "NYMEX_CLZ6.tar.xz").exists()
    assert por["ES"]["estado"] == "sem_captura" and por["ES"]["motivo"] == "interrompido"
    assert "NYMEX_CLZ6.tar.xz" not in (tmp_path / "DataSet" / DIA / "SHA256SUMS.txt").read_text()


def test_nunca_sobrescreve_no_destino(tmp_path):
    sessao = _dia(tmp_path)
    alvo = tmp_path / "DataSet" / DIA
    alvo.mkdir(parents=True)
    (alvo / "CBOT_ZNZ6.tar.xz").write_bytes(b"anterior")
    index = fechamento.fechar_dia(DIA, tmp_path / "staging", tmp_path / "DataSet",
                                  ativos=sessao.status_multi()["ativos"], captura_es=None, prova_es=None, processos=1)
    zn = next(e for e in index["ativos"] if e["simbolo_pedido"] == "ZN")
    assert (zn["estado"], zn["motivo"]) == ("nao_publicado", "destino_ja_existe")
    assert (alvo / "CBOT_ZNZ6.tar.xz").read_bytes() == b"anterior"


def test_dia_interrompido_publica_marcado(tmp_path):
    sessao = _dia(tmp_path, cair_apos=2, duracao=30)
    index = fechamento.fechar_dia(DIA, tmp_path / "staging", tmp_path / "DataSet",
                                  ativos=sessao.status_multi()["ativos"], captura_es=None, prova_es=None, processos=1)
    estados = {e["simbolo_pedido"]: e["estado"] for e in index["ativos"]}
    assert estados["CL"] == estados["ZN"] == "publicado_dia_interrompido"


def test_xz_corrompido_e_recusado(tmp_path):
    sessao = _dia(tmp_path)
    a = sessao.ativos[0]
    r = fechamento.compactar(a.gravador.pasta, tmp_path / "x.tar.xz", a.captura_id)
    dados = bytearray((tmp_path / "x.tar.xz").read_bytes())
    dados[len(dados) // 2] ^= 0xFF
    (tmp_path / "y.tar.xz").write_bytes(bytes(dados))
    with pytest.raises(Exception):
        fechamento.conferir(tmp_path / "y.tar.xz", a.captura_id, r["manifesto"], {})
