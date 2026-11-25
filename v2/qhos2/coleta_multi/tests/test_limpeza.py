"""Liberacao do bruto: so sai o que esta reconferido no DataSet; nada historico se perde."""
import tarfile
from datetime import date, timedelta
from pathlib import Path

from coleta_multi import fechamento, limpeza
from coleta_multi.tests.test_sessao_multi import ITENS, Servidor, _rodar, _sessao

DIA = "2026-10-30"
HOJE = date(2026, 10, 30) + timedelta(days=limpeza.DIAS_MINIMOS)


def _dia(tmp_path):
    sessao = _sessao(Servidor(), tmp_path, itens=ITENS, raiz=tmp_path / "staging" / DIA)  # layout real: staging/<dia>/
    _rodar(sessao)
    return sessao


def _fechar(tmp_path, sessao):
    fechamento.fechar_dia(DIA, tmp_path / "staging" / DIA, tmp_path / "DataSet", ativos=sessao.status_multi()["ativos"],
                          captura_es=None, prova_es=None, processos=1)


def _publicado(tmp_path):
    sessao = _dia(tmp_path)
    _fechar(tmp_path, sessao)
    return sessao


def test_bruto_reconferido_sai_e_o_historico_fica_inteiro(tmp_path):
    sessao = _publicado(tmp_path)
    originais = {a.captura_id: {n: (a.gravador.pasta / n).read_bytes() for n in fechamento.ARQUIVOS}
                 for a in sessao.ativos if a.gravador}
    r = limpeza.liberar(tmp_path / "staging", tmp_path / "DataSet", HOJE)
    assert sorted(x["captura"] for x in r["removidos"]) == sorted(originais) and r["bytes_liberados"] > 0
    for captura_id, arquivos in originais.items():
        assert not (tmp_path / "staging" / DIA / captura_id).exists()
        assert (tmp_path / "staging" / DIA / ".xz" / f"{captura_id}.tar.xz").exists()  # copia local fica
        with tarfile.open(tmp_path / "DataSet" / DIA / f"{captura_id}.tar.xz") as tar:
            for nome, bruto in arquivos.items():
                assert tar.extractfile(f"{captura_id}/{nome}").read() == bruto  # o historico continua inteiro


def test_dia_recente_nao_sai(tmp_path):
    _publicado(tmp_path)
    r = limpeza.liberar(tmp_path / "staging", tmp_path / "DataSet", HOJE - timedelta(days=1))
    assert r["removidos"] == [] and len(list((tmp_path / "staging" / DIA).glob("*_*"))) == 3


def test_xz_do_dataset_corrompido_mantem_o_bruto(tmp_path):
    _publicado(tmp_path)
    alvo = tmp_path / "DataSet" / DIA / "NYMEX_CLZ6.tar.xz"
    dados = bytearray(alvo.read_bytes())
    dados[len(dados) // 2] ^= 0xFF
    alvo.write_bytes(bytes(dados))
    r = limpeza.liberar(tmp_path / "staging", tmp_path / "DataSet", HOJE)
    assert (tmp_path / "staging" / DIA / "NYMEX_CLZ6" / "market_frames.jsonl").exists()
    assert {"dia": DIA, "captura": "NYMEX_CLZ6", "motivo": "xz_do_dataset_ausente_ou_diverge"} in r["mantidos"]
    assert len(r["removidos"]) == 2


def test_sem_index_ou_com_index_adulterado_nada_sai(tmp_path):
    _publicado(tmp_path)
    index = tmp_path / "DataSet" / DIA / "INDEX.json"
    index.write_text(index.read_text().replace('"publicado"', '"publicado" '))
    r = limpeza.liberar(tmp_path / "staging", tmp_path / "DataSet", HOJE)
    assert r["removidos"] == [] and r["mantidos"] == [{"dia": DIA, "motivo": "index_diverge_do_sha256sums"}]
    index.unlink()
    assert limpeza.liberar(tmp_path / "staging", tmp_path / "DataSet", HOJE)["removidos"] == []


def test_captura_nao_publicada_nunca_sai(tmp_path):
    sessao = _dia(tmp_path)
    cl = sessao.ativos[0].gravador.pasta
    with open(cl / "market_frames.jsonl", "ab") as f:
        f.write(b'{"index":99,"template_id":160,"received_ns":1793300000000000000,"protobuf_base64":""}\n')
    _fechar(tmp_path, sessao)
    r = limpeza.liberar(tmp_path / "staging", tmp_path / "DataSet", HOJE)
    assert (cl / "market_frames.jsonl").exists()
    assert {"dia": DIA, "captura": "NYMEX_CLZ6", "motivo": "nao_e_captura_publicada_do_coletor"} in r["mantidos"]


def test_reserva_do_es_sai_do_staging_so_depois_de_conferida(tmp_path):
    from coleta_multi.lista import ES
    from coleta_multi.tests.test_sessao_multi import FRONTS, REFS
    servidor = Servidor(fronts={**FRONTS, ("ES", "CME"): "ESZ6"}, refs={**REFS, ("ESZ6", "CME"): "20261218"})
    sessao = _sessao(servidor, tmp_path, itens=ITENS + (ES,), raiz=tmp_path / "staging" / DIA)
    _rodar(sessao)
    _fechar(tmp_path, sessao)
    assert (tmp_path / "DataSet" / DIA / "CME_ESZ6_reserva.tar.xz").exists()
    r = limpeza.liberar(tmp_path / "staging", tmp_path / "DataSet", HOJE)
    assert "CME_ESZ6" in {x["captura"] for x in r["removidos"]} and len(r["removidos"]) == 4
    assert not (tmp_path / "staging" / DIA / "CME_ESZ6").exists()
    assert (tmp_path / "staging" / DIA / ".xz" / "CME_ESZ6_reserva.tar.xz").exists()  # copia local fica


def test_reserva_com_xz_divergente_fica(tmp_path):
    from coleta_multi.lista import ES
    from coleta_multi.tests.test_sessao_multi import FRONTS, REFS
    servidor = Servidor(fronts={**FRONTS, ("ES", "CME"): "ESZ6"}, refs={**REFS, ("ESZ6", "CME"): "20261218"})
    sessao = _sessao(servidor, tmp_path, itens=ITENS + (ES,), raiz=tmp_path / "staging" / DIA)
    _rodar(sessao)
    _fechar(tmp_path, sessao)
    alvo = tmp_path / "DataSet" / DIA / "CME_ESZ6_reserva.tar.xz"
    dados = bytearray(alvo.read_bytes())
    dados[len(dados) // 2] ^= 0xFF
    alvo.write_bytes(bytes(dados))
    r = limpeza.liberar(tmp_path / "staging", tmp_path / "DataSet", HOJE)
    assert (tmp_path / "staging" / DIA / "CME_ESZ6" / "market_frames.jsonl").exists()
    assert {"dia": DIA, "captura": "CME_ESZ6", "motivo": "xz_do_dataset_ausente_ou_diverge"} in r["mantidos"]


def test_simulacao_nao_remove(tmp_path):
    _publicado(tmp_path)
    r = limpeza.liberar(tmp_path / "staging", tmp_path / "DataSet", HOJE, executar=False)
    assert len(r["removidos"]) == 3 and (tmp_path / "staging" / DIA / "NYMEX_CLZ6").exists()
