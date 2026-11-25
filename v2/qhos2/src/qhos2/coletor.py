"""Coletor QHOS (integracao, tarefas 4, 5, 6, 8, 9, 10 e 12): o servidor `qhos.dashboard.v1` que o terminal do navegador conecta.

So escuta em 127.0.0.1 (`ws://`, sem TLS, porque o trafego nao sai da maquina; decisao de 25/10). Contra outro
site aberto no mesmo navegador, tres barreiras, todas antes de qualquer dado:
  - Host do pedido igual a 127.0.0.1:<porta> (protege contra DNS rebinding);
  - Origin igual a pagina autorizada (o navegador sempre manda; sem Origin, recusado). Por padrao a pagina e a do
    proprio coletor (tarefa 8): `http://127.0.0.1:<porta>/` entrega o `terminal/index.html` do repositorio, entao a
    pagina e os dados ficam na mesma origem e o terminal nao depende do V1;
  - token no `hello`, comparado em tempo constante. O token fica num arquivo 0600 fora do repositorio, criado na
    primeira vez, e NUNCA e impresso. Nao e a senha da Rithmic e nunca deve ser.

Conexao automatica (tarefa 12): a pagina que o coletor serve busca `GET /conexao` e recebe o endereco e o token,
para o terminal conectar sozinho (inclusive depois de F5). O token nunca vai dentro da pagina nem para outro lugar:
so responde ao `fetch` da propria pagina, o que o navegador marca com `Sec-Fetch-Site: same-origin`,
`Sec-Fetch-Mode: cors|same-origin` e `Sec-Fetch-Dest: empty` (cabecalhos que script de outro site nao consegue
falsificar). Digitar o endereco na barra, abrir de outro site, incorporar em iframe, script ou imagem, ou pedir sem
esses cabecalhos recebe o mesmo 404 de qualquer endereco inexistente. Sem a pagina configurada, o endereco nao existe.
Sem cabecalhos CORS: outro site nao le a resposta. Nao e protecao contra um programa que roda no seu proprio usuario,
que ja le o arquivo do token.

Protocolo: hello/welcome, heartbeat, catalogo com um contrato so (ESZ6), selecao fixa (`selection.set` diferente
da atual e recusado) e historico de candles (tarefa 10, `historico.py`): capturas encerradas do ESZ6 mais a captura
seguida, com a janela de teste de 9 a 20/11 escondida ate 21/11. Nao faz login, nao importa a sessao nem o conector: a unica consumidora da
Rithmic continua sendo a captura (regra 16).

Dados (tarefa 5): segue a captura com a leitura ao vivo (`leitura.py`, em outra thread, so lendo o arquivo) e
traduz para o terminal, sem inventar nada:
  - livro: os 40 melhores niveis de cada lado, no maximo 4 vezes por segundo; `ready` so quando a leitura nao
    falhou e o livro esta qualificado (pronto, sem anomalia, nao cruzado); senao vai vazio com `ready` falso;
    `sequenceVerified` sempre falso, porque a Rithmic nao manda numero de sequencia confiavel (plano, secao 7);
  - negocio: cada 150 com preco e quantidade; lado do agressor so quando a Rithmic informou, senao `unknown`
    (o terminal nao adivinha); o id e `<indice do quadro>-<posicao>`, unico e igual em qualquer releitura; o
    negocio-retrato (o ultimo antes da assinatura, de minutos antes) nao e enviado;
  - cotacao: ultimo negocio e melhor compra e venda (estas so com o livro qualificado);
  - horario: o carimbo do servidor da Rithmic quando o quadro tem, senao a chegada local; nunca o relogio de agora;
  - lacuna: se a leitura falhar, o livro vai vazio, uma lacuna e enviada e nada mais e enviado.
Enquanto a leitura alcanca o fim do arquivo (terminal aberto no meio da captura), negocios antigos nao sao enviados.

Percurso (tarefa 9): a cada segundo, uma mensagem `pipeline` com uma bolinha por camada (rithmic, sessao,
gravacao, livro, leitura, coletor). O coletor nao fala com a Rithmic: so afirma o que o arquivo gravado mostra, e
nenhuma camada fica verde por falta de informacao. Muito tempo sem quadros fica cinza (desconhecido), porque daqui
nao da para distinguir mercado fechado de conexao caida.

Conferencia (tarefa 6, `--conferir`): le uma captura gravada inteira pelo mesmo caminho do terminal e compara com o
replay independente (`livro.livro_da_captura` e `eventos.eventos_da_captura`): os 40 niveis de cada lado do ultimo
livro, todos os negocios pelo id e o formato de cada mensagem com as regras da ponte do terminal. So mostra contagens.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import sys
import time
from http import HTTPStatus
from pathlib import Path
from typing import List, Optional

from qhos2 import eventos as ev
from qhos2.catalogo import ARQUIVO_LEGADO
from qhos2.historico import Barras, Historico, HistoricoError
from qhos2.leitura import AGUARDANDO, ENCERRADA, FALHA, Leitor

PROTOCOLO = "qhos.dashboard.v1"
HOST = "127.0.0.1"
PORTA_PADRAO = 8766
_PAGINA_INSTALADA = Path(__file__).resolve().parent / "terminal" / "index.html"
PAGINA = (_PAGINA_INSTALADA if _PAGINA_INSTALADA.is_file() else
          Path(__file__).resolve().parents[2] / "terminal" / "index.html")
_CABECALHOS_PAGINA = (("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store"),
                      ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"),
                      ("Referrer-Policy", "no-referrer"))
_CABECALHOS_CONEXAO = (("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store"),
                       ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"),
                       ("Referrer-Policy", "no-referrer"), ("Cross-Origin-Resource-Policy", "same-origin"))
_FETCH_DA_PAGINA = {"Sec-Fetch-Site": ("same-origin",), "Sec-Fetch-Mode": ("cors", "same-origin"),
                    "Sec-Fetch-Dest": ("empty",)}
TOKEN_PADRAO = Path.home() / ".config" / "qhos2" / "coletor.token"
HEARTBEAT_S = 5
PRAZO_HELLO_S = 5
MAX_CLIENTES = 4
MAX_MENSAGEM = 16 * 1024
MAX_INVALIDAS = 5
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_ORIGEM = re.compile(r"^http://127\.0\.0\.1:[0-9]{1,5}$")
_ID_PEDIDO = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_ID_TERMINAL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}$")     # mesma regra da ponte do terminal
NIVEIS = 40
LIVRO_A_CADA_S = 0.25
LER_A_CADA_S = 0.05
BLOCO_LEITURA = 512 * 1024             # bytes por volta: a leitura alcanca sem prender o servidor
ALCANCADO_BYTES = 1024 ** 2            # menos que isso por ler: a leitura acompanha a gravacao
PERCURSO_A_CADA_S = 1.0
QUADROS_ATENCAO_S = 5                  # arquivo parado ha mais que isso: amarelo
QUADROS_DESCONHECIDO_S = 60            # ha mais que isso: cinza (mercado fechado ou conexao caida, nao se sabe)

ESZ6 = {
    "id": "rithmic:CME:ESZ6", "provider": "rithmic", "symbol": "ESZ6", "name": "E-mini S&P 500 · dezembro de 2026",
    "exchange": "CME", "currency": "USD", "assetClass": "future", "expiry": "2026-12-18", "tickSize": 0.25,
    "priceDecimals": 2, "quantityUnit": "contratos", "contractMultiplier": 50,
    "capabilities": {"quotes": True, "trades": True, "depth": True, "history": False},
}


class ColetorError(ValueError):
    """Configuracao recusada; a mensagem e um codigo curto, sem o token."""


def ler_ou_criar_token(caminho: Path, *, novo: bool = False) -> str:
    """Token de 256 bits num arquivo so do usuario (0600, pasta 0700). Recusa arquivo com permissao aberta."""
    caminho = Path(caminho)
    caminho.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if caminho.is_symlink():
        raise ColetorError("token_nao_regular")
    if novo and caminho.exists():
        ler_ou_criar_token(caminho)  # valida antes de remover ou substituir
        caminho.unlink()
    if not caminho.exists():
        descritor = os.open(caminho, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descritor, "w", encoding="ascii") as f:
            f.write(secrets.token_urlsafe(32) + "\n")
    fd = os.open(caminho, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "r", encoding="ascii") as arquivo:
        info = os.fstat(arquivo.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ColetorError("token_nao_regular")
        if info.st_mode & 0o077:
            raise ColetorError("token_com_permissao_aberta")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise ColetorError("token_outro_usuario")
        try:
            token = arquivo.read(128).strip()
        except UnicodeError:
            raise ColetorError("token_invalido_no_arquivo") from None
    if not _TOKEN.fullmatch(token):
        raise ColetorError("token_invalido_no_arquivo")
    return token


def raiz_para_captura(captura=None, raiz=None, environ=None) -> Path:
    """Uma raiz explicita governa captura e derivados; nunca combina duas arvores silenciosamente."""
    from qhos2.config import ConfigError, Settings, validar_data_root
    env = os.environ if environ is None else environ
    explicita = raiz if raiz is not None else env.get("QHOS2_DATA_ROOT")
    dados = validar_data_root(explicita) if explicita else Settings.from_environ(env).data_root
    if captura is not None:
        pasta = Path(captura).expanduser().resolve()
        if pasta.parent.name != "capturas":
            raise ConfigError("captura_precisa_ficar_em_raiz_capturas")
        origem = validar_data_root(pasta.parent.parent)
        if explicita and origem != dados:
            raise ConfigError("captura_e_historico_em_raizes_diferentes")
        dados = origem
    return dados


def _sem_usuario(caminho: Path) -> str:
    """Caminho para mostrar na tela: a pasta pessoal vira ~ (o nome do usuario nao aparece em saida colada)."""
    caminho = Path(caminho).expanduser().resolve()
    casa = Path.home().resolve()
    return "~/" + str(caminho.relative_to(casa)) if casa in caminho.parents else str(caminho)


def _json(bruto) -> Optional[dict]:
    if not isinstance(bruto, str) or len(bruto) > MAX_MENSAGEM:
        return None
    try:
        obj = json.loads(bruto)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _tempo(ns: int) -> dict:
    return {"time": ns // 1_000_000, "timeNs": str(ns)}


def _quando(evento: ev.Evento) -> int:
    return evento.carimbo_ns if evento.carimbo_ns is not None else evento.recebido_ns


def mensagem_livro(livro, base: dict, tempo_ns: int, vale: bool, tick: float) -> dict:
    """Livro para o terminal: 40 niveis por lado em preco; vazio e `ready` falso se o livro nao vale."""
    compra, venda = livro.niveis(ev.COMPRA, NIVEIS), livro.niveis(ev.VENDA, NIVEIS)
    pronto = vale and bool(compra) and bool(venda)
    return {"type": "book", **base, **_tempo(tempo_ns), "ready": pronto, "sequenceVerified": False,
            "bids": [[p * tick, q] for p, q, _ in compra] if pronto else [],
            "asks": [[p * tick, q] for p, q, _ in venda] if pronto else []}


def mensagem_negocio(evento: ev.Evento, base: dict, tick: float) -> Optional[dict]:
    """Negocio para o terminal; None para o negocio-retrato e para negocio sem preco ou quantidade positivos."""
    if evento.tipo != ev.NEGOCIO or evento.retrato or not evento.preco or not evento.quantidade or evento.preco <= 0 \
            or evento.quantidade <= 0:
        return None
    lado = {ev.COMPRA: "buy", ev.VENDA: "sell"}.get(evento.lado, "unknown")
    return {"type": "trade", **base, **_tempo(_quando(evento)), "id": f"{evento.indice}-{evento.posicao}",
            "price": evento.preco * tick, "size": evento.quantidade, "side": lado}


def mensagem_cotacao(livro, ultimo_preco: Optional[float], base: dict, tempo_ns: int, vale: bool,
                     tick: float) -> Optional[dict]:
    compra = livro.melhor(ev.COMPRA) if vale else None
    venda = livro.melhor(ev.VENDA) if vale else None
    if ultimo_preco is None and (compra is None or venda is None):
        return None
    mensagem = {"type": "ticker", **base, **_tempo(tempo_ns)}
    if ultimo_preco is not None:
        mensagem["price"] = ultimo_preco
    if compra is not None and venda is not None:
        mensagem.update(bid=compra * tick, ask=venda * tick)
    return mensagem


def fora_do_formato(mensagem: dict, tick: float) -> Optional[str]:
    """O motivo pelo qual a ponte do terminal recusaria a mensagem, ou None (regras de `qhos-bridge.js`)."""
    tempo, tempo_ns = mensagem.get("time"), mensagem.get("timeNs")
    if not isinstance(tempo, int) or tempo <= 0 or not isinstance(tempo_ns, str) or int(tempo_ns) // 1_000_000 != tempo:
        return "horario"

    def no_tick(preco):
        return isinstance(preco, float) and preco > 0 and abs(preco / tick - round(preco / tick)) < 1e-9

    if mensagem["type"] == "trade":
        if not _ID_TERMINAL.fullmatch(mensagem.get("id", "")):
            return "id"
        if not no_tick(mensagem.get("price")) or not isinstance(mensagem.get("size"), int) or mensagem["size"] <= 0:
            return "preco_ou_quantidade"
        return None if mensagem.get("side") in ("buy", "sell", "unknown") else "lado"
    if mensagem["type"] == "book":
        if not isinstance(mensagem.get("ready"), bool):
            return "ready"
        if not mensagem["ready"]:
            return None
        for lado, decrescente in (("bids", True), ("asks", False)):
            niveis = mensagem.get(lado)
            if not isinstance(niveis, list) or not 1 <= len(niveis) <= NIVEIS:
                return "niveis"
            if not all(isinstance(n, list) and len(n) == 2 and no_tick(n[0]) and isinstance(n[1], int) and n[1] > 0
                       for n in niveis):
                return "nivel"
            precos = [n[0] for n in niveis]
            if precos != sorted(precos, reverse=decrescente) or len(set(precos)) != len(precos):
                return "ordem_dos_niveis"
        return "cruzado" if mensagem["bids"][0][0] >= mensagem["asks"][0][0] else None
    return None


def conferir_captura(pasta, *, tick: float = ESZ6["tickSize"], simbolo: str = ESZ6["symbol"],
                     bolsa: str = ESZ6["exchange"]) -> dict:
    """Tarefa 6: o que o terminal receberia de uma captura gravada, comparado com o replay independente."""
    from qhos2.livro import livro_da_captura

    leitor = Leitor(pasta, tick=tick, simbolo=simbolo, bolsa=bolsa)
    coletor = Coletor(token=secrets.token_urlsafe(32), porta=0, leitor=leitor, avisar=lambda _: None)
    negocios = []
    while leitor.estado not in (ENCERRADA, FALHA):
        quadros = leitor.avancar()
        if not quadros and not leitor.resumo()["pendente_bytes"] and leitor.estado != ENCERRADA:
            if not (Path(pasta) / "manifest.json").is_file():
                return {"ok": False, "codigo": "captura_sem_manifesto"}
        negocios += coletor.traduzir(quadros, alcancado=True)
    if leitor.estado == FALHA:
        return {"ok": False, "codigo": leitor.codigo}
    livro = mensagem_livro(leitor.livro, coletor._base, coletor._tempo_ns, True, tick)
    replay = livro_da_captura(pasta, tick=tick)
    ids_replay = [f"{e.indice}-{e.posicao}" for e in ev.eventos_da_captura(pasta, tick=tick)
                  if e.tipo == ev.NEGOCIO and not e.retrato and e.preco and e.quantidade]
    ids = [m["id"] for m in negocios]
    problemas: dict = {}
    for mensagem in negocios + [livro, *coletor.retrato_atual()]:
        motivo = fora_do_formato(mensagem, tick)
        if motivo:
            problemas[motivo] = problemas.get(motivo, 0) + 1
    resultado = {
        "livro_igual": (livro["bids"], livro["asks"]) == ([[p * tick, q] for p, q, _ in replay.niveis(ev.COMPRA, NIVEIS)],
                                                          [[p * tick, q] for p, q, _ in replay.niveis(ev.VENDA, NIVEIS)]),
        "niveis": [len(livro["bids"]), len(livro["asks"])],
        "livro_completo_igual": leitor.livro.assinatura() == replay.assinatura(),
        "livro_vale_no_fim": leitor.livro_vale,
        "negocios_enviados": len(ids), "negocios_gravados": len(ids_replay), "negocios_iguais": ids == ids_replay,
        "ids_unicos": len(set(ids)) == len(ids),
        "sem_agressor": sum(m["side"] == "unknown" for m in negocios),
        "fora_do_formato": problemas, "quadros": leitor.quadros,
    }
    resultado["ok"] = (resultado["livro_igual"] and resultado["livro_completo_igual"] and resultado["negocios_iguais"]
                       and resultado["ids_unicos"] and not problemas)
    return resultado


class Coletor:
    def __init__(self, *, token: str, origem: Optional[str] = None, porta: int = PORTA_PADRAO, relogio=time.time,
                 avisar=print, leitor: Optional[Leitor] = None, pagina: Optional[bytes] = None,
                 historico: Optional[Historico] = None):
        if origem is not None and not _ORIGEM.fullmatch(origem):
            raise ColetorError("origem_precisa_ser_http_127_0_0_1_com_porta")
        if not (isinstance(porta, int) and (porta == 0 or 1024 <= porta <= 65535)):   # 0: qualquer livre (testes)
            raise ColetorError("porta_invalida")
        if not _TOKEN.fullmatch(token or ""):
            raise ColetorError("token_invalido")
        self._token, self.origem, self.porta, self.pagina = token, origem, porta, pagina
        if self.origem is None and porta:
            self.origem = f"http://{HOST}:{porta}"          # a propria pagina do coletor
        self._relogio, self._avisar = relogio, avisar
        self.sessao = "qhos2-" + secrets.token_hex(8)
        self.historico, self._barras_vivas = historico, Barras()
        self.instrumento = {**ESZ6, "capabilities": {**ESZ6["capabilities"], "history": historico is not None}}
        self.selecao = {"revision": "1", "subscriptionId": "esz6-" + secrets.token_hex(6),
                        "activeId": ESZ6["id"], "instruments": [self.instrumento]}
        self.clientes: set = set()
        self.leitor, self.tick = leitor, ESZ6["tickSize"]
        self._base = {"subscriptionId": self.selecao["subscriptionId"], "revision": self.selecao["revision"],
                      "instrumentId": ESZ6["id"]}
        self._tempo_ns: Optional[int] = None         # horario do ultimo quadro lido
        self._ultimo_preco: Optional[float] = None
        self._parou = False
        self._ao_parar: List[dict] = []             # livro vazio e lacuna, repetidos a quem conectar depois
        self.enviados = {"book": 0, "trade": 0, "ticker": 0, "gap": 0, "pipeline": 0}
        self._latencia_ms: Optional[float] = None    # chegada menos carimbo do servidor, no ultimo quadro carimbado
        self._desde: dict = {}                       # camada: (estado, desde quando em ms)
        self.contagem = {"recusado_host": 0, "recusado_origem": 0, "recusado_token": 0, "recusado_protocolo": 0,
                         "recusado_lotado": 0, "recusado_conexao": 0, "sem_hello": 0, "invalidas": 0, "paginas": 0,
                         "nao_encontrado": 0, "conexoes_entregues": 0}

    # ---- aperto de mao ------------------------------------------------------------------------------

    def _conferir_pedido(self, conexao, pedido):
        """Antes do WebSocket: Host exato para tudo; pedido comum recebe so a pagina; WebSocket so com a Origin certa."""
        if pedido.headers.get("Host") != f"{HOST}:{self.porta}":
            return self._negar(conexao, "recusado_host")
        if pedido.headers.get("Upgrade", "").lower() != "websocket":
            from qhos2.observabilidade import ROTAS
            if pedido.path in ROTAS:
                return self._observacao(conexao, pedido)
            if pedido.path == "/conexao" and self.pagina is not None:
                return self._conexao(conexao, pedido)
            return self._pagina(conexao, pedido.path)
        if pedido.headers.get("Origin") != self.origem:
            return self._negar(conexao, "recusado_origem")
        return None

    def _observacao(self, conexao, pedido):
        """Contrato GET local sem credenciais, caminhos privados ou dados brutos de mercado."""
        from qhos2.observabilidade import resposta
        from websockets.datastructures import Headers
        from websockets.http11 import Response
        origem = pedido.headers.get("Origin")
        site = pedido.headers.get("Sec-Fetch-Site")
        if (origem is not None and origem != self.origem) or site in ("cross-site", "same-site"):
            return self._negar(conexao, "recusado_origem")
        corpo = json.dumps(resposta(self, pedido.path), allow_nan=False, separators=(",", ":")).encode("utf-8")
        return Response(200, "OK", Headers([*_CABECALHOS_CONEXAO, ("Content-Length", str(len(corpo)))]), corpo)

    def _pagina(self, conexao, caminho: str):
        from websockets.datastructures import Headers
        from websockets.http11 import Response
        if self.pagina is None or caminho not in ("/", "/index.html"):
            self.contagem["nao_encontrado"] += 1
            return conexao.respond(HTTPStatus.NOT_FOUND, "nao encontrado\n")
        self.contagem["paginas"] += 1
        cabecalhos = Headers([*_CABECALHOS_PAGINA, ("Content-Length", str(len(self.pagina)))])
        return Response(HTTPStatus.OK.value, "OK", cabecalhos, self.pagina)

    def _conexao(self, conexao, pedido):
        """Endereco e token so para o `fetch` da propria pagina; qualquer outra coisa recebe o 404 de sempre."""
        from websockets.datastructures import Headers
        from websockets.http11 import Response
        cab = pedido.headers
        origem = cab.get("Origin")
        if any(cab.get(nome) not in aceitos for nome, aceitos in _FETCH_DA_PAGINA.items()) \
                or (origem is not None and origem != self.origem):
            self.contagem["recusado_conexao"] += 1
            self._avisar("recusado conexao (pedido de /conexao que nao veio da pagina do coletor)")
            return conexao.respond(HTTPStatus.NOT_FOUND, "nao encontrado\n")
        self.contagem["conexoes_entregues"] += 1
        corpo = json.dumps({"url": f"ws://{HOST}:{self.porta}", "token": self._token}, separators=(",", ":")).encode("ascii")
        return Response(HTTPStatus.OK.value, "OK", Headers([*_CABECALHOS_CONEXAO, ("Content-Length", str(len(corpo)))]), corpo)

    def _negar(self, conexao, contador: str):
        self.contagem[contador] += 1
        self._avisar(f"{contador.replace('_', ' ')} (a pagina precisa ser {self.origem})")
        return conexao.respond(HTTPStatus.FORBIDDEN, "recusado\n")

    def welcome(self) -> dict:
        return {"type": "welcome", "protocol": PROTOCOLO, "provider": "rithmic", "environment": self.ambiente_declarado()[0],
                "canExecute": False,
                "sessionId": self.sessao, "selection": self.selecao,
                "capabilities": ["catalog", "selection", "pipeline"] + (["history"] if self.historico else [])}

    def ambiente_declarado(self) -> tuple:
        """Identidade declarada na captura; desconhecida nao vira Paper por suposicao."""
        from qhos2.catalogo import _ler_manifesto
        if self.leitor is None:
            return "unknown", "unknown"
        try:
            manifesto = _ler_manifesto(self.leitor.pasta, [])
        except OSError:
            manifesto = None
        status = manifesto.get("status", {}) if isinstance(manifesto, dict) else {}
        if manifesto is None or manifesto.get("capture_identity_sha256") is not None:
            from qhos2.operacao import OperacaoError, ler_identidade_captura
            try:
                status = ler_identidade_captura(self.leitor.pasta, manifesto=manifesto)
            except OperacaoError:
                return "unknown", "unknown"
        if not isinstance(status, dict) or (status.get("requested_symbol"), status.get("exchange")) != (
                self.leitor.simbolo, self.leitor.bolsa):
            return "unknown", "unknown"
        sistema = status.get("system_name")
        nomes = {"Rithmic Paper Trading": "paper", "Rithmic Test": "test", "QHOS2 Sintetico": "synthetic"}
        return (nomes[sistema], sistema) if isinstance(sistema, str) and sistema in nomes else ("unknown", "unknown")

    async def _enviar(self, ws, mensagem: dict) -> None:
        await ws.send(json.dumps(mensagem, separators=(",", ":"), allow_nan=False))

    async def _recusar(self, ws, codigo: str, contador: str) -> None:
        self.contagem[contador] += 1
        self._avisar(contador.replace("_", " "))
        await self._enviar(ws, {"type": "error", "code": codigo})
        await ws.close(1008, "recusado")

    async def atender(self, ws) -> None:
        from websockets.exceptions import ConnectionClosed
        try:
            await self._atender(ws)
        except ConnectionClosed:      # o terminal fechou a aba ou reconectou: nada a fazer
            pass

    async def _atender(self, ws) -> None:
        if len(self.clientes) >= MAX_CLIENTES:
            self.contagem["recusado_lotado"] += 1
            await ws.close(1013, "lotado")
            return
        try:
            hello = _json(await asyncio.wait_for(ws.recv(), PRAZO_HELLO_S))
        except asyncio.TimeoutError:
            self.contagem["sem_hello"] += 1
            await ws.close(1008, "sem hello")
            return
        if hello is None or hello.get("type") != "hello" or hello.get("protocol") != PROTOCOLO:
            await self._recusar(ws, "PROTOCOL_UNSUPPORTED", "recusado_protocolo")
            return
        token = hello.get("token")
        if not isinstance(token, str) or not hmac.compare_digest(token.encode("utf-8"), self._token.encode("ascii")):
            await self._recusar(ws, "UNAUTHORIZED", "recusado_token")
            return
        await self._enviar(ws, self.welcome())
        for mensagem in [*(self._ao_parar or self.retrato_atual()), self.percurso()]:   # quem chega ve o estado de agora
            await self._enviar(ws, mensagem)
        self.clientes.add(ws)
        self._avisar(f"terminal conectado ({len(self.clientes)} de {MAX_CLIENTES})")
        try:
            invalidas = 0
            async for bruto in ws:
                resposta = self.responder(_json(bruto))
                if resposta is None:
                    invalidas += 1
                    self.contagem["invalidas"] += 1
                    if invalidas >= MAX_INVALIDAS:
                        await ws.close(1008, "mensagens invalidas")
                        return
                    continue
                await self._enviar(ws, resposta)
        finally:
            self.clientes.discard(ws)
            self._avisar(f"terminal desconectado ({len(self.clientes)} de {MAX_CLIENTES})")

    # ---- pedidos ------------------------------------------------------------------------------------

    def responder(self, pedido: Optional[dict]) -> Optional[dict]:
        """Resposta a um `request`; None se a mensagem nao e um pedido valido (o terminal nunca manda isso)."""
        if pedido is None or pedido.get("type") != "request":
            return None
        ident, metodo, params = pedido.get("id"), pedido.get("method"), pedido.get("params")
        if not isinstance(ident, str) or not _ID_PEDIDO.fullmatch(ident) or not isinstance(params, dict):
            return None
        if metodo == "catalog.search":
            consulta = params.get("query")
            if not isinstance(consulta, str) or len(consulta) > 120:
                return None
            achou = consulta.strip().casefold() in f"{ESZ6['symbol']} {ESZ6['name']} {ESZ6['exchange']}".casefold()
            return {"type": "response", "id": ident, "ok": True,
                    "result": {"instruments": [self.instrumento] if achou else []}}
        if metodo == "history.candles":
            quadro, limite = params.get("timeframe"), params.get("limit", 240)
            if self.historico is None or params.get("instrumentId") != ESZ6["id"]:
                return {"type": "response", "id": ident, "ok": False, "error": {"code": "HISTORY_UNAVAILABLE"}}
            try:
                viva = None if self.leitor is not None and self.leitor.estado == FALHA else self._barras_vivas
                captura_id = self.leitor.pasta.name if self.leitor is not None and self.leitor.estado != FALHA else None
                historico = self.historico.candles_com_cobertura(quadro, limite, viva, captura_id=captura_id)
            except HistoricoError as erro:
                codigo = "HISTORY_OVERLAP" if str(erro) == "sobreposicao_sem_recorte_disponivel" else "HISTORY_UNAVAILABLE"
                return {"type": "response", "id": ident, "ok": False, "error": {"code": codigo}}
            return {"type": "response", "id": ident, "ok": True,
                    "result": {"instrumentId": ESZ6["id"], "timeframe": quadro, **historico}}
        # selection.set chega aqui so quando a selecao pedida e diferente da atual: o terminal resolve a identica
        # sem perguntar. Trocar de contrato exigiria outra assinatura na Rithmic, e a sessao e uma so.
        return {"type": "response", "id": ident, "ok": False, "error": {"code": "FORBIDDEN"}}

    # ---- dados ---------------------------------------------------------------------------------------

    def traduzir(self, quadros, alcancado: bool) -> List[dict]:
        """Negocios dos quadros recem-lidos (so se a leitura ja alcancou a gravacao) e atualiza horario e preco."""
        if self.leitor is not None and self.leitor.estado == FALHA:
            self._barras_vivas = Barras()
            self._ultimo_preco = self._latencia_ms = None
            self._tempo_ns = self.leitor.ultimo_recebido_ns or int(self._relogio() * 1e9)
            return []
        saida = []
        for _, recebido, eventos in quadros:
            carimbos = [e.carimbo_ns for e in eventos if e.carimbo_ns is not None and not e.retrato]
            self._tempo_ns = max(carimbos) if carimbos else recebido
            if carimbos:
                self._latencia_ms = (recebido - max(carimbos)) / 1e6
            for evento in eventos:
                self._barras_vivas.negocio(evento)            # historico da captura seguida, mesmo antes de alcancar
                mensagem = mensagem_negocio(evento, self._base, self.tick)
                if mensagem is None:
                    continue
                self._ultimo_preco = mensagem["price"]
                if alcancado:
                    saida.append(mensagem)
        return saida

    def retrato_atual(self) -> List[dict]:
        """Livro e cotacao do instante (vazio enquanto nenhum quadro foi lido)."""
        if self.leitor is None or self._tempo_ns is None:
            return []
        vale = self.leitor.livro_vale
        saida = [mensagem_livro(self.leitor.livro, self._base, self._tempo_ns, vale, self.tick)]
        cotacao = mensagem_cotacao(self.leitor.livro, self._ultimo_preco, self._base, self._tempo_ns, vale, self.tick)
        if cotacao is not None:
            saida.append(cotacao)
        return saida

    async def _difundir(self, mensagens: List[dict]) -> None:
        for mensagem in mensagens:
            self.enviados[mensagem["type"]] += 1
            for ws in list(self.clientes):
                try:
                    await self._enviar(ws, mensagem)
                except Exception:   # conexao caindo: o atender() limpa
                    pass

    async def _seguir(self) -> None:
        """Le a captura em outra thread e envia negocios a cada volta e livro e cotacao 4 vezes por segundo."""
        proximo_livro = 0.0
        while not self._parou:
            try:
                quadros = await asyncio.to_thread(self.leitor.avancar, BLOCO_LEITURA)
            except OSError:
                # O caminho e a mensagem do SO podem conter informacao privada.
                self.leitor._falhar("arquivo_inacessivel")
                quadros = []
            resumo = self.leitor.resumo()
            await self._difundir(self.traduzir(quadros, resumo["pendente_bytes"] < ALCANCADO_BYTES))
            if self.leitor.estado == FALHA:
                self._parou = True
                self._avisar(f"leitura parou: {self.leitor.codigo}; o terminal recebe livro vazio e uma lacuna")
                agora_ns = int(self._relogio() * 1e9)
                vazio = mensagem_livro(self.leitor.livro, self._base, self._tempo_ns or agora_ns, False, self.tick)
                self._ao_parar = [vazio, {"type": "gap", **self._base, "time": int(self._relogio() * 1000)}]
                await self._difundir(self._ao_parar)
                return
            if time.monotonic() >= proximo_livro:
                await self._difundir(self.retrato_atual())
                proximo_livro = time.monotonic() + LIVRO_A_CADA_S
            if not quadros:
                await asyncio.sleep(LER_A_CADA_S)

    # ---- percurso (tarefa 9) -------------------------------------------------------------------------

    def _idade_do_arquivo_s(self) -> Optional[float]:
        try:
            return max(0.0, self._relogio() - (self.leitor.pasta / ARQUIVO_LEGADO).stat().st_mtime)
        except OSError:
            return None

    def _camadas(self) -> list:
        """(id, estado, motivo, campos) de cada camada, so com o que o arquivo gravado mostra."""
        enviados = [("Terminais conectados", len(self.clientes)), ("Livros enviados", self.enviados["book"]),
                    ("Negócios enviados", self.enviados["trade"]),
                    ("Recusas", sum(v for k, v in self.contagem.items() if k.startswith("recusado_")))]
        coletor = ("coletor", "ok", "Enviando ao terminal", enviados)
        if self.leitor is None:
            sem = "O coletor não está seguindo nenhuma captura"
            return [(i, "desconhecido", sem, []) for i in ("rithmic", "sessao", "gravacao", "livro", "leitura")] + [coletor]
        leitor, r = self.leitor, self.leitor.resumo()
        livro, estado, codigo = r["livro"], leitor.estado, leitor.codigo
        idade = self._idade_do_arquivo_s()
        captura = [("Captura", leitor.pasta.name[:8]), ("Contrato", f"{leitor.simbolo} · {leitor.bolsa}")]
        latencia = [("Atraso do último quadro", f"{self._latencia_ms:.0f} ms")] if self._latencia_ms is not None else []
        if estado == AGUARDANDO:
            sem = "O arquivo da captura ainda não existe"
            return [(i, "desconhecido", sem, captura) for i in ("rithmic", "sessao", "gravacao", "livro", "leitura")] + [
                coletor]
        if estado == FALHA:
            parou = f"Leitura parou: {codigo}"
            return [("rithmic", "desconhecido", "Leitura parou; estado da conexão desconhecido", captura),
                    ("sessao", "desconhecido", "Leitura parou", []),
                    ("gravacao", "falha", parou, captura), ("livro", "desconhecido", "Leitura parou; livro não vale", []),
                    ("leitura", "falha", parou, [("Quadros lidos", r["quadros"])]), coletor]
        # rithmic: so o ritmo do arquivo prova que a conexao da captura esta viva
        if estado == ENCERRADA:
            rithmic = ("desconhecido", "Captura encerrada: a conexão terminou")
        elif idade is None:
            rithmic = ("desconhecido", "Arquivo da captura inacessível")
        elif idade <= QUADROS_ATENCAO_S:
            rithmic = ("ok", "Quadros chegando")
        elif idade <= QUADROS_DESCONHECIDO_S:
            rithmic = ("atencao", f"Sem quadros há {idade:.0f} s")
        else:
            rithmic = ("desconhecido", f"Sem quadros há {idade:.0f} s: mercado fechado ou conexão caída")
        fase = livro["fase"]
        sessao = {"PRONTO": ("ok", "Assinatura e retrato recebidos"), "RETRATO": ("atencao", "Recebendo o retrato")}.get(
            fase, ("desconhecido", "Nenhum quadro de livro ainda"))
        if estado == ENCERRADA:
            gravacao = ("ok", "Captura encerrada e conferida (quadros, bytes e SHA-256)")
        elif idade is not None and idade <= QUADROS_DESCONHECIDO_S:
            gravacao = ("ok", "Gravando")
        else:
            gravacao = ("atencao", "Arquivo parado e sem manifesto")
        if r["livro_vale"]:
            estado_livro = ("ok", "Qualificado")
        elif livro["suspeito"]:
            estado_livro = ("falha", "Anomalia depois do retrato: o livro não vale até nova sincronização")
        elif livro["cruzado"]:
            estado_livro = ("atencao", "Cruzado no fim do quadro")
        elif fase == "RETRATO":
            estado_livro = ("atencao", "Ainda no retrato")
        else:
            estado_livro = ("desconhecido", "Sem livro ainda")
        if estado == ENCERRADA:
            leitura = ("ok", "Leu a captura inteira")
        elif r["pendente_bytes"] >= ALCANCADO_BYTES:
            leitura = ("atencao", f"Alcançando: {r['pendente_bytes'] / 1024 ** 2:.1f} MB por ler")
        else:
            leitura = ("ok", "Acompanhando a gravação")
        niveis = livro["niveis"]
        return [
            ("rithmic", *rithmic, latencia + captura[1:]),
            ("sessao", *sessao, [("Fase do livro", fase)]),
            ("gravacao", *gravacao, captura[:1] + [("Quadros", r["quadros"]), ("Tamanho", f"{r['bytes'] / 1024 ** 2:.1f} MB")]),
            ("livro", *estado_livro, [("Ordens", livro["ordens"]),
                                      ("Níveis compra / venda", f"{niveis[ev.COMPRA]} / {niveis[ev.VENDA]}"),
                                      ("Spread", "—" if livro["spread_ticks"] is None else f"{livro['spread_ticks']} ticks"),
                                      ("Anomalias", sum(livro["anomalias"].values()))]),
            ("leitura", *leitura, [("Por ler", f"{r['pendente_bytes'] // 1024} KB"), ("Quadros lidos", r["quadros"])]),
            coletor,
        ]

    def percurso(self) -> dict:
        """Mensagem `pipeline` do contrato do terminal: uma camada por bolinha, com `since` de quando o estado mudou."""
        agora = int(self._relogio() * 1000)
        camadas = []
        for ident, estado, motivo, campos in self._camadas():
            if self._desde.get(ident, (None,))[0] != estado:
                self._desde[ident] = (estado, agora)
            camadas.append({"id": ident, "state": estado, "since": self._desde[ident][1], "reason": motivo,
                            "fields": [{"label": str(rotulo)[:40], "value": str(valor)[:80]}
                                       for rotulo, valor in campos[:12]]})
        return {"type": "pipeline", "sessionId": self.sessao, "time": agora, "stages": camadas}

    async def _percurso_periodico(self) -> None:
        while True:
            await asyncio.sleep(PERCURSO_A_CADA_S)
            if self.clientes:
                await self._difundir([self.percurso()])

    async def _preparar_historico(self) -> None:
        """Le ou tira do cache as capturas encerradas, em outra thread; o historico responde com o que ja estiver pronto."""
        ignorar = self.leitor.pasta.name if self.leitor is not None else None
        await asyncio.to_thread(self.historico.preparar, ignorar)
        recusadas = f"; recusadas {self.historico.recusadas}" if self.historico.recusadas else ""
        self._avisar(f"historico pronto: {len(self.historico.prontas)} capturas encerradas do ESZ6{recusadas}")

    async def _batimentos(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            mensagem = {"type": "heartbeat", "time": int(self._relogio() * 1000)}
            for ws in list(self.clientes):
                try:
                    await self._enviar(ws, mensagem)
                except Exception:   # conexao caindo: o atender() limpa
                    pass

    async def rodar(self, pronto: Optional[asyncio.Event] = None) -> None:
        from websockets.asyncio.server import serve
        async with serve(self.atender, HOST, self.porta, process_request=self._conferir_pedido,
                         max_size=MAX_MENSAGEM, server_header=None) as servidor:
            self.porta = servidor.sockets[0].getsockname()[1]
            if self.origem is None:
                self.origem = f"http://{HOST}:{self.porta}"
            tarefas = [asyncio.create_task(self._batimentos()), asyncio.create_task(self._percurso_periodico())]
            if self.leitor is not None:
                tarefas.append(asyncio.create_task(self._seguir()))
            if self.historico is not None:
                tarefas.append(asyncio.create_task(self._preparar_historico()))
            if pronto is not None:
                pronto.set()
            falha = asyncio.get_running_loop().create_future()

            def conferir_tarefa(tarefa):
                if tarefa.cancelled() or falha.done():
                    return
                erro = tarefa.exception()
                if erro is not None:
                    # O supervisor observa a saida do processo. Uma tarefa morta
                    # nunca deixa o servidor parecendo saudavel indefinidamente.
                    falha.set_exception(RuntimeError("tarefa_do_coletor_falhou"))

            for tarefa in tarefas:
                tarefa.add_done_callback(conferir_tarefa)
            try:
                await falha
            finally:
                for tarefa in tarefas:
                    tarefa.cancel()
                await asyncio.gather(*tarefas, return_exceptions=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="qhos2.coletor", description=__doc__.splitlines()[0])
    ap.add_argument("--porta", type=int, default=PORTA_PADRAO)
    ap.add_argument("--origem", help="outra pagina autorizada (http://127.0.0.1:<porta>); padrao: a do proprio coletor")
    ap.add_argument("--token", type=Path, default=TOKEN_PADRAO, help="arquivo do token (criado se faltar)")
    ap.add_argument("--novo-token", action="store_true", help="troca o token (o terminal precisa do novo)")
    ap.add_argument("--raiz", type=Path, help="raiz unica dos dados e derivados, fora do Git")
    fonte = ap.add_mutually_exclusive_group()
    fonte.add_argument("--captura", type=Path, help="pasta da captura a seguir (gravada ou em andamento)")
    fonte.add_argument("--em-andamento", action="store_true", help="a unica captura em andamento em <data_root>/capturas")
    ap.add_argument("--conferir", action="store_true",
                    help="nao serve nada: confere a captura gravada contra o replay e sai (tarefa 6)")
    args = ap.parse_args(argv)
    from qhos2.config import ConfigError
    from qhos2.leitura import LeituraError, captura_em_andamento
    if args.conferir:
        if not args.captura:
            print("PARE: --conferir precisa de --captura")
            return 2
        try:
            resultado = conferir_captura(args.captura)
        except (LeituraError, ev.EventoError) as erro:
            print(f"PARE: {erro}")
            return 2
        print(f"conferencia de {args.captura.name}: " + json.dumps(resultado, sort_keys=True))
        print("IGUAL ao replay" if resultado["ok"] else "DIFERENTE do replay ou fora do formato: nao siga")
        return 0 if resultado["ok"] else 1
    try:
        dados = raiz_para_captura(args.captura, args.raiz)
        if args.captura:
            pasta = args.captura
        elif args.em_andamento:
            pasta = captura_em_andamento(dados / "capturas")
        else:
            pasta = None
        leitor = Leitor(pasta, tick=ESZ6["tickSize"], simbolo=ESZ6["symbol"], bolsa=ESZ6["exchange"]) if pasta else None
        pagina = PAGINA.read_bytes()
        historico = Historico(dados / "capturas", dados / "derivados" / "candles_1m", tick=ESZ6["tickSize"],
                              simbolo=ESZ6["symbol"], bolsa=ESZ6["exchange"])
        coletor = Coletor(token=ler_ou_criar_token(args.token, novo=args.novo_token), origem=args.origem,
                          porta=args.porta, leitor=leitor, pagina=pagina, historico=historico)
    except (ColetorError, ConfigError, LeituraError, OSError) as erro:
        print(f"PARE: {erro}")
        return 2
    print(f"terminal em http://{HOST}:{coletor.porta}/ (index.html {hashlib.sha256(pagina).hexdigest()[:16]}); "
          f"a pagina busca o endereco e o token em /conexao, sem colar nada")
    print(f"aceitando so a pagina {coletor.origem}; " +
          (f"seguindo a captura {pasta.name}" if pasta else "sem captura selecionada; estado desconhecido"))
    print(f"token em {_sem_usuario(args.token)} (nao e mostrado nem digitado: so a propria pagina o recebe)")
    print("Ctrl+C encerra")
    try:
        asyncio.run(coletor.rodar())
    except KeyboardInterrupt:
        pass
    except OSError as erro:
        print(f"PARE: porta_indisponivel ({erro.errno})")
        return 2
    print(f"encerrado; leitura {leitor.estado if leitor else 'SEM_CAPTURA'}; enviados {coletor.enviados}; recusas {coletor.contagem}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
