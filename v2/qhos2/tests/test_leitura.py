"""Integracao, etapa 3: a leitura ao vivo monta o mesmo livro do replay, so le e para em qualquer duvida."""
import ast
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import pytest

from qhos2 import leitura as le
from qhos2 import livro as lv
from qhos2 import sintetico as si
from qhos2.gravador import Gravador

IDENTIDADE = {"system_name": si.SISTEMA, "requested_symbol": "SINTETICO", "exchange": "QHOS2"}
STATUS = {"estado": "FECHADA", "referencia": {"min_qprice_change": 0.25}}


def leitor(pasta, **extra):
    return le.Leitor(pasta, **{"tick": 0.25, "simbolo": "SINTETICO", "bolsa": "QHOS2", **extra})


def ao_vivo(tmp_path, semente=7, passos=400, max_bytes=700):
    """Grava o cenario quadro a quadro enquanto le em blocos pequenos (linhas cortadas no meio)."""
    cenario = si.gerar(semente, passos)
    gravador = Gravador(tmp_path / "capturas", identidade=IDENTIDADE, captura_id="viva")
    leit = leitor(gravador.pasta)
    lidos = []
    for i, (template, dados) in enumerate(cenario.quadros):
        gravador(dados, template, si.INICIO_NS + i * si.PASSO_NS)
        if i % 3 == 0:
            lidos += leit.avancar(max_bytes)
            assert leit.estado == le.LENDO
    gravador.fechar(STATUS, fechamento_normal=True)
    for _ in range(10 ** 4):
        if leit.estado != le.LENDO:
            break
        lidos += leit.avancar(max_bytes)
    return cenario, gravador.pasta, leit, lidos


def test_ao_vivo_monta_o_mesmo_livro_do_replay(tmp_path):
    cenario, pasta, leit, lidos = ao_vivo(tmp_path)
    assert leit.estado == le.ENCERRADA and leit.codigo is None
    assert [q[0] for q in lidos] == list(range(len(cenario.quadros)))
    replay = lv.livro_da_captura(pasta)
    assert leit.livro.assinatura() == replay.assinatura()
    assert leit.livro.resumo() == replay.resumo()
    assert leit.livro.canonico() == cenario.livro_final()
    assert leit.livro_vale and leit.resumo()["pendente_bytes"] == 0


def test_arquivo_ainda_inexistente_aguarda(tmp_path):
    leit = leitor(tmp_path / "ainda_nao")
    assert leit.avancar() == [] and leit.estado == le.AGUARDANDO and not leit.livro_vale


def test_linha_incompleta_espera_a_proxima_volta(tmp_path):
    pasta = si.gravar(si.gerar(1, 3), tmp_path / "origem")
    linhas = (pasta / "market_frames.jsonl").read_bytes().splitlines(keepends=True)
    viva = tmp_path / "viva"
    viva.mkdir()
    with open(viva / "market_frames.jsonl", "wb") as f:
        f.write(linhas[0] + linhas[1][:10])
        f.flush()
        leit = leitor(viva)
        assert [q[0] for q in leit.avancar()] == [0]
        assert leit.avancar() == [] and leit.estado == le.LENDO
        f.write(linhas[1][10:])
        f.flush()
        assert [q[0] for q in leit.avancar()] == [1]


def _copiar_e_estragar(tmp_path, estragar):
    pasta = si.gravar(si.gerar(2, 20), tmp_path / "origem")
    estragar(pasta)
    leit = leitor(pasta)
    for _ in range(100):
        if leit.estado in (le.ENCERRADA, le.FALHA):
            break
        leit.avancar()
    return leit


def _reescrever(pasta, mudar):
    arquivo = pasta / "market_frames.jsonl"
    linhas = arquivo.read_bytes().splitlines(keepends=True)
    arquivo.write_bytes(b"".join(mudar(linhas)))


def _manifesto(pasta, mudar):
    caminho = pasta / "manifest.json"
    manifesto = json.loads(caminho.read_text())
    mudar(manifesto)
    caminho.write_text(json.dumps(manifesto))


