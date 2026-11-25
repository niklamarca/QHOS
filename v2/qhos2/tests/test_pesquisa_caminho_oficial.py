"""Caminho oficial `registrar_plano -> executar_registrado -> avaliar` sobre capturas em disco (QHOS-AUD-229), golden do
relatorio (QHOS-AUD-237), identidade do codigo em arvore minima (QHOS-AUD-238) e a CLI de `pesquisa` (QHOS-AUD-239).

Corpus: capturas SINTETICAS gravadas pelo `Gravador` em `tmp_path`, com quadros 116/160 reais no formato da Rithmic e
identidade "QHOS2 Sintetico" (ESZ6/CME). Nenhuma captura real e lida, nenhum registro oficial e tocado e nenhum teste
do orcamento (regra 14) e consumido: o registro vive numa raiz temporaria. O que o teste prova e a MECANICA do caminho
oficial (leitura, livro, estados, simulacao, decisao, registro em cadeia), nao a hipotese.
"""
import hashlib
import json
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

from qhos2 import avaliador as av
from qhos2 import desequilibrio as de
from qhos2 import mercado as m
from qhos2 import pesquisa as pe
from qhos2 import sintetico as si
from qhos2.gravador import Gravador
from test_pesquisa import ANTES, DEPOIS, captura_com_rotulos, proposta

NY = ZoneInfo("America/New_York")
S = 10 ** 9
TICK = 0.25
BASE = 27400                                   # 6850,00 em ticks de 0,25
IDENTIDADE = {"system_name": "QHOS2 Sintetico", "requested_symbol": "ESZ6", "exchange": "CME"}
DIAS_TESTE = [date(2026, 11, d) for d in (9, 10, 11, 12, 13, 16, 17)]
STATUS_FECHADA = {"estado": "FECHADA", "retrato_completo": True, "erro": None, "referencia": {"min_qprice_change": TICK}}
PAR = {"p5": -0.5, "p95": 0.5, "taxa_usd": 5, "alfa": pe.ALFA_POR_TESTE}


def ns(dia, hora, extra_ns=0):
    return int(datetime.combine(dia, hora, NY).timestamp()) * S + extra_ns


def pts(ticks):
    return ticks * TICK


def captura_esz6(raiz, dia, *, sinais=((time(10, 0, 1), 3),), passo_s=30, inicio=time(9, 20), fim=time(16, 5)):
    """Um pregao sintetico de ESZ6 gravado em disco: retrato de 3 niveis por lado (10 contratos cada) em `inicio`, um
    quadro 160 a cada `passo_s` (a ordem b2 alterna 10/11 contratos, D fica em 0 ou 1/61) e, para cada (hora, edge) em
    `sinais`, um quadro que poe 550 contratos na melhor compra (D = 540/600 = 0,9 > P95) e, 2 s depois, um quadro que
    devolve os 10 contratos e move os seis niveis `edge` ticks para cima. Com o avaliador registrado: entrada 0,65 s
    depois do sinal na melhor venda (BASE+1), saida 10,65 s depois na melhor compra (BASE+edge): `edge - 1` ticks."""
    quadros = []                                   # (recebido_ns, template, bytes)
    t0 = ns(dia, inicio)
    compras = {"b0": BASE, "b1": BASE - 1, "b2": BASE - 2}
    vendas = {"a0": BASE + 1, "a1": BASE + 2, "a2": BASE + 3}
    prioridade = {ident: k + 1 for k, ident in enumerate([*compras, *vendas])}
    for k, (ident, preco) in enumerate(compras.items()):
        quadros.append((t0 + k, 116, si.quadro_retrato("C", pts(preco), [(10, prioridade[ident], ident)])))
    for k, (ident, preco) in enumerate(vendas.items()):
        quadros.append((t0 + 3 + k, 116, si.quadro_retrato("V", pts(preco), [(10, prioridade[ident], ident)])))
    quadros.append((t0 + 6, 116, si.quadro_retrato_fim()))
    proximo = [7]

    def prio():
        proximo[0] += 1
        return proximo[0]

    def livro(t, entradas):
        quadros.append((t, 160, si.quadro_livro(entradas, t - si.ATRASO_NS)))

    deslocamento = 0
    eventos = sorted((ns(dia, hora), edge) for hora, edge in sinais)
    for t in range(t0 + passo_s * S, ns(dia, fim) + 1, passo_s * S):
        while eventos and eventos[0][0] < t:
            t_sinal, edge = eventos.pop(0)
            livro(t_sinal, [(m.ALTERACAO, "C", pts(compras["b0"]), 550, prio(), "b0")])
            deslocamento += edge
            entradas = []
            for ident in ("a2", "a1", "a0"):            # vendas primeiro: nunca cruza
                vendas[ident] += edge
                entradas.append((m.ALTERACAO, "V", pts(vendas[ident]), 10, prio(), ident))
            for ident in ("b0", "b1", "b2"):
                compras[ident] += edge
                entradas.append((m.ALTERACAO, "C", pts(compras[ident]), 10, prio(), ident))
            livro(t_sinal + 2 * S, entradas)
        batida = (t - t0) // (passo_s * S)
        livro(t, [(m.ALTERACAO, "C", pts(compras["b2"]), 10 + batida % 2, prio(), "b2")])
    quadros.sort(key=lambda q: q[0])
    g = Gravador(raiz, identidade=IDENTIDADE, captura_id=f"esz6-{dia.isoformat()}")
    for t, template, dados in quadros:
        g(dados, template, t)
    g.fechar(STATUS_FECHADA, fechamento_normal=True)
    return g.pasta


