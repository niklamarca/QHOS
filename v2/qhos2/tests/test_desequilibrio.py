"""Peca 7: desequilibrio ao fim de cada quadro e calibracao que so olha a janela de calibracao e nunca resultado."""
import calendar
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from qhos2 import desequilibrio as de
from qhos2 import eventos as ev
from qhos2 import sintetico as si
from qhos2.eventos import Evento
from qhos2.gravador import Gravador
from qhos2.livro import Livro

C, V = ev.COMPRA, ev.VENDA
FIM = Evento(0, 0, 0, ev.RETRATO_FIM)


def utc_ns(texto):
    return calendar.timegm(datetime.strptime(texto, "%Y-%m-%d %H:%M:%S").timetuple()) * 10 ** 9


def ordem(ident, lado, preco, qtd, tipo=ev.RETRATO, indice=0):
    return Evento(indice, 0, 0, tipo, lado, preco, qtd, ident, 1)


def livro_com(*ordens):
    livro = Livro()
    for o in ordens:
        livro.aplicar(o)
    livro.aplicar(FIM)
    return livro


def gravar_em(cenario, raiz, inicio_ns, captura_id):
    """Grava o cenario sintetico com o relogio comecando em `inicio_ns` (1 ms por quadro)."""
    gravador = Gravador(raiz, identidade={"system_name": si.SISTEMA, "requested_symbol": "S", "exchange": "Q"},
                        captura_id=captura_id)
    for i, (template, dados) in enumerate(cenario.quadros):
        gravador(dados, template, inicio_ns + i * si.PASSO_NS)
    gravador.fechar({"referencia": {"min_qprice_change": cenario.tick}}, fechamento_normal=True)
    return gravador.pasta


# ---- desequilibrio ----------------------------------------------------------------------------------

def test_desequilibrio_dos_tres_melhores_niveis():
    livro = livro_com(ordem("c1", C, 100, 10), ordem("c2", C, 99, 5), ordem("c3", C, 98, 5), ordem("c4", C, 97, 100),
                      ordem("v1", V, 101, 2), ordem("v2", V, 102, 3), ordem("v3", V, 103, 5), ordem("v4", V, 104, 100))
    assert de.desequilibrio(livro) == (20, 10, (20 - 10) / 30)
    assert de.desequilibrio(livro, niveis=1) == (10, 2, 8 / 12)


def test_menos_niveis_que_o_pedido_usa_o_que_existe_e_lado_vazio_da_none():
    assert de.desequilibrio(livro_com(ordem("c", C, 100, 4), ordem("v", V, 101, 4))) == (4, 4, 0.0)
    assert de.desequilibrio(livro_com(ordem("c", C, 100, 4))) == (4, 0, None)
    assert de.desequilibrio(Livro()) == (0, 0, None)


def test_estados_um_por_quadro_e_qualificacao_depois_do_retrato():
    cenario = si.gerar(3, 200)
    eventos = []
    for i, (template, dados) in enumerate(cenario.quadros):
        eventos.extend(ev.normalizar(i, template, i, dados, cenario.tick))
    lista = list(de.estados(eventos))
    assert [e.indice for e in lista] == sorted({e.indice for e in eventos})
    fim_do_retrato = next(e.indice for e in eventos if e.tipo == ev.RETRATO_FIM)
    assert not any(e.qualificado for e in lista if e.indice < fim_do_retrato)
    assert all(e.qualificado for e in lista if e.indice >= fim_do_retrato)
    livro = Livro()
    for e in eventos:
        livro.aplicar(e)
    ultimo = lista[-1]
    assert (ultimo.compra, ultimo.venda) == (livro.melhor(C), livro.melhor(V))
    assert (ultimo.qc, ultimo.qv, ultimo.d) == de.desequilibrio(livro)


def test_estado_usa_o_relogio_do_ultimo_evento_do_quadro():
    eventos = [Evento(0, 0, 5, ev.RETRATO, C, 100, 1, "c", 1), Evento(0, 1, 9, ev.RETRATO, V, 101, 1, "v", 2),
               Evento(1, 0, 11, ev.RETRATO_FIM)]
    assert [(e.indice, e.recebido_ns, e.qualificado) for e in de.estados(eventos)] == [(0, 9, False), (1, 11, True)]


