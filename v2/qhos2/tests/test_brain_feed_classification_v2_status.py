"""evidence_from_status le o status real de Sessao.status() da V2 (QHOS-AUD-138). Sessao construida em bancada,
sem servidor; nenhuma taxonomia nova (so os nomes de chave)."""
from brain_service.feed_classification import classify_feed, evidence_from_status


def status_real():
    from qhos2.sessao import ConfigSessao, Sessao
    config = ConfigSessao(template_version="5.42", symbol="ESZ6", exchange="CME")
    sessao = Sessao(config, None, ao_receber=lambda *_: None)
    return sessao.status(), config.settings


def test_status_v2_nao_tem_as_chaves_v1():
    status, _ = status_real()
    assert "login_confirmado" in status and "sistema" in status
    assert "login_confirmed" not in status and "system_name" not in status and "endpoint" not in status


def test_evidence_from_status_le_sistema_e_login_confirmado_da_v2():
    status, settings = status_real()
    evidencia = evidence_from_status(status, source_kind="current_session")
    assert evidencia["system_name"] == status["sistema"] == settings.system_name
    assert evidencia["login_confirmed"] is False
    confirmado = evidence_from_status(dict(status, login_confirmado=True), source_kind="current_session",
                                      endpoint=settings.endpoint)
    assert confirmado["login_confirmed"] is True and confirmado["endpoint"] == settings.endpoint
    assert confirmado["provider"] == "rithmic_protocol" and confirmado["source_kind"] == "current_session"
    # A classificacao continua derivada so da evidencia; o nome do sistema V2 nao e promovido por este teste.
    resultado = classify_feed(confirmado)
    assert resultado["production_market_data"] is False and set(resultado) == {"environment_kind", "modality", "production_market_data", "basis"}


def test_evidence_from_status_mantem_o_formato_v1_do_manifesto():
    v1 = {"system_name": "Rithmic Test", "login_confirmed": True, "endpoint": "wss://rituz00100.rithmic.com:443", "plant": "ticker"}
    evidencia = evidence_from_status(v1, source_kind="historical_capture", verified=True)
    assert evidencia["system_name"] == "Rithmic Test" and evidencia["login_confirmed"] is True
    assert evidencia["endpoint"] == "wss://rituz00100.rithmic.com:443" and evidencia["plant"] == "ticker"
    assert classify_feed(evidencia) == {"environment_kind": "vendor_test", "modality": "replay", "production_market_data": False,
                                        "basis": "recorded_capture:Rithmic_Test:login_confirmed:endpoint_rituz00100.rithmic.com:replay_verified:policy_ENVIRONMENT_TAXONOMY_POLICY.json"}