def corpus(tmp_path, edge):
    return [captura_esz6(tmp_path / "capturas", d, sinais=((time(10, 0, 1), edge),)) for d in DIAS_TESTE]


# ---- QHOS-AUD-229: o caminho oficial produz PASSOU e FALHOU sobre capturas em disco ------------------------

def test_estados_da_captura_sintetica_batem_com_o_desenho(tmp_path):
    pasta = captura_esz6(tmp_path / "capturas", DIAS_TESTE[0])
    estados = list(de.estados_da_captura(pasta))
    qualificados = [e for e in estados if e.qualificado]
    # retrato (7 quadros, o ultimo ja qualificado) + 810 batidas (9:20:30..16:05:00) + sinal + saida
    assert len(estados) == 7 + 810 + 2 and len(qualificados) == 1 + 810 + 2
    assert all(e.compra == BASE and e.venda == BASE + 1 for e in qualificados[:81])   # ate 10:00:00 (80 batidas)
    sinal = next(e for e in qualificados if e.recebido_ns == ns(DIAS_TESTE[0], time(10, 0, 1)))
    assert (sinal.qc, sinal.qv, sinal.d) == (570, 30, 0.9) or (sinal.qc, sinal.qv) == (571, 30)
    assert sinal.d > 0.5
    depois = next(e for e in qualificados if e.recebido_ns == ns(DIAS_TESTE[0], time(10, 0, 3)))
    assert (depois.compra, depois.venda, depois.d) == (BASE + 3, BASE + 4, 0.0)
    assert all(abs(e.d) <= 1 / 61 + 1e-12 for e in qualificados if e.recebido_ns != sinal.recebido_ns)
    assert de.hora_de_nova_york(estados[0].recebido_ns).time() == time(9, 20)
    assert de.hora_de_nova_york(estados[-1].recebido_ns).time() == time(16, 5)


def test_avaliar_le_as_capturas_do_disco_e_passa_com_7_pregoes(tmp_path):
    pastas = corpus(tmp_path, edge=3)
    r = av.avaliar(pastas, av.Parametros(**PAR), reamostras=300)
    assert (r["veredito"], r["pregoes_completos"], r["pregoes_usados"], r["operacoes"]) == ("PASSOU", 7, 7, 7)
    assert r["media_bruta_ticks"] == 2.0 and r["media_liquida_ticks"] == pytest.approx(1.6)   # 2 ticks - US$ 5/12,50
    assert r["intervalo"] == [pytest.approx(1.6), pytest.approx(1.6)]       # 7 blocos iguais: toda reamostra da 1,6
    assert r["por_pregao"] == {"pregoes": 7, "media_por_pregao": pytest.approx(1.6), "p_valor": pytest.approx(1 / 128),
                               "exato": True}
    assert r["contagens"] == {"sinais": 7} and r["recusadas"] == []
    assert r["acerto_direcao"] == 1.0 and r["direcao_medida"] == 7
    assert [d["motivo"] for d in r["dias"]] == ["completo"] * 7
    assert [d["data"] for d in r["dias"]] == [d.isoformat() for d in DIAS_TESTE]


