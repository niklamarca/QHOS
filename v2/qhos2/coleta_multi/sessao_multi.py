"""Uma sessao de login no Ticker Plant com varias assinaturas, um Gravador por ativo.

Herda de qhos2.sessao.Sessao (que nao muda): descoberta, login, heartbeat, relogio, logout e o tratamento de
logout forcado e rejeicao sao os mesmos. Muda o miolo:

  1. resolucao  : por item, front month (113/114) e dado do instrumento (14/15); bolsa e contrato vem do servidor;
  2. assinatura : MBO (117/118) por ativo; recusa exclui so aquele ativo;
  3. retrato    : um ativo por vez, cada um com seu token (mesma correlacao da Sessao);
  4. negocios   : ultimo negocio (100/101) por ativo; sem MBO, grava so os negocios;
  5. transmissao: Sessao._transmitir; cada quadro vai ao Gravador do dono;
  6. saida      : cancela por ativo e faz logout.

Dono de um quadro: 160 e 150 pelo symbol/exchange do proprio quadro; 116 pelo retrato em curso (token);
161 por cada par listado. Sem dono conhecido o quadro nao vai a Gravador nenhum (conta em sem_dono).
Falha de gravacao ou teto de um ativo fecha so aquele ativo. Teto global ou disco abaixo da reserva pedem
logout limpo. Transporte, logout forcado e rejeicao derrubam a sessao inteira, como na Sessao.
"""
from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Optional, Sequence

from qhos2 import disco
from qhos2 import rprotocol as p
from qhos2.gravador import MIN_LIVRE_PADRAO, Gravador, GravadorError
from qhos2.sessao import TRANSITORIA, ConfigSessao, Sessao, SessaoError

from coleta_multi import fio
from coleta_multi.lista import BOLSAS_CME_GROUP, FRONT, Item

_MERCADO_ROTEADO = frozenset({p.PROFUNDIDADE_ORDEM, p.ULTIMO_NEGOCIO, p.PROFUNDIDADE_FIM})
_TRANSITO = frozenset({p.RESP_RETRATO_PROFUNDIDADE, p.PROFUNDIDADE_ORDEM, p.PROFUNDIDADE_FIM, p.ULTIMO_NEGOCIO,
                       p.RESP_HEARTBEAT, fio.ATUALIZACAO_FRONT_MONTH})
CHECAR_DISCO_A_CADA = 20_000


@dataclass
class Ativo:
    item: Item
    estado: str = "resolvendo"      # incluido | excluido | gravando | teto_do_ativo | falhou
    motivo: Optional[str] = None
    tentativas: list = field(default_factory=list)
    bolsa: Optional[str] = None
    contrato: Optional[str] = None
    front_month: Optional[dict] = None
    referencia: Optional[dict] = None
    mbo: Optional[str] = None        # "ok" ou codigo da recusa
    negocios: Optional[str] = None
    retrato: Optional[str] = None
    cancelamento: dict = field(default_factory=dict)
    gravador: Optional[Gravador] = None
    quadros: Counter = field(default_factory=Counter)
    descartados: int = 0
    sufixo: str = ""                 # "_p2", "_p3"... nas partes retomadas depois de queda

    @property
    def chave(self):
        return (self.bolsa, self.contrato)

    @property
    def captura_id(self) -> str:
        return f"{self.bolsa}_{self.contrato}{self.sufixo}"

    def resumo(self) -> dict:
        g = self.gravador
        return {"exibido": self.item.exibido, "tipo": self.item.tipo, "descricao": self.item.descricao,
                "bolsa": self.bolsa, "contrato": self.contrato, "estado": self.estado, "motivo": self.motivo,
                "tentativas": list(self.tentativas), "front_month": self.front_month, "referencia": self.referencia,
                "mbo": self.mbo, "negocios": self.negocios, "retrato": self.retrato,
                "cancelamento": dict(self.cancelamento),
                "quadros": {str(t): n for t, n in sorted(self.quadros.items())},
                "quadros_total": g.quadros if g else 0, "bytes_brutos": g.bytes if g else 0,
                "descartados": self.descartados, "captura_id": self.captura_id if g else None,
                "parte": self.sufixo.lstrip("_") or "p1"}


def _negociavel(referencia: dict, dia: date) -> Optional[str]:
    """Motivo de exclusao, ou None. is_tradable ausente nao exclui (o Paper nao o manda para o ESZ6)."""
    valor = referencia.get("is_tradable")
    if isinstance(valor, str) and valor.strip().lower() in {"false", "0", "no", "n"}:
        return "nao_negociavel"
    vencimento = referencia.get("expiration_date")
    if isinstance(vencimento, str) and len(vencimento) == 8 and vencimento.isdigit() \
            and vencimento < dia.strftime("%Y%m%d"):
        return f"vencido_{vencimento}"
    return None


