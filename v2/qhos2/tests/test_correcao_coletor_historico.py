"""Campanha de correcao, lote l5b-coletor: historico de candles servido pelo coletor
(QHOS-AUD-152, 156, 168, 169, 176, 177, 178, 179, 180, 187 a/b, 191).

Capturas sinteticas em `tmp_path`; nenhuma porta. `xfail(strict=True)` = defeito confirmado em codigo congelado
(`src/qhos2/historico.py`, `src/qhos2/coletor.py`); patch minimo em `09_correcao/EVIDENCIAS/<ID>/patch_proposto.diff`.
"""
import json
import threading
from pathlib import Path

import pytest

from qhos2 import eventos as ev, historico as hi, leitura as le, sintetico as si
from qhos2.coletor import Coletor, ESZ6

from test_coletor_falhas import negocio, pedido
from test_integridade_heranca import STATUS, gravar, leitor, retrato

TOKEN = "a" * 43
T0 = 1_761_246_000                      # segundo cheio (minuto 1_761_246_000 - 1_761_246_000 % 60)
MINUTO = T0 - T0 % 60


def evento_negocio(**extra):
    campos = {"indice": 7, "posicao": 0, "recebido_ns": MINUTO * 10 ** 9, "tipo": ev.NEGOCIO, "lado": ev.COMPRA,
              "preco": 400, "quantidade": 1, "carimbo_ns": MINUTO * 10 ** 9}
    return ev.Evento(**{**campos, **extra})


def historico_vazio(tmp_path, relogio=lambda: 2_000_000_000.0):
    return hi.Historico(tmp_path / "capturas", tmp_path / "cache", tick=0.25, simbolo="ESZ6", bolsa="CME", relogio=relogio)


def barras(*minutos):
    b = hi.Barras()
    for m in minutos:
        b.minutos[m] = [400, 401, 399, 400, 1]
    return b


CAMPOS_BASE_HISTORICO = {"capturas", "cache", "tick", "simbolo", "bolsa", "_relogio", "prontas", "recusadas"}


def _contadores(obj, ignorar=()):
    """Inteiros guardados no objeto (diretos ou dentro de dicts), fora dos campos ignorados: os testes exigem o fato
    (algo contou 1), nao o nome do contador, que e escolha de quem corrigir."""
    valores = []
    for chave, valor in vars(obj).items():
        if chave in ignorar or isinstance(valor, bool):
            continue
        if isinstance(valor, int):
            valores.append(valor)
        elif isinstance(valor, dict):
            valores += [x for x in valor.values() if isinstance(x, int) and not isinstance(x, bool)]
    return valores


# ---- QHOS-AUD-156: candle mistura carimbo do servidor e relogio local sem marcar -----------------------------------

def test_caracterizacao_negocio_sem_carimbo_cai_no_minuto_da_chegada_local():
    b = hi.Barras()
    b.negocio(evento_negocio())                                            # carimbo do servidor: minuto M
    b.negocio(evento_negocio(posicao=1, carimbo_ns=None, recebido_ns=(MINUTO + 90) * 10 ** 9))  # chegada: M + 1
    assert sorted(b.minutos) == [MINUTO, MINUTO + 60]                       # duas bases de tempo na mesma serie


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-156: `Barras.negocio` usa `recebido_ns` quando falta `carimbo_ns` "
                                       "sem contar nem marcar a barra (docstring documenta, nada observavel)")
def test_barras_contam_negocios_sem_carimbo_do_servidor():
    b = hi.Barras()
    b.negocio(evento_negocio())
    b.negocio(evento_negocio(posicao=1, carimbo_ns=None, recebido_ns=(MINUTO + 90) * 10 ** 9))
    assert 1 in _contadores(b, ignorar=("minutos",))               # um negocio sem carimbo ficou contado


# ---- QHOS-AUD-176: negocio com preco ou quantidade zero descartado sem contagem ----------------------------------

def test_caracterizacao_preco_zero_e_descartado_em_silencio():
    b = hi.Barras()
    b.negocio(evento_negocio(preco=0))
    b.negocio(evento_negocio(quantidade=0))
    assert b.minutos == {}


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-176: `Barras.negocio` descarta preco/quantidade zero ou ausente "
                                       "sem contador")
def test_barras_contam_negocio_descartado_por_preco_zero():
    b = hi.Barras()
    b.negocio(evento_negocio(preco=0))
    assert b.minutos == {} and 1 in _contadores(b, ignorar=("minutos",))   # o descarte ficou contado


