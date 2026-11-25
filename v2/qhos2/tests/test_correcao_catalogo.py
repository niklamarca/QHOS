"""Campanha de correcao, lote l5c-leitura: catalogo (QHOS-AUD-111, 196).

Sem xfail: caracterizacoes (chaves do relatorio por ponto de retorno; manifesto por link e seguido) e um teste do
FIFO. `catalogo.py` nao muda nesta janela; as opcoes de patch para a decisao estao em `EVIDENCIAS/<ID>/patch_proposto.diff`.
"""
import json
import os
import shutil

import pytest

from qhos2 import sintetico as si
from qhos2.catalogo import verificar_captura

CHAVES_BASE = {"captura_id", "caminho", "verificada", "divergencias"}
CHAVES_MANIFESTO = {"esquema", "sistema", "ambiente", "plant", "simbolo", "bolsa"}
CHAVES_TOTAIS = {"quadros", "bytes", "sha256", "indice_continuo", "por_template", "quadros_por_segundo",
                 "primeiro_ns", "ultimo_ns", "duracao_s", "relogio_voltou"}
CHAVES_FIXAS = CHAVES_BASE | CHAVES_MANIFESTO | CHAVES_TOTAIS | {"fechamento_normal"}


def _boa(tmp_path):
    return si.gravar(si.gerar(1, 5), tmp_path / "boa")


def _sem_manifesto(tmp_path):
    pasta = si.gravar(si.gerar(1, 5), tmp_path / "sem_manifesto")
    (pasta / "manifest.json").unlink()
    return pasta


def _esquema_desconhecido(tmp_path):
    pasta = si.gravar(si.gerar(1, 5), tmp_path / "esquema")
    m = json.loads((pasta / "manifest.json").read_text())
    m["schema"] = "outra_coisa"
    (pasta / "manifest.json").write_text(json.dumps(m))
    return pasta


def _sem_arquivo(tmp_path):
    pasta = si.gravar(si.gerar(1, 5), tmp_path / "sem_arquivo")
    (pasta / "market_frames.jsonl").unlink()
    return pasta


def _truncada(tmp_path):
    pasta = si.gravar(si.gerar(1, 5), tmp_path / "truncada")
    bruto = (pasta / "market_frames.jsonl").read_bytes()
    (pasta / "market_frames.jsonl").write_bytes(bruto[:-1])
    return pasta


CAMINHOS = {"boa": _boa, "manifesto_ausente": _sem_manifesto, "esquema_desconhecido": _esquema_desconhecido,
            "arquivo_ausente": _sem_arquivo, "linha_truncada": _truncada}
# ---- QHOS-AUD-111: relatorio com chaves condicionais -----------------------------------------------------------
# Hoje (36f0e1f): manifesto_ausente devolve so CHAVES_BASE (catalogo.py:59-65); esquema_desconhecido, arquivo_ausente e
# linha_truncada devolvem CHAVES_BASE | CHAVES_MANIFESTO (:68-88); so a captura boa chega a :105-107 com tudo.

def test_captura_boa_tem_todas_as_chaves(tmp_path):
    relatorio = verificar_captura(_boa(tmp_path))
    assert relatorio["verificada"] is True and set(relatorio) == CHAVES_FIXAS


CHAVES_HOJE = {"manifesto_ausente": CHAVES_BASE, "esquema_desconhecido": CHAVES_BASE | CHAVES_MANIFESTO,
               "arquivo_ausente": CHAVES_BASE | CHAVES_MANIFESTO, "linha_truncada": CHAVES_BASE | CHAVES_MANIFESTO}


@pytest.mark.parametrize("caminho", sorted(CHAVES_HOJE))
def test_caracterizacao_chaves_do_relatorio_por_ponto_de_retorno(tmp_path, caminho):
    """QHOS-AUD-111, caracterizacao: o relatorio e um tipo aberto (catalogo.py:59-78 vs :105-107); nenhum contrato fixa
    as chaves. Consumidores usam `.get` (agenda.py:216-221, captura.py:229-230, catalogo.py:117,125), o que hoje basta.
    Se o autor fixar as chaves (patch em EVIDENCIAS/QHOS-AUD-111), troca-se a assercao por `== CHAVES_FIXAS`."""
    relatorio = verificar_captura(CAMINHOS[caminho](tmp_path))
    assert relatorio["verificada"] is False and relatorio["divergencias"] == [caminho]
    assert set(relatorio) == CHAVES_HOJE[caminho]


# ---- QHOS-AUD-196: `_ler_manifesto` segue symlink -----------------------------------------------------------------

def _manifesto_por_link(tmp_path):
    pasta = si.gravar(si.gerar(1, 5), tmp_path / "ligada")
    fora = tmp_path / "fora"
    fora.mkdir()
    shutil.move(str(pasta / "manifest.json"), str(fora / "manifest.json"))
    try:
        os.symlink(fora / "manifest.json", pasta / "manifest.json")
    except (OSError, NotImplementedError, AttributeError) as erro:
        pytest.skip(f"sem symlink nesta plataforma: {erro}")
    return pasta


def test_caracterizacao_manifesto_que_e_symlink_e_seguido_e_a_captura_verifica(tmp_path):
    """QHOS-AUD-196, caracterizacao: `_ler_manifesto` usa is_file()+open() e segue o link (catalogo.py:211-215), ao
    contrario de operacao e supervisao_coletor (O_NOFOLLOW). O catalogo nao declara politica para link; recusar e
    decisao (patch em EVIDENCIAS/QHOS-AUD-196). Se decidido, troca-se para `verificada is False`."""
    relatorio = verificar_captura(_manifesto_por_link(tmp_path))
    assert relatorio["verificada"] is True and relatorio["divergencias"] == []


def test_manifesto_que_e_fifo_nao_trava_e_conta_como_ausente(tmp_path):
    """`is_file()` ja recusa o que nao e arquivo regular (FIFO): nao ha bloqueio na leitura. So o symlink escapa."""
    if not hasattr(os, "mkfifo"):
        pytest.skip("sem mkfifo nesta plataforma")
    pasta = si.gravar(si.gerar(1, 5), tmp_path / "fifo")
    (pasta / "manifest.json").unlink()
    os.mkfifo(pasta / "manifest.json")
    assert verificar_captura(pasta)["divergencias"] == ["manifesto_ausente"]
