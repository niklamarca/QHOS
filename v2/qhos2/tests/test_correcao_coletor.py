"""Campanha de correcao, lote l5c-leitura: coletor (QHOS-AUD-197 e QHOS-AUD-035).

035: a auditoria nao reproduziu a corrida leitura (thread) x retrato/percurso (laco) com threads livres. Aqui
os entrelacamentos sao DETERMINISTICOS: a thread de leitura e parada, por barreira, exatamente nos pontos em
que o interpretador pode trocar de thread (entre dois comandos de `livro.py`), e o laco le `retrato_atual()`
nesse instante. Sao os mesmos pontos de troca que o coletor real tem (`asyncio.to_thread(leitor.avancar)` em
coletor.py:557 contra `retrato_atual()` em coletor.py:454, quando um terminal conecta). Sementes e estruturas
ficam registradas no proprio teste. A rodada com threads livres (metodo da auditoria) fica como sentinela.
"""
import bisect
import threading
import time

import pytest

pytest.importorskip("websockets")

from qhos2 import coletor as co  # noqa: E402
from qhos2 import eventos as ev  # noqa: E402
from qhos2 import leitura as le  # noqa: E402
from qhos2 import livro as lv  # noqa: E402
from qhos2 import mercado as m  # noqa: E402
from qhos2 import rprotocol as p  # noqa: E402
from qhos2 import sintetico as si  # noqa: E402
from qhos2.coletor import Coletor, conferir_captura  # noqa: E402
from qhos2.gravador import Gravador  # noqa: E402

TOKEN = "A" * 43
IDENTIDADE = {"system_name": si.SISTEMA, "requested_symbol": "ESZ6", "exchange": "CME"}
STATUS = {"estado": "FECHADA", "retrato_completo": True, "referencia": {"min_qprice_change": 0.25}}


def gravar_es(raiz, quadros, nome="es"):
    g = Gravador(raiz, identidade=IDENTIDADE, captura_id=nome)
    for i, (template, dados) in enumerate(quadros):
        g(dados, template, si.INICIO_NS + i * si.PASSO_NS)
    g.fechar(STATUS, fechamento_normal=True)
    return g.pasta


# ---- QHOS-AUD-197: conferir_captura filtra negocios sem os `> 0` de mensagem_negocio ------------------------------

def test_caracterizacao_negocio_de_preco_negativo_diverge_entre_a_conferencia_e_a_mensagem(tmp_path):
    """`ticks()` aceita preco negativo (ev.ticks(-0.5, 0.25) == -2). `mensagem_negocio` o descarta (`preco <= 0`,
    coletor.py:199-200); `conferir_captura` o conta no replay (`e.preco and e.quantidade`, coletor.py:273-274).
    O contrato nao diz qual dos dois predicados e o certo: caracterizacao da divergencia latente (CS §2.6, §7)."""
    cenario = si.gerar(2, 30)
    negativo = si.quadro_negocio(-0.25, 1, "C", "p", "a", si.INICIO_NS)
    pasta = gravar_es(tmp_path, cenario.quadros + [(150, negativo)])
    eventos = [e for e in ev.eventos_da_captura(pasta) if e.tipo == ev.NEGOCIO and not e.retrato]
    assert eventos[-1].preco == -1 and eventos[-1].quantidade == 1
    assert co.mensagem_negocio(eventos[-1], {}, 0.25) is None
    r = conferir_captura(pasta, tick=0.25, simbolo="ESZ6", bolsa="CME")
    assert r["negocios_gravados"] == r["negocios_enviados"] + 1
    assert r["negocios_iguais"] is False and r["ok"] is False
    assert r["livro_igual"] and r["livro_completo_igual"] and r["fora_do_formato"] == {}


# ---- QHOS-AUD-035: corrida leitura x retrato/percurso ----------------------------------------------------------------

