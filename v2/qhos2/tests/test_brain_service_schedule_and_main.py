"""restore_test_schedule e `python -m brain_service` em bancada, sem servico real (QHOS-AUD-272)."""
import asyncio
import os

import pytest

from brain_service.config import ServiceConfig, normalize_test_interval, restore_test_schedule

NOW = 1_800_000_000.0
CAD = 3600


@pytest.fixture(autouse=True)
def isolado(monkeypatch, tmp_path):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    for name in ("BRAIN_QUANT_ROOT", "BRAIN_STATE_DIR", "BRAIN_RUNTIME_DIR", "BRAIN_QUANT_OBSERVE_URL",
                 "BRAIN_SERVICE_PORT", "BRAIN_SERVICE_CYCLE_S"):
        monkeypatch.delenv(name, raising=False)


# --- restore_test_schedule ---------------------------------------------------------

def test_restore_test_schedule_nem_reexecuta_apos_reboot_nem_espera_para_sempre():
    # Tentativa interrompida pelo reboot: o cooldown dela vale, sem virar execucao.
    state = {"phase": "INTERRUPTED", "last_run": NOW - 600, "next_due": NOW - 600 + CAD, "history": []}
    restore_test_schedule(state, CAD, previous_cadence=CAD, now=NOW)
    assert state["next_due"] == NOW - 600 + CAD > NOW
    assert state["cadence_anchor_at"] is None
    # DEFERRED legado sem ancora nem historico: espera um intervalo a partir da primeira recuperacao, e so um.
    legado = {"phase": "DEFERRED", "last_run": None, "next_due": NOW - 1, "history": []}
    restore_test_schedule(legado, CAD, previous_cadence=CAD, now=NOW)
    assert legado["next_due"] == NOW + CAD
    assert legado["schedule_recovery_reason"] == "legacy_anchor_unknown_not_an_execution"
    depois = dict(legado)
    assert restore_test_schedule(depois, CAD, previous_cadence=CAD, now=NOW + 1000) is False
    assert depois["schedule_recovery_until"] == legado["schedule_recovery_until"] == NOW + CAD


def test_restore_test_schedule_deferred_com_ancora_e_primeira_execucao_conhecida():
    com_ancora = {"phase": "DEFERRED", "next_due": NOW + 30, "cadence_anchor_at": NOW - 100,
                  "schedule_anchor_version": 1, "history": []}
    restore_test_schedule(com_ancora, CAD, previous_cadence=CAD, now=NOW)
    assert com_ancora["next_due"] == NOW - 100 + CAD
    primeira = {"phase": "DEFERRED", "next_due": "invalido", "cadence_anchor_at": None,
                "schedule_anchor_version": 1, "history": []}
    restore_test_schedule(primeira, CAD, previous_cadence=CAD, now=NOW)
    assert primeira["next_due"] == NOW + 30 and "schedule_recovery_until" not in primeira


def test_restore_test_schedule_recupera_ancora_do_historico_e_rejeita_timestamp_invalido():
    historico = {"phase": "DEFERRED", "next_due": NOW + 30, "history": [
        {"phase": "DEFERRED", "last_run": NOW - 10}, {"phase": "PASSED", "last_run": NOW - 900}]}
    restore_test_schedule(historico, CAD, previous_cadence=CAD, now=NOW)
    assert historico["cadence_anchor_at"] == NOW - 900 and historico["cadence_anchor_kind"] == "recovered_from_legacy_history"
    assert historico["next_due"] == NOW - 900 + CAD
    invalido = {"phase": "PASSED", "last_run": "ontem", "next_due": NOW - 5, "history": []}
    restore_test_schedule(invalido, CAD, previous_cadence=CAD, now=NOW)
    assert invalido["schedule_recovery_reason"] == "invalid_legacy_timestamp_not_an_execution"
    assert invalido["next_due"] == NOW + CAD and invalido["cadence_anchor_at"] is None


def test_restore_test_schedule_troca_de_cadencia_e_idempotencia():
    state = {"phase": "PASSED", "last_run": NOW - 1800, "next_due": NOW - 1800 + CAD, "history": []}
    assert restore_test_schedule(state, 7200, previous_cadence=CAD, now=NOW) is True
    assert state["cadence_anchor_kind"] == "legacy_last_run" and state["cadence_anchor_at"] == NOW - 1800
    assert state["next_due"] == NOW - 1800 + 7200 and state["cadence_s"] == 7200
    assert state["schedule_migration"]["from_interval_s"] == CAD and state["schedule_migration"]["to_interval_s"] == 7200
    congelado = dict(state)
    assert restore_test_schedule(state, 7200, previous_cadence=7200, now=NOW + 5) is False
    assert state == congelado
    assert normalize_test_interval(10) == 900 and normalize_test_interval("x") == 3600


