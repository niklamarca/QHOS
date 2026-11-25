"""Peca 3: o catalogo recalcula tudo, acusa qualquer divergencia e nao toca nas capturas."""
import hashlib
import json

import pytest

from qhos2.catalogo import (CatalogoError, classificar_ambiente, listar_capturas, main,
                            montar_catalogo, verificar_captura)

ESQUEMA = "qhos_rithmic_test_depth_capture_v1"
S = 1_000_000_000
T0 = 1_760_000_000 * S


def _linha(indice, template=160, recebido_ns=T0):
    obj = {"index": indice, "template_id": template, "received_ns": recebido_ns,
           "protobuf_base64": "AAEC"}
    return (json.dumps(obj, sort_keys=True) + "\n").encode()


def _manifesto(linhas, sistema="Rithmic Paper Trading", esquema=ESQUEMA, **extra):
    m = {"schema": esquema, "frames": len(linhas), "bytes": sum(map(len, linhas)),
         "sha256": hashlib.sha256(b"".join(linhas)).hexdigest(),
         "transport_closed_normally": True, "research_only": True, "can_execute": False,
         "status": {"system_name": sistema, "plant": "ticker", "requested_symbol": "ESZ6",
                    "exchange": "CME"}}
    m.update(extra)
    return m


def criar(raiz, nome="c1", quadros=None, **kw):
    """Captura legada de arquivo unico. `quadros` = lista de (template, recebido_ns)."""
    quadros = quadros if quadros is not None else [(160, T0), (160, T0), (161, T0 + S)]
    linhas = [_linha(i, t, ns) for i, (t, ns) in enumerate(quadros)]
    pasta = raiz / nome
    pasta.mkdir(parents=True)
    (pasta / "market_frames.jsonl").write_bytes(b"".join(linhas))
    (pasta / "manifest.json").write_text(json.dumps(_manifesto(linhas, **kw)))
    return pasta


def criar_segmentada(raiz, nome="seg", partes=((0, 2), (2, 5))):
    pasta = raiz / nome
    (pasta / "frames").mkdir(parents=True)
    todas, segmentos = [], []
    for k, (a, b) in enumerate(partes):
        linhas = [_linha(i, 160, T0 + i * S) for i in range(a, b)]
        todas += linhas
        rel = f"frames/{k:05d}.jsonl"
        (pasta / rel).write_bytes(b"".join(linhas))
        segmentos.append({"name": rel, "frames": len(linhas), "bytes": sum(map(len, linhas)),
                          "sha256": hashlib.sha256(b"".join(linhas)).hexdigest()})
    (pasta / "manifest.json").write_text(json.dumps(_manifesto(todas, segments=segmentos)))
    return pasta


def reescrever_manifesto(pasta, **mudancas):
    caminho = pasta / "manifest.json"
    m = json.loads(caminho.read_text())
    m.update(mudancas)
    caminho.write_text(json.dumps(m))