class _Entrelacador:
    """Barreira de duas pontas: a thread de leitura para em `ponto()` e so segue quando o laco libera."""

    def __init__(self):
        self.parada, self.segue = threading.Event(), threading.Event()
        self.ativo = True
        self.lado_do_laco = threading.get_ident()

    def ponto(self):
        if not self.ativo or threading.get_ident() == self.lado_do_laco:
            return
        self.segue.clear()
        self.parada.set()
        if not self.segue.wait(10):
            raise RuntimeError("laco nao liberou a leitura")

    def observar(self, thread, observar):
        """Enquanto a thread viver, a cada parada chama `observar()` (numa thread propria, como o laco do coletor) e
        libera a leitura. Se `observar` ficar bloqueado (uma trava, por exemplo), a leitura e liberada mesmo assim e
        segue sendo liberada ate o observador responder: assim o teste nao trava com uma correcao por trava."""
        saida = []
        while thread.is_alive():
            if not self.parada.wait(0.05):
                continue
            self.parada.clear()
            caixa = []
            observador = threading.Thread(target=lambda: caixa.append(observar()), daemon=True)
            observador.start()
            observador.join(0.1)
            self.segue.set()
            prazo = time.monotonic() + 30
            while observador.is_alive():
                if time.monotonic() > prazo:
                    raise RuntimeError("observador nao respondeu em 30 s (trava presa?)")
                if self.parada.wait(0.05):
                    self.parada.clear()
                    self.segue.set()
            saida.append(caixa[0] if caixa else ("sem_resposta",))
        return saida


def _coletor_sobre(pasta):
    leitor = le.Leitor(pasta, tick=0.25, simbolo="ESZ6", bolsa="CME")
    coletor = Coletor(token=TOKEN, porta=0, avisar=lambda _: None, leitor=leitor)
    coletor._tempo_ns = si.INICIO_NS                          # retrato_atual exige um quadro lido; como na auditoria
    return leitor, coletor


def _ler_em_thread(leitor, entrelacador, bloco=64 * 1024):
    erros = []

    def ler():
        try:
            while leitor.estado not in (le.ENCERRADA, le.FALHA):
                leitor.avancar(bloco)
        except Exception as erro:             # noqa: BLE001 - e o que o teste mede
            erros.append(repr(erro))
        finally:
            entrelacador.ativo = False
    t = threading.Thread(target=ler, daemon=True)
    t.start()
    return t, erros


def _retrato_formatado(coletor):
    """O que um terminal que conecta neste instante receberia (coletor.py:454), julgado por fora_do_formato."""
    try:
        mensagens = coletor.retrato_atual()
        coletor.percurso()
    except Exception as erro:                 # noqa: BLE001 - e o que o teste mede
        return ("excecao", repr(erro))
    return tuple(motivo for motivo in (co.fora_do_formato(x, 0.25) for x in mensagens) if motivo)


# Quadro 160 com a ordem agressiva ANTES das baixas que ela causa (a ordem do fio real, livro.py:22-25):
# no meio do quadro o livro esta cruzado; no fim nao esta. A sintetica nunca gera isso (so emite as baixas).
QUADRO_CRUZADO = si.quadro_livro([(m.NOVO, "C", 101.0, 1, 9, "agressora"), (m.REMOCAO, "V", 101.0, 0, 2, "v1")], si.INICIO_NS)
CAPTURA_CRUZADA = [(116, si.quadro_retrato("C", 100.0, [(1, 1, "c1")])),
                   (116, si.quadro_retrato("V", 101.0, [(1, 2, "v1")])),
                   (116, si.quadro_retrato("V", 102.0, [(1, 3, "v2")])),
                   (116, si.quadro_retrato_fim()),
                   (160, QUADRO_CRUZADO)]


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-035: entre dois `_aplicar` do mesmo quadro (livro.py:158-159) o laco ve o "
                                       "livro cruzado com ready=True: `cruzado` so e julgado no fim do quadro (livro.py:162)")
def test_entrelacamento_entre_eventos_do_mesmo_quadro_nunca_publica_livro_cruzado(tmp_path, monkeypatch):
    """Ponto de troca: entre dois `_aplicar` de `aplicar_quadro`. Esperado (coletor.py:188-194 e fora_do_formato):
    um terminal que conecta em qualquer instante recebe um livro no formato, nunca cruzado com ready=True.
    Em 36f0e1f a observacao depois da agressora e ("cruzado",)."""
    pasta = gravar_es(tmp_path, CAPTURA_CRUZADA)
    leitor, coletor = _coletor_sobre(pasta)
    entre = _Entrelacador()
    original = lv.Livro._aplicar

    def aplicar_e_parar(self, e):
        original(self, e)
        entre.ponto()
    monkeypatch.setattr(lv.Livro, "_aplicar", aplicar_e_parar)
    thread, erros = _ler_em_thread(leitor, entre)
    vistos = entre.observar(thread, lambda: _retrato_formatado(coletor))
    thread.join(5)
    assert not erros and leitor.estado == le.ENCERRADA and leitor.livro_vale    # a leitura em si termina bem
    assert vistos and set(vistos) == {()}, vistos


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-035: entre `del self._niveis[lado][preco]` e `del precos[...]` em `_retirar` "
                                       "(livro.py:137-139) `niveis()` levanta KeyError no laco do coletor")
