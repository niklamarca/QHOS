"""Adendo do lote l3b: configuracao por ambiente registrada (QHOS-AUD-273) e CodexChat com stderr, prazo e cancelamento (QHOS-AUD-279).

Nenhum teste habilita o Codex real: o executavel e um script Python em tmp_path e BRAIN_CODEX_CHAT fica em "0".
"""
import json
import logging
import sys
import threading
import time

import pytest

from brain_service import DEFAULT_PORT
from brain_service.config import ServiceConfig, load_config

ALL_BRAIN_ENV = ("BRAIN_SERVICE_PORT", "BRAIN_SERVICE_HOST", "BRAIN_SERVICE_CYCLE_S", "BRAIN_TEST_INTERVAL_S",
                 "BRAIN_DIAGNOSTICS_ENABLED", "BRAIN_QUANT_OBSERVE_TIMEOUT_S", "BRAIN_VOICE_TIMEOUT_S", "BRAIN_VOICE_ENABLED",
                 "BRAIN_VOICE_URL", "BRAIN_QUANT_WS_ENABLED", "BRAIN_QUANT_WS_MAX_MESSAGE_BYTES", "BRAIN_PUBLIC_ORIGIN",
                 "BRAIN_QUANT_ROOT", "BRAIN_STATE_DIR", "BRAIN_RUNTIME_DIR", "BRAIN_QUANT_OBSERVE_URL",
                 "BRAIN_CHAT_EFFORT", "BRAIN_CHAT_MODEL", "BRAIN_CODEX_EXECUTABLE")


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    monkeypatch.setenv("BRAIN_CODEX_CHAT", "0")
    for name in ALL_BRAIN_ENV:
        monkeypatch.delenv(name, raising=False)


def _warnings(caplog, needle):
    return [r for r in caplog.records if r.levelno >= logging.WARNING and needle in r.getMessage()]


# --- QHOS-AUD-273 -----------------------------------------------------------

def test_malformed_service_env_is_recorded_not_silently_defaulted(monkeypatch, caplog):
    monkeypatch.setenv("BRAIN_SERVICE_PORT", "abc token=segredo1")
    monkeypatch.setenv("BRAIN_SERVICE_CYCLE_S", "1e2")
    monkeypatch.setenv("BRAIN_TEST_INTERVAL_S", "60s")
    monkeypatch.setenv("BRAIN_DIAGNOSTICS_ENABLED", "flase")
    monkeypatch.setenv("BRAIN_QUANT_OBSERVE_TIMEOUT_S", "nan")
    monkeypatch.setenv("BRAIN_VOICE_TIMEOUT_S", "x")
    with caplog.at_level(logging.WARNING):
        cfg = load_config()
    # Comportamento primeiro (falha contra 36f0e1f sem depender dos campos novos):
    # um erro de digitacao nao desliga os diagnosticos em silencio...
    assert cfg.diagnostics_enabled is True, "BRAIN_DIAGNOSTICS_ENABLED=flase desligou os diagnosticos sem registro"
    # ...e cada chave degradada aparece no log, sem o valor cru.
    for key in ("BRAIN_SERVICE_PORT", "BRAIN_SERVICE_CYCLE_S", "BRAIN_TEST_INTERVAL_S", "BRAIN_DIAGNOSTICS_ENABLED",
                "BRAIN_QUANT_OBSERVE_TIMEOUT_S", "BRAIN_VOICE_TIMEOUT_S"):
        assert _warnings(caplog, key), f"{key} degradado sem registro"
    reasons = {row["key"]: row["reason"] for row in cfg.configuration_violations}
    assert reasons["BRAIN_SERVICE_PORT"] == "malformed_int" and cfg.port == DEFAULT_PORT
    assert reasons["BRAIN_SERVICE_CYCLE_S"] == "malformed_int" and cfg.cycle_interval_s == 60
    assert reasons["BRAIN_TEST_INTERVAL_S"] == "malformed_int" and cfg.test_interval_s == 3600
    # Gramatica de booleano da especificacao existente (brain.config_contract/_flag_value): valor
    # desconhecido mantem o padrao, em vez de virar False em silencio.
    assert reasons["BRAIN_DIAGNOSTICS_ENABLED"] == "malformed_bool" and cfg.diagnostics_enabled is True
    assert reasons["BRAIN_QUANT_OBSERVE_TIMEOUT_S"] == "non_finite_float" and cfg.observe_timeout_s == 2.0
    assert reasons["BRAIN_VOICE_TIMEOUT_S"] == "malformed_float" and cfg.voice_timeout_s == 45.0
    assert "segredo1" not in caplog.text
    assert "segredo1" not in json.dumps(cfg.configuration_violations)


