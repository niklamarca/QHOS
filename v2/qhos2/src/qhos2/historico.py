"""Candles de historico a partir das capturas da V2 (integracao, tarefa 10).

So negocios gravados (150), sem o negocio-retrato: abertura, maxima, minima, fechamento e volume por minuto, no
horario do carimbo do servidor (ou da chegada, se o quadro nao tiver carimbo). Captura encerrada vira, uma unica vez,
um arquivo pequeno de barras de 1 minuto em `<data_root>/derivados/candles_1m/`, fora do repositorio, com o SHA-256
da captura de origem: se nao bater, e refeito. A captura em andamento entra pelas barras que o coletor monta
enquanto segue o arquivo (`Barras`).

Janela de teste da hipotese (plano, secao 4.1 e regra 18): negocios de 9/11 00h a 21/11 00h de Nova York ficam fora
do historico ate 21/11 00h de Brasilia. So le capturas verificadas pelo catalogo; captura de outro contrato e ignorada.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from qhos2 import eventos as ev
from qhos2.catalogo import _escrever_atomico, _ler_manifesto, listar_capturas, verificar_captura

ESQUEMA = "qhos2.candles_1m.v2"  # v1 nao conferia identidade em todos os quadros nem recusa do retrato
QUADROS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1D": 86400}
OCULTO_DE_S = 1_794_200_400          # 2026-11-09 05:00 UTC = 9/11 00h em Nova York (EST)
OCULTO_ATE_S = 1_795_237_200         # 2026-11-21 05:00 UTC = 21/11 00h em Nova York
LIBERADO_EM_S = 1_795_230_000        # 2026-11-21 03:00 UTC = 21/11 00h em Brasilia (mesmo corte do terminal)


class HistoricoError(ValueError):
    """Pedido de historico invalido."""


def oculto(minuto_s: int, agora_s: float) -> bool:
    return agora_s < LIBERADO_EM_S and OCULTO_DE_S <= minuto_s < OCULTO_ATE_S


class Barras:
    """Barras de 1 minuto: {minuto em s: [abertura, maxima, minima, fechamento, volume]} (precos em ticks)."""

    def __init__(self):
        self.minutos: Dict[int, List[int]] = {}

    def negocio(self, evento: ev.Evento) -> None:
        if evento.tipo != ev.NEGOCIO or evento.retrato or not evento.preco or not evento.quantidade \
                or evento.preco <= 0 or evento.quantidade <= 0:
            return
        quando = evento.carimbo_ns if evento.carimbo_ns is not None else evento.recebido_ns
        minuto = quando // 10 ** 9 // 60 * 60
        barra = self.minutos.get(minuto)
        if barra is None:
            self.minutos[minuto] = [evento.preco, evento.preco, evento.preco, evento.preco, evento.quantidade]
        else:
            barra[1] = max(barra[1], evento.preco)
            barra[2] = min(barra[2], evento.preco)
            barra[3] = evento.preco
            barra[4] += evento.quantidade

    def eventos(self, eventos: Iterable[ev.Evento]) -> "Barras":
        for evento in eventos:
            self.negocio(evento)
        return self


def _do_cache(caminho: Path, captura: str, sha256: str, tick: float, *,
              simbolo: str, bolsa: str, manifesto_sha256: str) -> Optional[Barras]:
    try:
        obj = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(obj, dict) or (obj.get("esquema"), obj.get("captura"), obj.get("sha256"), obj.get("tick")) != (
            ESQUEMA, captura, sha256, tick):
        return None
    if (obj.get("simbolo"), obj.get("bolsa"), obj.get("manifesto_sha256")) != (simbolo, bolsa, manifesto_sha256):
        return None
    linhas = obj.get("barras")
    if not isinstance(linhas, list):
        return None
    barras = Barras()
    anterior = -1
    for linha in linhas:
        if not (isinstance(linha, list) and len(linha) == 6 and
                all(isinstance(x, int) and not isinstance(x, bool) for x in linha)):
            return None
        minuto, abertura, maxima, minima, fechamento, volume = linha
        if (minuto < 0 or minuto % 60 or minuto <= anterior or min(linha[1:]) <= 0 or
                not minima <= min(abertura, fechamento) <= max(abertura, fechamento) <= maxima):
            return None
        barras.minutos[linha[0]] = linha[1:]
        anterior = minuto
    return barras


def barras_da_captura(pasta, cache: Path, *, tick: float, simbolo: str, bolsa: str) -> Optional[Barras]:
    """Barras de uma captura encerrada do contrato; None se for de outro contrato. Refaz o cache se nao bater."""
    pasta = Path(pasta)
    manifesto = _ler_manifesto(pasta, [])
    if manifesto is None:
        return None
    status = manifesto.get("status") if isinstance(manifesto.get("status"), dict) else {}
    if (status.get("requested_symbol"), status.get("exchange")) != (simbolo, bolsa):
        return None
    # Um cache nao substitui a verificacao dos bytes atuais nem o resultado da sessao.
    relatorio = verificar_captura(pasta)
    if not relatorio["verificada"]:
        raise ev.EventoError("captura_nao_verificada:" + ",".join(relatorio["divergencias"]))
    ev.conferir_status_captura(manifesto, pasta=pasta)
    try:
        tick_manifesto = ev.tick_da_captura(manifesto)
    except ev.EventoError:
        tick_manifesto = None  # capturas legadas sem referencia aceitam tick explicito
    if tick_manifesto is not None and tick_manifesto != tick:
        raise ev.EventoError("tick_diverge")
    sha256 = manifesto.get("sha256")
    manifesto_sha256 = hashlib.sha256(json.dumps(manifesto, sort_keys=True, separators=(",", ":"),
                                                allow_nan=False).encode("utf-8")).hexdigest()
    arquivo = cache / f"{pasta.name}.json"
    barras = _do_cache(arquivo, pasta.name, sha256, tick, simbolo=simbolo, bolsa=bolsa,
                       manifesto_sha256=manifesto_sha256)
    if barras is not None:
        return barras
    barras = Barras().eventos(ev.eventos_da_captura(pasta, tick=tick))    # recusa captura nao verificada
    cache.mkdir(parents=True, exist_ok=True)
    corpo = {"esquema": ESQUEMA, "captura": pasta.name, "sha256": sha256, "tick": tick,
             "simbolo": simbolo, "bolsa": bolsa, "manifesto_sha256": manifesto_sha256,
             "barras": [[m, *b] for m, b in sorted(barras.minutos.items())]}
    _escrever_atomico(arquivo, json.dumps(corpo, separators=(",", ":")) + "\n")
    return barras


class Historico:
    def __init__(self, capturas, cache, *, tick: float, simbolo: str, bolsa: str, relogio=time.time):
        self.capturas, self.cache = Path(capturas), Path(cache)
        self.tick, self.simbolo, self.bolsa, self._relogio = tick, simbolo, bolsa, relogio
        self.prontas: Dict[str, Barras] = {}
        self.recusadas: Dict[str, str] = {}

    def preparar(self, ignorar: Optional[str] = None) -> None:
        """Le (ou tira do cache) cada captura encerrada do contrato; a captura em andamento fica de fora."""
        if not self.capturas.is_dir():
            return
        for pasta in listar_capturas(self.capturas):
            if pasta.name == ignorar or pasta.name in self.prontas or pasta.name in self.recusadas:
                continue
            if not (pasta / "manifest.json").is_file():
                continue
            try:
                barras = barras_da_captura(pasta, self.cache, tick=self.tick, simbolo=self.simbolo, bolsa=self.bolsa)
            except ev.EventoError as erro:
                self.recusadas[pasta.name] = str(erro).split(":")[0]
                continue
            if barras is not None:
                self.prontas[pasta.name] = barras

    def candles(self, quadro: str, limite: int, viva: Optional[Barras] = None) -> List[dict]:
        """Candles do quadro pedido, das capturas prontas mais a viva, sem a janela escondida e sem futuro."""
        if quadro not in QUADROS:
            raise HistoricoError("quadro_invalido")
        if not isinstance(limite, int) or not 1 <= limite <= 10000:
            raise HistoricoError("limite_invalido")
        agora = self._relogio()
        segundos = QUADROS[quadro]
        juntos: Dict[int, List[int]] = {}
        fontes = sorted(self.prontas.values(), key=lambda f: min(f.minutos, default=0)) + ([viva] if viva is not None else [])
        # Sem ids dos negocios, dois OHLCVs no mesmo minuto nao permitem distinguir
        # continuidade de duplicacao. Nao inventar abertura/fechamento nem somar duas vezes.
        vistos = set()
        for minuto, _, barra in sorted((m, ordem, b) for ordem, fonte in enumerate(fontes)
                                       for m, b in fonte.minutos.items()):
            if oculto(minuto, agora) or minuto > agora:
                continue
            if minuto in vistos:
                raise HistoricoError("capturas_sobrepostas_no_mesmo_minuto")
            vistos.add(minuto)
            inicio = minuto - minuto % segundos
            atual = juntos.get(inicio)
            if atual is None:
                juntos[inicio] = list(barra)
            else:
                atual[1], atual[2] = max(atual[1], barra[1]), min(atual[2], barra[2])
                atual[3] = barra[3]
                atual[4] += barra[4]
        saida = [{"time": inicio, "open": b[0] * self.tick, "high": b[1] * self.tick, "low": b[2] * self.tick,
                  "close": b[3] * self.tick, "volume": b[4]} for inicio, b in sorted(juntos.items())]
        return saida[-limite:]


    def candles_com_cobertura(self, quadro: str, limite: int, viva: Optional[Barras] = None,
                              *, captura_id: Optional[str] = None) -> dict:
        """Exibicao: se ha sobreposicao, entrega apenas o recorte da captura seguida.

        Nunca combina OHLCVs sobrepostos nem afirma cobertura integral do periodo.
        A API `candles` continua estrita para consumidores que precisam da uniao.
        """
        try:
            return {"candles": self.candles(quadro, limite, viva)}
        except HistoricoError as erro:
            if str(erro) != "capturas_sobrepostas_no_mesmo_minuto":
                raise
            if (viva is None or not isinstance(captura_id, str) or not captura_id
                    or len(captura_id) > 160 or any(c not in
                    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in captura_id)):
                raise HistoricoError("sobreposicao_sem_recorte_disponivel") from erro
            isolado = Historico(self.capturas, self.cache, tick=self.tick, simbolo=self.simbolo,
                                bolsa=self.bolsa, relogio=self._relogio)
            linhas = isolado.candles(quadro, limite, viva)
            if not linhas:
                raise HistoricoError("sobreposicao_sem_recorte_disponivel") from erro
            return {"candles": [{**linha, "partial": True} for linha in linhas],
                    "coverage": {"kind": "selected_capture_only", "reason": "overlapping_captures",
                                 "partial": True, "periodComplete": False, "captureId": captura_id}}
