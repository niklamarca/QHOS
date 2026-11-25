"""Sobreposicao nunca vira soma de volume; exibicao limitada informa sua cobertura."""
import base64
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from qhos2 import coletor as co
from qhos2 import historico as hi
from qhos2 import leitura as le
from qhos2 import sintetico as si
from qhos2.gravador import Gravador

from test_integridade_heranca import IDENTIDADE, gravar as gravar_caso, identificar, leitor as leitor_es, retrato


def historia(tmp_path, agora=10**9):
    h = hi.Historico(tmp_path / "inexistente", tmp_path / "sem-cache", tick=.25,
                     simbolo="ESZ6", bolsa="CME", relogio=lambda: agora)
    antiga = hi.Barras()
    antiga.minutos = {60: [40, 40, 40, 40, 99], 120: [80, 80, 80, 80, 99]}
    h.prontas["anterior"] = antiga
    viva = hi.Barras()
    viva.minutos = {120: [100, 104, 96, 102, 7], 180: [102, 108, 100, 108, 3]}
    return h, viva


@pytest.mark.parametrize("quadro", hi.QUADROS)
def test_recorte_so_captura_seguida_nunca_soma_ou_deduplica(tmp_path, quadro):
    h, viva = historia(tmp_path)
    with pytest.raises(hi.HistoricoError, match="capturas_sobrepostas"):
        h.candles(quadro, 100, viva)
    r = h.candles_com_cobertura(quadro, 100, viva, captura_id="selecionada")
    assert sum(c["volume"] for c in r["candles"]) == 10
    assert all(c["partial"] is True for c in r["candles"])
    assert r["candles"][0]["open"] == 25 and r["candles"][-1]["close"] == 27
    assert r["coverage"] == {"kind": "selected_capture_only", "reason": "overlapping_captures",
                              "partial": True, "periodComplete": False, "captureId": "selecionada"}
    assert h.prontas["anterior"].minutos[120][-1] == 99
    assert not h.cache.exists()  # nenhum cache ou fonte alterado pelo recorte


def test_sem_conflito_preserva_resultado_e_limite(tmp_path):
    h, viva = historia(tmp_path)
    h.prontas = {}
    assert h.candles_com_cobertura("1m", 1, viva) == {"candles": h.candles("1m", 1, viva)}
    h, viva = historia(tmp_path)
    assert len(h.candles_com_cobertura("1m", 1, viva, captura_id="selecionada")["candles"]) == 1
    for quadro, limite in (("2m", 1), ("1m", 0), ("1m", 10001)):
        with pytest.raises(hi.HistoricoError):
            h.candles_com_cobertura(quadro, limite, viva, captura_id="selecionada")


@pytest.mark.parametrize("viva_tipo", ["ausente", "vazia", "futura", "escondida", "sem_identidade"])
def test_conflito_sem_recorte_nunca_inventa_dados(tmp_path, viva_tipo):
    h, viva = historia(tmp_path, hi.LIBERADO_EM_S - 1)
    h.prontas["duplicada"] = h.prontas["anterior"]
    if viva_tipo == "ausente":
        viva = None
    elif viva_tipo == "vazia":
        viva.minutos = {}
    elif viva_tipo == "futura":
        viva.minutos = {hi.LIBERADO_EM_S + 600: [4, 4, 4, 4, 1]}
    elif viva_tipo == "escondida":
        viva.minutos = {hi.OCULTO_DE_S + 600: [4, 4, 4, 4, 1]}
    with pytest.raises(hi.HistoricoError, match="sobreposicao_sem_recorte_disponivel"):
        h.candles_com_cobertura("1D", 100, viva,
                               captura_id=None if viva_tipo == "sem_identidade" else "selecionada")


def test_recorte_respeita_futuro_e_janela_escondida(tmp_path):
    h, viva = historia(tmp_path, hi.LIBERADO_EM_S - 1)
    viva.minutos[hi.OCULTO_DE_S + 600] = [4, 4, 4, 4, 300]
    viva.minutos[hi.LIBERADO_EM_S + 600] = [4, 4, 4, 4, 400]
    r = h.candles_com_cobertura("1D", 100, viva, captura_id="selecionada")
    assert sum(c["volume"] for c in r["candles"]) == 10


