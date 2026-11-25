"""Campanha de correcao, lote l5b-coletor: lado do cliente (`terminal/modulos/qhos-bridge.js`, `app.js`)
(QHOS-AUD-146, 157, 158, 159, 182, 183, 185, 186, 190).

Molde de `tests/test_terminal.py`: a ponte roda no Node (script pelo stdin, UTF-8) com um WebSocket falso; sem Node
os testes de execucao sao pulados e so as caracterizacoes estaticas rodam. O terminal e release com SHA pinado:
`xfail(strict=True)` = defeito confirmado; patch minimo em `09_correcao/EVIDENCIAS/<ID>/patch_proposto.diff`.
"""
import base64
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from qhos2 import coletor as co

PASTA = Path(__file__).resolve().parents[1] / "terminal"
APP = (PASTA / "modulos" / "app.js").read_text(encoding="utf-8")
PONTE = (PASTA / "modulos" / "qhos-bridge.js").read_text(encoding="utf-8")


def _node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node ausente: a regra roda no navegador")
    return node


def _preambulo(coletor=None):
    coletor = coletor or co.Coletor(token="t" * 43, porta=0)
    welcome = coletor.welcome()
    sel = welcome["selection"]
    base = {"subscriptionId": sel["subscriptionId"], "revision": sel["revision"], "instrumentId": co.ESZ6["id"]}
    modulo = base64.b64encode(PONTE.encode("utf-8")).decode()
    return f"""
const {{createQhosBridge}} = await import('data:text/javascript;base64,{modulo}');
const WELCOME = {json.dumps(welcome)}, BASE = {json.dumps(base)};
const NOW = 1800000000000;
const avisos = [], erros = [];
console.warn = (...a) => avisos.push(a.map(String).join(' '));
console.error = (...a) => erros.push(a.map(String).join(' '));
function ponte(callbacks = {{}}, extra = {{}}) {{
  const ctx = {{sock: null, timers: []}};
  class Falso {{ constructor() {{ ctx.sock = this; this.readyState = 1; }} send() {{}} close() {{}} }}
  ctx.b = createQhosBridge(callbacks, {{WebSocketImpl: Falso, now: () => NOW,
    setTimeout: (fn, ms) => {{ ctx.timers.push({{fn, ms}}); return ctx.timers.length; }}, clearTimeout: () => {{}}, ...extra}});
  return ctx;
}}
async function abrir(callbacks = {{}}, welcome = WELCOME, extra = {{}}) {{
  const ctx = ponte(callbacks, extra);
  const pronto = ctx.b.connect({{url: 'ws://127.0.0.1:1', token: 'x'}});
  ctx.sock.onopen();
  ctx.sock.onmessage({{data: JSON.stringify(welcome)}});
  try {{ await pronto; ctx.erroDeConexao = null; }} catch (e) {{ ctx.erroDeConexao = e.message; }}
  return ctx;
}}
const horario = () => ({{time: NOW - 100, timeNs: String(BigInt(NOW - 100) * 1000000n)}});
const livro = (extra = {{}}) => ({{type: 'book', ...BASE, ...horario(), ready: true, sequenceVerified: false,
  bids: [[6850.0, 3], [6849.75, 2]], asks: [[6850.25, 2], [6850.5, 1]], ...extra}});
const negocio = (id, extra = {{}}) => ({{type: 'trade', ...BASE, ...horario(), id, price: 6850.25, size: 1, side: 'buy', ...extra}});
const mandar = (ctx, m) => ctx.sock.onmessage({{data: typeof m === 'string' ? m : JSON.stringify(m)}});
const sair = (obj) => console.log(JSON.stringify(obj));
"""