# ---- pregao -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("utc,dentro", [
    ("2026-10-27 13:29:59", False), ("2026-10-27 13:30:00", True), ("2026-10-27 19:59:59", True),
    ("2026-10-27 20:00:00", False), ("2026-10-31 15:00:00", False),           # sabado
    ("2026-11-02 13:30:00", False), ("2026-11-02 14:30:00", True),           # depois do fim do horario de verao
    ("2026-11-02 20:59:59", True), ("2026-11-02 21:00:00", False),
])
def test_pregao_regular_em_nova_york_com_a_troca_de_horario(utc, dentro):
    assert de.no_pregao(de.hora_de_nova_york(utc_ns(utc))) is dentro


def test_sem_base_de_fusos_a_calibracao_para(monkeypatch):
    import zoneinfo
    monkeypatch.setattr(de, "_FUSO", None)
    monkeypatch.setattr(zoneinfo, "ZoneInfo", lambda nome: (_ for _ in ()).throw(zoneinfo.ZoneInfoNotFoundError(nome)))
    with pytest.raises(de.DesequilibrioError, match="fuso_horario_indisponivel"):
        de.fuso()


# ---- histograma -------------------------------------------------------------------------------------

def test_percentil_pelo_posto_mais_proximo():
    h = de.Histograma()
    for d in [-0.5] * 5 + [0.0] * 90 + [0.5] * 5:
        h.adicionar(d)
    assert (h.n, h.percentil(5), h.percentil(5.01), h.percentil(95), h.percentil(96), h.percentil(100)) == (
        100, -0.5, 0.0, 0.0, 0.5, 0.5)
    assert h.percentil(0) == -0.5


def test_histograma_arredonda_a_quatro_casas_e_vazio_da_none():
    h = de.Histograma()
    for d in (0.12344, 0.12346, 1 / 3):
        h.adicionar(d)
    assert sorted(h.contagem) == [1234, 1235, 3333]
    assert de.Histograma().percentil(50) is None


# ---- calibracao -------------------------------------------------------------------------------------

def test_calibracao_usa_so_quadros_qualificados_no_pregao_da_janela(tmp_path):
    cenario = si.gerar(4, 300)
    dentro = gravar_em(cenario, tmp_path, utc_ns("2026-10-27 14:00:00"), "dentro")
    r = de.calibrar([dentro])
    lista = [e for e in de.estados_da_captura(dentro) if e.qualificado and e.d is not None]
    assert r["quadros_usados"] == len(lista) > 0 and r["por_dia"] == {"2026-10-27": len(lista)}
    assert r["p5"] <= r["p95"] and r["janela"] == ["2026-10-26", "2026-11-06"] and r["recusadas"] == []
    assert r["fomc"] == ["2026-10-28", "2026-12-09"]
    assert r["descartados"] == {"nao_qualificado": sum(1 for e in de.estados_da_captura(dentro) if not e.qualificado)}
    esperado = de.Histograma()
    for e in lista:
        esperado.adicionar(e.d)
    assert (r["p5"], r["p95"]) == (esperado.percentil(5), esperado.percentil(95))


def test_calibracao_nunca_usa_a_janela_de_teste_nem_fora_do_pregao(tmp_path):
    cenario = si.gerar(4, 100)
    teste = gravar_em(cenario, tmp_path, utc_ns("2026-11-10 15:00:00"), "teste")
    madrugada = gravar_em(cenario, tmp_path, utc_ns("2026-10-27 12:00:00"), "madrugada")
    r = de.calibrar([teste, madrugada])
    quadros = len(list(de.estados_da_captura(teste)))
    assert r["quadros_usados"] == 0 and r["p5"] is None and r["p95"] is None and r["por_dia"] == {}
    assert r["descartados"] == {"fora_da_janela": quadros, "fora_do_pregao": quadros}


