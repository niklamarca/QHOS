"""Peca 4c: o gravador escreve capturas que o catalogo e a medicao de atraso aceitam, e nunca deixa passar meia captura."""
import json
import os
import shutil
import stat
from collections import namedtuple

import pytest

from qhos2 import atraso, catalogo
from qhos2 import gravador as g
from qhos2.gravador import Gravador, GravadorError

IDENT = {"system_name": "Rithmic Paper Trading", "requested_symbol": "GCZ6", "exchange": "COMEX"}
SSBOE = 1_760_000_000


def _v(n):
    saida = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        saida.append(b | (0x80 if n else 0))
        if not n:
            return bytes(saida)


def quadro(i, template=160, atraso_ms=70):
    """Quadro com carimbo `ssboe`; devolve (bytes, recebido_ns) com o atraso pedido."""
    dados = _v(154467 << 3) + _v(template) + _v(150100 << 3) + _v(SSBOE) + _v(150101 << 3) + _v(i)
    return dados, SSBOE * 10 ** 9 + i * 1000 + atraso_ms * 1_000_000


def um(gravador, i, template=160):
    dados, ns = quadro(i, template=template)
    gravador(dados, template, ns)


def gravar(pasta, quantos=5, **kw):
    gravador = Gravador(pasta, identidade=IDENT, **kw)
    for i in range(quantos):
        dados, ns = quadro(i, template=(116, 160, 161)[i % 3])
        gravador(dados, (116, 160, 161)[i % 3], ns)
    return gravador


STATUS = {"estado": "FECHADA", "logout_confirmado": True}


def test_fsync_periodico_a_cada_1000_quadros(tmp_path, monkeypatch):
    # a descarga periodica e observada, nao so lida no codigo (QHOS-AUD-120): fsync do arquivo no 1000o e no 2000o
    # quadro, nenhum antes nem entre eles; no fechamento, mais o arquivo e a pasta
    assert g.SINCRONIZAR_A_CADA == 1000
    gravador = Gravador(tmp_path, identidade=IDENT)
    bruto = (gravador.pasta / "market_frames.jsonl").stat().st_ino
    sincronizados, fsync = [], os.fsync

    def espiao(fd):
        sincronizados.append((os.fstat(fd).st_ino == bruto, gravador.quadros))   # (e o arquivo bruto?, quadros ja gravados)
        fsync(fd)
    monkeypatch.setattr(g.os, "fsync", espiao)
    total = 2 * g.SINCRONIZAR_A_CADA + 1
    for i in range(total):
        um(gravador, i)
    assert sincronizados == [(True, g.SINCRONIZAR_A_CADA - 1), (True, 2 * g.SINCRONIZAR_A_CADA - 1)]
    gravador.fechar(STATUS, fechamento_normal=True)
    assert (True, total) in sincronizados[2:] and all(q == total for _, q in sincronizados[2:])   # fechar: bruto e pasta
    assert catalogo.verificar_captura(gravador.pasta)["quadros"] == total


def test_captura_gravada_e_verificada_pelo_catalogo(tmp_path):
    gravador = gravar(tmp_path)
    manifesto = gravador.fechar(STATUS, fechamento_normal=True)
    assert manifesto == gravador.pasta / "manifest.json"
    r = catalogo.verificar_captura(gravador.pasta)
    assert r["verificada"] is True and r["divergencias"] == []
    assert (r["quadros"], r["ambiente"], r["simbolo"], r["bolsa"]) == (5, "paper", "GCZ6", "COMEX")
    assert r["esquema"] == g.ESQUEMA and r["fechamento_normal"] is True
    assert r["por_template"] == {"116": 2, "160": 2, "161": 1}


def test_atraso_e_medido_na_captura_gravada(tmp_path):
    gravador = Gravador(tmp_path, identidade=IDENT)
    for i in range(20):
        dados, ns = quadro(i, atraso_ms=70)
        gravador(dados, 160, ns)
    gravador.fechar(STATUS, fechamento_normal=True)
    medida = atraso.medir(gravador.pasta)["por_template"]["160"]["servidor"]
    assert medida["n"] == 20 and 69.9 < medida["p50_ms"] < 70.1


def test_manifesto_so_existe_depois_de_fechar(tmp_path):
    gravador = gravar(tmp_path)
    assert catalogo.verificar_captura(gravador.pasta)["divergencias"] == ["manifesto_ausente"]
    gravador.fechar(STATUS, fechamento_normal=True)
    assert catalogo.verificar_captura(gravador.pasta)["verificada"] is True


def test_fechar_duas_vezes_nao_muda_nada(tmp_path):
    gravador = gravar(tmp_path)
    primeiro = gravador.fechar(STATUS, fechamento_normal=True)
    texto = primeiro.read_text()
    assert gravador.fechar({"estado": "OUTRO"}, fechamento_normal=False) == primeiro
    assert primeiro.read_text() == texto