def rodar_node(codigo, coletor=None):
    node = _node()
    r = subprocess.run([node, "--input-type=module"], input=_preambulo(coletor) + codigo, capture_output=True,
                       text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


# ---- QHOS-AUD-146: invoke engole excecoes dos callbacks e o status continua "live" -----------------------------------

CENA_146 = """
const ctx = await abrir({onBook() { throw new Error('bug do app'); }});
mandar(ctx, livro());
const s = ctx.b.getSnapshot();
sair({estado: s.state, callbackErrors: s.callbackErrors ?? null, erros: erros.length, livroPronto: s.bookReady});
"""


def test_caracterizacao_callback_que_lanca_e_engolido_e_o_status_fica_live():
    saida = rodar_node(CENA_146)
    assert saida == {"estado": "live", "callbackErrors": None, "erros": 0, "livroPronto": True}


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-146: `invoke` tem `catch {}` vazio; o livro se perde no app, "
                                       "`markData` ainda roda e a tela mostra AO VIVO sem nada no console")
def test_callback_que_lanca_e_contado_ou_tira_o_status_de_live():
    saida = rodar_node(CENA_146)
    assert saida["callbackErrors"] == 1 or saida["erros"] >= 1 or saida["estado"] != "live"


# ---- QHOS-AUD-157: invalidMessages/ignoredMessages nunca chegam a interface ----------------------------------------

CENA_157 = """
const ctx = await abrir({});
mandar(ctx, '{nope');
const depoisInvalida = ctx.b.getSnapshot();
mandar(ctx, negocio('1-0'));
const depoisValida = ctx.b.getSnapshot();
sair({invalidas: depoisValida.invalidMessages, erroLogoApos: depoisInvalida.error, erroDepoisDaValida: depoisValida.error,
      estado: depoisValida.state});
"""


def test_caracterizacao_ponte_conta_a_invalida_mas_a_proxima_valida_apaga_o_erro():
    saida = rodar_node(CENA_157)
    assert saida["invalidas"] == 1 and saida["erroLogoApos"] == "O coletor enviou uma mensagem inválida."
    assert saida["erroDepoisDaValida"] is None and saida["estado"] == "live"


def test_caracterizacao_app_nao_mostra_invalidas_nem_ignoradas():
    assert "invalidMessages" in PONTE and "ignoredMessages" in PONTE
    assert "invalidMessages" not in APP and "ignoredMessages" not in APP


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-157: `renderHealth` nao lista `invalidMessages`/`ignoredMessages`; "
                                       "o erro aparece por milissegundos ate a proxima mensagem valida")
def test_painel_de_saude_mostra_mensagens_invalidas():
    saude = re.search(r"function renderHealth\(\)\{[\s\S]*?\n\}", APP)
    assert saude and "invalidMessages" in saude.group(0)


# ---- QHOS-AUD-158: falha antes do welcome colapsa todas as causas numa frase ---------------------------------------

CENA_158 = """
const w = JSON.parse(JSON.stringify(WELCOME)); w.selection.instruments[0].expiry = '18/12/2026';
const ctx = await abrir({}, w);
sair({erro: ctx.b.getSnapshot().error, conexao: ctx.erroDeConexao, estado: ctx.b.getSnapshot().state, avisos, erros});
"""


def test_caracterizacao_vencimento_fora_do_formato_vira_frase_generica():
    saida = rodar_node(CENA_158)
    assert saida["erro"] == saida["conexao"] == "O serviço não confirmou um protocolo e uma seleção válidos."
    assert saida["estado"] == "error" and saida["avisos"] == [] and saida["erros"] == []


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-158: toda validacao do welcome vira a mesma frase; o campo "
                                       "('Vencimento inválido.') nao chega ao status nem ao console")
def test_vencimento_fora_do_formato_e_nomeado_ao_operador():
    saida = rodar_node(CENA_158)
    assert "Vencimento" in (saida["erro"] or "") or any("Vencimento" in a for a in saida["avisos"] + saida["erros"])


# ---- QHOS-AUD-159: negocios perdidos durante queda de conexao nao sao repostos nem contados ----------------------