# ---- QHOS-AUD-168 e 187 a/b: erros de pedido e tipos inesperados no history.candles --------------------------------

def test_caracterizacao_erro_de_pedido_responde_history_unavailable(tmp_path):
    """QHOS-AUD-168: `limit: "10"` (erro do cliente) e `timeframe: "2m"` recebem o mesmo codigo que 'sem historico'.
    Trocar o codigo e decisao de contrato (a ponte so conhece HISTORY_UNAVAILABLE/HISTORY_OVERLAP); fica registrado."""
    c = Coletor(token=TOKEN, porta=0, historico=historico_vazio(tmp_path))
    for params in ({"limit": "10"}, {"limit": 0}, {"timeframe": "2m"}):
        r = c.responder({**pedido(), "params": {**pedido()["params"], **params}})
        assert r["ok"] is False and r["error"] == {"code": "HISTORY_UNAVAILABLE"}, params


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-187(a): `timeframe` nao hashable faz `quadro not in QUADROS` lancar "
                                       "TypeError fora do `except HistoricoError`; a excecao escapa de `responder`")
@pytest.mark.parametrize("quadro", [[], {}, ["1m"]])
def test_timeframe_nao_hashable_recebe_resposta_e_nao_excecao(tmp_path, quadro):
    c = Coletor(token=TOKEN, porta=0, historico=historico_vazio(tmp_path))
    r = c.responder({**pedido(), "params": {**pedido()["params"], "timeframe": quadro}})
    assert isinstance(r, dict) and r["type"] == "response" and r["ok"] is False
    assert r["error"]["code"] in ("HISTORY_UNAVAILABLE", "INVALID_PARAMS")


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-187(b): `limit: true` passa em `isinstance(limite, int)` e vira 1; "
                                       "no resto do projeto bool e excluido onde se espera inteiro")
def test_limite_true_e_pedido_invalido(tmp_path):
    c = Coletor(token=TOKEN, porta=0, historico=historico_vazio(tmp_path))
    r = c.responder({**pedido(), "params": {**pedido()["params"], "limit": True}})
    assert r["ok"] is False


def test_caracterizacao_limite_true_vira_1_e_false_e_recusado(tmp_path):
    c = Coletor(token=TOKEN, porta=0, historico=historico_vazio(tmp_path))
    aceito = c.responder({**pedido(), "params": {**pedido()["params"], "limit": True}})
    recusado = c.responder({**pedido(), "params": {**pedido()["params"], "limit": False}})
    assert aceito["ok"] is True and aceito["result"]["candles"] == []
    assert recusado["ok"] is False


# ---- QHOS-AUD-152: apos FALHA da leitura, history.candles responde ok com barras removidas e sem marcador ---------

def _coletor_que_falha_depois_de_servir(tmp_path):
    g = gravar(tmp_path / "capturas", retrato() + [negocio()], fechar=False)
    l = leitor(g.pasta)
    h = hi.Historico(tmp_path / "capturas", tmp_path / "cache", tick=0.25, simbolo="ESZ6", bolsa="CME",
                     relogio=lambda: si.INICIO_NS / 1e9 + 10)
    c = Coletor(token=TOKEN, leitor=l, historico=h)
    assert c.traduzir(l.avancar(), True)
    antes = c.responder(pedido())
    assert antes["ok"] and antes["result"]["candles"]
    g.fechar({**STATUS, "estado": "FALHOU", "retrato_completo": False}, fechamento_normal=False)
    l.avancar()
    assert l.estado == le.FALHA
    return c


def test_caracterizacao_pos_falha_candles_somem_com_ok_true_e_sem_cobertura(tmp_path):
    c = _coletor_que_falha_depois_de_servir(tmp_path)
    depois = c.responder(pedido())
    assert depois["ok"] is True and depois["result"]["candles"] == [] and "coverage" not in depois["result"]


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-152: a resposta pos-FALHA e identica a 'sem candles'; o terminal "
                                       "apaga o grafico sem ligacao com 'Leitura parou'")
def test_pos_falha_a_resposta_de_historico_e_distinguivel_de_vazio(tmp_path):
    c = _coletor_que_falha_depois_de_servir(tmp_path)
    depois = c.responder(pedido())
    assert depois["ok"] is False or "coverage" in depois["result"]


# ---- QHOS-AUD-169: corrida entre preparar (thread) e candles (laco) sobre self.prontas -----------------------------

