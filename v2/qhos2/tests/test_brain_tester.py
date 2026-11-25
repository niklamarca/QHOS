"""SoftwareTester (smoke) do Brain com relogio controlado e coletor real em bancada (QHOS-AUD-276, 282, 287)."""
import pytest

from brain_service import tester
from brain_service.config import load_config


@pytest.fixture(autouse=True)
def isolado(monkeypatch, tmp_path):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    for name in ("BRAIN_QUANT_ROOT", "BRAIN_STATE_DIR", "BRAIN_RUNTIME_DIR", "BRAIN_QUANT_OBSERVE_URL"):
        monkeypatch.delenv(name, raising=False)


def produtor_real():
    from qhos2.coletor import Coletor
    from qhos2.observabilidade import resposta
    coletor = Coletor(token="t" * 43)
    return lambda url, timeout: resposta(coletor, "/" + url.split("/", 3)[3])


def caso(smoke, nome):
    return next(c for c in smoke.state["cases"] if c["name"] == nome)


# --- QHOS-AUD-276: detalhe da falha nomeia a verificacao interna ----------------

def test_smoke_failure_detail_names_internal_reason(tmp_path, monkeypatch):
    producer = produtor_real()
    def busy_sem_bool(url, timeout):
        r = producer(url, timeout)
        if "/api/busy" in url:
            r["busy"] = "sim"
        return r
    monkeypatch.setattr(tester, "http_json_get", busy_sem_bool)
    smoke = tester.SoftwareTester(load_config().target_root, tmp_path / "smoke")
    smoke.state["run_id"] = "fixture"
    smoke._checks()
    assert smoke.state["phase"] == "FAILED"
    assert caso(smoke, "API: /api/busy")["detail"] == "busy_not_confirmed"


def test_smoke_failure_detail_keeps_only_class_name_for_foreign_errors(tmp_path, monkeypatch):
    producer = produtor_real()
    def com_segredo(url, timeout):
        if "/api/status" in url:
            raise ValueError("token=segredo-ficticio")
        return producer(url, timeout)
    monkeypatch.setattr(tester, "http_json_get", com_segredo)
    smoke = tester.SoftwareTester(load_config().target_root, tmp_path / "smoke")
    smoke.state["run_id"] = "fixture"
    smoke._checks()
    assert caso(smoke, "API: /api/status")["detail"] == "ValueError"
    assert "segredo" not in str(smoke.state["cases"])


# --- QHOS-AUD-282: PASSED antigo nao e verde atual -------------------------------

def test_snapshot_exposes_stale_smoke_for_ui(tmp_path, monkeypatch):
    agora = [1_800_000_000.0]
    monkeypatch.setattr(tester, "http_json_get", produtor_real())
    monkeypatch.setattr(tester.time, "time", lambda: agora[0])  # relogio do modulo: este teste tambem roda em 36f0e1f (antes.log)
    smoke = tester.SoftwareTester(load_config().target_root, tmp_path / "smoke")
    smoke.state["run_id"] = "fixture"
    smoke._checks()
    smoke.state["next_due"] = agora[0] + smoke.cadence_s
    fresco = smoke.snapshot()
    assert fresco["phase"] == "PASSED" and fresco["stale"] is False and fresco["display_phase"] == "PASSED"
    agora[0] += smoke.cadence_s + 1
    velho = smoke.snapshot()
    assert velho["phase"] == "PASSED" and velho["stale"] is True
    assert velho["display_phase"] == "PASSED_STALE"


def test_tick_and_cooldown_follow_the_injected_clock(tmp_path):
    agora = [1_800_000_000.0]
    smoke = tester.SoftwareTester(load_config().target_root, tmp_path / "smoke", clock=lambda: agora[0])
    smoke.state["next_due"] = agora[0] + 10
    assert smoke.tick() == {"accepted": False, "reason": "not_due"}
    assert smoke.start_run()["reason"] == "cooldown"


# --- QHOS-AUD-287: PASSED com coletor sem captura declara o que nao prova -------

def test_smoke_passed_without_capture_declares_no_market_data(tmp_path, monkeypatch):
    monkeypatch.setattr(tester, "http_json_get", produtor_real())
    smoke = tester.SoftwareTester(load_config().target_root, tmp_path / "smoke")
    smoke.state["run_id"] = "fixture"
    smoke._checks()
    assert smoke.state["phase"] == "PASSED" and smoke.state["passed"] == 10
    assert smoke.state["reader_state"] == "SEM_CAPTURA"
    assert smoke.state["market_data_confirmed"] is False
    assert smoke.state["history"][0]["market_data_confirmed"] is False
