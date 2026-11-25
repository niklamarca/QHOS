"""Brain empacotado e contrato V2 em isolamento; nenhuma API de mercado/modelo."""
import json
import time
from pathlib import Path

import pytest

from brain_service.config import ServiceConfig, load_config
from brain_service.qhos2_observation import accept


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    monkeypatch.setenv("BRAIN_CODEX_CHAT", "0")
    for name in ("BRAIN_QUANT_ROOT", "BRAIN_STATE_DIR", "BRAIN_RUNTIME_DIR", "BRAIN_QUANT_OBSERVE_URL"):
        monkeypatch.delenv(name, raising=False)


NOW = 1_700_000_000.0  # relogio fixo: o contrato nao depende do relogio real (QHOS-AUD-311)


def payload(observed_at=NOW):
    return {"schema_version": "qhos2.observation.v1", "observed_at": observed_at,
            "environment": "Rithmic Paper Trading", "source_kind": "historical_capture",
            "capture": {"id": "fixture", "symbol": "ESZ6", "exchange": "CME", "closed": True,
                        "reader_state": "ENCERRADA", "error_code": None, "frames": 12, "bytes": 300},
            "quality": {"book_valid": True, "sequence_verified": False, "available_as_live": False,
                        "recent_file_activity": False, "last_frame_received_ns": "1800000000000000000"},
            "permissions": {"can_execute": False, "research_only": True, "can_control_capture": False}}


def test_brain_defaults_independentes(tmp_path):
    c = load_config()
    assert c.quant_base == "http://127.0.0.1:8766" and not c.quant_ws_enabled
    assert c.state_dir == tmp_path / "dados" / "brain" and c.brain_lake_dir == c.state_dir
    assert c.target_root.name == "qhos2" and (c.target_root / "coletor.py").is_file()
    with pytest.raises(ValueError, match="legacy_relight"):
        ServiceConfig(relight_enabled=True)


def test_observacao_preserva_paper_e_nao_inventa_live():
    p = payload()
    r = accept(p, now=NOW)
    assert r["available"] and r["environment"] == "Rithmic Paper Trading"
    assert r["quality"]["book_valid"] and not r["quality"]["available_as_live"]
    assert r["capture"]["closed"] and not r["permissions"]["can_execute"]
    assert not accept(p, now=p["observed_at"] + 91)["available"]


def test_contrato_real_produtor_coletor_e_consumidor_brain():
    from qhos2.coletor import Coletor
    from qhos2.observabilidade import snapshot
    coletor = Coletor(token="t" * 43)
    r = accept(snapshot(coletor))
    assert r["available"] and r["source_kind"] == "none"
    assert r["environment"] == "unknown"
    assert not r["quality"]["book_valid"] and not r["permissions"]["can_execute"]


@pytest.mark.parametrize("field,value", [("can_execute", True), ("research_only", False), ("can_control_capture", True)])
def test_observacao_recusa_expansao_de_autoridade(field, value):
    p = payload()
    p["permissions"][field] = value
    assert not accept(p, now=NOW)["available"]


def test_observacao_recusa_nan_futuro_e_live():
    for at in (float("nan"), float("inf"), NOW + 100):
        p = payload()
        p["observed_at"] = at
        assert not accept(p, now=NOW)["available"]
    p = payload()
    p["quality"]["available_as_live"] = True
    assert not accept(p, now=NOW)["available"]


def test_observer_consume_snapshot_v2_e_respeita_indisponibilidade(monkeypatch):
    from brain_service import observer
    p = payload(observed_at=time.time())  # o observer (lote 3b) aceita com o relogio real; sem clock injetavel
    respostas = {"/api/health": {"process": {"state": "PROCESS_ONLINE", "pid": 123, "component": "qhos2.coletor"}},
                 "/api/status": {"modules": {"coletor": {"status": "running"}}}, "/api/truth": {},
                 "/api/busy": {"busy": False}, "/api/qhos2/snapshot": p}
    monkeypatch.setattr(observer, "get_json", lambda base, path, **_: respostas[path])
    monkeypatch.setattr(observer, "_git", lambda *_: {"stdout": "", "available": True, "error": None})  # QHOS-AUD-275: _git devolve medicao
    o = observer.QuantObserver(load_config())
    result = o.collect()
    assert result["application_ready"] and result["qhos2_observation"]["available"]
    assert result["qhos2_observation"]["environment"] == "Rithmic Paper Trading"
    assert result["rithmic_protocol_observation"]["quality"]["available_as_live"] is False
    respostas["/api/qhos2/snapshot"] = {"_observation_error": "TimeoutError"}
    assert not o.collect()["qhos2_observation"]["available"]