def test_insercao_concorrente_em_prontas_nao_quebra_candles(tmp_path):
    """Hipotese do achado: `sorted(self.prontas.values())` lancaria `RuntimeError: dictionary changed size during
    iteration` enquanto `preparar` insere em outra thread. Em CPython `sorted(view)` materializa a lista numa unica
    chamada C com o GIL preso; a insercao de `preparar` so pode entrar entre bytecodes. O teste forca a janela com
    barreiras (sem sleeps) e repete muitas vezes; passar = NAO reproduzido."""
    h = historico_vazio(tmp_path)
    for m in range(50):
        h.prontas[f"c{m}"] = barras(MINUTO - 60 * (m + 1))
    pronto, erros = threading.Barrier(2), []

    def insere():
        pronto.wait()
        for m in range(5000):
            h.prontas[f"novo{m}"] = barras(MINUTO - 60 * (100 + m))

    t = threading.Thread(target=insere)
    t.start()
    pronto.wait()
    for _ in range(500):
        try:
            h.candles("1m", 10)
        except RuntimeError as erro:          # "dictionary changed size during iteration"
            erros.append(repr(erro))
            break
    t.join()
    assert not erros and len(h.prontas) == 5050


# ---- QHOS-AUD-177: cache de historico invalido e refeito em silencio ------------------------------------------------

def _captura_com_cache(tmp_path):
    g = gravar(tmp_path / "capturas", retrato() + [negocio()])
    cache = tmp_path / "cache"
    assert hi.barras_da_captura(g.pasta, cache, tick=0.25, simbolo="ESZ6", bolsa="CME") is not None
    return g.pasta, cache / "caso.json"


def test_caracterizacao_cache_invalido_e_reescrito_sem_sinal(tmp_path):
    pasta, arquivo = _captura_com_cache(tmp_path)
    valido = arquivo.read_text(encoding="utf-8")
    arquivo.write_text("{nao e json", encoding="utf-8")
    h = hi.Historico(tmp_path / "capturas", tmp_path / "cache", tick=0.25, simbolo="ESZ6", bolsa="CME")
    h.preparar()
    assert "caso" in h.prontas and h.recusadas == {}
    assert arquivo.read_text(encoding="utf-8") == valido            # refeito...
    assert set(vars(h)) == CAMPOS_BASE_HISTORICO
    # ...e a superficie de estado do historico nao tem onde registrar isso (`cache` e so o caminho)


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-177: cache de candles invalido e descartado e refeito sem nenhum "
                                       "registro (nem motivo nem contagem)")
def test_cache_invalido_refeito_fica_registrado_com_motivo(tmp_path):
    pasta, arquivo = _captura_com_cache(tmp_path)
    arquivo.write_text(json.dumps({"esquema": "qhos2.candles_1m.v1"}), encoding="utf-8")
    h = hi.Historico(tmp_path / "capturas", tmp_path / "cache", tick=0.25, simbolo="ESZ6", bolsa="CME")
    h.preparar()
    assert "caso" in h.prontas
    registros = {k: v for k, v in vars(h).items() if k not in CAMPOS_BASE_HISTORICO and isinstance(v, dict)}
    assert any(v.get("caso") for v in registros.values()), registros   # algum registro da captura com motivo


# ---- QHOS-AUD-178: preparar() retorna em silencio se capturas/ nao existe -------------------------------------------

def test_caracterizacao_pasta_de_capturas_ausente_e_silencio(tmp_path):
    h = hi.Historico(tmp_path / "nao_existe", tmp_path / "cache", tick=0.25, simbolo="ESZ6", bolsa="CME")
    h.preparar()
    assert h.prontas == {} and h.recusadas == {}
    c = Coletor(token=TOKEN, porta=0, historico=h)
    assert "history" in c.welcome()["capabilities"]                   # o welcome anuncia historico que nao existe


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-178: `Historico.preparar` retorna sem registrar que `capturas/` "
                                       "nao existe; o coletor imprime 'historico pronto: 0 capturas'")
def test_pasta_de_capturas_ausente_e_registrada_como_recusa(tmp_path):
    h = hi.Historico(tmp_path / "nao_existe", tmp_path / "cache", tick=0.25, simbolo="ESZ6", bolsa="CME")
    h.preparar()
    assert "pasta_de_capturas_ausente" in h.recusadas.values()


# ---- QHOS-AUD-179: detalhe da recusa de captura truncado ------------------------------------------------------------