def test_janela_explicita_e_lado_vazio(tmp_path):
    livro_um_lado = [(116, si.quadro_retrato("C", 25.0, [(1, 1, "c")])), (116, si.quadro_retrato_fim())]
    gravador = Gravador(tmp_path, identidade={"system_name": si.SISTEMA, "requested_symbol": "S", "exchange": "Q"})
    for i, (template, dados) in enumerate(livro_um_lado):
        gravador(dados, template, utc_ns("2026-10-23 18:00:00") + i)
    gravador.fechar({"referencia": {"min_qprice_change": 0.25}}, fechamento_normal=True)
    r = de.calibrar([gravador.pasta], janela=(date(2026, 10, 23), date(2026, 10, 23)))
    assert r["descartados"] == {"nao_qualificado": 1, "sem_os_dois_lados": 1} and r["quadros_usados"] == 0


def test_captura_recusada_e_listada_e_a_cli_devolve_2(tmp_path, capsys):
    cenario = si.gerar(4, 300, base=43210)
    boa = gravar_em(cenario, tmp_path, utc_ns("2026-10-28 15:00:00"), "boa")
    ruim = gravar_em(cenario, tmp_path, utc_ns("2026-10-28 15:00:00"), "ruim")
    (ruim / "manifest.json").unlink()
    assert de.main([str(ruim), str(boa), str(boa / "manifest.json")]) == 2
    saida = capsys.readouterr().out
    assert "recusadas=['ruim: captura_nao_verificada:manifesto_ausente']" in saida and "capturas=1" in saida
    assert "P5=" in saida and "4321" not in saida
    assert de.main(["--json", str(boa)]) == 0
    assert '"esquema": "qhos2.calibracao.v1"' in capsys.readouterr().out


def test_captura_recusada_no_meio_nao_deixa_nada_na_calibracao(tmp_path):
    cenario = si.gerar(4, 300)
    boa = gravar_em(cenario, tmp_path, utc_ns("2026-10-28 15:00:00"), "boa")
    quadro_ruim = (160, si.quadro_livro([(1, "C", 6850.1, 1, 9, "z")], si.INICIO_NS))
    ruim = gravar_em(si.Cenario(4, cenario.tick, cenario.quadros + [quadro_ruim]), tmp_path,
                     utc_ns("2026-10-28 15:00:00"), "ruim")
    so_a_boa, as_duas = de.calibrar([boa]), de.calibrar([ruim, boa])
    assert as_duas["recusadas"] == [f"ruim: preco_fora_da_grade:{len(cenario.quadros)}"]
    for chave in ("quadros_usados", "p5", "p95", "por_dia", "descartados", "capturas"):
        assert as_duas[chave] == so_a_boa[chave], chave


# ---- FOMC (C1, 28/10) --------------------------------------------------------------------------------

def estado_em(utc, d=0.1, qualificado=True):
    return de.Estado(0, utc_ns(utc), qualificado, 100, 101, 10, 10, d)


@pytest.mark.parametrize("utc,destino", [
    ("2026-10-28 17:54:59", "usado"),            # 13:54:59 de Nova York (EDT), dia de FOMC: ainda conta
    ("2026-10-28 17:55:00", "fomc"),             # 13:55:00
    ("2026-10-28 19:59:59", "fomc"),             # 15:59:59
    ("2026-10-28 20:00:00", "fora_do_pregao"),   # 16:00:00
    ("2026-10-29 17:55:00", "usado"),            # dia sem FOMC
    ("2026-12-09 18:54:59", "usado"),            # 13:54:59 de Nova York (EST)
    ("2026-12-09 18:55:00", "fomc"),
])
def test_calibracao_exclui_a_faixa_do_fomc_e_conta(monkeypatch, utc, destino):
    monkeypatch.setattr(de, "estados_da_captura", lambda pasta, niveis=de.NIVEIS: iter([estado_em(utc)]))
    r = de.calibrar(["x"], janela=(date(2026, 10, 26), date(2026, 12, 31)))
    if destino == "usado":
        assert r["quadros_usados"] == 1 and r["descartados"] == {}
    else:
        assert r["quadros_usados"] == 0 and r["descartados"] == {destino: 1}
    assert r["fomc"] == ["2026-10-28", "2026-12-09"]


