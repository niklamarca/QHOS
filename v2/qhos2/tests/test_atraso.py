"""Peca 3b: o atraso e medido so em captura verificada, sem confundir carimbo com interpretacao."""
import base64
import hashlib
import json
import struct

import pytest

from qhos2.atraso import AtrasoError, ler_campos, main, medir

F = {"template_id": 154467, "ssboe": 150100, "usecs": 150101, "source_ssboe": 150400,
     "source_usecs": 150401, "source_nsecs": 150404, "jop_ssboe": 150600, "jop_nsecs": 150604,
     "campo_110121": 110121}
SSBOE = 1_760_000_000
MS = 1_000_000


def _v(n):
    saida = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        saida.append(b | (0x80 if n else 0))
        if not n:
            return bytes(saida)


def proto(**campos):
    """Mensagem protobuf com campos varint, mais ruido de outros tipos de fio."""
    saida = b""
    for nome, valor in campos.items():
        saida += _v(F[nome] << 3) + _v(valor)
    saida += _v(110100 << 3 | 2) + _v(6) + b"ESZ6__"                # string
    saida += _v(154405 << 3 | 1) + struct.pack("<d", 5000.25)        # double repetido
    saida += _v(154406 << 3 | 0) + _v(3)                             # int32 repetido
    saida += _v(99999 << 3 | 5) + b"\x00\x00\x00\x00"                # fixed32 desconhecido
    return saida


def quadro(template, recebido_ns, **campos):
    return template, recebido_ns, proto(template_id=template, **campos)


def criar(raiz, quadros, nome="c1", sistema="Rithmic Paper Trading"):
    linhas = []
    for i, (template, recebido, bruto) in enumerate(quadros):
        obj = {"index": i, "template_id": template, "received_ns": recebido,
               "protobuf_base64": base64.b64encode(bruto).decode()}
        linhas.append((json.dumps(obj, separators=(",", ":")) + "\n").encode())
    pasta = raiz / nome
    pasta.mkdir(parents=True)
    (pasta / "market_frames.jsonl").write_bytes(b"".join(linhas))
    (pasta / "manifest.json").write_text(json.dumps({
        "schema": "qhos_rithmic_test_depth_capture_v1", "frames": len(linhas),
        "bytes": sum(map(len, linhas)), "sha256": hashlib.sha256(b"".join(linhas)).hexdigest(),
        "transport_closed_normally": True,
        "status": {"system_name": sistema, "requested_symbol": "GCZ6", "exchange": "COMEX"}}))
    return pasta


