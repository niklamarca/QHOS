"""Produtor HTTP real -> Brain real, somente loopback e dados sinteticos."""
import asyncio
import json
import urllib.request

from qhos2 import sintetico as si
from qhos2.coletor import Coletor, PAGINA
from qhos2.gravador import Gravador
from qhos2.leitura import Leitor


def test_brain_le_o_coletor_sem_servidor_v1(tmp_path, monkeypatch):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    monkeypatch.setenv("BRAIN_CODEX_CHAT", "0")
    from brain_service.config import ServiceConfig
    from brain_service.observer import QuantObserver
    mercado = si.gerar(17, 30)
    g = Gravador(tmp_path / "dados/capturas", identidade={"system_name": si.SISTEMA,
                 "requested_symbol": "ESZ6", "exchange": "CME"})
    # Quadros sem identidade explicita, como o gerador documenta; manifesto distingue sintetico de Paper.
    for i, (template, dados) in enumerate(mercado.quadros):
        g(dados, template, si.INICIO_NS + i * si.PASSO_NS)
    g.fechar({"estado": "FECHADA", "retrato_completo": True,
              "referencia": {"min_qprice_change": 0.25}}, fechamento_normal=True)
    leitor = Leitor(g.pasta, tick=0.25, simbolo="ESZ6", bolsa="CME")

    async def ensaio():
        coletor = Coletor(token="T" * 43, porta=0, pagina=PAGINA.read_bytes(), leitor=leitor,
                          avisar=lambda _: None)
        pronto = asyncio.Event()
        tarefa = asyncio.create_task(coletor.rodar(pronto))
        try:
            await asyncio.wait_for(pronto.wait(), 5)
            base = f"http://127.0.0.1:{coletor.porta}"
            observer = QuantObserver(ServiceConfig(quant_url=base, state_path=str(tmp_path / "brain"),
                                                  diagnostics_enabled=False))
            recebido = await asyncio.to_thread(observer.collect)
            assert recebido["application_ready"]
            observacao = recebido["qhos2_observation"]
            assert observacao["available"] and observacao["environment"] == si.SISTEMA
            assert observacao["source_kind"] == "synthetic_fixture"
            assert not observacao["quality"]["available_as_live"]
            assert not observacao["permissions"]["can_execute"]
            def pedir(rota, headers=None):
                req = urllib.request.Request(base + rota, headers=headers or {})
                with urllib.request.urlopen(req, timeout=3) as resposta:
                    return resposta.status, resposta.read()
            status, pagina = await asyncio.to_thread(pedir, "/")
            assert status == 200 and pagina == PAGINA.read_bytes()
            try:
                await asyncio.to_thread(pedir, "/api/qhos2/snapshot", {"Origin": "https://outro.example"})
                assert False, "origem externa nao pode ler observacao"
            except urllib.error.HTTPError as erro:
                assert erro.code == 403
        finally:
            tarefa.cancel()
            await asyncio.gather(tarefa, return_exceptions=True)
    asyncio.run(ensaio())