def test_clamped_service_env_is_recorded(monkeypatch, caplog):
    monkeypatch.setenv("BRAIN_SERVICE_CYCLE_S", "5")
    monkeypatch.setenv("BRAIN_TEST_INTERVAL_S", "10")
    with caplog.at_level(logging.WARNING):
        cfg = load_config()
    reasons = {row["key"]: row for row in cfg.configuration_violations}
    assert cfg.cycle_interval_s == 15 and reasons["BRAIN_SERVICE_CYCLE_S"]["reason"] == "below_minimum"
    assert reasons["BRAIN_SERVICE_CYCLE_S"]["applied"] == 15
    assert cfg.test_interval_s == 900 and reasons["BRAIN_TEST_INTERVAL_S"]["reason"] == "below_minimum"
    assert _warnings(caplog, "BRAIN_TEST_INTERVAL_S")


def test_valid_and_absent_service_env_record_nothing(monkeypatch):
    assert load_config().configuration_violations == ()
    monkeypatch.setenv("BRAIN_SERVICE_PORT", "8095")
    monkeypatch.setenv("BRAIN_SERVICE_CYCLE_S", "90")
    monkeypatch.setenv("BRAIN_TEST_INTERVAL_S", "1800")
    monkeypatch.setenv("BRAIN_DIAGNOSTICS_ENABLED", "false")
    monkeypatch.setenv("BRAIN_VOICE_TIMEOUT_S", "30")
    monkeypatch.setenv("BRAIN_CHAT_MODEL", "   ")  # omissao legitima
    cfg = load_config()
    assert cfg.configuration_violations == ()
    assert (cfg.port, cfg.cycle_interval_s, cfg.test_interval_s, cfg.diagnostics_enabled, cfg.voice_timeout_s) == (8095, 90, 1800, False, 30.0)


@pytest.mark.parametrize("key,value,match", [
    ("BRAIN_SERVICE_HOST", "0.0.0.0", "loopback|127.0.0.1"),
    ("BRAIN_QUANT_OBSERVE_URL", "http://10.0.0.5:8766", "loopback"),
    ("BRAIN_PUBLIC_ORIGIN", "https://x/p", "BRAIN_PUBLIC_ORIGIN"),
    ("BRAIN_VOICE_URL", "http://10.0.0.5:8092", "loopback"),
    ("BRAIN_QUANT_WS_MAX_MESSAGE_BYTES", "10", "ws_max_message_bytes"),
])
def test_out_of_domain_service_env_is_refused_explicitly(monkeypatch, key, value, match):
    """Caracterizacao: estes ja recusam (ValueError) em vez de degradar; o adendo nao muda isso."""
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=match):
        load_config()


def test_port_outside_tcp_range_is_recorded_but_not_reinterpreted(monkeypatch, caplog):
    """Sem dominio documentado para a porta no brain_service: o valor e mantido (o bind falhara) e a anomalia registrada."""
    monkeypatch.setenv("BRAIN_SERVICE_PORT", "70000")
    with caplog.at_level(logging.WARNING):
        cfg = load_config()
    assert cfg.port == 70000
    assert {row["key"]: row["reason"] for row in cfg.configuration_violations} == {"BRAIN_SERVICE_PORT": "out_of_range"}


def test_codex_chat_env_violations_are_recorded_without_enabling_codex(monkeypatch, tmp_path):
    from brain_service.codex_chat import CodexChat
    monkeypatch.setenv("BRAIN_CHAT_EFFORT", "ultra")
    monkeypatch.setenv("BRAIN_CODEX_EXECUTABLE", str(tmp_path / "nao-existe"))
    monkeypatch.setenv("BRAIN_CODEX_CHAT", "off")  # 270 fora do lote: semantica intocada, so registro
    chat = CodexChat(tmp_path / "state")
    reasons = {row["key"]: row["reason"] for row in chat.configuration_violations}
    assert chat.effort == "medium" and reasons["BRAIN_CHAT_EFFORT"] == "out_of_domain"
    assert chat.command is None and chat.status == "not_configured"
    assert reasons["BRAIN_CODEX_EXECUTABLE"] == "not_an_absolute_regular_file"
    # "off" nao e "0": hoje LIGA o chat (so nao liga aqui porque nao ha executavel); o motivo diz isso.
    assert reasons["BRAIN_CODEX_CHAT"] == "unrecognized_value_enables_chat" and chat.enabled is False
    assert [row["applied"] for row in chat.configuration_violations if row["key"] == "BRAIN_CODEX_CHAT"] == [True]
    assert "nao-existe" not in json.dumps(chat.configuration_violations)


