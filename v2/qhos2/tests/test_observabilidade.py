import json

from qhos2.coletor import Coletor
from qhos2.observabilidade import ROTAS, resposta, snapshot


def test_processo_sem_dados_nao_se_declara_mercado_ou_execucao():
    coletor = Coletor(token="Z" * 43, relogio=lambda: 1234)
    s = snapshot(coletor)
    assert s["schema_version"] == "qhos2.observation.v1"
    assert s["process"]["state"] == "PROCESS_ONLINE"
    assert s["source_kind"] == "none"
    assert s["environment"] == "unknown"
    assert not s["quality"]["book_valid"] and not s["quality"]["available_as_live"]
    assert not s["permissions"]["can_execute"]
    for rota in ROTAS:
        serialized = json.dumps(resposta(coletor, rota))
        assert coletor._token not in serialized
        assert "/home/" not in serialized


def test_saude_truth_e_busy_tem_escopos_explicitos():
    coletor = Coletor(token="Z" * 43)
    assert resposta(coletor, "/api/health")["status"]["trading_ready"] is False
    assert resposta(coletor, "/api/truth")["research_approved"] is False
    assert resposta(coletor, "/api/busy")["can_restart_capture"] is False
