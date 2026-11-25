"""Correcao 1, lote l5d: `qhos2.catalogo` (QHOS-COR-L5D-005, achado novo da revisao do lote).

`_Totais.resumo()` (catalogo.py:194-206) monta `sorted(self.por_segundo.get(s, 0) for s in range(primeiro, ultimo + 1))`:
uma lista com um elemento por segundo entre o menor e o maior `received_ns`, sem limite. `verificar_captura` chama o
resumo mesmo com divergencias (catalogo.py:105). Uma unica linha com `received_ns` absurdo (por exemplo 1, isto e
1970) leva a ~1,8e9 elementos e ~14 GB de RSS (medido pela revisao). CARACTERIZACAO BARATA: o intervalo usado aqui e
de 1 000 000 s (~10 MB); o teste prova que a media por segundo e calculada sobre o intervalo inteiro em segundos,
nao sobre os quadros, e que isso acontece com a captura ja divergente. Nada aqui aloca gigabytes.
"""
import json

from qhos2 import catalogo as ca
from test_pesquisa import captura

INTERVALO_S = 1_000_000


def test_caracterizacao_resumo_varre_cada_segundo_do_intervalo_mesmo_com_divergencia(tmp_path):
    pasta = captura(tmp_path)
    quadros = pasta / "market_frames.jsonl"
    linhas = quadros.read_bytes().splitlines()
    primeiro = json.loads(linhas[0])["received_ns"]
    linha = {"index": len(linhas), "template_id": 160, "received_ns": primeiro + INTERVALO_S * 10 ** 9,
             "protobuf_base64": ""}
    with quadros.open("ab") as f:
        f.write(json.dumps(linha).encode() + b"\n")
    r = ca.verificar_captura(pasta)
    assert r["verificada"] is False and "frames_divergem" in r["divergencias"]     # ja divergente...
    assert r["quadros"] == len(linhas) + 1 and r["duracao_s"] == INTERVALO_S         # ...e o resumo rodou mesmo assim
    segundos = INTERVALO_S + 1                                                       # range(primeiro, ultimo + 1)
    assert r["quadros_por_segundo"]["media"] == round((len(linhas) + 1) / segundos, 2)
    assert r["quadros_por_segundo"]["p99"] == 0                                      # 99 % dos segundos estao vazios


def test_leitura_estatica_o_resumo_nao_tem_limite_de_intervalo():
    import inspect
    fonte = inspect.getsource(ca._Totais.resumo)
    assert "range(primeiro, ultimo + 1)" in fonte
    assert "CAPTURA_MAX" not in fonte and "SEGUNDOS_MAX" not in fonte               # nenhum teto sobre o intervalo
    chamada = inspect.getsource(ca.verificar_captura)
    assert chamada.index("relatorio.update(total.resumo())") > chamada.index('div.append("indice_com_buracos")')