def test_brain_flag_env_violations_are_recorded(tmp_path, caplog):
    from brain.local_config import BrainConfig
    env = {"QHOS2_DATA_ROOT": str(tmp_path / "dados"), "BRAIN_MAX_RETRIES": "two", "BRAIN_COGNITIVE_MODE": "turbo",
           "BRAIN_ENABLED": "flase", "BRAIN_LLM_TIMEOUT_S": "900", "BRAIN_MAX_DEPTH": "4"}
    with caplog.at_level(logging.WARNING):
        cfg = BrainConfig(environ=env)
    reasons = {row["key"]: row["reason"] for row in cfg.configuration_violations}
    assert cfg.brain_max_retries == 2 and reasons["BRAIN_MAX_RETRIES"] == "malformed_int"
    assert cfg.brain_cognitive_mode == "deterministic_only" and reasons["BRAIN_COGNITIVE_MODE"] == "out_of_domain"
    assert cfg.brain_enabled is True and reasons["BRAIN_ENABLED"] == "malformed_bool"
    assert cfg.brain_llm_timeout_s == 20.0 and reasons["BRAIN_LLM_TIMEOUT_S"] == "out_of_range"
    assert cfg.brain_max_depth == 4 and "BRAIN_MAX_DEPTH" not in reasons
    assert _warnings(caplog, "BRAIN_COGNITIVE_MODE")


def test_service_start_records_configuration_violations_in_the_action_trail(monkeypatch, tmp_path):
    from brain import local_config
    from brain_service.process import StandaloneBrain
    monkeypatch.setattr(local_config.config, "configuration_violations",
                        [{"key": "BRAIN_COGNITIVE_MODE", "reason": "out_of_domain", "applied": "deterministic_only"}], raising=False)
    cfg = ServiceConfig(state_path=str(tmp_path / "state"), diagnostics_enabled=False,
                        configuration_violations=({"key": "BRAIN_SERVICE_CYCLE_S", "reason": "below_minimum", "fallback": 15, "applied": 15},))
    brain = StandaloneBrain(cfg)
    brain.assistant.model.configuration_violations = [{"key": "BRAIN_CHAT_EFFORT", "reason": "out_of_domain", "applied": "medium"}]
    try:
        keys = {(row["source"], row["key"]) for row in brain.identity()["configuration_violations"]}
        assert keys == {("service", "BRAIN_SERVICE_CYCLE_S"), ("brain", "BRAIN_COGNITIVE_MODE"), ("chat", "BRAIN_CHAT_EFFORT")}
        brain._record_configuration_violations()
        rows = [row for row in brain.linux_actions.snapshot()["actions"] if row["action"] == "configuration_violation"]
        assert len(rows) == 3 and all(row["outcome"] == "degraded" for row in rows)
        details = " | ".join(row["detail"] for row in rows)
        assert "BRAIN_SERVICE_CYCLE_S" in details and "BRAIN_COGNITIVE_MODE" in details and "BRAIN_CHAT_EFFORT" in details
    finally:
        brain.log_feed.close()


# --- QHOS-AUD-279 -----------------------------------------------------------

FAKE_FAIL = "import sys\nsys.stderr.write('Error: not logged in (token=abc123segredo)\\n')\nsys.exit(1)\n"
FAKE_SLOW = ("import signal, sys, time\n"
             "if hasattr(signal, 'SIGTERM'):\n    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
             "sys.stderr.write('starting slow model\\n')\nsys.stderr.flush()\ntime.sleep(30)\n")
FAKE_OK = ("import json\n"
           "print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'ola'}}))\n"
           "print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 1}}))\n")


def _chat(tmp_path, script, *, timeout_s=None):
    from brain_service.codex_chat import CodexChat
    path = tmp_path / "fake_codex.py"
    path.write_text(script, encoding="utf-8")
    chat = CodexChat(tmp_path / "state")
    assert chat.enabled is False, "BRAIN_CODEX_CHAT=0: o Codex real nunca e habilitado pelo teste"
    chat.command = [sys.executable, "-I", str(path)]  # processo simulado; argumentos do codex sao ignorados pelo script
    chat.enabled = True
    if timeout_s is not None:
        chat.timeout_s = timeout_s
    return chat