class SessaoMulti(Sessao):
    def __init__(self, itens: Sequence[Item], fabrica, *, settings, raiz: Path, dia: date,
                 max_bytes_ativo: int, max_bytes_global: int, reserva_disco_bytes: int,
                 duracao_s: Optional[float] = None, template_version: str = "5.42",
                 app_version: str = "rithmic-session-1", min_livre_bytes: int = MIN_LIVRE_PADRAO,
                 ao_logar: Optional[Callable[[], None]] = None, relogio_ns=None, gravar: bool = True,
                 sufixo: str = ""):
        config = ConfigSessao(template_version=template_version, symbol="MULTI", exchange="MULTI",
                              duracao_s=duracao_s, app_version=app_version, negocios=True, settings=settings)
        extras = {} if relogio_ns is None else {"relogio_ns": relogio_ns}
        super().__init__(config, fabrica, ao_receber=self._nunca, **extras)
        self.ativos = [Ativo(item, sufixo=sufixo) for item in itens]
        self.raiz, self.dia, self.gravar = Path(raiz), dia, gravar
        self.max_bytes_ativo, self.max_bytes_global = max_bytes_ativo, max_bytes_global
        self.reserva_disco_bytes, self.min_livre_bytes = reserva_disco_bytes, min_livre_bytes
        self.ao_logar = ao_logar
        self.logado = asyncio.Event()
        self.parada: Optional[str] = None           # motivo de parada pedida por teto ou disco
        self.sem_dono: Counter = Counter()
        self.bytes_gravados = 0
        self._por_chave = {}
        self._retrato_ativo: Optional[Ativo] = None
        self._proximo_hb = None
        self._desde_disco = 0
        self._relogio_serie = {"amostras": 0, "min_ms": None, "max_ms": None, "primeiro_ms": None, "ultimo_ms": None,
                               "rtt_max_ms": None}

    @staticmethod
    def _nunca(*_):  # a Sessao so chama ao_receber em _tratar, que aqui e substituido
        raise GravadorError("roteamento_ausente")

    # ---- API ------------------------------------------------------------------------------------

    def status_multi(self) -> dict:
        estado = self.status()
        estado.update(instrumento="MULTI", ativos=[a.resumo() for a in self.ativos], sem_dono=dict(self.sem_dono),
                      parada=self.parada, bytes_gravados=self.bytes_gravados, relogio_serie=dict(self._relogio_serie))
        return estado

    def status_ativo(self, ativo: Ativo) -> dict:
        estado = self.status()
        estado.update(instrumento=f"{ativo.bolsa}/{ativo.contrato}", ativo=ativo.resumo(),
                      relogio_serie=dict(self._relogio_serie), parada=self.parada,
                      coleta="qhos2.coleta_multi (uma sessao de login, varias assinaturas)")
        return estado

    def fechar_gravadores(self) -> list:
        """Fecha cada Gravador com o manifesto do ativo. Devolve [(ativo, manifesto ou None, erro ou None)]."""
        sessao_ok = self._estado == "FECHADA" and self._logout
        saida = []
        for a in self.ativos:
            if a.gravador is None:
                continue
            normal = (sessao_ok and a.estado == "gravando" and all(v == "ok" for v in a.cancelamento.values()))
            try:
                saida.append((a, a.gravador.fechar(self.status_ativo(a), fechamento_normal=normal), None))
            except (GravadorError, OSError) as erro:
                saida.append((a, None, str(erro) if isinstance(erro, GravadorError) else type(erro).__name__))
        return saida

    # ---- etapas ---------------------------------------------------------------------------------

    async def _autenticar(self, credenciais) -> None:
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
            self.logado.set()
            if self.ao_logar is not None:
                self.ao_logar()
            await self._resolver_todos(con)
            # parada pedida no meio do inicio (teto, disco, vigia do ES): nada mais e assinado; cancela e sai
            if self.parada is None:
                await self._assinar_todos(con)
            if self.parada is None:
                await self._retratos(con)
            if self.parada is None:
                await self._negocios_todos(con)
            self._assinatura = any(a.mbo == "ok" for a in self.ativos)
            self._retrato = all(a.retrato == "ok" for a in self.ativos if a.mbo == "ok")
            self._negocios = any(a.negocios == "ok" for a in self.ativos)
            if self.parada is None:
                await self._transmitir(con)
            await self._cancelar_todos(con)
            await self._sair(con)
        finally:
            await self._fechar(con)

    async def _hb_se_devido(self, con) -> None:
        agora = asyncio.get_running_loop().time()
        self._conferir_heartbeat(agora)
        if self._proximo_hb is None or agora >= self._proximo_hb:
            await self._enviar_heartbeat(con, agora)
            self._proximo_hb = agora + self._periodo

    async def _pedir(self, con, dados: bytes, resposta: int) -> p.Mensagem:
        await self._hb_se_devido(con)
        await con.enviar(dados)
        return await self._esperar(con, resposta, self.config.resposta_timeout_s)

    async def _resolver_todos(self, con) -> None:
        self._estado = "RESOLVENDO"
        for a in self.ativos:
            await self._resolver(con, a)
            if a.estado == "excluido" and (a.motivo or "").startswith("vencido_") and a.item.raiz:
                a.tentativas.append(f"{a.bolsa}:{a.contrato}_{a.motivo}_passa_ao_front_month_{a.item.raiz}")
                original = a.motivo
                a.bolsa = a.contrato = a.referencia = None
                await self._resolver(con, a, raiz_front=a.item.raiz)
                if a.estado == "excluido" and a.motivo == "nao_resolvido":
                    a.motivo = f"{original}_e_sem_front_month_{a.item.raiz}"
            if a.estado != "incluido":
                continue
            outro = self._por_chave.get(a.chave)
            if outro is not None:
                a.estado, a.motivo = "excluido", f"duplicado_de_{outro.item.exibido}"
                continue
            self._por_chave[a.chave] = a
            if self.gravar:
                try:
                    a.gravador = Gravador(self.raiz, identidade={"system_name": self.config.settings.system_name,
                                                                 "requested_symbol": a.contrato, "exchange": a.bolsa},
                                          max_bytes=self.max_bytes_ativo, min_livre_bytes=self.min_livre_bytes,
                                          captura_id=a.captura_id)
                except (GravadorError, OSError) as erro:
                    del self._por_chave[a.chave]
                    a.estado = "excluido"
                    a.motivo = "gravador_" + (str(erro) if isinstance(erro, GravadorError) else type(erro).__name__)

    async def _resolver(self, con, a: Ativo, raiz_front: Optional[str] = None) -> None:
        item = a.item
        bolsas = (item.bolsa_esperada,) + tuple(b for b in BOLSAS_CME_GROUP if b != item.bolsa_esperada)
        front = raiz_front or (item.exibido if item.tipo == FRONT else None)
        for bolsa in bolsas:
            contrato, bolsa_contrato = item.exibido, bolsa
            if front:
                r = await self._pedir(con, fio.codificar_front_month(front, bolsa), fio.RESP_FRONT_MONTH)
                rp = r.campos.get("rp_code")
                if rp != ["0"]:
                    a.tentativas.append(f"{bolsa}:front_month_{fio.codigo_seguro(rp)}")
                    continue
                contrato = r.campos.get("trading_symbol") or None
                bolsa_contrato = r.campos.get("trading_exchange") or bolsa
                a.front_month = {k: r.campos.get(k) for k in ("symbol", "exchange", "is_front_month_symbol",
                                                             "symbol_name", "trading_symbol", "trading_exchange")}
                try:
                    p._identificador(contrato, "symbol")
                    p._identificador(bolsa_contrato, "exchange")
                except p.ProtocolError:
                    a.tentativas.append(f"{bolsa}:front_month_sem_contrato")
                    continue
            r = await self._pedir(con, p.codificar_dado_referencia(contrato, bolsa_contrato), p.RESP_DADO_REFERENCIA)
            rp = r.campos.get("rp_code")
            if rp != ["0"]:
                a.tentativas.append(f"{bolsa_contrato}:referencia_{fio.codigo_seguro(rp)}")
                continue
            if (r.campos.get("symbol"), r.campos.get("exchange")) != (contrato, bolsa_contrato):
                a.tentativas.append(f"{bolsa_contrato}:referencia_de_outro_instrumento")
                continue
            chaves = ("symbol", "exchange", "symbol_name", "product_code", "instrument_type", "expiration_date",
                      "currency", "is_tradable", "min_qprice_change", "min_fprice_change", "single_point_value")
            a.referencia = {k: r.campos.get(k) for k in chaves}
            a.bolsa, a.contrato = bolsa_contrato, contrato
            motivo = _negociavel(a.referencia, self.dia)
            a.estado, a.motivo = ("excluido", motivo) if motivo else ("incluido", None)
            return
        a.estado, a.motivo = "excluido", "nao_resolvido"

    def _incluidos(self):
        return [a for a in self.ativos if a.estado == "incluido"]

    async def _assinar_todos(self, con) -> None:
        self._estado = "ASSINANDO"
        for a in self._incluidos():
            r = await self._pedir(con, p.codificar_assinatura_profundidade(a.contrato, a.bolsa, assinar=True),
                                  p.RESP_ASSINATURA_PROFUNDIDADE)
            a.mbo = "ok" if r.campos.get("rp_code") == ["0"] else f"recusada_{fio.codigo_seguro(r.campos.get('rp_code'))}"

    async def _retratos(self, con) -> None:
        loop = asyncio.get_running_loop()
        for a in self._incluidos():
            if a.mbo != "ok":
                continue
            await self._hb_se_devido(con)
            self._estado = "RETRATO"
            token = uuid.uuid4().hex
            self._retrato_token, self._retrato_ativo = token, a
            await con.enviar(p.codificar_retrato_profundidade(a.contrato, a.bolsa, token))
            prazo = loop.time() + self.config.retrato_timeout_s
            while True:
                dados, recebido_ns = await self._proximo_ate(con, prazo, "retrato_sem_resposta")
                mensagem = self._tratar(dados, recebido_ns)
                if mensagem.template_id == p.RESP_RETRATO_PROFUNDIDADE and mensagem.campos.get("rp_code"):
                    rp = mensagem.campos["rp_code"]
                    a.retrato = "ok" if rp == ["0"] else f"recusado_{fio.codigo_seguro(rp)}"
                    break
                if mensagem.template_id not in _TRANSITO:
                    raise SessaoError("resposta_inesperada_para_o_estado")
                await self._hb_se_devido(con)
            self._retrato_token, self._retrato_ativo = None, None
        self._estado = "ASSINADO"

    async def _negocios_todos(self, con) -> None:
        self._estado = "NEGOCIOS"
        for a in self._incluidos():
            r = await self._pedir(con, p.codificar_assinatura_negocios(a.contrato, a.bolsa, assinar=True),
                                  p.RESP_ATUALIZACAO_MERCADO)
            a.negocios = "ok" if r.campos.get("rp_code") == ["0"] else f"recusada_{fio.codigo_seguro(r.campos.get('rp_code'))}"
            if a.mbo != "ok" and a.negocios != "ok":
                a.estado, a.motivo = "excluido", "assinaturas_recusadas"
            else:
                a.estado = "gravando"

    async def _cancelar_todos(self, con) -> None:
        self._estado = "CANCELANDO"
        for a in self.ativos:
            if a.negocios == "ok":
                r = await self._pedir(con, p.codificar_assinatura_negocios(a.contrato, a.bolsa, assinar=False),
                                      p.RESP_ATUALIZACAO_MERCADO)
                a.cancelamento["negocios"] = "ok" if r.campos.get("rp_code") == ["0"] else \
                    f"recusado_{fio.codigo_seguro(r.campos.get('rp_code'))}"
            if a.mbo == "ok":
                r = await self._pedir(con, p.codificar_assinatura_profundidade(a.contrato, a.bolsa, assinar=False),
                                      p.RESP_ASSINATURA_PROFUNDIDADE)
                a.cancelamento["mbo"] = "ok" if r.campos.get("rp_code") == ["0"] else \
                    f"recusado_{fio.codigo_seguro(r.campos.get('rp_code'))}"
        self._negocios_cancelados = all(a.cancelamento.get("negocios") == "ok" for a in self.ativos if a.negocios == "ok")
        self._cancelamento = all(a.cancelamento.get("mbo") == "ok" for a in self.ativos if a.mbo == "ok")

    # ---- recepcao e roteamento ------------------------------------------------------------------

    def _tratar(self, dados: bytes, recebido_ns: int) -> p.Mensagem:
        template, simbolos, bolsas = fio.ler_rapido(dados)
        if template in _MERCADO_ROTEADO:
            self._quadros[template] += 1
            self._bytes += len(dados)
            self._rotear(dados, template, recebido_ns, simbolos, bolsas)
            return p.Mensagem(template, {})
        if template in fio.RESPOSTAS_POR_ATIVO:
            return fio.decodificar_resposta(dados)
        if template == fio.ATUALIZACAO_FRONT_MONTH:
            self._anomalias["front_month_update_nao_pedido"] += 1
            return p.Mensagem(template, {})
        if template == p.RESP_RETRATO_PROFUNDIDADE:
            return self._tratar_retrato(dados, recebido_ns)
        return super()._tratar(dados, recebido_ns)  # login, logout, heartbeat, 75, 77, info do sistema

    def _tratar_retrato(self, dados: bytes, recebido_ns: int) -> p.Mensagem:
        mensagem = p.decodificar(dados)
        a = self._retrato_ativo
        if self._estado != "RETRATO" or self._retrato_token is None or a is None:
            self._anomalias["retrato_fora_de_hora"] += 1
            raise SessaoError("retrato_fora_de_hora")
        if mensagem.campos.get("user_msg") not in (None, [self._retrato_token]):
            self._anomalias["retrato_correlacao_divergente"] += 1
            raise SessaoError("retrato_correlacao_divergente")
        simbolo, bolsa = mensagem.campos.get("symbol"), mensagem.campos.get("exchange")
        if (simbolo is not None and simbolo != a.contrato) or (bolsa is not None and bolsa != a.bolsa):
            self._anomalias["retrato_de_outro_instrumento"] += 1
            raise SessaoError("retrato_de_outro_instrumento")
        self._retrato_quadros += 1
        self._quadros[p.RESP_RETRATO_PROFUNDIDADE] += 1
        self._bytes += len(dados)
        self._gravar(a, dados, p.RESP_RETRATO_PROFUNDIDADE, recebido_ns)
        return mensagem

    def _rotear(self, dados: bytes, template: int, recebido_ns: int, simbolos: list, bolsas: list) -> None:
        if template == p.PROFUNDIDADE_FIM:
            pares = set(zip(simbolos, bolsas)) if len(simbolos) == len(bolsas) else set()
        else:
            pares = {(simbolos[0], bolsas[0])} if len(set(simbolos)) == 1 and len(set(bolsas)) == 1 else set()
        donos = [self._por_chave.get((b, s)) for s, b in pares]
        if not donos or any(d is None for d in donos):
            self.sem_dono[str(template)] += 1
            donos = [d for d in donos if d is not None]  # 161 com par desconhecido: so os conhecidos recebem
        for dono in donos:
            self._gravar(dono, dados, template, recebido_ns)

    def _gravar(self, a: Ativo, dados: bytes, template: int, recebido_ns: int) -> None:
        g = a.gravador
        if g is None or a.estado not in ("incluido", "gravando"):
            a.descartados += 1
            return
        antes = g.bytes
        try:
            g(dados, template, recebido_ns)
        except GravadorError as erro:
            a.estado = "teto_do_ativo" if str(erro) == "limite_de_bytes" else "falhou"
            a.motivo, a.descartados = str(erro), a.descartados + 1
            return
        a.quadros[template] += 1
        self.bytes_gravados += g.bytes - antes
        self._desde_disco += 1
        if self.parada is None and self.bytes_gravados > self.max_bytes_global:
            self._pedir_parada("teto_global")
        elif self._desde_disco >= CHECAR_DISCO_A_CADA:
            self._desde_disco = 0
            if self.parada is None and disco.livre_bytes(self.raiz) < self.reserva_disco_bytes:
                self._pedir_parada("disco_abaixo_da_reserva")

    def _pedir_parada(self, motivo: str) -> None:
        self.parada = motivo
        self.parar()

    # ---- relogio --------------------------------------------------------------------------------

    def _heartbeat_recebido(self, mensagem, recebido_ns) -> None:
        antes = (self._relogio or {}).get("amostras", 0)
        super()._heartbeat_recebido(mensagem, recebido_ns)
        if self._relogio and self._relogio.get("amostras", 0) > antes:
            s, desvio, rtt = self._relogio_serie, self._relogio["ultimo_desvio_ms"], self._relogio["ultimo_rtt_ms"]
            s["amostras"] += 1
            s["min_ms"] = desvio if s["min_ms"] is None else min(s["min_ms"], desvio)
            s["max_ms"] = desvio if s["max_ms"] is None else max(s["max_ms"], desvio)
            s["primeiro_ms"] = desvio if s["primeiro_ms"] is None else s["primeiro_ms"]
            s["ultimo_ms"] = desvio
            s["rtt_max_ms"] = rtt if s["rtt_max_ms"] is None else max(s["rtt_max_ms"], rtt)


def falha_de_login(sessao: SessaoMulti) -> Optional[str]:
    """'login_recusado' ou 'logout_forcado' (param tudo, sem nova tentativa), senao None."""
    estado = sessao.status()
    erro = estado.get("erro") or ""
    if not estado["login_confirmado"] and erro.startswith("resposta_recusada_11"):
        return "login_recusado"
    if estado.get("tipo_de_falha") == "FORCED_LOGOUT":
        return "logout_forcado"
    return None


__all__ = ["Ativo", "SessaoMulti", "falha_de_login", "TRANSITORIA"]
