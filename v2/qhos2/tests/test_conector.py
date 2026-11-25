"""Peca 4c: o conector WebSocket/TLS real, contra um servidor local com certificado efemero (so loopback).

Precisa das bibliotecas `websockets` e `cryptography`; sem elas estes testes sao pulados, e o resto da V2 nao muda.
"""
import asyncio
import contextlib
import datetime
import ipaddress
import socket
import ssl
import sys
from http import HTTPStatus

import pytest

pytest.importorskip("websockets")
pytest.importorskip("cryptography")

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID  # noqa: E402
from websockets.asyncio.server import serve  # noqa: E402

from qhos2 import captura, catalogo, conector  # noqa: E402
from qhos2 import rprotocol as p  # noqa: E402
from qhos2.config import Settings  # noqa: E402
from qhos2.sessao import ConexaoFechada, ConfigSessao, SessaoError  # noqa: E402

# Reaproveita o servidor falso da sessao: o pytest poe esta pasta no caminho de importacao.
from test_sessao import CRED, FECHAR, ConexaoFalsa, Servidor, quadro160  # noqa: E402


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    """CA e certificado de servidor para 127.0.0.1, validos por 1 dia, com tudo que o modo estrito do Python exige."""
    pasta = tmp_path_factory.mktemp("pki")
    agora = datetime.datetime.now(datetime.timezone.utc)
    ca_chave = ec.generate_private_key(ec.SECP256R1())
    nome_ca = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "qhos2-teste-ca")])
    ca = (x509.CertificateBuilder().subject_name(nome_ca).issuer_name(nome_ca).public_key(ca_chave.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(agora - datetime.timedelta(hours=1))
          .not_valid_after(agora + datetime.timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                                       data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                                       encipher_only=False, decipher_only=False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_chave.public_key()), critical=False)
          .sign(ca_chave, hashes.SHA256()))
    chave = ec.generate_private_key(ec.SECP256R1())
    servidor = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]))
                .issuer_name(nome_ca).public_key(chave.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(agora - datetime.timedelta(hours=1)).not_valid_after(agora + datetime.timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(chave.public_key()), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_chave.public_key()), critical=False)
                .sign(ca_chave, hashes.SHA256()))
    caminhos = {"ca": pasta / "ca.pem", "cert": pasta / "servidor.pem", "chave": pasta / "servidor.key"}
    caminhos["ca"].write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    caminhos["cert"].write_bytes(servidor.public_bytes(serialization.Encoding.PEM))
    caminhos["chave"].write_bytes(chave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                      serialization.NoEncryption()))
    return {k: str(v) for k, v in caminhos.items()}


@contextlib.asynccontextmanager
async def servir(pki, handler, **kw):
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexto.load_cert_chain(pki["cert"], pki["chave"])
    async with serve(handler, "127.0.0.1", 0, ssl=contexto, **kw) as servidor:
        yield f"wss://127.0.0.1:{servidor.sockets[0].getsockname()[1]}"


async def eco(ws):
    async for quadro in ws:
        await ws.send(quadro)


def rodar(coro):
    return asyncio.run(coro)


# ---- conexao ---------------------------------------------------------------------------------------------

def test_ida_e_volta_com_certificado_verificado(pki):
    async def cena():
        async with servir(pki, eco) as url:
            con = await conector.fabrica_wss(url, ca_file=pki["ca"])()
            await con.enviar(b"\x01\x02\x03")
            resposta = await con.receber()
            await con.fechar()
            return resposta
    assert rodar(cena()) == b"\x01\x02\x03"


def test_certificado_nao_confiavel_e_recusado_sem_repetir(pki):
    async def cena():
        async with servir(pki, eco) as url:
            with pytest.raises(SessaoError) as erro:
                await conector.fabrica_wss(url)()  # sem a CA de teste
            return erro.value
    erro = rodar(cena())
    assert (erro.codigo, erro.tipo) == ("tls_falhou", "TLS")


def test_contexto_tls_e_estrito():
    contexto = conector.contexto_tls()
    assert contexto.verify_mode == ssl.CERT_REQUIRED and contexto.check_hostname is True
    assert contexto.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_servidor_fecha_a_conexao(pki):
    async def encerrar(ws):
        await ws.close()

    async def cena():
        async with servir(pki, encerrar) as url:
            con = await conector.fabrica_wss(url, ca_file=pki["ca"])()
            with pytest.raises(ConexaoFechada):
                await con.receber()
            with pytest.raises(ConexaoFechada):
                await con.enviar(b"x")
            await con.fechar()
            return con.fechamento
    fechamento = rodar(cena())
    assert fechamento["codigo"] == 1000 and isinstance(fechamento["motivo_len"], int)


def test_quadro_de_texto_e_recusado(pki):
    async def texto(ws):
        await ws.send("oi")
        await asyncio.sleep(0.3)

    async def cena():
        async with servir(pki, texto) as url:
            con = await conector.fabrica_wss(url, ca_file=pki["ca"])()
            with pytest.raises(p.ProtocolError, match="quadro_de_texto_inesperado"):
                await con.receber()
            await con.fechar()
    rodar(cena())