CENA_159 = """
const lacunas = [], estados = [];
const ctx = await abrir({onGap: g => lacunas.push(g), onStatus: s => estados.push(s.state)}, WELCOME, {reconnectDelaysMs: [777]});
mandar(ctx, negocio('1-0'));
ctx.sock.onclose();
const caiu = ctx.b.getSnapshot();
const religar = ctx.timers.filter(t => t.ms === 777);
religar.at(-1).fn();
ctx.sock.onopen(); mandar(ctx, WELCOME);
mandar(ctx, negocio('1-5'));
const voltou = ctx.b.getSnapshot();
sair({religacoes: religar.length, retrying: caiu.retrying, reconnects: voltou.reconnects, connected: voltou.connected,
      gaps: voltou.gaps, lacunas: lacunas.map(l => l.reason), negociosDepois: voltou.tradesReceived});
"""


def test_caracterizacao_reconexao_nao_registra_lacuna_na_ponte():
    saida = rodar_node(CENA_159)
    assert saida["religacoes"] == 1 and saida["retrying"] is True
    assert saida["reconnects"] == 1 and saida["connected"] is True and saida["negociosDepois"] == 1
    assert saida["gaps"] is None and saida["lacunas"] == []


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-159: na reconexao a ponte limpa `tradeIds` e nao chama `onGap`; "
                                       "fluxo (CVD, footprint) e `status.gaps` nao registram o buraco")
def test_reconexao_chama_on_gap_com_motivo_reconnect():
    saida = rodar_node(CENA_159)
    assert "reconnect" in saida["lacunas"]


# ---- QHOS-AUD-182: tipo de mensagem desconhecido e ignorado sem contador -------------------------------------------

CENA_182 = """
const ctx = await abrir({});
mandar(ctx, {type: 'telemetria', valor: 1});
const s = ctx.b.getSnapshot();
sair({ignoradas: s.ignoredMessages, invalidas: s.invalidMessages, erro: s.error});
"""


def test_caracterizacao_tipo_desconhecido_nao_conta():
    assert rodar_node(CENA_182) == {"ignoradas": 0, "invalidas": 0, "erro": None}


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-182: `receive` devolve em silencio para tipo fora da lista; "
                                       "`ignoredMessages` so conta assinatura/instrumento desconhecidos")
def test_tipo_desconhecido_e_contado_como_ignorado():
    assert rodar_node(CENA_182)["ignoradas"] == 1


# ---- QHOS-AUD-183: respostas sem pedido pendente e codigos desconhecidos descartados sem log --------------------

CENA_183 = """
const ctx = await abrir({});
mandar(ctx, {type: 'response', id: 'qhos-999', ok: false, error: {code: 'X'}});
const semPedido = ctx.b.getSnapshot();
const p = ctx.b.search('es');
mandar(ctx, {type: 'response', id: 'qhos-1', ok: false, error: {code: 'CODIGO_NOVO'}});
const e = await p.catch(x => x);
sair({ignoradas: semPedido.ignoredMessages, invalidas: semPedido.invalidMessages, codigo: e.code, mensagem: e.message,
      avisos: avisos.length});
"""


def test_caracterizacao_resposta_orfa_e_codigo_desconhecido_nao_deixam_rastro():
    saida = rodar_node(CENA_183)
    assert saida["ignoradas"] == 0 and saida["invalidas"] == 0 and saida["avisos"] == 0
    assert saida["codigo"] == "BRIDGE_ERROR" and saida["mensagem"] == "O coletor recusou a solicitação."


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-183: `receiveResponse` descarta resposta sem pedido pendente e "
                                       "`messageError` colapsa codigo desconhecido em BRIDGE_ERROR sem log nem contador")
def test_resposta_orfa_ou_codigo_desconhecido_deixa_rastro():
    saida = rodar_node(CENA_183)
    assert saida["ignoradas"] + saida["invalidas"] + saida["avisos"] >= 1