def captura_seguida(raiz, nome, minutos):
    """Captura real (retrato + negocios) cujas barras de minuto sao exatamente `minutos`; fica aberta, como a do dia."""
    g = Gravador(raiz, identidade=IDENTIDADE, captura_id=nome)
    quadros = list(retrato())
    for minuto, (abertura, maxima, minima, fechamento, volume) in sorted(minutos.items()):
        if fechamento == maxima:
            precos = [abertura, minima, maxima]
        elif fechamento == minima:
            precos = [abertura, maxima, minima]
        else:
            precos = [abertura, maxima, minima, fechamento]
        quantidades = [1] * (len(precos) - 1) + [volume - len(precos) + 1]
        for k, (preco, quantidade) in enumerate(zip(precos, quantidades)):
            carimbo = (minuto + k) * 10 ** 9
            quadros.append((150, identificar(si.quadro_negocio(preco * .25, quantidade, "C", "c", "a", carimbo))))
    for i, (template, dados) in enumerate(quadros):
        g(dados, template, si.INICIO_NS + i)
    return g.pasta


# a captura real e o Leitor real dependem da identidade por inode (os.O_NOFOLLOW em operacao.ler_identidade_captura):
# no Windows nem o Gravador nem qhos2.sintetico.gravar conseguem grava-la, e nao ha outro caminho suportado la
@pytest.mark.skipif(sys.platform != "linux", reason="Gravador exige identidade por inode (Linux); coberto no Linux")
def test_coletor_expoe_cobertura_e_recusa_falha_ou_sem_captura(tmp_path):
    h, viva = historia(tmp_path)
    # a captura seguida e real e lida por um Leitor real (QHOS-AUD-072, 117): os negocios gravados dao as barras de `viva`
    seguida = le.Leitor(captura_seguida(tmp_path / "capturas", "selecionada", viva.minutos),
                        tick=.25, simbolo="ESZ6", bolsa="CME")
    c = co.Coletor(token="a" * 43, leitor=seguida, historico=h)
    assert c.traduzir(seguida.avancar(), True) and seguida.estado == le.LENDO
    pedido = {"type": "request", "id": "qhos-1", "method": "history.candles",
              "params": {"instrumentId": co.ESZ6["id"], "timeframe": "1D", "limit": 240}}
    esperado = h.candles_com_cobertura("1D", 240, viva, captura_id="selecionada")
    r = c.responder(pedido)
    assert r["ok"] and r["result"]["coverage"] == esperado["coverage"] and r["result"]["coverage"]["captureId"] == "selecionada"
    assert r["result"]["candles"] == esperado["candles"] and r["result"]["candles"][0]["volume"] == 10
    c.leitor = None
    assert c.responder(pedido)["error"]["code"] == "HISTORY_OVERLAP"
    h.prontas["duplicada"] = h.prontas["anterior"]
    falha = leitor_es(gravar_caso(tmp_path / "falha", retrato(("4",))).pasta)       # retrato recusado: Leitor em FALHA
    falha.avancar()
    assert falha.estado == le.FALHA
    c.leitor = falha
    assert c.responder(pedido)["error"]["code"] == "HISTORY_OVERLAP"


