"""Correcao 1, lote l5d: `qhos2.eventos` (QHOS-AUD-257: codigos de erro com sufixo lidos por parse de string).

Caracterizacao do contrato de `str(EventoError)` que `leitura.py:153` (`startswith("simbolo_ou_bolsa_diverge:")`) e
`historico.py:150` (`split(":")[0]`) leem hoje: `codigo:indice` para o quadro, `captura_nao_verificada:` mais as
divergencias para a captura, e `"{nome}: {erro}"` nas listas `recusadas` de G3. Nenhuma mudanca de formato e decidida.
"""
import json

import pytest

from qhos2 import eventos as ev
from qhos2 import mercado as m
from qhos2 import rprotocol as p
from qhos2 import sintetico as si
from test_pesquisa import captura

T = 10 ** 18
INDICE = 7
_160 = si._int(p.F_TEMPLATE, p.PROFUNDIDADE_ORDEM)
_116 = si._int(p.F_TEMPLATE, p.RESP_RETRATO_PROFUNDIDADE)
LIVRO_OK = si.quadro_livro([(m.NOVO, "C", 25.0, 1, 1, "o")], T)
CASOS = {
    "tipo_desconhecido": (p.PROFUNDIDADE_ORDEM, si.quadro_livro([(7, "C", 25.0, 1, 1, "o")], T)),
    "lado_desconhecido": (p.PROFUNDIDADE_ORDEM, _160 + si._int(110121, m.NOVO) + si._int(153612, 9) + si._dbl(154405, 25.0)
                          + si._int(154406, 1) + si._int(153613, 1) + si._txt(149238, "o")),
    "preco_fora_da_grade": (p.PROFUNDIDADE_ORDEM, si.quadro_livro([(m.NOVO, "C", 25.1, 1, 1, "o")], T)),
    "template_divergente": (p.ULTIMO_NEGOCIO, LIVRO_OK),
    "quadro_160_desalinhado": (p.PROFUNDIDADE_ORDEM, _160 + si._int(110121, m.NOVO) + si._int(110121, m.NOVO)
                               + si._int(153612, m.COMPRA)),
    "negocio_sem_preco": (p.ULTIMO_NEGOCIO, si._int(p.F_TEMPLATE, p.ULTIMO_NEGOCIO) + si._int(110121, 0)),
    "varint_invalido": (p.PROFUNDIDADE_ORDEM, b"\xff\xff\xff"),                 # MercadoError reembrulhado
    "template_fora_do_mercado": (999, si._int(p.F_TEMPLATE, 999)),
    "retrato_desalinhado": (p.RESP_RETRATO_PROFUNDIDADE, _116 + si._int(153612, m.COMPRA) + si._dbl(154405, 25.0)
                            + si._int(154406, 1) + si._int(154406, 2) + si._int(153613, 1) + si._txt(149238, "a")),
    "retrato_sem_preco": (p.RESP_RETRATO_PROFUNDIDADE, _116 + si._int(153612, m.COMPRA) + si._int(154406, 1)
                          + si._int(153613, 1) + si._txt(149238, "a")),
    "simbolo_ou_bolsa_diverge": (p.PROFUNDIDADE_ORDEM, LIVRO_OK + si._txt(p.F_SYMBOL, "NQZ6")),   # o que leitura.py le
}


@pytest.mark.parametrize("codigo", sorted(CASOS))
def test_codigo_do_quadro_e_codigo_estatico_dois_pontos_indice_uma_vez_so(codigo):
    template, dados = CASOS[codigo]
    with pytest.raises(ev.EventoError) as erro:
        ev.normalizar(INDICE, template, T, dados, 0.25, simbolo="ESZ6", bolsa="CME")
    texto = str(erro.value)
    assert texto == f"{codigo}:{INDICE}"                                       # nunca `codigo:indice:indice`
    assert texto.split(":")[0] == codigo and texto.startswith(codigo + ":")    # historico.py:150 e leitura.py:153


def test_quadro_valido_com_a_identidade_certa_nao_e_recusado():
    assert ev.normalizar(INDICE, p.PROFUNDIDADE_ORDEM, T, LIVRO_OK + si._txt(p.F_SYMBOL, "ESZ6"), 0.25,
                         simbolo="ESZ6", bolsa="CME")[0].tipo == ev.NOVO


def test_codigo_da_captura_nao_verificada_leva_as_divergencias_depois_dos_dois_pontos(tmp_path):
    pasta = captura(tmp_path)
    quadros = pasta / "market_frames.jsonl"
    ultimo = json.loads(quadros.read_bytes().splitlines()[-1])["received_ns"]
    # received_ns realista (1 ns depois do ultimo quadro): um valor absurdo faria o resumo do catalogo varrer cada
    # segundo desde 1970 (QHOS-COR-L5D-005, test_correcao_catalogo.py)
    linha = {"index": 99, "template_id": 1, "received_ns": ultimo + 1, "protobuf_base64": ""}
    with quadros.open("ab") as f:
        f.write(json.dumps(linha).encode() + b"\n")
    with pytest.raises(ev.EventoError) as erro:
        list(ev.eventos_da_captura(pasta))
    texto = str(erro.value)
    assert texto.split(":")[0] == "captura_nao_verificada"
    assert set(texto.split(":", 1)[1].split(",")) >= {"frames_divergem", "bytes_divergem", "sha256_diverge"}


def test_listas_recusadas_de_g3_levam_nome_da_pasta_dois_pontos_espaco_codigo(monkeypatch):
    from qhos2 import custo as cu, desequilibrio as de, poder as po
    monkeypatch.setattr(de, "estados_da_captura",
                        lambda pasta, niveis=de.NIVEIS: (_ for _ in ()).throw(ev.EventoError("tipo_desconhecido:3")))
    assert de.calibrar(["/x/nome-da-pasta"])["recusadas"] == ["nome-da-pasta: tipo_desconhecido:3"]
    assert cu.medir(["/x/nome-da-pasta"])["recusadas"] == ["nome-da-pasta: tipo_desconhecido:3"]
    assert po.contar_sinais_das_capturas(["/x/nome-da-pasta"], -0.5, 0.5)["recusadas"] == ["nome-da-pasta: tipo_desconhecido:3"]
