"""Campanha de correcao, lote l5c-leitura: terminal (QHOS-AUD-043, 160, 161, 193, 203).

Testes de COMPORTAMENTO no Node (pulados sem Node; no WSL da bancada nao ha Node, no Windows ha v24). Modulo que
importa no Node (`qhos-bridge.js`, `microstructure-ui.js`) e importado inteiro por `data:` URL, como em
`tests/test_terminal.py`. Modulo que depende do DOM no topo (`app.js`, `execution-ui.js`) nao importa: a funcao em
questao e recortada do fonte, linha inteira, e avaliada isolada, com `localStorage` falso. O que nem assim da para
executar fica como caracterizacao estatica, dita como tal. `terminal/` nao muda nesta janela.
"""
import base64
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PASTA = Path(__file__).resolve().parents[1] / "terminal"
MODULOS = PASTA / "modulos"


def _node():
    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        pytest.skip("node ausente: a regra roda no navegador")
    return node


def rodar_node(codigo: str):
    # pelo stdin e em UTF-8 (QHOS-AUD-005/006): o modulo inteiro passa do limite da linha de comando do Windows
    r = subprocess.run([_node(), "--input-type=module"], input=codigo, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads(r.stdout)


_DEPENDENCIA = re.compile(r"(from\s*)'qhos-module/([a-z0-9-]+\.js)'")


def _url_do_modulo(nome: str, vistos=()) -> str:
    """`data:` URL do modulo com as importacoes `qhos-module/...` (resolvidas pelo importmap no navegador) trocadas
    pelas `data:` URLs das dependencias, recursivamente: o Node nao tem o importmap do index.html."""
    assert nome not in vistos, ("ciclo de importacao", vistos, nome)
    fonte = (MODULOS / nome).read_text(encoding="utf-8")
    fonte = _DEPENDENCIA.sub(lambda m: f"{m.group(1)}'{_url_do_modulo(m.group(2), (*vistos, nome))}'", fonte)
    return "data:text/javascript;base64," + base64.b64encode(fonte.encode("utf-8")).decode()


def importar(nome: str) -> str:
    return f"await import('{_url_do_modulo(nome)}')"


def trecho(nome: str, padrao: str, flags=re.M) -> str:
    """Um unico trecho do fonte (modulo que nao importa no Node), para avaliar isolado."""
    achados = re.findall(padrao, (MODULOS / nome).read_text(encoding="utf-8"), flags)
    assert len(achados) == 1, (nome, padrao, len(achados))
    return achados[0]


# ---- QHOS-AUD-043: comportamento no lugar de substrings do JS minificado --------------------------------------------
# ATENCAO para a integracao: estes testes so rodam com Node (pulam sem ele). Enquanto Node nao for obrigatorio na
# bancada/CI, as substrings de tests/test_terminal.py NAO devem ser removidas: sao a unica protecao sem Node.
# Substitui, em tests/test_terminal.py::test_estudos_do_es_desligados_ate_21_11, a asserção
#   `"const ES_LIBERADO_EM=Date.UTC(2026,10,21,3);" in fonte`.
# Os tres pontos de `throw new Error(AVISO_ES)` e `canExperiment` ficam dentro de metodos do painel que exigem DOM:
# continuam estaticos ate a integracao decidir.

def test_es_da_rithmic_fica_bloqueado_ate_21_11_00h_de_brasilia_e_so_ele():
    consts = "\n".join(trecho("microstructure-ui.js", rf"^const {nome}=.*;$")
                       for nome in ("providerOf", "ES_LIBERADO_EM", "esBloqueado"))
    saida = rodar_node(consts + r"""
const casos = {ESZ6: {instrument: {provider: 'rithmic', symbol: 'ESZ6'}}, ESH7: {instrument: {provider: 'rithmic', symbol: 'ESH7'}},
  ESZ26: {instrument: {provider: 'rithmic', symbol: 'ESZ26'}}, NQZ6: {instrument: {provider: 'rithmic', symbol: 'NQZ6'}},
  ES_na_coinbase: {instrument: {provider: 'coinbase', symbol: 'ESZ6'}}, BTC: {symbol: 'BTCUSD'},
  so_id_sem_instrumento: {symbol: 'rithmic:CME:ESZ6'}};
const saida = {liberado_iso: new Date(ES_LIBERADO_EM).toISOString()};
for (const [quando, t] of [['antes', ES_LIBERADO_EM - 1], ['no_instante', ES_LIBERADO_EM], ['depois', ES_LIBERADO_EM + 1]]) {
  Date.now = () => t;
  saida[quando] = Object.fromEntries(Object.entries(casos).map(([k, c]) => [k, esBloqueado(c)]));
}
console.log(JSON.stringify(saida));
""")
    assert saida["liberado_iso"] == "2026-11-21T03:00:00.000Z"                      # 21/11 00h de Brasilia
    assert saida["antes"] == {"ESZ6": True, "ESH7": True, "ESZ26": True, "NQZ6": False, "ES_na_coinbase": False,
                              "BTC": False, "so_id_sem_instrumento": False}
    assert saida["no_instante"] == saida["depois"] == {k: False for k in saida["antes"]}


# Substitui, em tests/test_terminal.py::test_percurso_na_tela_nunca_fica_verde_sozinho, as asserções
#   `"const PERCURSO_VELHO_MS=5000;" in fonte` e
#   `"PERCURSO_ESTADOS[old?'desconhecido':stage.state]??PERCURSO_ESTADOS.desconhecido" in fonte`.
# A de app.js (`pipeline:!replaying&&currentProvider==='rithmic'?...`) exige o app inteiro: continua estatica.

def test_percurso_na_tela_fica_desconhecido_quando_velho_ou_inventado_e_some_fora_da_rithmic():
    saida = rodar_node(f"const {{MicrostructurePanel}} = {importar('microstructure-ui.js')};" + r"""
const render = MicrostructurePanel.prototype.renderRithmicPath;
const agora = 1700000000000; Date.now = () => agora;
const estagio = (id, state) => ({id, state, reason: 'motivo', since: agora - 60000, fields: [{label: 'a', value: 'b'}]});
const estagios = () => [estagio('rithmic', 'ok'), estagio('sessao', 'atencao'), estagio('gravacao', 'falha'),
  estagio('livro', 'verde'), estagio('leitura', 'desconhecido'), estagio('coletor', 'ok')];
const pipeline = receivedAt => ({time: agora, receivedAt, stages: estagios()});
function caso(context) {
  const box = {hidden: false, innerHTML: ''};
  render.call({context, $: () => box});
  return {hidden: box.hidden, estados: [...box.innerHTML.matchAll(/data-estado="([^"]*)"/g)].map(m => m[1]),
    cores: [...box.innerHTML.matchAll(/background:(#[0-9a-f]{6})/g)].map(m => m[1]),
    velho: box.innerHTML.includes('Sem atualização há'), sem_percurso: box.innerHTML.includes('Sem percurso')};
}
const rithmic = {instrument: {provider: 'rithmic', symbol: 'ESZ6'}};
console.log(JSON.stringify({
  fresco: caso({...rithmic, pipeline: pipeline(agora - 4999)}),
  no_limite: caso({...rithmic, pipeline: pipeline(agora - 5000)}),
  velho: caso({...rithmic, pipeline: pipeline(agora - 5001)}),
  sem_recebido_em: caso({...rithmic, pipeline: pipeline(undefined)}),
  sem_percurso: caso({...rithmic, pipeline: null}),
  coinbase: caso({symbol: 'BTCUSD', pipeline: pipeline(agora)}),
  replay: caso({...rithmic, replaying: true, pipeline: pipeline(agora)}),
}));
""")
    verde, amarelo, vermelho, cinza = "#79c27a", "#e6b969", "#e2675f", "#607080"
    for nome in ("fresco", "no_limite"):
        assert saida[nome]["estados"] == ["ok", "atencao", "falha", "verde", "desconhecido", "ok"], nome
        assert saida[nome]["cores"] == [verde, amarelo, vermelho, cinza, cinza, verde], nome   # 'verde' inventado: cinza
        assert not saida[nome]["velho"] and not saida[nome]["hidden"]
    for nome in ("velho", "sem_recebido_em"):
        assert saida[nome]["estados"] == ["desconhecido"] * 6 and saida[nome]["cores"] == [cinza] * 6, nome
        assert saida[nome]["velho"] and not saida[nome]["hidden"]
    assert saida["sem_percurso"]["sem_percurso"] and saida["sem_percurso"]["estados"] == []
    assert saida["coinbase"]["hidden"] and saida["replay"]["hidden"]


# ---- QHOS-AUD-160: historico inteiro rejeitado se um candle esta a frente do relogio do navegador -----------------

def _welcome_com_historico():
    pytest.importorskip("websockets")
    from qhos2 import coletor as co
    welcome = co.Coletor(token="a" * 43).welcome()
    if "history" not in welcome["capabilities"]:
        welcome["capabilities"].append("history")
    welcome["selection"]["instruments"][0]["capabilities"]["history"] = True
    return welcome


T = 1_699_999_980                      # multiplo de 60: inicio de um minuto
AGORA_MS = (T - 5) * 1000              # relogio do navegador 5 s atras do inicio do minuto corrente do coletor


def _candles(*tempos):
    return [{"time": t, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 1} for t in tempos]


def _pedir_historico(resultados):
    codigo = f"const {{createQhosBridge}} = {importar('qhos-bridge.js')};\n"
    codigo += f"const welcome = {json.dumps(_welcome_com_historico())}, resultados = {json.dumps(resultados)};\n"
    codigo += r"""
let sock, request;
class Fake {constructor(){sock=this;this.readyState=1;}send(raw){request=JSON.parse(raw);}close(){}}
const b = createQhosBridge({}, {WebSocketImpl: Fake, setTimeout: () => 0, clearTimeout: () => {}, now: () => """ + str(AGORA_MS) + r"""});
const ready = b.connect({url: 'ws://127.0.0.1:8766', token: 'test'});
sock.onopen(); sock.onmessage({data: JSON.stringify(welcome)}); await ready;
async function run(candles) {
  const p = b.loadCandles('rithmic:CME:ESZ6', '1m');
  sock.onmessage({data: JSON.stringify({type: 'response', id: request.id, ok: true,
    result: {instrumentId: 'rithmic:CME:ESZ6', timeframe: '1m', candles}})});
  try { const rows = await p; return {ok: true, tempos: rows.map(r => r.time), parciais: rows.map(r => r.partial)}; }
  catch (erro) { return {ok: false, erro: erro.message}; }
}
const saida = {}; for (const [nome, candles] of Object.entries(resultados)) saida[nome] = await run(candles);
b.disconnect(); console.log(JSON.stringify(saida));
"""
    return rodar_node(codigo)


def test_historico_todo_no_passado_do_navegador_e_aceito_e_o_ultimo_minuto_e_parcial():
    saida = _pedir_historico({"passado": _candles(T - 180, T - 120, T - 60)})
    assert saida["passado"] == {"ok": True, "tempos": [T - 180, T - 120, T - 60], "parciais": [False, False, True]}


def test_caracterizacao_candle_a_frente_do_relogio_do_navegador_derruba_o_historico_inteiro():
    """QHOS-AUD-160, caracterizacao: qhos-bridge.js:563 recusa o historico INTEIRO quando um `row.time` passa de
    now()/1000 do navegador (o coletor filtra pelo relogio dele, historico.py:170), com mensagem generica. Recusar o
    futuro e regra da ponte; aceitar como parcial ou citar o relogio e decisao do autor (sem patch proposto)."""
    saida = _pedir_historico({"adiantado": _candles(T - 120, T - 60, T)})
    assert saida["adiantado"] == {"ok": False, "erro": "O coletor retornou candles inválidos."}


# ---- QHOS-AUD-161: load() do app engole erro de localStorage/JSON e o boot regrava o default ----------------------

def _carregar_load():
    return trecho("app.js", r"^const load=\(k,f\)=>.*$")


def _rodar_load(modo):
    return rodar_node(r"""
const avisos = []; const toast = (...a) => avisos.push(a);
const gravacoes = [];
globalThis.localStorage = {
  getItem(k) { if (MODO === 'bloqueado') throw new Error('SecurityError'); return MODO === 'corrompido' ? '{broken' : null; },
  setItem(k, v) { gravacoes.push(k); }};
""".replace("MODO", json.dumps(modo)) + _carregar_load() + r"""
let valor, levantou = false;
try { valor = load('account', {marca: 'default'}); } catch { levantou = true; }
await new Promise(r => setTimeout(r, 0));   // um aviso adiado (microtarefa) ainda conta como sinal
console.log(JSON.stringify({default: JSON.stringify(valor) === JSON.stringify({marca: 'default'}), levantou, avisos: avisos.length, gravacoes}));
""")


def test_chave_ausente_devolve_o_default_sem_aviso_nem_gravacao():
    assert _rodar_load("ausente") == {"default": True, "levantou": False, "avisos": 0, "gravacoes": []}


@pytest.mark.parametrize("modo", ["corrompido", "bloqueado"])
def test_caracterizacao_leitura_que_falha_devolve_o_default_sem_sinal(modo):
    """QHOS-AUD-161, caracterizacao: app.js:36 `catch{return f;}` devolve o default sem aviso para JSON corrompido ou
    localStorage bloqueado; so save() avisa (app.js:37). Somado ao boot (teste estatico abaixo), a conta e regravada.
    Avisar e nao regravar e decisao de UX do autor (sem patch proposto)."""
    assert _rodar_load(modo) == {"default": True, "levantou": False, "avisos": 0, "gravacoes": []}


def test_caracterizacao_estatica_o_boot_regrava_a_conta_sem_saber_se_a_leitura_falhou():
    """app.js nao importa no Node (DOM no topo): o encadeamento load -> persistAccount no boot e lido no fonte."""
    app = (MODULOS / "app.js").read_text(encoding="utf-8")
    assert re.search(r"^const rawAccount=load\('account',\{\}\);$", app, re.M)                       # :73
    assert re.search(r"^const persistAccount=\(\)=>\{save\('account',account\);", app, re.M)          # :94
    assert re.search(r"^account\.schemaVersion=3;[^\n]{0,60}persistAccount\(\);", app, re.M)         # :904, no topo do modulo
    assert re.search(r"^const save=\(k,v\)=>\{try\{localStorage\.setItem\(`qhos-v2-\$\{k\}`,JSON\.stringify\(v\)\);\}catch\{toast\(", app, re.M)


# ---- QHOS-AUD-193: extrair_modulos.py --conferir nao ve `scopes` nem um segundo importmap ---------------------------

def _extrair_modulos():
    spec = importlib.util.spec_from_file_location("extrair_modulos_sob_teste", PASTA / "extrair_modulos.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


EMBUTIDO = "data:text/javascript;base64," + base64.b64encode(b"export const x = 1;\n").decode()
EXTERNO = "https://exemplo.invalid/market.js"


def _html(*blocos):
    return "<!doctype html><html><head>" + "".join(blocos) + "</head><body></body></html>"


def _importmap(mapa, atributos='type="importmap"'):
    return f"<script {atributos}>{json.dumps(mapa)}</script>"


def test_importmap_embutido_e_aceito_e_importacao_externa_direta_e_recusada():
    em = _extrair_modulos()
    assert em.modulos_do_html(_html(_importmap({"imports": {"qhos-module/a.js": EMBUTIDO}}))) == {"a.js": b"export const x = 1;\n"}
    with pytest.raises(em.TerminalError, match="externa"):
        em.modulos_do_html(_html(_importmap({"imports": {"qhos-module/a.js": EXTERNO}})))


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-193: extrair_modulos.modulos_do_html le so `imports` (extrair_modulos.py:32); "
                                       "`scopes` apontando para fora do arquivo passa na conferencia")
def test_scopes_para_fora_do_arquivo_e_recusado():
    em = _extrair_modulos()
    mapa = {"imports": {"qhos-module/a.js": EMBUTIDO}, "scopes": {"data:": {"qhos-module/market.js": EXTERNO}}}
    with pytest.raises(em.TerminalError):
        em.modulos_do_html(_html(_importmap(mapa)))


@pytest.mark.parametrize("atributos", ['type="importmap" data-x="1"', "type='importmap'", "type=importmap"],
                         ids=["atributo_extra", "aspas_simples", "sem_aspas"])
@pytest.mark.xfail(strict=True, reason="QHOS-AUD-193: o regex exige o texto exato `<script type=\"importmap\">` "
                                       "(extrair_modulos.py:29); um segundo importmap com outra grafia nao e contado")
def test_segundo_importmap_com_outra_grafia_e_recusado(atributos):
    em = _extrair_modulos()
    bom = _importmap({"imports": {"qhos-module/a.js": EMBUTIDO}})
    outro = _importmap({"imports": {"qhos-module/b.js": EXTERNO}}, atributos)
    with pytest.raises(em.TerminalError):
        em.modulos_do_html(_html(bom, outro))


# ---- QHOS-AUD-203: `catch {}` nos modulos nao examinados ---------------------------------------------------------------

CATCH_VAZIO = re.compile(r"catch\s*\{\s*\}")
# Censo em 36f0e1f (todas as ocorrencias lidas): app.js:166 (URL invalida -> rejectConfiguration, nao silencioso);
# execution-ui.js:18 (readPreferences: default silencioso, SEM regravacao no boot); feed.js:57 (detalhe de erro HTTP)
# e :202 (fechar socket antigo); qhos-bridge.js:189 (fechar socket antigo). Os demais `catch` tem corpo.
CENSO = {"app.js": 1, "execution-ui.js": 1, "feed.js": 2, "qhos-bridge.js": 1}


def test_censo_dos_catch_vazios_nos_modulos():
    achados = {arq.name: len(CATCH_VAZIO.findall(arq.read_text(encoding="utf-8"))) for arq in sorted(MODULOS.glob("*.js"))}
    assert len(achados) == 16
    assert {nome: n for nome, n in achados.items() if n} == CENSO


def _ler_preferencias():
    fonte = (MODULOS / "execution-ui.js").read_text(encoding="utf-8")
    chave = trecho("execution-ui.js", r"^const PREF_KEY = .*;$")
    funcao = re.search(r"^function readPreferences\([^)]*\) \{\n.*?\n\}$", fonte, re.M | re.S).group(0)
    return rodar_node(r"""
const gravacoes = [], avisos = []; let MODO = 'corrompido';
const notify = (...a) => avisos.push(MODO);
globalThis.localStorage = {
  getItem() { if (MODO === 'bloqueado') throw new Error('SecurityError'); return MODO === 'corrompido' ? '{broken' : MODO === 'valido' ? JSON.stringify({market: false, side: 'buy', minSize: 5}) : null; },
  setItem(k) { gravacoes.push(k); }};
""" + chave + "\n" + funcao + r"""
const saida = {};
for (MODO of ['corrompido', 'bloqueado', 'ausente', 'valido']) { let levantou = false, prefs = null; try { prefs = readPreferences(notify); } catch { levantou = true; } saida[MODO] = {prefs, levantou}; }
await new Promise(r => setTimeout(r, 0));
saida.gravacoes = gravacoes; saida.avisos = avisos; console.log(JSON.stringify(saida));
""")


def test_preferencias_de_execucao_corrompidas_voltam_ao_default_sem_regravar():
    """execution-ui.js:18: o `catch {}` devolve os filtros padrao; diferente de app.js (161), nada e regravado no boot
    (savePreferences so roda por acao do usuario, execution-ui.js:43-44, :94-96)."""
    saida = _ler_preferencias()
    padrao = {"market": True, "paper": True, "side": "all", "minSize": 0}
    assert saida["corrompido"] == saida["bloqueado"] == saida["ausente"] == {"prefs": padrao, "levantou": False}
    assert saida["valido"] == {"prefs": {"market": False, "paper": True, "side": "buy", "minSize": 5}, "levantou": False}
    assert saida["gravacoes"] == []


def test_caracterizacao_preferencias_de_execucao_ilegiveis_nao_sao_sinalizadas():
    """QHOS-AUD-203, caracterizacao: execution-ui.js:18 `catch {}` em readPreferences nao avisa; `notify` so e usado ao
    gravar (execution-ui.js:96). Filtros de exibicao, sem regravacao no boot. Avisar e decisao do autor (sem patch proposto)."""
    assert _ler_preferencias()["avisos"] == []