def _captura_com_bytes_corrompidos(tmp_path):
    g = gravar(tmp_path / "capturas", retrato() + [negocio()])
    bruto = g.pasta / "market_frames.jsonl"
    bruto.write_bytes(bruto.read_bytes() + b"\n")
    with pytest.raises(ev.EventoError) as erro:
        hi.barras_da_captura(g.pasta, tmp_path / "cache", tick=0.25, simbolo="ESZ6", bolsa="CME")
    codigo, detalhe = str(erro.value).split(":", 1)
    assert codigo == "captura_nao_verificada" and detalhe          # ex.: "linha_invalida" (a linha vazia acrescentada)
    return detalhe


def test_caracterizacao_recusa_perde_a_lista_de_divergencias(tmp_path):
    detalhe = _captura_com_bytes_corrompidos(tmp_path)
    h = hi.Historico(tmp_path / "capturas", tmp_path / "cache", tick=0.25, simbolo="ESZ6", bolsa="CME")
    h.preparar()
    assert h.recusadas == {"caso": "captura_nao_verificada"} and detalhe not in h.recusadas["caso"]


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-179: `str(erro).split(':')[0]` em `preparar` perde a lista de "
                                       "divergencias de `verificar_captura`")
def test_recusa_conserva_as_divergencias(tmp_path):
    detalhe = _captura_com_bytes_corrompidos(tmp_path)
    h = hi.Historico(tmp_path / "capturas", tmp_path / "cache", tick=0.25, simbolo="ESZ6", bolsa="CME")
    h.preparar()
    assert detalhe in h.recusadas["caso"]


# ---- QHOS-AUD-180: minutos "no futuro" descartados sem contagem ---------------------------------------------------

def test_caracterizacao_minuto_futuro_some_sem_contagem(tmp_path):
    h = historico_vazio(tmp_path, relogio=lambda: float(MINUTO - 30))   # relogio local 30 s atrasado
    h.prontas["x"] = barras(MINUTO - 60, MINUTO)
    assert [c["time"] for c in h.candles("1m", 10)] == [MINUTO - 60]


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-180: `minuto > agora` descarta a barra mais recente sem contador; "
                                       "relogio local atrasado apaga os candles novos em silencio")
def test_minuto_futuro_descartado_e_contado(tmp_path):
    h = historico_vazio(tmp_path, relogio=lambda: float(MINUTO - 30))
    h.prontas["x"] = barras(MINUTO - 60, MINUTO)
    assert len(h.candles("1m", 10)) == 1
    assert 1 in _contadores(h, ignorar=CAMPOS_BASE_HISTORICO)        # o minuto descartado ficou contado


# ---- QHOS-AUD-191: candles de borda (1D, 4h) saem como completos -----------------------------------------------------

DIA_9_11_UTC = hi.OCULTO_DE_S - 5 * 3600            # 2026-11-09 00:00 UTC
AGORA_ANTES_DA_LIBERACAO = hi.OCULTO_DE_S + 3600


def test_caracterizacao_candle_1d_cortado_pela_janela_sai_sem_partial(tmp_path):
    h = historico_vazio(tmp_path, relogio=lambda: float(AGORA_ANTES_DA_LIBERACAO))
    h.prontas["x"] = barras(hi.OCULTO_DE_S - 60, hi.OCULTO_DE_S + 60)
    saida = h.candles("1D", 10)
    assert [c["time"] for c in saida] == [DIA_9_11_UTC] and saida[0]["volume"] == 1
    assert "partial" not in saida[0]


def test_caracterizacao_partial_na_borda_e_decisao_em_aberto(tmp_path):
    """QHOS-AUD-191: o corte da janela oculta e por minuto, antes da agregacao, e `partial` hoje so marca candle em
    andamento (ponte) ou sobreposicao (coletor). Se tambem deve marcar periodo cortado pela janela e decisao do autor
    (o ACHADOS diz 'media em ser defeito'): fica caracterizado que a ponte aceitaria `partial` booleano por candle."""
    h = historico_vazio(tmp_path, relogio=lambda: float(AGORA_ANTES_DA_LIBERACAO))
    h.prontas["x"] = barras(hi.OCULTO_DE_S - 60, hi.OCULTO_DE_S + 60)
    saida = h.candles("1D", 10)
    assert [c["time"] for c in saida] == [DIA_9_11_UTC] and "partial" not in saida[0]
    ponte = (Path(__file__).resolve().parents[1] / "terminal" / "modulos" / "qhos-bridge.js").read_text(encoding="utf-8")
    assert "(row.partial != null && typeof row.partial !== 'boolean')" in ponte
