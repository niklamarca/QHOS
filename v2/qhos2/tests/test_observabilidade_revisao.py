import asyncio
from pathlib import Path
import pytest
from qhos2.coletor import Coletor
from qhos2.leitura import Leitor
from qhos2.observabilidade import snapshot


def test_received_clock_is_not_vendor_clock(tmp_path):
    # captura real: o carimbo do fornecedor (75 ms antes) e a chegada local diferem, e o snapshot mostra a chegada;
    # nada de estado privado do coletor (QHOS-AUD-072)
    from qhos2 import eventos as ev, sintetico as si
    pasta = si.gravar(si.gerar(1, 3), tmp_path)
    reader = Leitor(pasta, tick=.25, simbolo="SINTETICO", bolsa="QHOS2")
    collector = Coletor(token="Z" * 43, leitor=reader)
    collector.traduzir(reader.avancar(), True)
    fornecedor = max(e.carimbo_ns for e in ev.eventos_da_captura(pasta) if e.carimbo_ns is not None)
    assert reader.ultimo_recebido_ns is not None and fornecedor != reader.ultimo_recebido_ns
    assert snapshot(collector)["quality"]["last_frame_received_ns"] == str(reader.ultimo_recebido_ns)


def test_reader_exception_is_not_silent_zombie_collector(tmp_path):
    async def run():
        reader = Leitor(tmp_path, tick=.25, simbolo="ESZ6", bolsa="CME")
        def inaccessible(*a):
            raise PermissionError("synthetic filesystem denial")
        reader.avancar = inaccessible
        avisos, avisou = [], asyncio.Event()

        def avisar(texto):
            avisos.append(texto)
            avisou.set()                      # o coletor avisa no proprio loop: sinal deterministico, sem sleep
        collector = Coletor(token="Z" * 43, leitor=reader, porta=0, avisar=avisar)
        ready = asyncio.Event()
        task = asyncio.create_task(collector.rodar(ready))
        await ready.wait()
        aviso = asyncio.ensure_future(avisou.wait())
        try:
            feitas, _ = await asyncio.wait({task, aviso}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
            assert feitas, "nem aviso nem fim do coletor em 5 s"
            # a leitura morta vira FALHA com codigo estatico e o coletor segue no ar (anuncia livro vazio e lacuna);
            # 'task.done() or FALHA' aceitava qualquer morte da tarefa (QHOS-AUD-074)
            assert (reader.estado, reader.codigo) == ("FALHA", "arquivo_inacessivel") and not task.done()
            assert avisos
        finally:
            aviso.cancel()
            task.cancel()
            await asyncio.gather(task, aviso, return_exceptions=True)
    asyncio.run(run())


def test_unexpected_task_exception_exits_with_static_error(tmp_path):
    async def run():
        reader = Leitor(tmp_path, tick=.25, simbolo="ESZ6", bolsa="CME")
        def unexpected(*a):
            raise RuntimeError("PRIVATE_PATH_MUST_NOT_ESCAPE")
        reader.avancar = unexpected
        collector = Coletor(token="Z" * 43, leitor=reader, porta=0, avisar=lambda *a:None)
        with pytest.raises(RuntimeError, match="^tarefa_do_coletor_falhou$"):
            await asyncio.wait_for(collector.rodar(), 2)
    asyncio.run(run())


def test_api_routes_are_not_browser_cross_origin_or_token_channels():
    from websockets.datastructures import Headers
    from websockets.http11 import Request, Response
    from qhos2.observabilidade import ROTAS
    class Conn:
        def respond(self, status, body):
            return Response(int(status), str(status), Headers(), body.encode())
    collector = Coletor(token="Z" * 43, porta=8766, avisar=lambda *a:None)
    for path in ROTAS:
        for extra in ({"Origin":"https://attacker.example"}, {"Origin":"null"},
                      {"Sec-Fetch-Site":"cross-site"}, {"Sec-Fetch-Site":"same-site"},
                      {"Host":"attacker.example:8766"}):
            headers = {"Host":"127.0.0.1:8766", **extra}
            assert collector._conferir_pedido(Conn(), Request(path, Headers(headers))).status_code == 403
        response = collector._conferir_pedido(Conn(), Request(path, Headers({"Host":"127.0.0.1:8766"})))
        assert response.status_code == 200
        assert b"Z" * 43 not in response.body
        assert "Access-Control-Allow-Origin" not in response.headers