# --- python -m brain_service -------------------------------------------------------

class _BrainFalso:
    instancias = []

    def __init__(self, cfg):
        self.cfg = cfg
        self.eventos = []
        _BrainFalso.instancias.append(self)

    async def start(self):
        self.eventos.append("start")

    async def stop(self):
        self.eventos.append("stop")


def _preparar(monkeypatch, tmp_path, servir):
    import uvicorn
    import brain_service.config as config
    import brain_service.process as process
    import brain_service.server as server
    cfg = ServiceConfig(state_path=str(tmp_path / "state"), runtime_path=str(tmp_path / "run"), diagnostics_enabled=False)
    monkeypatch.setattr(config, "load_config", lambda: cfg)
    monkeypatch.setattr(process, "StandaloneBrain", _BrainFalso)
    monkeypatch.setattr(server, "build_app", lambda brain: {"app": brain})
    monkeypatch.setattr(server, "assert_no_privileged_routes", lambda app: None)
    chamadas = []
    class _Config:
        def __init__(self, app, **kw):
            chamadas.append(kw)
    class _Server:
        def __init__(self, config):
            pass
        async def serve(self):
            await servir()
    monkeypatch.setattr(uvicorn, "Config", _Config)
    monkeypatch.setattr(uvicorn, "Server", _Server)
    for name in ("BRAIN_QUANT_OBSERVE_URL", "BRAIN_SERVICE_PORT", "BRAIN_SERVICE_CYCLE_S"):
        monkeypatch.setenv(name, "")
    _BrainFalso.instancias.clear()
    return cfg, chamadas


def test_main_brain_service_prepara_dirs_e_lock(monkeypatch, tmp_path):
    from brain_service.__main__ import _parse, _serve
    from brain_service.supervisor import SingleInstanceLock
    async def servir():
        return None
    cfg, chamadas = _preparar(monkeypatch, tmp_path, servir)
    args = _parse(["--port", "18099"])
    assert args.host == "127.0.0.1" and args.port == 18099 and args.quant_url == cfg.quant_url
    asyncio.run(_serve(args))
    assert cfg.state_dir.is_dir() and cfg.runtime_dir.is_dir()
    if os.name != "nt":
        assert oct(cfg.state_dir.stat().st_mode & 0o777) == "0o750"
    assert chamadas == [{"host": "127.0.0.1", "port": 18099, "log_level": "info"}]
    assert [b.eventos for b in _BrainFalso.instancias] == [["start", "stop"]]
    assert os.environ["BRAIN_SERVICE_PORT"] == "18099"
    # O lock foi liberado no fim: outra instancia consegue adquiri-lo.
    lock = SingleInstanceLock(cfg.runtime_dir / "brain_service.lock")
    assert lock.acquire()
    lock.release()


def test_main_brain_service_recusa_segunda_instancia_e_binding_nao_loopback(monkeypatch, tmp_path):
    from brain_service.__main__ import _parse, _serve
    from brain_service.runtime_fs import prepare_runtime_dirs
    from brain_service.supervisor import SingleInstanceLock
    async def servir():
        pytest.fail("o servidor nao pode subir com o lock ocupado")
    cfg, _ = _preparar(monkeypatch, tmp_path, servir)
    prepare_runtime_dirs((cfg.runtime_dir,))
    ocupado = SingleInstanceLock(cfg.runtime_dir / "brain_service.lock")
    assert ocupado.acquire()
    try:
        with pytest.raises(RuntimeError, match="already running"):
            asyncio.run(_serve(_parse([])))
    finally:
        ocupado.release()
    assert _BrainFalso.instancias == []
    with pytest.raises(SystemExit):
        _parse(["--host", "0.0.0.0"])


def test_main_brain_service_trata_ctrl_c_como_saida_limpa(monkeypatch, tmp_path):
    from brain_service.__main__ import main
    async def servir():
        raise KeyboardInterrupt
    cfg, _ = _preparar(monkeypatch, tmp_path, servir)
    assert main([]) == 0
    assert [b.eventos for b in _BrainFalso.instancias] == [["start", "stop"]]
