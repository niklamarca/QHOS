"""Cross-review of scientific guardrails; no market/model connections."""
import json,os,sys,subprocess,math
import pytest
from qhos2 import pesquisa as pe, avaliador as av, desequilibrio as de
from test_pesquisa import proposta, captura, ANTES, DEPOIS


def test_tuple_window_cannot_bypass_global_budget(tmp_path):
    p=proposta()
    pe.registrar_plano(tmp_path,p,agora=ANTES)
    p["pergunta"]="Outra hipotese";p["teste"]=tuple(p["teste"])
    with pytest.raises(pe.PesquisaError,match="teste_anterior_sem_resultado"):
        pe.registrar_plano(tmp_path,p,agora=ANTES)


def test_changed_official_input_is_rejected_before_recomputation(tmp_path, monkeypatch):
    monkeypatch.setattr(pe,"identidade_codigo",lambda:{"sha256":"fixed-review-fixture"})
    plan=pe.registrar_plano(tmp_path,proposta(),agora=ANTES); data=captura(tmp_path)
    pe.executar_registrado(tmp_path,plan,[data],agora=DEPOIS)
    file=data/"manifest.json"; m=json.loads(file.read_text());m["note"]="different input";file.write_text(json.dumps(m))
    called=[]; real=av.avaliar
    def spy(*a,**kw):called.append(1);return real(*a,**kw)
    monkeypatch.setattr(av,"avaliar",spy)
    with pytest.raises(pe.PesquisaError,match="dados_diferentes"):
        pe.executar_registrado(tmp_path,plan,[data],agora=DEPOIS)
    assert not called, "Official input rejection occurs only after computing another evaluation"


@pytest.mark.posix
@pytest.mark.lento
def test_research_manifest_fifo_must_not_block(tmp_path):
    p=tmp_path/"capture";p.mkdir();os.mkfifo(p/"manifest.json");(p/"market_frames.jsonl").write_bytes(b"")
    program="from qhos2.pesquisa import _identificar_capturas;import sys;_identificar_capturas([sys.argv[1]])"
    # teto so contra o travamento no FIFO: 2 s nao cabia importar o pacote num filho sob carga (QHOS-AUD-252)
    try:r=subprocess.run([sys.executable,"-c",program,str(p)],capture_output=True,text=True,timeout=30)
    except subprocess.TimeoutExpired:pytest.fail("Research blocked opening FIFO manifest")
    assert r.returncode!=0


def test_bootstrap_finite_inputs_must_not_return_infinite_sums():
    t=int(DEPOIS.timestamp())*10**9
    # QHOS-AUD-250: 3 x 1e308 no mesmo bloco estoura o double; o contrato e recusar com codigo, nunca devolver inf
    estouro=[av.Operacao("C",t,t,100,t+1,101,1e308,None) for _ in range(3)]
    with pytest.raises(de.DesequilibrioError,match="resultado_nao_finito"):
        av.intervalo(estouro,0,.05,reamostras=3)
    # 3 x 1e307 cabem (3e307 < 1,8e308): aqui a assercao de finitude roda de fato
    grandes=[av.Operacao("C",t,t,100,t+1,101,1e307,None) for _ in range(3)]
    result=av.intervalo(grandes,0,.05,reamostras=3)
    assert all(math.isfinite(v) for v in result), result
    assert result==(pytest.approx(1e307),pytest.approx(1e307),pytest.approx(1e307))


@pytest.mark.posix
def test_raw_fifo_and_symlink_must_not_block(tmp_path):
    raw = tmp_path / "raw"
    os.mkfifo(raw)
    with pytest.raises(pe.PesquisaError, match="arquivo_nao_regular"):
        pe._arquivo(raw)
    link = tmp_path / "link"
    link.symlink_to(raw)
    with pytest.raises(OSError):
        pe._arquivo(link)


def test_concurrent_divergent_evaluation_never_reaches_evaluator(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    monkeypatch.setattr(pe, "identidade_codigo", lambda: {"sha256": "fixed-review-fixture"})
    plan = pe.registrar_plano(tmp_path, proposta(), agora=ANTES)
    first, second = captura(tmp_path / "a"), captura(tmp_path / "b")
    mf = second / "manifest.json"
    doc = json.loads(mf.read_text()); doc["note"] = "different-input"
    mf.write_text(json.dumps(doc))
    entered, release, calls = Event(), Event(), []
    real = av.avaliar
    def waiting(*args, **kw):
        calls.append(1); entered.set()
        assert release.wait(timeout=30)          # tetos so contra travamento (QHOS-AUD-252)
        return real(*args, **kw)
    monkeypatch.setattr(av, "avaliar", waiting)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(pe.executar_registrado, tmp_path, plan, [first], agora=DEPOIS)
        assert entered.wait(timeout=30)
        b = pool.submit(pe.executar_registrado, tmp_path, plan, [second], agora=DEPOIS)
        release.set()
        assert a.result(timeout=30)["tipo"] == "AVALIACAO"
        with pytest.raises(pe.PesquisaError, match="dados_diferentes"):
            b.result(timeout=30)
    assert calls == [1]