def test_codex_failure_keeps_redacted_stderr_tail(tmp_path):
    chat = _chat(tmp_path, FAKE_FAIL)
    before = time.time()
    assert chat.answer("oi", {}) is None
    # Comportamento primeiro: o motivo real (stderr) tem de sobreviver em algum lugar do estado do chat.
    detail = getattr(chat, "last_error_detail", None) or ""
    assert "not logged in" in detail, f"stderr descartado; so restou last_error={chat.last_error!r}"
    assert "abc123segredo" not in detail
    assert chat.last_error == "model_request_failed" and chat.last_exit_code == 1
    assert chat.cooldown_until >= before + 299, "politica existente de cooldown preservada"
    assert chat.busy is False


def test_codex_timeout_kills_child_that_ignores_sigterm_and_releases_it(tmp_path):
    chat = _chat(tmp_path, FAKE_SLOW, timeout_s=0.5)
    started = time.monotonic()
    assert chat.answer("oi", {}) is None
    assert time.monotonic() - started < 10
    assert chat.last_error == "model_timeout"
    assert "starting slow model" in (chat.last_error_detail or "")
    assert chat.busy is False and chat.last_exit_code is not None, "filho colhido (sem zumbi)"


def test_codex_cancel_from_another_thread_is_responsive(tmp_path):
    chat = _chat(tmp_path, FAKE_SLOW, timeout_s=20)
    outcome = {}

    def worker():
        outcome["answer"] = chat.answer("oi", {})

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not chat.busy and time.monotonic() < deadline:
        time.sleep(0.02)
    assert chat.busy is True
    started = time.monotonic()
    assert chat.answer("segunda", {}) is None and time.monotonic() - started < 0.5, "chamada concorrente responde na hora"
    cooldown_before = chat.cooldown_until
    chat.cancel()
    thread.join(10)
    assert not thread.is_alive(), "cancelamento nao destravou answer()"
    assert outcome["answer"] is None and chat.last_error == "model_cancelled"
    assert chat.cooldown_until == cooldown_before, "cancelar nao e falha do modelo: sem 5 min de congelamento"
    assert chat.busy is False and chat.last_exit_code is not None


def test_codex_cancel_with_nothing_in_flight_does_not_poison_the_next_turn(tmp_path):
    """Revisao do adendo: cancel() antes do Popen deixava a proxima resposta boa ser relatada como cancelada."""
    chat = _chat(tmp_path, FAKE_OK)
    assert chat.cancel() is False
    assert chat.answer("oi", {}) == "ola" and chat.last_error is None


def test_codex_cancel_racing_with_a_finished_child_keeps_the_answer(tmp_path, monkeypatch):
    """Corrida deterministica: cancel() chega depois de o filho terminar e antes de answer() decidir.
    Nada foi encerrado, entao a resposta boa nao pode virar model_cancelled."""
    import subprocess
    from brain_service import codex_chat
    chat = _chat(tmp_path, FAKE_OK)
    real_popen = subprocess.Popen

    class CancelRightAfterExit(real_popen):
        def communicate(self, *args, **kwargs):
            out = super().communicate(*args, **kwargs)
            chat.cancel()  # filho ja saiu: poll() != None
            return out

    monkeypatch.setattr(codex_chat.subprocess, "Popen", CancelRightAfterExit)
    assert chat.answer("oi", {}) == "ola", f"resposta boa relatada como {chat.last_error}"
    assert chat.last_error is None and chat.status == "connected"


def test_codex_kill_failure_is_counted_not_swallowed(tmp_path, caplog):
    chat = _chat(tmp_path, FAKE_OK)

    class Unkillable:
        def terminate(self):
            pass

        def wait(self, timeout=None):
            import subprocess
            raise subprocess.TimeoutExpired("codex", timeout)

        def kill(self):
            raise PermissionError(1, "Operation not permitted")

    with caplog.at_level(logging.WARNING):
        chat._end_process(Unkillable())
    assert chat.process_end_failures == 1 and _warnings(caplog, "not ended")


def test_codex_success_path_unchanged(tmp_path):
    chat = _chat(tmp_path, FAKE_OK)
    assert chat.answer("oi", {}) == "ola"
    assert chat.status == "connected" and chat.last_error is None and chat.last_error_detail is None
    assert json.loads((tmp_path / "state" / "chat_memory.json").read_text(encoding="utf-8"))[-1]["text"] == "ola"
