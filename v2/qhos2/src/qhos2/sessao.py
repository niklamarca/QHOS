"""Sessao do R|Protocol no Ticker Plant do Paper: do login ao logout, sem rede propria.

A sessao so fala por uma conexao injetada (`enviar`, `receber`, `fechar`), entao pode ser testada com
um servidor falso e usada depois com o conector real. Fluxo, o mesmo do V1 que ja foi validado:

  1. descoberta   : conexao 1, pede os sistemas (16/17), confere que o Paper e anunciado, fecha;
  2. login        : conexao 2, login (10/11), le o intervalo de heartbeat;
  3. referencia   : dado do instrumento (14/15), confere simbolo e bolsa;
  4. assinatura   : atualizacoes MBO (117/118), depois o retrato (115, respostas 116 ate a final);
  5. negocios     : se pedido, ultimo negocio (100/101, so o bit LAST_TRADE; quadros 150);
  6. transmissao  : heartbeat a cada periodo (18/19), todo quadro de mercado vai a `ao_receber`;
  7. saida        : cancela os negocios (100/101) e a assinatura (117/118) e faz logout (12/13).

Todo quadro de mercado (116, 150, 160, 161) e entregue a `ao_receber(bytes, template_id, recebido_ns)`
logo apos chegar, na ordem de chegada; se essa funcao falhar, a sessao falha (nunca perde em silencio).
Cada heartbeat estima o desvio local; o menor RTT e escolhido numa janela recente,
e a amostra mais nova sempre participa dos alertas. Isso nao qualifica UTC/NTP.
Erros sao codigos curtos e estaticos; a senha nunca aparece em status, erro ou repr.
"""
from __future__ import annotations

import asyncio
import math
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, Optional

from qhos2 import rprotocol as p
from qhos2.config import Settings

DESVIO_RELOGIO_ALERTA_MS = 1000.0
JANELA_RELOGIO_S = 30.0
ESPERA_MAXIMA_S = 0.25
_MERCADO = frozenset({p.RESP_RETRATO_PROFUNDIDADE, p.PROFUNDIDADE_ORDEM, p.PROFUNDIDADE_FIM, p.ULTIMO_NEGOCIO})
_TRANSITO = _MERCADO | {p.RESP_HEARTBEAT}

TRANSITORIA, PROTOCOLO, LOGOUT_FORCADO, REJEICAO, LOCAL, CANCELADA = (
    "TRANSIENT_TRANSPORT", "PROTOCOL", "FORCED_LOGOUT", "REJECT", "LOCAL_ERROR", "CANCELLED")


class ConexaoFechada(Exception):
    """A conexao caiu: fechada pelo outro lado ou erro de transporte."""


class SessaoError(RuntimeError):
    def __init__(self, codigo: str, tipo: str = PROTOCOLO):
        super().__init__(codigo)
        self.codigo, self.tipo = codigo, tipo


@dataclass(frozen=True)
class Credenciais:
    user: str = field(repr=False)
    password: str = field(repr=False)


@dataclass(frozen=True)
class ConfigSessao:
    template_version: str
    symbol: str
    exchange: str
    duracao_s: Optional[float] = None
    resposta_timeout_s: float = 10.0
    retrato_timeout_s: float = 30.0
    app_version: str = "qhos2-0.1"
    negocios: bool = False
    settings: Settings = field(default_factory=Settings)

    def __post_init__(self):
        if not isinstance(self.template_version, str) or not 0 < len(self.template_version) <= 32:
            raise ValueError("template_version_invalido")
        if not isinstance(self.negocios, bool):
            raise ValueError("negocios_invalido")
        p._identificador(self.symbol, "symbol")
        p._identificador(self.exchange, "exchange")
        if self.duracao_s is not None and not (type(self.duracao_s) in (int, float)
                and math.isfinite(self.duracao_s) and 0 < self.duracao_s <= 7 * 86400):
            raise ValueError("duracao_invalida")
        for valor, teto in ((self.resposta_timeout_s, 600), (self.retrato_timeout_s, 3600)):
            if not (type(valor) in (int, float) and math.isfinite(valor) and 0 < valor <= teto):
                raise ValueError("timeout_invalido")