def test_quadro_maior_que_1_mib_derruba_a_conexao(pki):
    async def gigante(ws):
        await ws.send(b"x" * (p.LIMITE_QUADRO + 1))
        await asyncio.sleep(0.5)

    async def cena():
        async with servir(pki, gigante, max_size=None) as url:
            con = await conector.fabrica_wss(url, ca_file=pki["ca"])()
            with pytest.raises(ConexaoFechada):
                await con.receber()
            await con.fechar()
            return con.fechamento
    fechamento = rodar(cena())
    assert fechamento["codigo_enviado"] == 1009 and fechamento["codigo"] is None  # fomos nos que derrubamos


def test_handshake_recusado(pki):
    def negar(conexao, requisicao):
        return conexao.respond(HTTPStatus.FORBIDDEN, "negado\n")

    async def cena():
        async with servir(pki, eco, process_request=negar) as url:
            with pytest.raises(SessaoError) as erro:
                await conector.fabrica_wss(url, ca_file=pki["ca"])()
            return erro.value
    erro = rodar(cena())
    assert (erro.codigo, erro.tipo) == ("handshake_recusado_403", "PROTOCOL") and "negado" not in str(erro)


def test_porta_fechada_sobe_como_erro_de_rede_transitorio(pki):
    # porta ligada por este teste e nunca posta em listen: ninguem mais a ocupa no intervalo e todo connect e
    # recusado; nao reutiliza a porta de um servidor recem-fechado (QHOS-AUD-124). No Linux o RST chega na hora;
    # no Windows o connect recusado so sobe depois das retransmissoes do SYN (~2 s medidos), entao o prazo de
    # abertura fica folgado para que a recusa, e nao o prazo, seja o que o teste observa: TimeoutError nao passa
    reservada = socket.socket()
    reservada.bind(("127.0.0.1", 0))
    porta = reservada.getsockname()[1]

    async def cena():
        with pytest.raises(ConnectionRefusedError):
            await conector.fabrica_wss(f"wss://127.0.0.1:{porta}", ca_file=pki["ca"], abertura_timeout_s=10.0)()
    try:
        rodar(cena())
    finally:
        reservada.close()


def test_sem_a_biblioteca_o_erro_tem_codigo_proprio(monkeypatch):
    monkeypatch.setitem(sys.modules, "websockets.asyncio.client", None)
    with pytest.raises(SessaoError) as erro:
        rodar(conector.fabrica_wss("wss://127.0.0.1:1")())
    assert erro.value.codigo == "biblioteca_websockets_ausente"


# ---- sessao inteira pela rede real (TLS em loopback) ---------------------------------------------------------

def servidor_de_protocolo(servidor):
    """Faz o servidor falso da sessao falar por um WebSocket de verdade."""
    async def tratar(ws):
        con = ConexaoFalsa(servidor)
        servidor.conexoes.append(con)

        async def bombear():
            while True:
                item = await con.fila.get()
                if item is FECHAR:
                    await ws.close()
                    return
                await ws.send(item)
        tarefa = asyncio.create_task(bombear())
        try:
            async for dados in ws:
                await con.enviar(dados)
        finally:
            tarefa.cancel()
    return tratar


def test_captura_inteira_sobre_websocket_tls(pki, tmp_path):
    servidor = Servidor(durante_retrato=(quadro160(1),), apos_assinar=tuple(quadro160(i) for i in range(10, 16)))
    ajustes = Settings(data_root=tmp_path / "dados")

    async def cena():
        async with servir(pki, servidor_de_protocolo(servidor)) as url:
            fabrica = conector.fabrica_wss(url, ca_file=pki["ca"])
            cfg = ConfigSessao(template_version="5.42", symbol="ESZ6", exchange="CME", duracao_s=0.5,
                               resposta_timeout_s=2.0, retrato_timeout_s=2.0, settings=ajustes)
            return await captura.capturar(cfg, fabrica, tmp_path / "capturas", CRED)
    resultado = rodar(cena())
    assert resultado["erro"] is None and resultado["status"]["estado"] == "FECHADA"
    assert len(servidor.conexoes) == 2  # descoberta + autenticada
    relatorio = catalogo.verificar_captura(resultado["pasta"])
    assert relatorio["verificada"] is True and relatorio["quadros"] == len(servidor.mercado) > 0
    assert resultado["status"]["relogio"] is not None and resultado["status"]["heartbeats_confirmados"] >= 2


def test_queda_do_servidor_no_meio_da_sessao(pki, tmp_path):
    servidor = Servidor(apos_assinar=(quadro160(1), FECHAR))
    ajustes = Settings(data_root=tmp_path / "dados")

    async def cena():
        async with servir(pki, servidor_de_protocolo(servidor)) as url:
            fabrica = conector.fabrica_wss(url, ca_file=pki["ca"])
            cfg = ConfigSessao(template_version="5.42", symbol="ESZ6", exchange="CME", duracao_s=2.0,
                               resposta_timeout_s=2.0, retrato_timeout_s=2.0, settings=ajustes)
            return await captura.capturar(cfg, fabrica, tmp_path / "capturas", CRED)
    resultado = rodar(cena())
    assert (resultado["erro"], resultado["tipo_de_falha"]) == ("conexao_perdida", "TRANSIENT_TRANSPORT")
    assert catalogo.verificar_captura(resultado["pasta"])["verificada"] is True