def com_atraso(template, atraso_ms, *, fonte_ms=None, jop_ms=None, usecs=123456, base_ns=None):
    """Quadro cujo atraso medido contra cada carimbo e exatamente o pedido."""
    carimbo_servidor = SSBOE * 10 ** 9 + usecs * 1000
    recebido = carimbo_servidor + int(atraso_ms * MS) if base_ns is None else base_ns
    campos = {"ssboe": SSBOE, "usecs": usecs}
    if fonte_ms is not None:
        fonte_ns = recebido - int(fonte_ms * MS)
        campos.update(source_ssboe=fonte_ns // 10 ** 9, source_usecs=(fonte_ns % 10 ** 9) // 1000)
    if jop_ms is not None:
        jop_ns = recebido - int(jop_ms * MS)
        campos.update(jop_ssboe=jop_ns // 10 ** 9, jop_nsecs=jop_ns % 10 ** 9)
    return quadro(template, recebido, **campos)


# ---- leitor de protobuf --------------------------------------------------------------------

def test_ler_campos_acha_os_varints_e_pula_o_resto():
    dados = proto(template_id=160, ssboe=SSBOE, usecs=999999, source_ssboe=SSBOE - 1, jop_nsecs=7)
    assert ler_campos(dados) == {"template_id": 160, "ssboe": SSBOE, "usecs": 999999,
                                 "source_ssboe": SSBOE - 1, "jop_nsecs": 7}


def test_ler_campos_ultimo_valor_vence():
    dados = _v(F["ssboe"] << 3) + _v(1) + _v(F["ssboe"] << 3) + _v(2)
    assert ler_campos(dados) == {"ssboe": 2}


@pytest.mark.parametrize("dados", [
    _v(F["ssboe"] << 3),                      # varint sem valor
    b"\x80",                                   # varint sem fim
    _v(5 << 3 | 2) + _v(10) + b"abc",          # comprimento maior que o resto
    _v(5 << 3 | 1) + b"\x00\x00",              # fixed64 curto
    _v(5 << 3 | 3),                            # grupo: nao suportado
])
def test_ler_campos_recusa_lixo(dados):
    with pytest.raises(AtrasoError):
        ler_campos(dados)


# ---- medicao -------------------------------------------------------------------------------

def test_atraso_exato_por_carimbo(tmp_path):
    quadros = [com_atraso(160, ms, fonte_ms=ms + 2, jop_ms=ms + 1) for ms in range(1, 101)]
    r = medir(criar(tmp_path, quadros))
    t = r["por_template"]["160"]
    assert (t["servidor"]["n"], t["servidor"]["min_ms"], t["servidor"]["max_ms"]) == (100, 1.0, 100.0)
    assert (t["servidor"]["p50_ms"], t["servidor"]["p99_ms"]) == (51.0, 100.0)
    assert (t["fonte"]["min_ms"], t["fonte"]["max_ms"]) == (3.0, 102.0)
    assert (t["jop"]["min_ms"], t["jop"]["max_ms"]) == (2.0, 101.0)
    assert t["jop_ambiguo"] is False and r["alertas"] == []
    assert (r["ambiente"], r["bolsa"], r["simbolo"]) == ("paper", "COMEX", "GCZ6")


def test_fonte_ignora_nsecs(tmp_path):
    q = com_atraso(160, 5, fonte_ms=4)
    campos = ler_campos(q[2])
    bruto = proto(template_id=160, ssboe=campos["ssboe"], usecs=campos["usecs"],
                  source_ssboe=campos["source_ssboe"], source_usecs=campos["source_usecs"],
                  source_nsecs=987_654_321)
    r = medir(criar(tmp_path, [(160, q[1], bruto)]))
    assert r["por_template"]["160"]["fonte"]["p50_ms"] == 4.0


def test_jop_com_nsecs_pequeno_e_ambiguo_e_ignorado(tmp_path):
    recebido = SSBOE * 10 ** 9 + 5 * MS
    quadros = [quadro(160, recebido, ssboe=SSBOE, jop_ssboe=SSBOE, jop_nsecs=n) for n in (1, 2, 999)]
    t = medir(criar(tmp_path, quadros))["por_template"]["160"]
    assert t["jop"] is None and t["jop_ambiguo"] is True


def test_templates_separados(tmp_path):
    quadros = [com_atraso(160, 10), com_atraso(160, 20), com_atraso(161, 5)]
    r = medir(criar(tmp_path, quadros))
    assert r["por_template"]["160"]["quadros"] == 2 and r["por_template"]["161"]["quadros"] == 1
    assert r["por_template"]["161"]["servidor"]["p50_ms"] == 5.0


def test_quadro_sem_carimbo_e_contado(tmp_path):
    quadros = [com_atraso(160, 10), quadro(116, SSBOE * 10 ** 9)]
    r = medir(criar(tmp_path, quadros))
    assert r["quadros_sem_carimbo"] == 1 and r["por_template"]["116"]["servidor"] is None


def test_protobuf_invalido_e_contado_e_nao_derruba(tmp_path):
    quadros = [com_atraso(160, 10), (160, SSBOE * 10 ** 9, b"\x80")]
    r = medir(criar(tmp_path, quadros))
    assert r["quadros_protobuf_invalido"] == 1 and r["por_template"]["160"]["servidor"]["n"] == 1


def test_atraso_negativo_gera_alerta(tmp_path):
    r = medir(criar(tmp_path, [com_atraso(160, -3), com_atraso(160, 4)]))
    assert r["por_template"]["160"]["servidor"]["negativos"] == 1
    assert any(a.startswith("atrasos_negativos") for a in r["alertas"])


def test_negocio_retrato_fica_fora_do_atraso_e_nao_gera_alerta(tmp_path):
    velho = quadro(150, SSBOE * 10 ** 9 + 1 * MS, ssboe=SSBOE - 3085, campo_110121=1)  # negocio de 51 min antes
    ao_vivo = quadro(150, SSBOE * 10 ** 9 + 70 * MS, ssboe=SSBOE, campo_110121=0)
    r = medir(criar(tmp_path, [velho, ao_vivo]))
    t = r["por_template"]["150"]
    assert t["quadros"] == 2 and t["servidor"]["n"] == 1 and t["servidor"]["p50_ms"] == 70.0
    assert r["quadros_retrato_negocio"] == 1 and r["alertas"] == []


def test_campo_110121_no_livro_e_update_type_e_e_medido(tmp_path):
    r = medir(criar(tmp_path, [quadro(160, SSBOE * 10 ** 9 + 70 * MS, ssboe=SSBOE, campo_110121=1)]))
    assert r["por_template"]["160"]["servidor"]["n"] == 1 and r["quadros_retrato_negocio"] == 0


def test_relogio_desalinhado_gera_alerta(tmp_path):
    r = medir(criar(tmp_path, [com_atraso(160, 10_000), com_atraso(160, 10_001)]))
    assert any(a.startswith("mediana_acima_de_1s") for a in r["alertas"])


def test_captura_nao_verificada_e_recusada(tmp_path):
    pasta = criar(tmp_path, [com_atraso(160, 5)])
    m = json.loads((pasta / "manifest.json").read_text())
    m["frames"] = 9
    (pasta / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(AtrasoError, match="captura_nao_verificada:frames_divergem"):
        medir(pasta)


def test_main_imprime_e_devolve_zero(tmp_path, capsys):
    pasta = criar(tmp_path, [com_atraso(160, ms, fonte_ms=ms + 1) for ms in (4, 5, 6)])
    assert main([str(pasta)]) == 0
    saida = capsys.readouterr().out
    assert "template  160 servidor" in saida and "ambiente=paper COMEX/GCZ6" in saida
    assert "retrato_negocio_ignorado=0" in saida


def test_main_recusa_com_codigo_2(tmp_path, capsys):
    pasta = criar(tmp_path, [com_atraso(160, 5)])
    (pasta / "manifest.json").unlink()
    assert main([str(pasta)]) == 2
    assert "recusada: c1: captura_nao_verificada:manifesto_ausente" in capsys.readouterr().out


def test_main_varias_capturas_uma_recusada_nao_impede_as_outras(tmp_path, capsys):
    boa = criar(tmp_path, [com_atraso(160, 5)], nome="boa")
    ruim = criar(tmp_path, [com_atraso(160, 5)], nome="ruim")
    (ruim / "manifest.json").unlink()
    (tmp_path / "solto.txt").write_text("x")
    assert main([str(ruim), str(boa), str(tmp_path / "solto.txt")]) == 2
    saida = capsys.readouterr().out
    assert "recusada: ruim:" in saida and "captura=boa" in saida and "solto" not in saida