def test_ponte_valida_cobertura_e_preserva_aviso(tmp_path):
    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        pytest.skip("Node indisponivel")
    c = co.Coletor(token="a" * 43)
    h, viva = historia(tmp_path)
    c.historico = h
    welcome = c.welcome()
    welcome["capabilities"].append("history") if "history" not in welcome["capabilities"] else None
    welcome["selection"]["instruments"][0]["capabilities"]["history"] = True
    coverage = h.candles_com_cobertura("1D", 100, viva, captura_id="selecionada")
    modulo = Path(__file__).resolve().parents[1] / "terminal/modulos/qhos-bridge.js"
    source = base64.b64encode(modulo.read_bytes()).decode()
    code = "const {createQhosBridge,historyCoverageNotice}=await import('data:text/javascript;base64," + source + "');\n"
    code += "const welcome=" + json.dumps(welcome) + ",payload=" + json.dumps(coverage) + ";\n"
    code += r"""
let sock, request;
class Fake {constructor(){sock=this;this.readyState=1;}send(raw){request=JSON.parse(raw);}close(){}}
const b=createQhosBridge({}, {WebSocketImpl:Fake,setTimeout:()=>0,clearTimeout:()=>{},now:()=>2e12});
const ready=b.connect({url:'ws://127.0.0.1:8766',token:'test'});
sock.onopen();sock.onmessage({data:JSON.stringify(welcome)});await ready;
async function run(result){
 const p=b.loadCandles('rithmic:CME:ESZ6','1D');
 sock.onmessage({data:JSON.stringify({type:'response',id:request.id,ok:true,result:{instrumentId:'rithmic:CME:ESZ6',timeframe:'1D',...result}})});
 try {const rows=await p;return {ok:true,partial:rows.every(r=>r.partial),coverage:rows.coverage,
    notice:historyCoverageNotice(rows.coverage),frozen:rows.coverage?Object.isFrozen(rows.coverage):false};}
 catch{return {ok:false};}
}
const cases=[payload,
 {...payload,coverage:{...payload.coverage,periodComplete:true}},
 {...payload,coverage:{...payload.coverage,reason:'unknown'}},
 {...payload,coverage:{...payload.coverage,captureId:'../private'}},
 {...payload,candles:payload.candles.map(r=>({...r,partial:false}))},
 {...payload,candles:[]},
 {candles:payload.candles.map(({partial,...r})=>r)}];
const out=[];for(const c of cases)out.push(await run(c));b.disconnect();console.log(JSON.stringify(out));
"""
    # Node le e escreve UTF-8; sem encoding, o Windows decodifica pela localidade (QHOS-AUD-005)
    r = subprocess.run([node, "--input-type=module"], input=code, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr[-2000:]
    values = json.loads(r.stdout)
    assert values[0]["ok"] and values[0]["partial"] and values[0]["frozen"]
    assert "Histórico parcial" in values[0]["notice"] and "período inteiro" in values[0]["notice"]
    assert all(v == {"ok": False} for v in values[1:6])
    assert values[6]["ok"] and values[6]["coverage"] is None and values[6]["notice"] == ""


def test_legenda_exibe_limite_de_cobertura_no_grafico():
    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        pytest.skip("Node indisponivel")
    modules = Path(__file__).resolve().parents[1] / "terminal/modulos"
    app = (modules / "app.js").read_text(encoding="utf-8")
    renderer = app[app.index("function renderChartViewState(state){"):app.index("function restoreCandleView()")]
    bridge = base64.b64encode((modules / "qhos-bridge.js").read_bytes()).decode()
    code = "const {historyCoverageNotice}=await import('data:text/javascript;base64," + bridge + "');\n"
    code += r"""
const caption={textContent:''},overview={},$=id=>id==='#chartCoverage'?caption:overview;
let historyCoverage={kind:'selected_capture_only'},replayTimer=null,candles=[{partial:true}];
const num=String,displayTime={dateTime:()=>''};
""" + renderer + r"""
renderChartViewState({totalCandles:1,mode:'candles'});
const partial=caption.textContent;
historyCoverage=null;replayTimer=true;renderChartViewState({totalCandles:1,mode:'candles'});
const replay=caption.textContent;
replayTimer=null;candles=[];renderChartViewState({totalCandles:0,mode:'candles'});
console.log(JSON.stringify({partial,replay,empty:caption.textContent}));
"""
    r = subprocess.run([node, "--input-type=module"], input=code, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr[-2000:]
    out = json.loads(r.stdout)
    assert "Histórico parcial: somente a captura selecionada" in out["partial"]
    assert "outras capturas se sobrepõem" in out["partial"]
    assert "período inteiro" in out["partial"] and "candles parciais" in out["replay"]
    assert "Nenhum candle" in out["empty"] and "selecionada" not in out["empty"]