def test_depois_de_fechado_nao_aceita_quadros(tmp_path):
    gravador = gravar(tmp_path)
    gravador.fechar(STATUS, fechamento_normal=True)
    dados, ns = quadro(9)
    with pytest.raises(GravadorError, match="gravador_fechado"):
        gravador(dados, 160, ns)


@pytest.mark.parametrize("template", [19, 11, 75, 0])
def test_so_grava_templates_de_mercado(tmp_path, template):
    gravador = Gravador(tmp_path, identidade=IDENT)
    dados, ns = quadro(1)
    with pytest.raises(GravadorError, match="template_de_mercado_esperado"):
        gravador(dados, template, ns)
    assert gravador.quadros == 0


def test_limite_de_bytes_para_a_gravacao_e_a_captura_parcial_continua_valida(tmp_path):
    medidor = Gravador(tmp_path / "medidor", identidade=IDENT)
    um(medidor, 0)
    tamanho_linha = medidor.bytes
    gravador = Gravador(tmp_path / "limitado", identidade=IDENT, max_bytes=tamanho_linha + 5)
    um(gravador, 0)
    with pytest.raises(GravadorError, match="limite_de_bytes"):
        um(gravador, 1)
    gravador.fechar(STATUS, fechamento_normal=False)
    r = catalogo.verificar_captura(gravador.pasta)
    assert r["verificada"] is True and r["quadros"] == 1 and r["fechamento_normal"] is False


def test_disco_quase_cheio_no_inicio_nao_cria_pasta(tmp_path, monkeypatch):
    Uso = namedtuple("Uso", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _: Uso(100, 100, 1))
    with pytest.raises(GravadorError, match="disco_quase_cheio"):
        Gravador(tmp_path / "capturas", identidade=IDENT)
    assert list((tmp_path / "capturas").iterdir()) == []


def test_disco_quase_cheio_no_meio_da_gravacao(tmp_path, monkeypatch):
    Uso = namedtuple("Uso", "total used free")
    monkeypatch.setattr(g, "VERIFICAR_DISCO_A_CADA", 2)
    gravador = Gravador(tmp_path, identidade=IDENT)
    for i in range(2):
        um(gravador, i)
    monkeypatch.setattr(shutil, "disk_usage", lambda _: Uso(100, 100, 1))
    with pytest.raises(GravadorError, match="disco_quase_cheio"):
        um(gravador, 2)
    assert gravador.quadros == 2


def test_nunca_sobrescreve_uma_captura(tmp_path):
    Gravador(tmp_path, identidade=IDENT, captura_id="abc123")
    with pytest.raises(FileExistsError):
        Gravador(tmp_path, identidade=IDENT, captura_id="abc123")


@pytest.mark.parametrize("captura_id", ["../fora", "a/b", "", "x" * 65, "a b", "..", "a\x00b"])
def test_captura_id_invalido(tmp_path, captura_id):
    with pytest.raises(GravadorError, match="captura_id_invalido"):
        Gravador(tmp_path, identidade=IDENT, captura_id=captura_id)
    assert [p for p in tmp_path.iterdir()] == []


@pytest.mark.parametrize("faltando", ["system_name", "requested_symbol", "exchange"])
def test_identidade_incompleta(tmp_path, faltando):
    ident = {k: v for k, v in IDENT.items() if k != faltando}
    with pytest.raises(GravadorError, match="identidade_incompleta"):
        Gravador(tmp_path, identidade=ident)


def test_pasta_so_para_o_dono(tmp_path):
    gravador = Gravador(tmp_path, identidade=IDENT)
    assert stat.S_IMODE(gravador.pasta.stat().st_mode) == 0o700


def test_linha_no_mesmo_formato_do_v1(tmp_path):
    gravador = gravar(tmp_path, quantos=1)
    gravador.fechar(STATUS, fechamento_normal=True)
    linha = (gravador.pasta / g.ARQUIVO).read_text().splitlines()[0]
    assert list(json.loads(linha)) == ["index", "template_id", "received_ns", "protobuf_base64"]
    assert " " not in linha.split('"protobuf_base64"')[0]  # separadores compactos, como no V1


def test_manifesto_traz_o_status_da_sessao_e_marcas_de_seguranca(tmp_path):
    gravador = gravar(tmp_path)
    gravador.fechar({"estado": "FECHADA", "quadros": {"160": 2}}, fechamento_normal=True)
    m = json.loads((gravador.pasta / "manifest.json").read_text())
    assert m["research_only"] is True and m["can_execute"] is False and m["transport_closed_normally"] is True
    assert m["status"]["sessao"] == {"estado": "FECHADA", "quadros": {"160": 2}}
    assert m["status"]["plant"] == "TICKER_PLANT" and m["status"]["system_name"] == "Rithmic Paper Trading"
    assert m["schema"] == "qhos_rithmic_depth_capture_v1"