def test_entrelacamento_dentro_de_retirar_nunca_quebra_niveis(tmp_path, monkeypatch):
    """Ponto de troca: dentro de `_retirar`, quando um nivel esvazia. `niveis()` (livro.py:93-95) percorre `_precos`
    e indexa `_niveis`. Esperado: `retrato_atual()` nunca levanta. Em 36f0e1f a observacao e ("excecao", "KeyError(...)")."""
    pasta = gravar_es(tmp_path, CAPTURA_CRUZADA)
    leitor, coletor = _coletor_sobre(pasta)
    entre = _Entrelacador()

    class BisectComParada:
        insort = staticmethod(bisect.insort)

        @staticmethod
        def bisect_left(a, x):
            entre.ponto()
            return bisect.bisect_left(a, x)
    monkeypatch.setattr(lv, "bisect", BisectComParada)
    thread, erros = _ler_em_thread(leitor, entre)
    vistos = entre.observar(thread, lambda: _retrato_formatado(coletor))
    thread.join(5)
    assert not erros and leitor.estado == le.ENCERRADA
    assert vistos and set(vistos) == {()}, vistos


@pytest.mark.parametrize("semente", [40, 41, 42])
def test_entrelacamento_entre_eventos_na_captura_sintetica_nao_reproduz(tmp_path, monkeypatch, semente):
    """Mesmo entrelacamento do primeiro teste sobre o corpus sintetico (sementes 40-42, 60 passos): a sintetica nao
    emite ordem agressiva antes das baixas, logo nenhum estado intermediario cruza. Consistente com a auditoria.
    Sem trava ha uma observacao por evento; com uma trava (patch proposto) as paradas dentro do mesmo quadro se fundem."""
    pasta = gravar_es(tmp_path, si.gerar(semente, 60).quadros)
    leitor, coletor = _coletor_sobre(pasta)
    entre = _Entrelacador()
    original = lv.Livro._aplicar

    def aplicar_e_parar(self, e):
        original(self, e)
        entre.ponto()
    monkeypatch.setattr(lv.Livro, "_aplicar", aplicar_e_parar)
    thread, erros = _ler_em_thread(leitor, entre)
    vistos = entre.observar(thread, lambda: _retrato_formatado(coletor))
    thread.join(5)
    assert not erros and leitor.estado == le.ENCERRADA
    assert 0 < len(vistos) <= leitor.livro.eventos and set(vistos) == {()}


@pytest.mark.parametrize("rodada", range(3))
def test_sentinela_threads_livres_informativa(tmp_path, rodada, capsys):
    """Metodo da auditoria (3 rodadas, sementes 40+rodada, 3000 passos), so INFORMATIVO: a corrida existe (testes
    deterministicos acima), entao este teste nao afirma a ausencia dela. Afirma so que a leitura termina sem erro e
    imprime o que o laco viu (com -s ou -rP); a sintetica nao gera livro cruzado no meio do quadro, e o KeyError
    depende de cair na janela de poucos bytecodes de `_retirar`."""
    pasta = gravar_es(tmp_path, si.gerar(40 + rodada, 3000).quadros)
    leitor, coletor = _coletor_sobre(pasta)
    entre = _Entrelacador()
    entre.ativo = False
    thread, erros = _ler_em_thread(leitor, entre, bloco=32 * 1024)
    motivos, voltas = {}, 0
    while thread.is_alive():
        visto = _retrato_formatado(coletor)
        for motivo in visto:
            motivos[motivo] = motivos.get(motivo, 0) + 1
        voltas += 1
    thread.join(5)
    assert leitor.estado == le.ENCERRADA and not erros and voltas > 10
    print(f"sentinela rodada={rodada} voltas={voltas} fora_do_formato={motivos}")