@pytest.mark.parametrize("estragar, codigo", [
    (lambda p: _reescrever(p, lambda ls: ls[:3] + ls[4:]), "indice_com_buracos"),
    (lambda p: _reescrever(p, lambda ls: ls[:3] + [b"{nao e json\n"] + ls[4:]), "linha_invalida"),
    (lambda p: _reescrever(p, lambda ls: ls[:-1] + [ls[-1][:-1]]), "linha_truncada"),
    (lambda p: _manifesto(p, lambda m: m.update(sha256="0" * 64)), "manifesto_diverge_sha256"),
    (lambda p: _manifesto(p, lambda m: m.update(frames=m["frames"] + 1)), "manifesto_diverge_contagem"),
    (lambda p: _manifesto(p, lambda m: m["status"]["sessao"]["referencia"].update(min_qprice_change=0.5)),
     "tick_diverge"),
    (lambda p: _manifesto(p, lambda m: m["status"].update(requested_symbol="ESH7")), "simbolo_ou_bolsa_diverge"),
    (lambda p: _manifesto(p, lambda m: m.update(segments=[])), "segmentos_nao_suportados"),
])
def test_qualquer_duvida_para_a_leitura_com_codigo(tmp_path, estragar, codigo):
    leit = _copiar_e_estragar(tmp_path, estragar)
    assert (leit.estado, leit.codigo) == (le.FALHA, codigo)
    assert not leit.livro_vale and leit.avancar() == []


def test_captura_intacta_termina_conferida(tmp_path):
    leit = _copiar_e_estragar(tmp_path, lambda p: None)
    assert (leit.estado, leit.codigo) == (le.ENCERRADA, None)


def test_quadro_que_nao_vira_evento_para_a_leitura(tmp_path):
    leit = leitor(si.gravar(si.gerar(2, 20), tmp_path), tick=0.3)   # tick errado: preco fora da grade
    leit.avancar()
    assert (leit.estado, leit.codigo.split(":")[0]) == (le.FALHA, "preco_fora_da_grade")


@pytest.mark.parametrize("simbolo, bolsa, estado", [("ESZ6", "CME", le.LENDO), ("ESH7", "CME", le.FALHA),
                                                    ("ESZ6", "CBOT", le.FALHA)])
def test_simbolo_e_bolsa_dos_quadros_reais_sao_conferidos(tmp_path, monkeypatch, simbolo, bolsa, estado):
    from test_mercado import D116, D116_FIM, D160, D161
    from test_negocios import LT
    monkeypatch.setattr(le, "CONFERIR_SIMBOLO_A_CADA", 1)
    gravador = Gravador(tmp_path, identidade={**IDENTIDADE, "requested_symbol": "ESZ6", "exchange": "CME"})
    for i, (template, dados) in enumerate([(116, D116), (116, D116_FIM), (160, D160), (161, D161), (150, LT)]):
        gravador(dados, template, i)
    leit = leitor(gravador.pasta, simbolo=simbolo, bolsa=bolsa)
    lidos = leit.avancar()
    assert leit.estado == estado
    assert len(lidos) == (5 if estado == le.LENDO else 0)
    assert leit.codigo == (None if estado == le.LENDO else "simbolo_ou_bolsa_diverge")