def test_http_recusa_endereco_externo_redirect_e_corpo_grande(monkeypatch):
    from brain_service.local_http import open_local, NoRedirect, get_json
    with pytest.raises(ValueError, match="url_refused"):
        open_local("https://example.invalid/", 1)
    with pytest.raises(ValueError, match="redirect_refused"):
        NoRedirect().redirect_request(None, None, None, None, None, None)
    class Corpo:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, n): return b" " * n
    monkeypatch.setattr("brain_service.local_http.open_local", lambda *_: Corpo())
    with pytest.raises(ValueError, match="body_too_large"):
        get_json("http://127.0.0.1:8766/api/status", 1)


def test_brain_e_ui_iniciam_sem_main_v1_sem_rede(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from brain_service.process import StandaloneBrain
    from brain_service.server import build_app, assert_no_privileged_routes
    c = ServiceConfig(state_path=str(tmp_path / "state"), diagnostics_enabled=False)
    brain = StandaloneBrain(c)
    try:
        app = build_app(brain)
        assert_no_privileged_routes(app)
        with TestClient(app, base_url="http://127.0.0.1") as client:   # `testserver` nao e Host da politica (QHOS-AUD-030)
            assert client.get("/").status_code == 200
            assert "QHOS V2" in client.get("/").text
            assert client.get("/app.js").status_code == 200
            assert client.get("/rithmic-observation.js").status_code == 200
            result = client.get("/api/assistant/state")
            assert result.status_code == 200
            data = result.json()
            assert not data["trader"]["qhos2_observation"]["available"]
            assert not data["watchdog"]["enabled"]
    finally:
        brain.log_feed.close()


@pytest.mark.lento  # worker pytest real em subprocesso (QHOS-AUD-310)
def test_regressao_embarcada_roda_codigo_copiado_e_persiste(tmp_path):
    from brain_service.regression import RegressionRunner, REGRESSION_PROFILE
    assert REGRESSION_PROFILE == "qhos2-operational-v1"
    import shutil
    frozen = tmp_path / "qhos2"
    shutil.copytree(load_config().target_root, frozen, ignore=shutil.ignore_patterns("__pycache__"))
    r = RegressionRunner(frozen, tmp_path / "state", deadline_s=45)
    assert r.start_run()["accepted"]
    r._thread.join(50)
    if r._thread.is_alive():
        r.stop()
    state = r.snapshot()
    assert state["phase"] == "PASSED", state
    assert state["verified"] and state["passed"] == 8  # suite pinada por hash: contagem exata, como em test_brain_regression_installed (QHOS-AUD-310)
    artifact = json.loads(Path(state["artifact"]).read_text())
    assert artifact["blocked_side_effects"] == [] and artifact["source_hash"] == state["source_hash"]


def test_retencao_journal_limita_linhas_sem_reusar_cursores(tmp_path, monkeypatch):
    from brain_service import log_feed
    monkeypatch.setattr(log_feed, "MAX_LOG_RECORDS", 3)
    f = log_feed.RuntimeLogFeed(tmp_path)
    try:
        for i in range(6):
            f.append({"__CURSOR": str(i), "MESSAGE": "teste", "__REALTIME_TIMESTAMP": i * 1_000_000})
        records = f.page()["records"]
        assert len(records) == 3 and [x["cursor"] for x in records] == [4, 5, 6]
    finally:
        f.close()


def test_tester_consume_rotas_reais_v2_e_recusa_identidade_v1(tmp_path, monkeypatch):
    from brain_service import tester
    from qhos2.coletor import Coletor
    from qhos2.observabilidade import resposta
    coletor = Coletor(token="t" * 43)
    producer = lambda url, timeout: resposta(coletor, "/" + url.split("/", 3)[3])
    monkeypatch.setattr(tester, "http_json_get", producer)
    smoke = tester.SoftwareTester(load_config().target_root, tmp_path / "smoke")
    smoke.state["run_id"] = "fixture"
    smoke._checks()
    assert smoke.state["phase"] == "PASSED" and smoke.state["passed"] == 10
    def wrong(url, timeout):
        r = producer(url, timeout)
        if "/api/health" in url: r["process"]["component"] = "legacy.main"
        return r
    monkeypatch.setattr(tester, "http_json_get", wrong)
    smoke._checks()
    assert smoke.state["phase"] == "FAILED" and smoke.state["failed"] == 1


def test_religamento_legado_direto_nao_dispara_processo(tmp_path, monkeypatch):
    from brain_service.supervisor import run_command
    monkeypatch.setattr("subprocess.run", lambda *a, **k: pytest.fail("process must not run"))
    with pytest.raises(RuntimeError, match="legacy_relight_not_supported"):
        run_command(["old-script"], tmp_path, 1)


@pytest.mark.parametrize("environment,source", [("unknown", "none"), ("QHOS2 Sintetico", "synthetic_fixture")])
def test_brain_preserva_origem_sem_transformar_em_paper(environment, source):
    p = payload(); p.update(environment=environment, source_kind=source)
    r = accept(p, now=NOW)
    assert r["available"] and r["environment"] == environment and r["source_kind"] == source
