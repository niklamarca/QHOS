"""Conector WebSocket/TLS real para a sessao (biblioteca `websockets`, mesma versao do ambiente Rithmic do V1).

A biblioteca so e importada na hora de conectar: o resto da V2 e dos testes nao depende dela. A verificacao
do certificado e sempre ligada (contexto padrao do sistema, TLS 1.2 no minimo); `ca_file` so acrescenta uma
autoridade confiavel, para ensaios em loopback. Sem proxy, sem compressao, quadro maximo de 1 MiB.

Falhas de conexao viram erros da sessao com tipo proprio: TLS invalido e handshake recusado NAO sao
transitorios (repetir nao resolve); queda de rede e tempo esgotado sao, e sobem como OSError/TimeoutError.
Nenhuma mensagem inclui texto do servidor.
"""
from __future__ import annotations

import logging
import ssl
from typing import Optional

from qhos2 import rprotocol as p
from qhos2.sessao import PROTOCOLO, ConexaoFechada, SessaoError

TLS = "TLS"
LOCAL_ERROR = "LOCAL_ERROR"

try:
    from websockets.exceptions import ConnectionClosed as _Fechada
    from websockets.exceptions import InvalidHandshake as _Handshake
    from websockets.exceptions import InvalidStatus as _Status
except ImportError:  # sem a biblioteca, so a fabrica real falha (com codigo proprio)
    class _Fechada(Exception):
        pass

    class _Handshake(Exception):
        pass

    class _Status(_Handshake):
        pass


def contexto_tls(ca_file: Optional[str] = None) -> ssl.SSLContext:
    contexto = ssl.create_default_context()
    if ca_file:
        contexto.load_verify_locations(cafile=ca_file)
    contexto.minimum_version = ssl.TLSVersion.TLSv1_2
    return contexto


class ConexaoWs:
    """Uma conexao aberta, no formato que a sessao espera: enviar, receber, fechar."""

    def __init__(self, ws):
        self._ws = ws
        self.fechamento: Optional[dict] = None  # so o codigo e o tamanho do motivo, nunca o texto

    def _registrar(self, erro) -> None:
        recebido, enviado = getattr(erro, "rcvd", None), getattr(erro, "sent", None)
        self.fechamento = {"codigo": getattr(recebido, "code", None),
                           "motivo_len": len(getattr(recebido, "reason", "") or "") if recebido else None,
                           "codigo_enviado": getattr(enviado, "code", None)}

    async def enviar(self, dados: bytes) -> None:
        try:
            await self._ws.send(bytes(dados))
        except _Fechada as erro:
            self._registrar(erro)
            raise ConexaoFechada() from None

    async def receber(self) -> bytes:
        try:
            quadro = await self._ws.recv()
        except _Fechada as erro:
            self._registrar(erro)
            raise ConexaoFechada() from None
        if isinstance(quadro, str):
            raise p.ProtocolError("quadro_de_texto_inesperado")
        return quadro

    async def fechar(self) -> None:
        await self._ws.close()


def fabrica_wss(endpoint: str, *, ca_file: Optional[str] = None, abertura_timeout_s: float = 10.0):
    """Devolve a `fabrica` que a sessao chama uma vez por conexao (descoberta e login)."""

    async def abrir() -> ConexaoWs:
        try:
            from websockets.asyncio.client import connect
        except ImportError:
            raise SessaoError("biblioteca_websockets_ausente", LOCAL_ERROR) from None
        # Logger mudo: nenhum quadro de protocolo vai parar em log, nem em modo DEBUG.
        silencioso = logging.Logger("qhos2.wire", level=logging.CRITICAL + 1)
        silencioso.addHandler(logging.NullHandler())
        silencioso.propagate = False
        try:
            ws = await connect(endpoint, ssl=contexto_tls(ca_file), proxy=None, compression=None,
                               ping_interval=20, ping_timeout=20, open_timeout=abertura_timeout_s,
                               close_timeout=3, max_size=p.LIMITE_QUADRO, max_queue=16, logger=silencioso)
        except ssl.SSLError:
            raise SessaoError("tls_falhou", TLS) from None
        except _Status as erro:
            raise SessaoError(f"handshake_recusado_{erro.response.status_code}", PROTOCOLO) from None
        except _Handshake:
            raise SessaoError("handshake_invalido", PROTOCOLO) from None
        return ConexaoWs(ws)

    return abrir