def snapshot(pasta):
    return {str(p.relative_to(pasta)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(pasta.rglob("*")) if p.is_file()}


# ---- captura boa ---------------------------------------------------------------------------

def test_captura_boa_e_verificada(tmp_path):
    r = verificar_captura(criar(tmp_path))
    assert r["verificada"] is True and r["divergencias"] == []
    assert r["quadros"] == 3 and r["indice_continuo"] is True
    assert r["por_template"] == {"160": 2, "161": 1}
    assert r["ambiente"] == "paper" and r["sistema"] == "Rithmic Paper Trading"
    assert (r["simbolo"], r["bolsa"], r["plant"]) == ("ESZ6", "CME", "ticker")
    assert r["fechamento_normal"] is True
    assert r["primeiro_ns"] == T0 and r["ultimo_ns"] == T0 + S and r["duracao_s"] == 1.0


def test_ritmo_por_segundo(tmp_path):
    quadros = [(160, T0)] * 5 + [(161, T0 + S)] * 2 + [(160, T0 + 3 * S)]
    r = verificar_captura(criar(tmp_path, quadros=quadros))
    q = r["quadros_por_segundo"]
    assert q["pico"] == 5 and q["media"] == 2.0  # 8 quadros em 4 s de janela
    assert q["pico_por_template"] == {"160": 5, "161": 2}


def test_relogio_que_volta_nao_derruba_e_e_contado(tmp_path):
    r = verificar_captura(criar(tmp_path, quadros=[(160, T0 + 2 * S), (160, T0), (161, T0 + S)]))
    assert r["verificada"] is True and r["relogio_voltou"] == 1
    assert r["duracao_s"] == 2.0 and r["quadros_por_segundo"]["pico"] == 1
    assert (r["primeiro_ns"], r["ultimo_ns"]) == (T0 + 2 * S, T0 + S)
    assert verificar_captura(criar(tmp_path, nome="igual"))["relogio_voltou"] == 0  # horario repetido nao volta


def test_captura_vazia_e_coerente(tmp_path):
    r = verificar_captura(criar(tmp_path, quadros=[]))
    assert r["verificada"] is True and r["quadros"] == 0
    assert r["quadros_por_segundo"] is None and r["duracao_s"] is None


def test_captura_segmentada_boa(tmp_path):
    r = verificar_captura(criar_segmentada(tmp_path))
    assert r["verificada"] is True and r["quadros"] == 5 and r["indice_continuo"] is True


# ---- ambiente ------------------------------------------------------------------------------

@pytest.mark.parametrize("sistema,esperado", [
    ("Rithmic Paper Trading", "paper"), ("Rithmic Test", "test_sintetico"),
    ("Rithmic 01", "desconhecido"), (None, "desconhecido")])
def test_ambiente_vem_do_sistema_e_nao_do_nome_do_esquema(sistema, esperado):
    assert classificar_ambiente(sistema) == esperado


def test_esquema_com_test_no_nome_continua_paper(tmp_path):
    r = verificar_captura(criar(tmp_path, sistema="Rithmic Paper Trading"))
    assert "test" in r["esquema"] and r["ambiente"] == "paper"


# ---- divergencias --------------------------------------------------------------------------

def test_manifesto_ausente(tmp_path):
    pasta = criar(tmp_path)
    (pasta / "manifest.json").unlink()
    assert verificar_captura(pasta)["divergencias"] == ["manifesto_ausente"]


@pytest.mark.parametrize("conteudo", ["{nao e json", "[1, 2]", "null"])
def test_manifesto_invalido(tmp_path, conteudo):
    pasta = criar(tmp_path)
    (pasta / "manifest.json").write_text(conteudo)
    assert verificar_captura(pasta)["divergencias"] == ["manifesto_invalido"]


@pytest.mark.parametrize("esquema", ["outra_coisa", "", None, 7, "qhos_rithmic_test_depth_capture_v9"])
def test_esquema_desconhecido(tmp_path, esquema):
    pasta = criar(tmp_path)
    reescrever_manifesto(pasta, schema=esquema)
    r = verificar_captura(pasta)
    assert "esquema_desconhecido" in r["divergencias"] and r["verificada"] is False


def test_esquema_de_mercado_tambem_e_aceito(tmp_path):
    assert verificar_captura(criar(tmp_path, esquema="qhos_rithmic_market_capture_v1"))["verificada"]


def test_frames_divergem(tmp_path):
    pasta = criar(tmp_path)
    reescrever_manifesto(pasta, frames=4)
    assert verificar_captura(pasta)["divergencias"] == ["frames_divergem"]


def test_bytes_divergem(tmp_path):
    pasta = criar(tmp_path)
    reescrever_manifesto(pasta, bytes=1)
    assert verificar_captura(pasta)["divergencias"] == ["bytes_divergem"]


def test_sha256_diverge_quando_um_byte_muda_sem_mudar_o_tamanho(tmp_path):
    pasta = criar(tmp_path)
    arq = pasta / "market_frames.jsonl"
    dados = bytearray(arq.read_bytes())
    i = dados.index(b"AAEC")
    dados[i] = ord("B")  # mesmo tamanho, conteudo diferente
    arq.write_bytes(bytes(dados))
    assert verificar_captura(pasta)["divergencias"] == ["sha256_diverge"]


def test_indice_com_buracos(tmp_path):
    pasta = criar(tmp_path, quadros=[(160, T0)] * 3)
    linhas = [_linha(0), _linha(2), _linha(3)]  # falta o 1
    (pasta / "market_frames.jsonl").write_bytes(b"".join(linhas))
    (pasta / "manifest.json").write_text(json.dumps(_manifesto(linhas)))
    r = verificar_captura(pasta)
    assert r["divergencias"] == ["indice_com_buracos"] and r["indice_continuo"] is False


def test_linha_truncada(tmp_path):
    pasta = criar(tmp_path)
    arq = pasta / "market_frames.jsonl"
    arq.write_bytes(arq.read_bytes()[:-10])
    assert verificar_captura(pasta)["divergencias"] == ["linha_truncada"]


@pytest.mark.parametrize("linha", [
    b"isto nao e json\n",
    b'{"index": 0, "template_id": 160}\n',
    b'{"index": true, "template_id": 160, "received_ns": 1}\n',
    b'{"index": 0, "template_id": "160", "received_ns": 1}\n',
    b'{"index": 0, "template_id": 160, "received_ns": 1.5}\n',
    b"[1, 2, 3]\n",
])
def test_linha_invalida(tmp_path, linha):
    pasta = criar(tmp_path)
    (pasta / "market_frames.jsonl").write_bytes(linha)
    reescrever_manifesto(pasta, frames=1, bytes=len(linha))
    assert verificar_captura(pasta)["divergencias"] == ["linha_invalida"]


def test_arquivo_de_quadros_ausente(tmp_path):
    pasta = criar(tmp_path)
    (pasta / "market_frames.jsonl").unlink()
    assert verificar_captura(pasta)["divergencias"] == ["arquivo_ausente"]


def test_limite_de_bytes(tmp_path):
    r = verificar_captura(criar(tmp_path), limite_bytes=50)
    assert r["divergencias"] == ["limite_de_bytes_excedido"] and r["verificada"] is False


def test_linha_grande_demais(tmp_path, monkeypatch):
    monkeypatch.setattr("qhos2.catalogo.LINHA_MAX_BYTES", 20)
    assert verificar_captura(criar(tmp_path))["divergencias"] == ["linha_grande_demais"]


def test_manifesto_grande_demais(tmp_path, monkeypatch):
    monkeypatch.setattr("qhos2.catalogo.MANIFESTO_MAX_BYTES", 10)
    assert verificar_captura(criar(tmp_path))["divergencias"] == ["manifesto_grande_demais"]


# ---- segmentos -----------------------------------------------------------------------------

def test_segmento_com_sha256_errado(tmp_path):
    pasta = criar_segmentada(tmp_path)
    m = json.loads((pasta / "manifest.json").read_text())
    m["segments"][1]["sha256"] = "0" * 64
    (pasta / "manifest.json").write_text(json.dumps(m))
    assert verificar_captura(pasta)["divergencias"] == ["segmento_sha256_diverge:frames/00001.jsonl"]


def test_segmento_com_contagem_errada(tmp_path):
    pasta = criar_segmentada(tmp_path)
    m = json.loads((pasta / "manifest.json").read_text())
    m["segments"][0]["frames"] = 9
    (pasta / "manifest.json").write_text(json.dumps(m))
    assert verificar_captura(pasta)["divergencias"] == ["segmento_diverge:frames/00000.jsonl"]


@pytest.mark.parametrize("nome", ["../fora.jsonl", "/etc/passwd", "frames/../../fora.jsonl"])
def test_segmento_fora_da_captura_e_recusado(tmp_path, nome):
    pasta = criar_segmentada(tmp_path)
    (tmp_path / "fora.jsonl").write_bytes(b"")
    m = json.loads((pasta / "manifest.json").read_text())
    m["segments"][0]["name"] = nome
    (pasta / "manifest.json").write_text(json.dumps(m))
    r = verificar_captura(pasta)
    assert r["verificada"] is False
    assert r["divergencias"][0] in {"segmento_fora_da_captura", "segmentos_invalidos"}


def test_segmento_via_link_simbolico_para_fora_e_recusado(tmp_path):
    pasta = criar_segmentada(tmp_path)
    fora = tmp_path / "fora.jsonl"
    fora.write_bytes((pasta / "frames/00000.jsonl").read_bytes())
    (pasta / "frames/00000.jsonl").unlink()
    (pasta / "frames/00000.jsonl").symlink_to(fora)
    assert verificar_captura(pasta)["divergencias"] == ["segmento_fora_da_captura"]


@pytest.mark.parametrize("segmentos", [[], "x", [1], [{"name": ""}], [{"nome": "a"}]])
def test_segmentos_invalidos(tmp_path, segmentos):
    pasta = criar_segmentada(tmp_path)
    reescrever_manifesto(pasta, segments=segmentos)
    assert verificar_captura(pasta)["divergencias"] == ["segmentos_invalidos"]


def test_segmento_ausente(tmp_path):
    pasta = criar_segmentada(tmp_path)
    (pasta / "frames/00001.jsonl").unlink()
    assert verificar_captura(pasta)["divergencias"] == ["arquivo_ausente"]


# ---- listagem e catalogo -------------------------------------------------------------------

def test_listar_capturas_ignora_o_que_nao_e_captura(tmp_path):
    criar(tmp_path, "b")
    criar(tmp_path, "a")
    (tmp_path / "lixo").mkdir()
    (tmp_path / "arquivo.txt").write_text("x")
    assert [p.name for p in listar_capturas(tmp_path)] == ["a", "b"]


def test_listar_capturas_raiz_inexistente(tmp_path):
    with pytest.raises(CatalogoError):
        listar_capturas(tmp_path / "nao_existe")


def test_catalogo_escreve_arquivo_e_resume(tmp_path):
    raiz = tmp_path / "capturas"
    raiz.mkdir()
    criar(raiz, "boa")
    ruim = criar(raiz, "ruim")
    reescrever_manifesto(ruim, frames=99)
    destino = tmp_path / "catalogo"
    cat = montar_catalogo(raiz, destino, gerado_em="2026-10-23T00:00:00+00:00")
    assert cat["resumo"] == {"total": 2, "verificadas": 1, "quadros": 6, "bytes": cat["resumo"]["bytes"]}
    em_disco = json.loads((destino / "catalogo.json").read_text())
    assert em_disco == json.loads(json.dumps(cat))
    assert em_disco["esquema"] == "qhos2.catalogo.v1"
    assert [c["captura_id"] for c in em_disco["capturas"]] == ["boa", "ruim"]
    assert [p.name for p in destino.iterdir()] == ["catalogo.json"]  # sem temporarios


def test_catalogo_nao_altera_as_capturas(tmp_path):
    raiz = tmp_path / "capturas"
    raiz.mkdir()
    criar(raiz, "a")
    criar_segmentada(raiz, "b")
    antes = snapshot(raiz)
    montar_catalogo(raiz, tmp_path / "catalogo")
    assert snapshot(raiz) == antes


def test_catalogo_e_deterministico(tmp_path):
    raiz = tmp_path / "capturas"
    raiz.mkdir()
    criar(raiz, "a")
    montar_catalogo(raiz, tmp_path / "c1", gerado_em="x")
    montar_catalogo(raiz, tmp_path / "c2", gerado_em="x")
    assert ((tmp_path / "c1/catalogo.json").read_text().replace(str(tmp_path / "c1"), "")
            == (tmp_path / "c2/catalogo.json").read_text().replace(str(tmp_path / "c2"), ""))


@pytest.mark.parametrize("sub", ["", "interno"])
def test_destino_dentro_da_raiz_e_recusado(tmp_path, sub):
    raiz = tmp_path / "capturas"
    raiz.mkdir()
    criar(raiz, "a")
    with pytest.raises(CatalogoError):
        montar_catalogo(raiz, raiz / sub if sub else raiz)
    assert not (raiz / "interno").exists()


def test_catalogo_sobrescreve_o_anterior_de_forma_atomica(tmp_path):
    raiz = tmp_path / "capturas"
    raiz.mkdir()
    criar(raiz, "a")
    destino = tmp_path / "catalogo"
    montar_catalogo(raiz, destino)
    criar(raiz, "b")
    cat = montar_catalogo(raiz, destino)
    assert cat["resumo"]["total"] == 2
    assert json.loads((destino / "catalogo.json").read_text())["resumo"]["total"] == 2
    assert [p.name for p in destino.iterdir()] == ["catalogo.json"]


def test_main_codigo_de_saida(tmp_path, capsys):
    raiz = tmp_path / "capturas"
    raiz.mkdir()
    criar(raiz, "a")
    assert main([str(raiz), str(tmp_path / "cat")]) == 0
    assert "verificada=True" in capsys.readouterr().out
    reescrever_manifesto(raiz / "a", frames=99)
    assert main([str(raiz), str(tmp_path / "cat")]) == 2
