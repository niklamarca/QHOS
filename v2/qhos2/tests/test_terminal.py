"""O terminal versionado e um arquivo so, sem importacao externa, e a copia legivel dos modulos bate com ele."""
import base64
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PASTA = Path(__file__).resolve().parents[1] / "terminal"


def test_modulos_batem_com_o_index_html():
    r = subprocess.run([sys.executable, str(PASTA / "extrair_modulos.py"), "--conferir"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_nenhuma_importacao_fora_do_arquivo():
    texto = (PASTA / "index.html").read_text(encoding="utf-8")
    assert "<script src=" not in texto


ENDERECOS = {
    "ws://127.0.0.1:8765": "ws://127.0.0.1:8765/",
    "wss://coletor.exemplo": "wss://coletor.exemplo/",
    "ws://127.0.0.1": None,                    # sem porta
    "ws://localhost:8765": None,               # nome pode resolver para outro endereco
    "ws://[::1]:8765": None,
    "ws://127.0.0.2:8765": None,
    "ws://192.168.0.10:8765": None,            # sai da maquina
    "ws://u:p@127.0.0.1:8765": None,
    "ws://127.0.0.1:8765/?chave=1": None,
    "ws://127.0.0.1:8765/#x": None,
    "http://127.0.0.1:8765": None,
}


def test_endereco_do_coletor_ws_so_no_loopback():
    node = shutil.which("node")
    if not node:
        pytest.skip("node ausente: a regra roda no navegador")
    modulo = base64.b64encode((PASTA / "modulos" / "qhos-bridge.js").read_bytes()).decode()
    codigo = (f"const {{validateBridgeURL}} = await import('data:text/javascript;base64,{modulo}');\n"
              f"const saida = {{}};\n"
              f"for (const e of {json.dumps(list(ENDERECOS))}) {{ try {{ saida[e] = validateBridgeURL(e); }}"
              f" catch {{ saida[e] = null; }} }}\n"
              f"console.log(JSON.stringify(saida));\n")
    # pelo stdin: o modulo inteiro passa do limite da linha de comando do Windows (QHOS-AUD-006)
    r = subprocess.run([node, "--input-type=module"], input=codigo, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == ENDERECOS


def test_estudos_do_es_desligados_ate_21_11():
    # regra 18 emendada: no ES da Rithmic, estudo de eventos e MBO/fila recusam ate 21/11; o resto do terminal segue
    fonte = (PASTA / "modulos" / "microstructure-ui.js").read_text(encoding="utf-8")
    assert "const ES_LIBERADO_EM=Date.UTC(2026,10,21,3);" in fonte          # 21/11 00h de Brasilia
    assert fonte.count("if(esBloqueado(source))throw new Error(AVISO_ES);const result=runEventStudy(") == 1
    assert fonte.count("if(esBloqueado(this.context))throw new Error(AVISO_ES);") == 1
    assert fonte.count("/^ES/i.test(String(archive.symbol)))throw new Error(AVISO_ES);this.archive=archive;") == 1
    assert "canExperiment(){return providerOf(this.context)==='coinbase'" in fonte   # carteira so Coinbase


def test_percurso_do_coletor_passa_pela_ponte_e_o_invalido_e_recusado():
    # tarefa 9: mensagens geradas pelo proprio coletor; estado fora da lista nunca vira bolinha, e o percurso some
    # ao desconectar (sem conexao, nada fica verde)
    node = shutil.which("node")
    if not node:
        pytest.skip("node ausente: a regra roda no navegador")
    from qhos2 import coletor as co
    coletor = co.Coletor(token="t" * 43, porta=8766)
    welcome, percurso = coletor.welcome(), coletor.percurso()
    assert [c["id"] for c in percurso["stages"]] == ["rithmic", "sessao", "gravacao", "livro", "leitura", "coletor"]
    sem_capacidade = {**welcome, "capabilities": ["catalog", "selection"]}
    modulo = base64.b64encode((PASTA / "modulos" / "qhos-bridge.js").read_bytes()).decode()
    codigo = f"""
const {{createQhosBridge}} = await import('data:text/javascript;base64,{modulo}');
const welcome = {json.dumps(welcome)}, semCapacidade = {json.dumps(sem_capacidade)}, ok = {json.dumps(percurso)};
const mudar = (estagio) => ({{...ok, stages: [{{...ok.stages[0], ...estagio}}, ...ok.stages.slice(1)]}});
const casos = [['valido', ok], ['estado_inventado', mudar({{state: 'verde'}})], ['valido_de_novo', ok],
  ['outra_sessao', {{...ok, sessionId: 'qhos2-outra'}}], ['desde_no_futuro', mudar({{since: ok.time + 1}})],
  ['campo_longo', mudar({{fields: [{{label: 'x', value: 'y'.repeat(81)}}]}})], ['sem_camadas', {{...ok, stages: []}}],
  ['camada_repetida', {{...ok, stages: [ok.stages[0], ok.stages[0]]}}], ['valido_antes_de_sair', ok]];
async function rodar(boasVindas, lista) {{
  let sock; class Falso {{ constructor() {{ sock = this; this.readyState = 1; }} send() {{}} close() {{}} }}
  const vistos = [];
  const b = createQhosBridge({{onPipeline: p => vistos.push(p)}}, {{WebSocketImpl: Falso, setTimeout: () => 0, clearTimeout: () => {{}}}});
  const pronto = b.connect({{url: 'ws://127.0.0.1:8766', token: 'x'}});
  sock.onopen(); sock.onmessage({{data: JSON.stringify(boasVindas)}}); await pronto;
  const saida = {{}};
  for (const [nome, msg] of lista) {{
    sock.onmessage({{data: JSON.stringify(msg)}});
    const s = b.getSnapshot();
    saida[nome] = [s.pipeline ? s.pipeline.stages.map(x => x.state).join(',') : null, s.invalidMessages];
    if (nome === 'valido') saida.congelado = s.pipeline ? Object.isFrozen(s.pipeline) && Object.isFrozen(s.pipeline.stages[0]) : null;
  }}
  b.disconnect();
  saida.depois_de_desconectar = b.getSnapshot().pipeline;
  saida.avisos = vistos.length;
  return saida;
}}
console.log(JSON.stringify({{com: await rodar(welcome, casos), sem: await rodar(semCapacidade, [['valido', ok]])}}));
"""
    r = subprocess.run([node, "--input-type=module"], input=codigo, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    saida = json.loads(r.stdout)
    estados = ",".join(c["state"] for c in percurso["stages"])
    assert estados == "desconhecido,desconhecido,desconhecido,desconhecido,desconhecido,ok"
    assert saida["com"] == {"valido": [estados, 0], "estado_inventado": [None, 1], "valido_de_novo": [estados, 1],
                            "outra_sessao": [None, 2], "desde_no_futuro": [None, 3], "campo_longo": [None, 4],
                            "sem_camadas": [None, 5], "camada_repetida": [None, 6], "valido_antes_de_sair": [estados, 6],
                            "congelado": True, "depois_de_desconectar": None, "avisos": 3}
    assert saida["sem"] == {"valido": [None, 1], "congelado": None, "depois_de_desconectar": None, "avisos": 0}


def test_percurso_na_tela_nunca_fica_verde_sozinho():
    fonte = (PASTA / "modulos" / "microstructure-ui.js").read_text(encoding="utf-8")
    assert "const PERCURSO_VELHO_MS=5000;" in fonte
    assert "PERCURSO_ESTADOS[old?'desconhecido':stage.state]??PERCURSO_ESTADOS.desconhecido" in fonte
    app = (PASTA / "modulos" / "app.js").read_text(encoding="utf-8")
    assert "pipeline:!replaying&&currentProvider==='rithmic'?qhosBridge.getSnapshot().pipeline:null," in app