# ---- lacuna de entrega (N6, 28/10) ------------------------------------------------------------------

def calibrar_estados(monkeypatch, *utcs):
    lista = [estado_em(u) for u in utcs]
    monkeypatch.setattr(de, "estados_da_captura", lambda pasta, niveis=de.NIVEIS: iter(lista))
    return de.calibrar(["x"])


def test_depois_de_uma_lacuna_maior_que_60s_no_pregao_nada_entra_na_calibracao(monkeypatch):
    r = calibrar_estados(monkeypatch, "2026-10-29 14:00:00", "2026-10-29 14:00:30", "2026-10-29 14:01:31",
                         "2026-10-29 14:02:00", "2026-10-29 19:00:00")
    assert r["quadros_usados"] == 2 and r["descartados"] == {"depois_da_lacuna": 3}
    assert r["por_dia"] == {"2026-10-29": 2}


def test_lacuna_de_exatos_60s_ou_fora_do_pregao_nao_corta(monkeypatch):
    r = calibrar_estados(monkeypatch, "2026-10-29 12:00:00", "2026-10-29 13:00:00", "2026-10-29 13:29:00",
                         "2026-10-29 13:30:00", "2026-10-29 13:31:00", "2026-10-29 13:32:00")
    assert r["quadros_usados"] == 3 and r["descartados"] == {"fora_do_pregao": 3}


def test_lacuna_que_toca_a_abertura_corta(monkeypatch):
    r = calibrar_estados(monkeypatch, "2026-10-29 13:29:00", "2026-10-29 13:31:00", "2026-10-29 13:32:00")
    assert r["quadros_usados"] == 0 and r["descartados"] == {"fora_do_pregao": 1, "depois_da_lacuna": 2}


def test_faixa_do_fomc_e_compartilhada_com_o_avaliador_e_datas_invalidas_param():
    from qhos2 import avaliador as av
    momento = de.hora_de_nova_york(utc_ns("2026-10-28 17:55:00"))
    assert de.na_faixa_do_fomc(momento) and not de.na_faixa_do_fomc(momento, ())
    assert av.FOMC_INICIO is de.FOMC_INICIO
    with pytest.raises(de.DesequilibrioError, match="fomc_invalido"):
        de.calibrar([], fomc=("2026-10-28",))


def test_cli_sem_fuso_devolve_2(tmp_path, monkeypatch, capsys):
    pasta = gravar_em(si.gerar(1, 5), tmp_path, utc_ns("2026-10-28 15:00:00"), "x")
    monkeypatch.setattr(de, "fuso", lambda: (_ for _ in ()).throw(de.DesequilibrioError("fuso_horario_indisponivel")))
    assert de.main([str(pasta)]) == 2
    assert "calibracao impossivel: fuso_horario_indisponivel" in capsys.readouterr().out


# ---- QHOS-AUD-244: regimes de horario alem de 1/11/2026, pela base de fusos instalada -------------------------

def _versao_da_base_de_fusos():
    for caminho in ("/usr/share/zoneinfo/tzdata.zi", "/usr/lib/zoneinfo/tzdata.zi"):
        try:
            with open(caminho, encoding="ascii", errors="replace") as f:
                primeira = f.readline().strip()
        except OSError:
            continue
        if primeira.startswith("# version"):
            return "sistema " + primeira.split()[-1]
    try:
        import tzdata
        return "pip tzdata " + tzdata.IANA_VERSION
    except Exception:
        return "desconhecida"


# (dia util, deslocamento de Nova York em horas) pela regra dos EUA desde 2007: 2.o domingo de marco e 1.o de novembro
REGIMES = [(date(2026, 10, 30), -4), (date(2026, 11, 2), -5),        # fim do horario de verao em 1/11/2026
           (date(2027, 3, 12), -5), (date(2027, 3, 15), -4),         # inicio em 14/3/2027 (antes de PRAZO_FINAL)
           (date(2027, 11, 5), -4), (date(2027, 11, 8), -5)]         # fim em 7/11/2027