Fabrica = Callable[[], Awaitable[object]]
AoReceber = Callable[[bytes, int, int], None]


class Sessao:
    def __init__(self, config: ConfigSessao, fabrica: Fabrica, *, ao_receber: AoReceber,
                 relogio_ns: Callable[[], int] = time.time_ns):
        self.config, self._fabrica, self._ao_receber, self._relogio_ns = config, fabrica, ao_receber, relogio_ns
        self._parar = asyncio.Event()
        self._estado, self._erro, self._tipo = "NOVA", None, None
        self._login = self._assinatura = self._retrato = self._cancelamento = self._logout = False
        self._negocios = self._negocios_cancelados = False
        self._referencia: Optional[dict] = None
        self._retrato_quadros = 0
        self._retrato_token = None
        self._quadros: Counter = Counter()
        self._bytes = 0
        self._periodo = float(config.settings.heartbeat_seconds)
        self._hb_pendente: Optional[tuple] = None  # (instante monotonico, relogio_ns do envio)
        self._hb_confirmados = 0
        self._relogio: Optional[dict] = None
        self._amostras_relogio = []
        self._ultima_amostra_mono = None
        self._relogio_invalido = False
        self._anomalias: Counter = Counter()

    # ---- API ------------------------------------------------------------------------------------

    def parar(self) -> None:
        """Pede encerramento gracioso: cancela a assinatura e faz logout."""
        self._parar.set()

    def status(self) -> dict:
        relogio = dict(self._relogio) if self._relogio else None
        if relogio:
            idade = max(0.0, time.monotonic() - self._ultima_amostra_mono)
            relogio["idade_s"] = round(idade, 3)
            relogio["fresco"] = idade <= max(3 * self._periodo, 1.0)
        return {
            "estado": self._estado, "erro": self._erro, "tipo_de_falha": self._tipo,
            "sistema": self.config.settings.system_name,
            "instrumento": f"{self.config.exchange}/{self.config.symbol}",
            "login_confirmado": self._login, "referencia": dict(self._referencia) if self._referencia else None,
            "assinatura_confirmada": self._assinatura, "retrato_completo": self._retrato,
            "retrato_quadros": self._retrato_quadros, "cancelamento_confirmado": self._cancelamento,
            "logout_confirmado": self._logout, "negocios_confirmados": self._negocios,
            "negocios_cancelados": self._negocios_cancelados, "quadros": {str(t): n for t, n in sorted(self._quadros.items())},
            "bytes": self._bytes, "heartbeats_confirmados": self._hb_confirmados,
            "periodo_heartbeat_s": self._periodo, "relogio": relogio,
            "relogio_alerta": self._relogio_invalido or bool(relogio and (not relogio["fresco"] or
                max(abs(relogio["desvio_ms"]), abs(relogio["ultimo_desvio_ms"])) > DESVIO_RELOGIO_ALERTA_MS)),
            "anomalias": dict(self._anomalias),
        }

    async def executar(self, credenciais: Credenciais) -> dict:
        if self._estado != "NOVA":
            raise SessaoError("sessao_nao_reutilizavel", LOCAL)
        if not credenciais.user or not credenciais.password:
            raise SessaoError("credenciais_ausentes", LOCAL)
        try:
            await self._descobrir()
            await self._autenticar(credenciais)
            self._estado = "FECHADA"
            return self.status()
        except asyncio.CancelledError:
            self._falhar("cancelada", CANCELADA)
            raise
        except SessaoError as erro:
            self._falhar(erro.codigo, erro.tipo)
            raise
        except (ConexaoFechada, ConnectionError, TimeoutError, OSError):
            self._falhar("conexao_perdida", TRANSITORIA)
            raise SessaoError("conexao_perdida", TRANSITORIA) from None
        except p.ProtocolError as erro:
            self._falhar(str(erro), PROTOCOLO)
            raise SessaoError(str(erro), PROTOCOLO) from None
        except Exception as erro:
            self._falhar(type(erro).__name__, LOCAL)
            raise SessaoError(type(erro).__name__, LOCAL) from None

    # ---- etapas ---------------------------------------------------------------------------------

    async def _descobrir(self) -> None:
        self._estado = "DESCOBRINDO"
        con = await self._fabrica()
        try:
            await con.enviar(p.codificar_info_sistema())
            resposta = await self._esperar(con, p.RESP_INFO_SISTEMA, self.config.resposta_timeout_s)
            p.sucesso(resposta)
            if self.config.settings.system_name not in resposta.campos.get("system_name", []):
                raise SessaoError("sistema_nao_anunciado")
        finally:
            await self._fechar(con)

    async def _autenticar(self, credenciais: Credenciais) -> None:
        cfg = self.config
        self._estado = "CONECTANDO"
        con = await self._fabrica()
        try:
            await con.enviar(p.codificar_login(
                template_version=cfg.template_version, user=credenciais.user, password=credenciais.password,
                app_name=cfg.settings.app_name, app_version=cfg.app_version, system_name=cfg.settings.system_name))
            resposta = await self._esperar(con, p.RESP_LOGIN, cfg.resposta_timeout_s)
            p.sucesso(resposta)
            self._periodo = min(p.periodo_de_heartbeat(p.intervalo_de_heartbeat(resposta)),
                                float(cfg.settings.heartbeat_seconds))
            self._login, self._estado = True, "AUTENTICADA"
            await self._ler_referencia(con)
            await self._assinar(con)
            await self._receber_retrato(con)
            if cfg.negocios:
                await self._assinar_negocios(con)
            await self._transmitir(con)
            await self._cancelar(con)
            await self._sair(con)
        finally:
            await self._fechar(con)

    async def _ler_referencia(self, con) -> None:
        cfg = self.config
        self._estado = "REFERENCIA"
        await con.enviar(p.codificar_dado_referencia(cfg.symbol, cfg.exchange))
        resposta = await self._esperar(con, p.RESP_DADO_REFERENCIA, cfg.resposta_timeout_s)
        p.sucesso(resposta)
        if (resposta.campos.get("symbol"), resposta.campos.get("exchange")) != (cfg.symbol, cfg.exchange):
            raise SessaoError("referencia_de_outro_instrumento")
        chaves = ("symbol", "exchange", "symbol_name", "product_code", "instrument_type", "expiration_date",
                  "currency", "is_tradable", "min_qprice_change", "min_fprice_change", "single_point_value")
        self._referencia = {k: resposta.campos.get(k) for k in chaves}

    async def _assinar(self, con) -> None:
        cfg = self.config
        self._estado = "ASSINANDO"
        await con.enviar(p.codificar_assinatura_profundidade(cfg.symbol, cfg.exchange, assinar=True))
        p.sucesso(await self._esperar(con, p.RESP_ASSINATURA_PROFUNDIDADE, cfg.resposta_timeout_s))
        self._assinatura = True

    async def _receber_retrato(self, con) -> None:
        cfg = self.config
        self._estado = "RETRATO"
        token = uuid.uuid4().hex
        self._retrato_token = token
        await con.enviar(p.codificar_retrato_profundidade(cfg.symbol, cfg.exchange, token))
        prazo = asyncio.get_running_loop().time() + cfg.retrato_timeout_s
        while True:
            dados, recebido_ns = await self._proximo_ate(con, prazo, "retrato_sem_resposta")
            mensagem = self._tratar(dados, recebido_ns)
            if mensagem.template_id == p.RESP_RETRATO_PROFUNDIDADE:
                self._retrato_quadros += 1
                if mensagem.campos.get("rp_code"):
                    p.sucesso(mensagem)
                    self._retrato = True
                    return
            elif mensagem.template_id not in _TRANSITO:
                raise SessaoError("resposta_inesperada_para_o_estado")

    async def _assinar_negocios(self, con) -> None:
        cfg = self.config
        self._estado = "NEGOCIOS"
        await con.enviar(p.codificar_assinatura_negocios(cfg.symbol, cfg.exchange, assinar=True))
        p.sucesso(await self._esperar(con, p.RESP_ATUALIZACAO_MERCADO, cfg.resposta_timeout_s))
        self._negocios = True

    async def _transmitir(self, con) -> None:
        loop = asyncio.get_running_loop()
        self._estado = "STREAMING"
        inicio = loop.time()
        fim = inicio + self.config.duracao_s if self.config.duracao_s else math.inf
        proximo_hb = inicio + self._periodo
        while not self._parar.is_set():
            agora = loop.time()
            if agora >= fim:
                break
            self._conferir_heartbeat(agora)
            if agora >= proximo_hb:
                await self._enviar_heartbeat(con, agora)
                proximo_hb = agora + self._periodo
            espera = max(min(proximo_hb - agora, fim - agora, ESPERA_MAXIMA_S), 0.001)
            recebido = await self._proximo(con, espera)
            if recebido is not None:
                self._tratar(*recebido)

    async def _cancelar(self, con) -> None:
        cfg = self.config
        self._estado = "CANCELANDO"
        if self._negocios:
            await con.enviar(p.codificar_assinatura_negocios(cfg.symbol, cfg.exchange, assinar=False))
            p.sucesso(await self._esperar(con, p.RESP_ATUALIZACAO_MERCADO, cfg.resposta_timeout_s))
            self._negocios_cancelados = True
        await con.enviar(p.codificar_assinatura_profundidade(cfg.symbol, cfg.exchange, assinar=False))
        p.sucesso(await self._esperar(con, p.RESP_ASSINATURA_PROFUNDIDADE, cfg.resposta_timeout_s))
        self._cancelamento = True

    async def _sair(self, con) -> None:
        self._estado = "SAINDO"
        await con.enviar(p.codificar_logout())
        p.sucesso(await self._esperar(con, p.RESP_LOGOUT, self.config.resposta_timeout_s))
        self._logout = True

    # ---- recepcao -------------------------------------------------------------------------------

    async def _proximo(self, con, limite: float):
        """Um quadro em ate `limite` s, com o relogio lido logo apos a chegada; None se estourou."""
        try:
            dados = await asyncio.wait_for(con.receber(), timeout=limite)
        except asyncio.TimeoutError:
            return None
        return dados, self._relogio_ns()

    async def _proximo_ate(self, con, prazo: float, codigo: str):
        restante = prazo - asyncio.get_running_loop().time()
        recebido = await self._proximo(con, restante) if restante > 0 else None
        if recebido is None:
            raise SessaoError(codigo, TRANSITORIA)
        return recebido

    async def _esperar(self, con, template: int, timeout: float) -> p.Mensagem:
        prazo = asyncio.get_running_loop().time() + timeout
        while True:
            dados, recebido_ns = await self._proximo_ate(con, prazo, f"sem_resposta_{template}")
            mensagem = self._tratar(dados, recebido_ns)
            if mensagem.template_id == template:
                return mensagem
            if mensagem.template_id not in _TRANSITO:
                raise SessaoError("resposta_inesperada_para_o_estado")

    def _tratar(self, dados: bytes, recebido_ns: int) -> p.Mensagem:
        mensagem = p.decodificar(dados)
        template = mensagem.template_id
        if template == p.LOGOUT_FORCADO:
            raise SessaoError("logout_forcado", LOGOUT_FORCADO)
        if template == p.REJEICAO:
            raise SessaoError("rejeicao_do_servidor", REJEICAO)
        # Toda resposta conhecida com status explicito deve confirma-lo, mesmo
        # quando chega fora da etapa que a esperava. Templates desconhecidos ja
        # sao recusados pelo decodificador. Retrato parcial pode omitir rp_code.
        if mensagem.campos.get("rp_code"):
            p.sucesso(mensagem)
        if template in _MERCADO:
            if template == p.RESP_RETRATO_PROFUNDIDADE:
                if self._estado != "RETRATO" or self._retrato_token is None:
                    self._anomalias["retrato_fora_de_hora"] += 1
                    raise SessaoError("retrato_fora_de_hora")
                if mensagem.campos.get("user_msg") not in (None, [self._retrato_token]):
                    self._anomalias["retrato_correlacao_divergente"] += 1
                    raise SessaoError("retrato_correlacao_divergente")
                if mensagem.campos.get("rp_code"):
                    p.sucesso(mensagem)
            self._quadros[template] += 1
            self._bytes += len(dados)
            try:
                self._ao_receber(dados, template, recebido_ns)
            except Exception:
                raise SessaoError("gravacao_falhou", LOCAL) from None
        elif template == p.RESP_HEARTBEAT:
            p.sucesso(mensagem)
            self._heartbeat_recebido(mensagem, recebido_ns)
        return mensagem

    # ---- heartbeat e relogio --------------------------------------------------------------------

    async def _enviar_heartbeat(self, con, agora: float) -> None:
        if self._hb_pendente is None:
            self._hb_pendente = (agora, self._relogio_ns())
            await con.enviar(p.codificar_heartbeat())

    def _conferir_heartbeat(self, agora: float) -> None:
        if self._hb_pendente is not None and agora - self._hb_pendente[0] > self.config.resposta_timeout_s:
            raise SessaoError("heartbeat_sem_resposta", TRANSITORIA)

    def _heartbeat_recebido(self, mensagem: p.Mensagem, recebido_ns: int) -> None:
        if self._hb_pendente is None:
            self._anomalias["heartbeat_nao_solicitado"] += 1
            return
        enviado_ns = self._hb_pendente[1]
        enviado_mono = self._hb_pendente[0]
        self._hb_pendente = None
        self._hb_confirmados += 1
        ssboe, usecs = mensagem.campos.get("ssboe"), mensagem.campos.get("usecs", 0)
        if ssboe is None:
            self._anomalias["heartbeat_sem_carimbo"] += 1
            return
        rtt_ns = recebido_ns - enviado_ns
        agora = time.monotonic()
        rtt_mono_ns = (agora - enviado_mono) * 1e9
        if rtt_ns < 0 or abs(rtt_ns - rtt_mono_ns) > 250_000_000:
            self._anomalias["relogio_salto_local"] += 1
            self._relogio_invalido = True
            return
        if not (isinstance(ssboe, int) and isinstance(usecs, int) and 0 <= usecs < 1_000_000):
            self._anomalias["heartbeat_carimbo_invalido"] += 1
            self._relogio_invalido = True
            return
        desvio_ns = ssboe * 10 ** 9 + usecs * 1000 - (enviado_ns + recebido_ns) // 2
        amostras = (self._relogio or {}).get("amostras", 0) + 1
        self._amostras_relogio = [x for x in self._amostras_relogio if agora - x[0] <= JANELA_RELOGIO_S]
        self._amostras_relogio.append((agora, rtt_ns, desvio_ns))
        self._amostras_relogio = self._amostras_relogio[-256:]
        melhor = min(self._amostras_relogio, key=lambda x: (x[1], -x[0]))
        self._ultima_amostra_mono = agora
        self._relogio_invalido = False
        self._relogio = {"desvio_ms": round(melhor[2] / 1e6, 3), "rtt_ms": round(melhor[1] / 1e6, 3),
                         "ultimo_desvio_ms": round(desvio_ns / 1e6, 3), "ultimo_rtt_ms": round(rtt_ns / 1e6, 3),
                         "amostras": amostras, "amostras_na_janela": len(self._amostras_relogio),
                         "utc_qualificado": False}

    # ---- utilidades -----------------------------------------------------------------------------

    def _falhar(self, codigo: str, tipo: str) -> None:
        self._estado, self._erro, self._tipo = "FALHOU", codigo, tipo

    @staticmethod
    async def _fechar(con) -> None:
        try:
            await con.fechar()
        except Exception:
            pass