# ---- QHOS-AUD-185: historyCoverage nao e persistido na sessao ----------------------------------------------------

def _funcao(nome):
    achado = re.search(r"function " + nome + r"\([^)]*\)\{[\s\S]*?\n\}", APP)
    assert achado, nome
    return achado.group(0)


def test_caracterizacao_sessao_salva_candles_mas_nao_a_cobertura():
    assert "historyCandles," in _funcao("snapshotLive") and "historyCoverage" not in _funcao("snapshotLive")
    assert "historyCoverage" in _funcao("renderChartViewState")      # a legenda ao vivo depende dela


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-185: `snapshotLive` grava `historyCandles` sem `historyCoverage`; "
                                       "no replay a legenda perde 'somente a captura selecionada'")
def test_sessao_persiste_a_cobertura_do_historico():
    assert "historyCoverage" in _funcao("snapshotLive")


# ---- QHOS-AUD-186: rejectConfiguration desliga a conexao automatica para sempre sem dizer a causa ------------------

def test_caracterizacao_rejeicao_de_configuracao_sem_causa_e_sem_volta():
    corpo = _funcao("rejectConfiguration")
    assert corpo.startswith("function rejectConfiguration(){") and "enabled=false;manualOnly=true" in corpo
    assert "message='Conexão automática indisponível. Configure a conexão manual.'" in corpo
    assert re.findall(r"rejectConfiguration\(([^)]*)\)", APP) == [""] * APP.count("rejectConfiguration(")
    assert "enabled=true" not in APP                                   # nada religa `enabled` sem recarregar


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-186: `rejectConfiguration()` nao recebe nem mostra a causa "
                                       "(status != 200, origem, JSON, token) e desliga `enabled` ate recarregar")
def test_rejeicao_de_configuracao_diz_a_causa():
    chamadas = [c for c in re.findall(r"rejectConfiguration\(([^)]*)\)", APP) if not APP.count("function rejectConfiguration(" + c)]
    assert chamadas and all(c.strip() for c in chamadas)


# ---- QHOS-AUD-190: recuo do relogio: a ponte recusa o percurso inteiro ---------------------------------------------

def _percursos_com_recuo():
    relogio = [1000.0]
    coletor = co.Coletor(token="t" * 43, porta=0, relogio=lambda: relogio[0])
    primeiro = coletor.percurso()
    relogio[0] = 990.0
    segundo = coletor.percurso()
    return coletor, primeiro, segundo


def _cena_190(primeiro, segundo):
    return f"""
const vistos = [];
const ctx = await abrir({{onPipeline: p => vistos.push(p.time)}});
mandar(ctx, {json.dumps(primeiro)});
mandar(ctx, {json.dumps(segundo)});
const s = ctx.b.getSnapshot();
sair({{vistos, invalidas: s.invalidMessages, erro: s.error, percurso: s.pipeline ? s.pipeline.time : null}});
"""


def test_caracterizacao_percurso_apos_recuo_e_recusado_pela_ponte():
    coletor, primeiro, segundo = _percursos_com_recuo()
    assert all(c["since"] > segundo["time"] for c in segundo["stages"])   # o coletor manda `since` no futuro
    saida = rodar_node(_cena_190(primeiro, segundo), coletor)
    assert saida == {"vistos": [1_000_000], "invalidas": 1, "erro": "Percurso do coletor inválido.", "percurso": None}


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-190: com `since > time` depois do recuo, `normalizePipeline` "
                                       "rejeita a mensagem; por ate delta as seis luzes ficam cinza")
def test_percurso_apos_recuo_chega_ao_on_pipeline():
    coletor, primeiro, segundo = _percursos_com_recuo()
    saida = rodar_node(_cena_190(primeiro, segundo), coletor)
    assert saida["vistos"] == [1_000_000, 990_000]