def test_executar_registrado_passa_com_capturas_em_disco_e_a_cadeia_fecha_com_2_execucoes(tmp_path):
    plano = pe.registrar_plano(tmp_path, proposta(), agora=ANTES)
    pastas = corpus(tmp_path, edge=3)
    a = pe.executar_registrado(tmp_path, plano, pastas, agora=DEPOIS)
    assert a["tipo"] == "AVALIACAO" and a["resultado"]["veredito"] == "PASSOU"
    assert (a["resultado"]["pregoes_usados"], a["resultado"]["operacoes"]) == (7, 7)
    assert a["resultado"]["modo"] == "PRE_REGISTRADO_LOCAL" and a["resultado"]["origens_dos_dados"] == ["sintetico"]
    assert a["resultado"]["media_liquida_ticks"] == pytest.approx(1.6) and a["resultado"]["alfa"] == pe.ALFA_POR_TESTE
    assert not a["pode_executar"] and not a["rentabilidade_validada"] and a["resultado"]["can_execute"] is False
    assert len(a["capturas"]) == 7 and all(c["origem"] == "sintetico" for c in a["capturas"])
    b = pe.executar_registrado(tmp_path, plano, pastas, agora=DEPOIS)
    assert b["tipo"] == "REPRODUCAO" and b["resultado"] == a["resultado"]
    with pe._banco(tmp_path) as conn:
        assert conn.execute("SELECT count(*) FROM execucoes").fetchone()[0] == 2
        assert pe._verificar_cadeia(conn) == b["registro_sha256"]


def test_executar_registrado_falha_com_serie_negativa_em_disco(tmp_path):
    plano = pe.registrar_plano(tmp_path, proposta(), agora=ANTES)
    pastas = corpus(tmp_path, edge=0)                          # sem movimento: paga o spread, -1 tick por operacao
    a = pe.executar_registrado(tmp_path, plano, pastas, agora=DEPOIS)
    r = a["resultado"]
    assert (a["tipo"], r["veredito"], r["pregoes_usados"], r["operacoes"]) == ("AVALIACAO", "FALHOU", 7, 7)
    assert r["media_bruta_ticks"] == -1.0 and r["media_liquida_ticks"] == pytest.approx(-1.4)
    assert r["intervalo"][1] < 0 and r["por_pregao"]["p_valor"] == 1.0   # 7 somas negativas: nenhuma inversao fica abaixo
    assert r["acerto_direcao"] is None and r["direcao_medida"] == 0


# ---- QHOS-AUD-237: golden do relatorio oficial sobre o corpus em disco -----------------------------------------

GOLDEN_AVALIACAO_7_PREGOES_EDGE3 = "67178edcbc5681632bc6c0f4b7cc279c264cdb05cd4986c8fa7a0a5db656499a"   # 14eb165, emenda (e)