def test_arquivo_trocado_ou_que_encolheu_para_a_leitura(tmp_path):
    pasta = si.gravar(si.gerar(3, 20), tmp_path / "a")
    (pasta / "manifest.json").unlink()
    arquivo = pasta / "market_frames.jsonl"
    original = arquivo.read_bytes()
    leit = leitor(pasta)
    leit.avancar()
    arquivo.unlink()
    arquivo.write_bytes(original)
    leit.avancar()
    assert (leit.estado, leit.codigo) == (le.FALHA, "arquivo_trocado")
    # Um segundo cenario independente; o primeiro header continua vinculado ao
    # inode removido, e corretamente recusaria reabrir esse arquivo substituto.
    pasta = si.gravar(si.gerar(3, 20), tmp_path / "b")
    (pasta / "manifest.json").unlink()
    arquivo = pasta / "market_frames.jsonl"
    leit = leitor(pasta)
    leit.avancar()
    with open(arquivo, "r+b") as f:
        f.truncate(len(original) // 2)
    leit.avancar()
    assert (leit.estado, leit.codigo) == (le.FALHA, "arquivo_encolheu")


def test_resumo_nao_traz_preco_quantidade_nem_id(tmp_path):
    _, _, leit, _ = ao_vivo(tmp_path, passos=50)
    texto = json.dumps(leit.resumo())
    assert "27400" not in texto and "6850" not in texto and not re.search(r'"O\d', texto)


def test_atraso_e_novidade_vem_do_relogio(tmp_path):
    pasta = si.gravar(si.gerar(1, 2), tmp_path)
    leit = leitor(pasta, relogio=lambda: si.INICIO_NS + 10 ** 10)
    leit.avancar()
    r = leit.resumo()
    assert r["sem_novidade_s"] == 0.0 and r["atraso_s"] == pytest.approx(10 - (r["quadros"] - 1) * 0.001)


@pytest.mark.parametrize("extra", [{"tick": 0.0}, {"tick": 1}, {"tick": float("inf")}, {"simbolo": ""},
                                   {"bolsa": None}])
def test_parametros_invalidos(tmp_path, extra):
    with pytest.raises(le.LeituraError):
        leitor(tmp_path, **extra)


def test_captura_em_andamento_so_aceita_uma(tmp_path):
    raiz = tmp_path / "capturas"
    pronta = si.gravar(si.gerar(1, 2), raiz, captura_id="pronta")
    with pytest.raises(le.LeituraError, match="nenhuma_captura_em_andamento"):
        le.captura_em_andamento(raiz)
    viva = Gravador(raiz, identidade=IDENTIDADE, captura_id="viva")
    viva(si.gerar(1, 0).quadros[0][1], si.gerar(1, 0).quadros[0][0], 1)
    assert le.captura_em_andamento(raiz) == viva.pasta
    morta = Gravador(raiz, identidade=IDENTIDADE, captura_id="morta")   # caiu sem manifesto, ha muito tempo
    antigo = (morta.pasta / "market_frames.jsonl").stat().st_mtime - 3600
    os.utime(morta.pasta / "market_frames.jsonl", (antigo, antigo))
    assert le.captura_em_andamento(raiz) == viva.pasta
    Gravador(raiz, identidade=IDENTIDADE, captura_id="outra")
    with pytest.raises(le.LeituraError, match="mais_de_uma_captura_em_andamento"):
        le.captura_em_andamento(raiz)
    assert pronta.is_dir()


def _arvore(pasta):
    """(nome, modo, tamanho, mtime, sha256) de tudo na pasta: muda se algo for escrito, criado, renomeado ou alterado."""
    saida = []
    for caminho in sorted(pasta.rglob("*")) + [pasta]:
        info = caminho.lstat()
        corpo = hashlib.sha256(caminho.read_bytes()).hexdigest() if caminho.is_file() else ""
        saida.append((caminho.relative_to(pasta).as_posix(), info.st_mode, info.st_size, info.st_mtime_ns, corpo))
    return saida


def test_leitura_nao_toca_na_sessao_nem_escreve(tmp_path):
    fonte = Path(le.__file__).read_text(encoding="utf-8")
    importados = set()
    for no in ast.walk(ast.parse(fonte)):
        if isinstance(no, ast.Import):
            importados |= {a.name for a in no.names}
        elif isinstance(no, ast.ImportFrom):
            importados.add(no.module)
            importados |= {f"{no.module}.{a.name}" for a in no.names}
    proibidos = {"qhos2.sessao", "qhos2.conector", "qhos2.captura", "qhos2.gravador", "websockets", "socket"}
    assert not {i for i in importados for p in proibidos if i == p or i.startswith(p + ".") or
                i.endswith("." + p.split(".")[-1])}
    # "so le" e comportamento, nao forma do codigo (QHOS-AUD-073): a leitura inteira de uma captura termina ENCERRADA
    # com a arvore inteira do teste so de leitura, e nada nela muda (byte, nome ou modo) -- nem na pasta da captura nem
    # nas pastas acima dela ate tmp_path (onde ficariam capturas vizinhas, cache e derivados).
    # Fora do Linux nem o Gravador nem qhos2.sintetico.gravar gravam a identidade por inode (os.O_NOFOLLOW), e o
    # Leitor real tambem a exige: so a checagem de importacoes acima roda la; a prova de comportamento fica no Linux
    if sys.platform != "linux":
        pytest.skip("Gravador exige identidade por inode (Linux); coberto no Linux")
    pasta = si.gravar(si.gerar(3, 60), tmp_path / "capturas")
    modos = {c: c.lstat().st_mode for c in (tmp_path, *tmp_path.rglob("*"))}
    for caminho in sorted(modos, key=lambda c: len(c.parts), reverse=True):        # de baixo para cima
        os.chmod(caminho, 0o500 if caminho.is_dir() else 0o400)
    try:
        antes = _arvore(tmp_path)
        leit = leitor(pasta)
        for _ in range(10 ** 4):
            if leit.estado not in (le.AGUARDANDO, le.LENDO):
                break
            leit.avancar(700)
        assert (leit.estado, leit.codigo) == (le.ENCERRADA, None) and leit.livro_vale
        assert _arvore(tmp_path) == antes
    finally:
        for caminho, modo in sorted(modos.items(), key=lambda item: len(item[0].parts)):   # de cima para baixo
            os.chmod(caminho, modo)