@pytest.mark.parametrize("dia, deslocamento_h", REGIMES, ids=[d.isoformat() for d, _ in REGIMES])
def test_pregao_regular_atravessa_os_regimes_de_horario_ate_2027(dia, deslocamento_h, record_property):
    record_property("base_de_fusos", _versao_da_base_de_fusos())
    instalado = datetime(dia.year, dia.month, dia.day, 12, tzinfo=ZoneInfo("America/New_York")).utcoffset()
    assert instalado == timedelta(hours=deslocamento_h), f"base de fusos {_versao_da_base_de_fusos()} diverge da regra"

    def utc(hh, mm, ss=0):       # hora de Nova York -> instante UTC em ns, pelo deslocamento da regra
        local = datetime(dia.year, dia.month, dia.day, hh, mm, ss, tzinfo=timezone.utc)
        return (int(local.timestamp()) - deslocamento_h * 3600) * 10 ** 9
    assert de.no_pregao(de.hora_de_nova_york(utc(9, 29, 59))) is False
    assert de.no_pregao(de.hora_de_nova_york(utc(9, 30))) is True
    assert de.no_pregao(de.hora_de_nova_york(utc(15, 59, 59))) is True
    assert de.no_pregao(de.hora_de_nova_york(utc(16, 0))) is False
    assert de.hora_de_nova_york(utc(13, 55)).time() == de.FOMC_INICIO
    assert de.hora_de_nova_york(utc(9, 30)).date() == dia


# ---- QHOS-AUD-251: percentil pelo posto mais proximo em n pequeno ------------------------------------------

def test_percentil_posto_mais_proximo_em_n_pequeno():
    um = de.Histograma()
    um.adicionar(0.3)
    assert um.percentil(5) == um.percentil(50) == um.percentil(95) == 0.3        # n = 1: posto 1 para todo p
    valores = [round(-0.9 + 0.1 * i, 4) for i in range(19)]                        # -0,9 .. 0,9, 19 distintos
    dezenove = de.Histograma()
    for d in valores:
        dezenove.adicionar(d)
    assert (dezenove.percentil(5), dezenove.percentil(95)) == (-0.9, 0.9)         # postos ceil(0,95) = 1 e ceil(18,05) = 19
    vinte = de.Histograma()
    for d in valores + [1.0]:
        vinte.adicionar(d)
    assert (vinte.percentil(5), vinte.percentil(95)) == (-0.9, 0.9)               # postos 1 e 19: o 20.o (1,0) fica fora
    assert (vinte.percentil(0), vinte.percentil(100)) == (-0.9, 1.0)
    assert (vinte.n, dezenove.n, um.n) == (20, 19, 1)


# ---- QHOS-AUD-237: golden do gerador sintetico (gabarito dos ensaios) ------------------------------------------

GOLDEN_GERADOR_7_300 = ("46a43ae365ef5763d59b954ebf322ad69752820fc42e554e0b741a07fcabb837",     # bytes dos quadros
                        "5adb764674150bb2e6bd6cab2386750915c4a67ea34c1e15c505d3f5934e7d91")     # verdades; 36f0e1f


def test_gerador_sintetico_bate_com_o_golden():
    """Golden de IMPLEMENTACAO: `si.gerar(7, 300)` byte a byte. Nao afirma correcao (test_sintetico.py afirma);
    detecta mudanca silenciosa do gerador, que e o gabarito dos ensaios de calibracao."""
    cenario = si.gerar(7, 300)
    quadros = hashlib.sha256(b"".join(dados for _, dados in cenario.quadros)).hexdigest()
    verdades = hashlib.sha256(json.dumps(cenario.verdades, sort_keys=True).encode()).hexdigest()
    assert (quadros, verdades) == GOLDEN_GERADOR_7_300, (quadros, verdades)