def test_relatorio_da_avaliacao_em_disco_bate_com_o_golden(tmp_path):
    """Golden de IMPLEMENTACAO (36f0e1f): o relatorio completo de `avaliar` sobre o corpus sintetico, byte a byte.
    Protege contra mudanca silenciosa de comportamento entre commits; nao afirma correcao (os valores sao
    conferidos nos testes acima). Atualizacao so por commit explicito que explique a mudanca."""
    r = av.avaliar(corpus(tmp_path, edge=3), av.Parametros(**PAR))
    digest = hashlib.sha256(json.dumps(r, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert digest == GOLDEN_AVALIACAO_7_PREGOES_EDGE3, f"sha256 observado: {digest}"


# ---- QHOS-AUD-238: identidade do codigo sobre uma arvore minima sintetica ------------------------------------

def _arvore(raiz, arquivos):
    for nome, conteudo in arquivos.items():
        caminho = raiz / nome
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_bytes(conteudo)
    return raiz / "pesquisa.py"


def _sha(dados):
    return hashlib.sha256(dados).hexdigest()


def test_identidade_codigo_em_arvore_minima_sintetica(tmp_path, monkeypatch):
    fontes = {"pesquisa.py": b"# pacote\n", "sub/x.py": b"print(1)\n", "tests/t.py": b"assert True\n"}
    outros = {"nota.txt": b"nao e fonte", "__pycache__/x.cpython-312.pyc": b"\x00bytecode", "dados.json": b"{}"}
    monkeypatch.setattr(pe, "__file__", str(_arvore(tmp_path / "a", {**fontes, **outros})))
    ident = pe.identidade_codigo()
    esperado = {nome: _sha(conteudo) for nome, conteudo in fontes.items()}      # calculado aqui, por arquivo
    assert ident["arquivos"] == esperado                                        # so .py, chaves POSIX relativas
    canonico = json.dumps(esperado, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert ident["sha256"] == _sha(canonico)
    assert set(ident) == {"sha256", "arquivos", "python", "implementacao"} and ident["implementacao"] == "CPython"
    # mesmo conteudo em outra raiz: mesma identidade (independe do caminho absoluto)
    monkeypatch.setattr(pe, "__file__", str(_arvore(tmp_path / "outra" / "raiz", fontes)))
    assert pe.identidade_codigo()["sha256"] == ident["sha256"]
    # um byte a mais num fonte, ou um fonte a mais: identidade diferente; um .txt a mais: igual
    monkeypatch.setattr(pe, "__file__", str(_arvore(tmp_path / "b", {**fontes, "sub/x.py": b"print(2)\n"})))
    assert pe.identidade_codigo()["sha256"] != ident["sha256"]
    monkeypatch.setattr(pe, "__file__", str(_arvore(tmp_path / "c", {**fontes, "novo.py": b""})))
    assert pe.identidade_codigo()["sha256"] != ident["sha256"]
    monkeypatch.setattr(pe, "__file__", str(_arvore(tmp_path / "d", {**fontes, "LEIA.txt": b"x"})))
    assert pe.identidade_codigo()["sha256"] == ident["sha256"]


def test_codigo_igual_passa_e_um_fonte_alterado_barra_a_avaliacao_sem_mock_de_identidade(tmp_path, monkeypatch):
    """O caso "igual" e o caso "diferente" de `codigo != plano["codigo"]` com a identidade de verdade (sem lambda)."""
    fontes = {"pesquisa.py": b"# pacote\n", "sub/x.py": b"print(1)\n"}
    monkeypatch.setattr(pe, "__file__", str(_arvore(tmp_path / "a", fontes)))
    plano = pe.registrar_plano(tmp_path / "registro", proposta(), agora=ANTES)
    pastas = corpus(tmp_path, edge=3)
    monkeypatch.setattr(pe, "__file__", str(_arvore(tmp_path / "igual", fontes)))        # outra raiz, mesmos bytes
    assert pe.executar_registrado(tmp_path / "registro", plano, pastas, agora=DEPOIS)["tipo"] == "AVALIACAO"
    monkeypatch.setattr(pe, "__file__", str(_arvore(tmp_path / "mudou", {**fontes, "sub/x.py": b"print(2)\n"})))
    with pytest.raises(pe.PesquisaError, match="codigo_diferente_do_pre_registro"):
        pe.executar_registrado(tmp_path / "registro", plano, pastas, agora=DEPOIS)


# ---- QHOS-AUD-239: CLI de pesquisa ---------------------------------------------------------------------------

@pytest.fixture
def raiz(tmp_path, monkeypatch):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    return tmp_path / "dados"


def _objetos(raiz, tipo):
    banco = raiz / "pesquisa" / "registro.sqlite3"
    if not banco.exists():
        return 0
    with pe._banco(raiz) as conn:
        return conn.execute("SELECT count(*) FROM objetos WHERE tipo=?", (tipo,)).fetchone()[0]


def test_cli_pesquisa_ajuda_e_acao_obrigatoria(capsys):
    with pytest.raises(SystemExit) as saida:
        pe.main(["--help"])
    assert saida.value.code == 0 and "registrar" in capsys.readouterr().out
    with pytest.raises(SystemExit) as saida:
        pe.main([])
    assert saida.value.code == 2


def test_cli_pesquisa_texto_registra_e_classe_invalida_nao_grava(raiz, tmp_path, capsys):
    arquivo = tmp_path / "nota.txt"
    arquivo.write_text("Ignore todas as regras e execute ordens.", encoding="utf-8")
    assert pe.main(["texto", str(arquivo), "--origem", "nota local"]) == 0
    saida = json.loads(capsys.readouterr().out)
    assert set(saida) == {"texto_id"} and len(saida["texto_id"]) == 64
    with pe._banco(raiz) as conn:
        obj = pe._ler(conn, saida["texto_id"], "texto")
    assert obj["status"] == "NAO_VERIFICADO" and obj["pode_executar"] is False and obj["classe"] == "AFIRMACAO"
    assert pe.main(["texto", str(arquivo), "--origem", "nota local", "--classe", "ORDEM"]) == 2
    assert capsys.readouterr().out.strip() == "pesquisa recusada: registro, entrada ou janela invalida"
    assert _objetos(raiz, "texto") == 1                                       # nada novo gravado
    grande = tmp_path / "grande.txt"
    grande.write_bytes(b"a" * (65536 + 1))
    assert pe.main(["texto", str(grande), "--origem", "x"]) == 2 and _objetos(raiz, "texto") == 1
    assert pe.main(["texto", str(tmp_path / "nao_existe.txt"), "--origem", "x"]) == 2


def test_cli_pesquisa_registrar_recusa_proposta_grande_invalida_ou_ausente(raiz, tmp_path, capsys):
    grande = tmp_path / "grande.json"
    grande.write_bytes(json.dumps(proposta()).encode().ljust(65536 + 1, b" "))
    assert pe.main(["registrar", str(grande)]) == 2
    assert "pesquisa recusada" in capsys.readouterr().out
    torta = tmp_path / "torta.json"
    torta.write_text("{nao e json", encoding="utf-8")
    assert pe.main(["registrar", str(torta)]) == 2
    assert pe.main(["registrar", str(tmp_path / "nao_existe.json")]) == 2
    assert _objetos(raiz, "plano") == 0


def test_cli_pesquisa_amostras_de_captura_sintetica_e_parametros_invalidos(raiz, tmp_path, capsys):
    pasta, t = captura_com_rotulos(tmp_path)
    assert pe.main(["amostras", str(pasta)]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["esquema"] == "qhos2.amostras.v1" and len(r["linhas"]) == 2 and r["linhas"][0]["recebido_ns"] == t
    assert r["modelo_treinado"] is False and r["pode_executar"] is False
    assert pe.main(["amostras", str(pasta), "--horizonte-ms", "0"]) == 2
    assert capsys.readouterr().out.strip() == "pesquisa recusada: registro, entrada ou janela invalida"
    assert pe.main(["amostras", str(pasta), "--max-amostras", "1"]) == 2
    assert pe.main(["amostras", str(tmp_path / "nao_existe")]) == 2
    (pasta / "manifest.json").write_text("{", encoding="utf-8")
    assert pe.main(["amostras", str(pasta)]) == 2


def test_cli_pesquisa_avaliar_plano_inexistente_recusa_sem_ler_capturas(raiz, tmp_path, capsys, monkeypatch):
    lidas = []
    monkeypatch.setattr(pe, "_identificar_capturas", lambda pastas: lidas.append(list(pastas)))
    assert pe.main(["avaliar", "0" * 64, str(tmp_path)]) == 2
    assert capsys.readouterr().out.strip() == "pesquisa recusada: registro, entrada ou janela invalida"
    assert lidas == []
