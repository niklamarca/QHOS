"""Vigia da captura (etapa 3, passo 9: o minimo da etapa 5): silencio e atraso crescente com a sessao viva, e o
livro relido no fechamento de cada dia.

So le (regras 16 e 21): segue a captura em andamento pelo mesmo `Leitor` do coletor, em outro processo e com
prioridade baixa, e escreve apenas no seu proprio diario, `<data_root>/vigia/<captura_id>.jsonl`. Nao fala com a
Rithmic, nao controla processo nenhum e o formato da captura nao muda.

O que a captura de 28/10 (FOMC) mostrou e que isto detecta, com os limiares escritos no plano (secao 4.2):
  - silencio: nenhum quadro de mercado por mais de SILENCIO_S (60 s, o mesmo limiar da lacuna N6, por
    construcao) sem que a sessao tenha terminado (sem manifesto). Os heartbeats que mantem a sessao viva nao vao
    ao arquivo; daqui, "sessao viva" = a captura nao foi encerrada. O fechamento confirma depois, pelo manifesto:
    quadros depois do silencio, ou sessao encerrada normalmente (logout confirmado), provam que ela estava viva;
  - atraso crescente: a mediana por minuto de (chegada local - carimbo do servidor), nos quadros com carimbo,
    sobe em SUBIDAS minutos seguidos, cada um ao menos DEGRAU_S acima do anterior, e termina acima de
    ATRASO_ALERTA_S. Um desvio constante do relogio local (que `qhos2.atraso` ja alerta) nao sobe: nao dispara.
    Em 28/10 o atraso foi de 80 ms a 132 s em 13 min, antes de 62 min sem entrega;
  - livro: o instante exato em que o livro deixa de valer depois do retrato (suspeito ou cruzado) e em que volta.

Fechamento (quando o manifesto aparece; ou de uma vez, com `--captura` numa captura encerrada): o livro e relido
do zero por um objeto independente (`eventos_da_captura`, que exige captura verificada), e comparado com o livro
seguido ao vivo (mesma assinatura ou nao); lacunas > 60 s medidas no arquivo inteiro; perfil do atraso por minuto
com a mesma regra do ao vivo; e, pela sessao gravada no manifesto, se o dia terminou com a sessao viva. Um
silencio entre o ultimo quadro e o fim da sessao (`silencio_final`) tambem e medido: desde 29/10 (decisao do autor)
o avaliador conta esse dia ate o ultimo quadro quando a sessao foi encerrada normalmente as 16h ou depois
(`completo_ate_silencio_final`); a vigia registra para que o corte seja visto no mesmo dia.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from qhos2 import eventos as ev
from qhos2.atraso import RELOGIO_SUSPEITO_S
from qhos2.catalogo import _escrever_atomico, _ler_manifesto, verificar_captura
from qhos2.desequilibrio import LACUNA_MAX_NS
from qhos2.leitura import BLOCO_BYTES, ENCERRADA, FALHA, LENDO, Leitor, LeituraError, Quadro, captura_em_andamento
from qhos2.livro import PRONTO, Livro

ESQUEMA = "qhos2.vigia.v1"
ESQUEMA_FECHAMENTO = "qhos2.vigia.fechamento.v1"
SILENCIO_NS = LACUNA_MAX_NS                       # 60 s: o mesmo limiar da lacuna N6
MINUTO_NS = 60 * 10 ** 9
ATRASO_ALERTA_NS = int(RELOGIO_SUSPEITO_S * 10 ** 9)   # 1 s: o mesmo limiar de qhos2.atraso
DEGRAU_NS = 500_000_000                           # cada minuto ao menos 0,5 s acima do anterior
SUBIDAS = 3                                       # tres minutos seguidos subindo
MAX_TRANSICOES = 50                               # linhas de livro_valido/livro_invalido no diario; depois so conta
TICK_PADRAO = 0.25                                # ES; o tick certo vem do manifesto no fechamento
ACHADOS = ("captura_nao_verificada", "sessao_caiu", "releitura_recusada", "releitura_diferente_do_vivo",
           "ao_vivo_diverge_da_releitura", "lacuna", "silencio_final", "atraso_crescente", "livro_invalido")


def _iso(ns: Optional[int]) -> str:
    if ns is None:
        return "-"
    return datetime.fromtimestamp(ns / 1e9, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _minutos_ms(minutos: List[list]) -> List[list]:
    """[minuto UTC, mediana em ms, quadros com carimbo] por minuto, para diario e relatorio."""
    return [[datetime.fromtimestamp(minuto * 60, timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), round(mediana / 1e6, 3), n]
            for minuto, mediana, n in minutos]


def _anexar(caminho: Path, texto: str) -> None:
    fd = os.open(caminho, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as arquivo:
        arquivo.write(texto + "\n")
        arquivo.flush()
        os.fsync(arquivo.fileno())


class _Atrasos:
    """Mediana por minuto de (chegada local - carimbo do servidor) e a regra do atraso crescente.

    A mesma classe serve ao vivo e na releitura, para que as duas vejam a mesma coisa por construcao.
    """

    def __init__(self):
        self.minutos: List[list] = []       # [minuto, mediana_ns, n]
        self.crescentes: List[list] = []    # cada deteccao: os SUBIDAS + 1 minutos que a formaram
        self._minuto: Optional[int] = None
        self._amostras: List[int] = []
        self._ativo = False

    def quadro(self, recebido_ns: int, eventos) -> Optional[list]:
        """Soma o quadro; devolve a deteccao de atraso crescente quando um minuto fecha com ela."""
        carimbos = [e.carimbo_ns for e in eventos if e.carimbo_ns is not None and not e.retrato]
        if not carimbos:
            return None
        minuto, deteccao = recebido_ns // MINUTO_NS, None
        if minuto != self._minuto:
            deteccao = self.fechar()
            self._minuto = minuto
        self._amostras.append(recebido_ns - max(carimbos))
        return deteccao

    def fechar(self) -> Optional[list]:
        """Fecha o minuto corrente (mediana) e aplica a regra: SUBIDAS degraus seguidos, o ultimo acima do alerta."""
        if not self._amostras:
            return None
        ordenadas = sorted(self._amostras)
        self.minutos.append([self._minuto, ordenadas[len(ordenadas) // 2], len(ordenadas)])
        self._amostras = []
        ultimos = self.minutos[-(SUBIDAS + 1):]
        sobe = (len(ultimos) == SUBIDAS + 1
                and all(b[0] == a[0] + 1 and b[1] >= a[1] + DEGRAU_NS for a, b in zip(ultimos, ultimos[1:]))
                and ultimos[-1][1] >= ATRASO_ALERTA_NS)
        if sobe and not self._ativo:
            self._ativo = True
            self.crescentes.append([list(m) for m in ultimos])
            return self.crescentes[-1]
        if not sobe:
            self._ativo = False          # parou de subir: a proxima rampa e outra deteccao
        return None

    def resumo(self) -> dict:
        medianas = [m[1] for m in self.minutos]
        return {"atraso_por_minuto_ms": _minutos_ms(self.minutos),
                "atraso_mediana_ms": None if not medianas else round(sorted(medianas)[len(medianas) // 2] / 1e6, 3),
                "atraso_maximo_ms": None if not medianas else round(max(medianas) / 1e6, 3),
                "atraso_crescente": [_minutos_ms(c) for c in self.crescentes]}


class Vigia:
    """Segue uma captura em andamento com o Leitor e registra no diario so o que o arquivo gravado mostra."""

    def __init__(self, leitor: Leitor, *, diario: Optional[Path] = None, relogio=time.time_ns, avisar=print):
        self.leitor, self.diario, self._relogio, self._avisar = leitor, diario, relogio, avisar
        self.livro = Livro()                  # o mesmo livro do Leitor, quadro a quadro: o instante exato de cada transicao
        self.eventos: List[dict] = []
        self.contagens: Counter = Counter()
        self.atrasos = _Atrasos()
        self.silencio: Optional[dict] = None
        self.fechamento: Optional[dict] = None
        self._anterior_ns: Optional[int] = None
        self._livro_valia = False

    def registrar(self, evento: str, **campos) -> dict:
        linha = {"esquema": ESQUEMA, "captura_id": self.leitor.pasta.name, "evento": evento,
                 "registrado_ns": self._relogio(), **campos}
        self.eventos.append(linha)
        texto = json.dumps(linha, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if self.diario is not None:
            _anexar(self.diario, texto)
        self._avisar(texto)
        return linha

    # ---- uma volta -----------------------------------------------------------------------------------

    def passo(self, max_bytes: int = BLOCO_BYTES) -> List[Quadro]:
        quadros = self.leitor.avancar(max_bytes)
        for indice, recebido, eventos in quadros:
            self._quadro(indice, recebido, eventos)
        self._conferir_silencio()
        if self.leitor.estado in (ENCERRADA, FALHA) and self.fechamento is None:
            self._encerrar()
        return quadros

    def _quadro(self, indice: int, recebido: int, eventos) -> None:
        self.contagens["quadros"] += 1
        anterior, self._anterior_ns = self._anterior_ns, recebido
        if self.silencio is not None:
            desde = self.silencio["desde_ns"]
            self.silencio = None
            # o arquivo ficou mudo; se o dado nao mostra a lacuna, quem parou foi a gravacao, nao a entrega
            nome = "lacuna" if recebido - desde > SILENCIO_NS else "silencio_sem_lacuna"
            self.registrar(nome, desde_ns=desde, ate_ns=recebido, duracao_s=round((recebido - desde) / 1e9, 3),
                           vista_ao_vivo=True)
        elif anterior is not None and recebido - anterior > SILENCIO_NS:     # a vigia chegou depois, ou estava atrasada
            self.registrar("lacuna", desde_ns=anterior, ate_ns=recebido, duracao_s=round((recebido - anterior) / 1e9, 3),
                           vista_ao_vivo=False)
        deteccao = self.atrasos.quadro(recebido, eventos)
        if deteccao is not None:
            self.registrar("atraso_crescente", minutos=_minutos_ms(deteccao))
        self.livro.aplicar_quadro(eventos)
        if self.livro.fase != PRONTO:
            return
        self.contagens["quadros_depois_do_retrato"] += 1
        vale = self.livro.qualificado          # a falha da leitura e registrada a parte, nao como livro invalido
        if vale:
            self.contagens["quadros_qualificados"] += 1
        if vale == self._livro_valia:
            return
        self._livro_valia = vale
        nome = "livro_valido" if vale else "livro_invalido"
        self.contagens[nome] += 1
        if self.contagens[nome] > MAX_TRANSICOES:
            return
        campos = {"instante_ns": recebido, "quadro": indice}
        if not vale:
            campos.update(suspeito=self.livro.suspeito, cruzado=self.livro.cruzado,
                          anomalias=dict(sorted(self.livro.anomalias.items())))
        self.registrar(nome, **campos)

    def _conferir_silencio(self) -> None:
        leitor = self.leitor
        if self.silencio is not None or leitor.estado != LENDO or leitor.ultimo_recebido_ns is None:
            return
        if (leitor.pasta / "manifest.json").is_file():       # a sessao terminou: silencio dai em diante nao e dela
            return
        if leitor.resumo()["pendente_bytes"]:                 # a leitura esta atras da gravacao: o silencio pode ser nosso
            return
        agora = self._relogio()
        if agora - leitor.ultimo_recebido_ns > SILENCIO_NS:
            self.silencio = {"desde_ns": leitor.ultimo_recebido_ns, "detectado_ns": agora, "quadros": leitor.quadros}
            self.registrar("silencio", **self.silencio)

    def _encerrar(self) -> None:
        leitor = self.leitor
        deteccao = self.atrasos.fechar()
        if deteccao is not None:
            self.registrar("atraso_crescente", minutos=_minutos_ms(deteccao))
        if leitor.estado == FALHA:
            self.registrar("leitura_falhou", codigo=leitor.codigo, quadros=leitor.quadros)
        else:
            self.registrar("captura_encerrada", quadros=leitor.quadros, bytes=leitor.bytes)
        self.fechamento = fechamento(leitor.pasta, tick=leitor.tick, vigia=self)
        if self.diario is not None:
            _escrever_atomico(self.diario.with_name(self.diario.stem + ".fechamento.json"),
                              json.dumps(self.fechamento, indent=2, sort_keys=True, allow_nan=False) + "\n")
        self.registrar("fechamento", achados=self.fechamento["achados"], igual_ao_vivo=self.fechamento.get("igual_ao_vivo"))

    def seguir(self, *, intervalo_s: float = 0.5, dormir=time.sleep, parar=lambda: False) -> Optional[dict]:
        """Volta apos volta ate o fechamento (manifesto conferido ou leitura falhada); None se `parar` pediu antes."""
        while not parar():
            quadros = self.passo()
            if self.fechamento is not None:
                return self.fechamento
            if not quadros and not self.leitor.resumo()["pendente_bytes"]:
                dormir(intervalo_s)
        return None


# ---- fechamento do dia ----------------------------------------------------------------------------------

def _sessao(sessao: dict, manifesto: dict, duracao_s: Optional[float]) -> dict:
    """O que o manifesto diz da sessao. Campos ausentes (manifestos antigos, sintetico) nao sao prova de falha."""
    periodo, erro = sessao.get("periodo_heartbeat_s"), sessao.get("erro")
    esperados = int(duracao_s / periodo) if periodo and duracao_s else None
    normal = (manifesto.get("transport_closed_normally") is not False and sessao.get("estado") in (None, "FECHADA")
              and not erro and sessao.get("logout_confirmado") is not False)
    return {"estado": sessao.get("estado"), "erro": erro, "tipo_de_falha": sessao.get("tipo_de_falha"),
            "logout_confirmado": sessao.get("logout_confirmado"),
            "transporte_fechado_normalmente": manifesto.get("transport_closed_normally"),
            "heartbeats_confirmados": sessao.get("heartbeats_confirmados"), "periodo_heartbeat_s": periodo,
            "heartbeats_esperados_aprox": esperados, "relogio_alerta": sessao.get("relogio_alerta"),
            "anomalias": sessao.get("anomalias"), "encerrada_normalmente": normal}


def _reler(pasta: Path, tick: Optional[float]) -> dict:
    """Releitura independente, do zero: livro, lacunas, qualificacao quadro a quadro e atraso por minuto."""
    livro, atrasos = Livro(), _Atrasos()
    anterior = primeiro_invalido = suspeito_desde = None
    lacunas: List[dict] = []
    depois = qualificados = 0
    for indice, grupo in itertools.groupby(ev.eventos_da_captura(pasta, tick=tick), key=lambda e: e.indice):
        eventos = tuple(grupo)
        recebido = eventos[0].recebido_ns
        if anterior is not None and recebido - anterior > SILENCIO_NS:
            lacunas.append({"desde_ns": anterior, "ate_ns": recebido, "duracao_s": round((recebido - anterior) / 1e9, 3),
                            "quadro": indice, "sessao_viva": True})     # houve quadros depois: a sessao seguiu entregando
        livro.aplicar_quadro(eventos)
        atrasos.quadro(recebido, eventos)
        if livro.fase == PRONTO:
            depois += 1
            if livro.qualificado:
                qualificados += 1
            elif primeiro_invalido is None:
                primeiro_invalido = recebido
            if livro.suspeito and suspeito_desde is None:
                suspeito_desde = recebido
        anterior = recebido
    atrasos.fechar()
    return {"livro": livro.resumo(), "assinatura": livro.assinatura(), "quadros_depois_do_retrato": depois,
            "quadros_qualificados": qualificados,
            "fracao_qualificada": round(qualificados / depois, 4) if depois else None,
            "primeiro_invalido_ns": primeiro_invalido, "suspeito_desde_ns": suspeito_desde, "lacunas": lacunas,
            **atrasos.resumo()}


def _silencio_final(status: dict, ultimo_ns: Optional[int], sessao_viva: bool) -> Optional[dict]:
    """Do ultimo quadro ao fim da sessao (o manifesto e escrito no fim). So acima do limiar do silencio."""
    try:
        fim_ns = int(datetime.fromisoformat(str(status.get("criado_em")).replace("Z", "+00:00")).timestamp() * 1e9)
    except ValueError:
        return None
    if ultimo_ns is None or fim_ns - ultimo_ns <= SILENCIO_NS:
        return None
    return {"desde_ns": ultimo_ns, "ate_ns": fim_ns, "duracao_s": round((fim_ns - ultimo_ns) / 1e9, 3),
            "sessao_viva": sessao_viva}


def fechamento(pasta, *, tick: Optional[float] = None, vigia: Optional[Vigia] = None) -> dict:
    """Relatorio do dia; `achados` vazio e um dia limpo. Com `vigia`, compara com o que foi visto ao vivo."""
    pasta = Path(pasta).resolve()
    saida: dict = {"esquema": ESQUEMA_FECHAMENTO, "captura_id": pasta.name, "achados": []}
    achados = set()
    relatorio = verificar_captura(pasta)
    saida["verificada"] = relatorio["verificada"]
    if not relatorio["verificada"]:
        saida["divergencias"] = relatorio["divergencias"]
        saida["achados"] = ["captura_nao_verificada"]
        return saida
    manifesto = _ler_manifesto(pasta, [])
    status = manifesto.get("status") if isinstance(manifesto.get("status"), dict) else {}
    sessao = status.get("sessao") if isinstance(status.get("sessao"), dict) else {}
    saida.update(quadros=relatorio["quadros"], primeiro_ns=relatorio["primeiro_ns"], ultimo_ns=relatorio["ultimo_ns"],
                 duracao_s=relatorio["duracao_s"], ambiente=relatorio.get("ambiente"))
    saida["sessao"] = _sessao(sessao, manifesto, relatorio["duracao_s"])
    if not saida["sessao"]["encerrada_normalmente"]:
        achados.add("sessao_caiu")
    try:
        releitura = _reler(pasta, tick)
    except ev.EventoError as erro:
        releitura = {"recusada": str(erro)}
        achados.add("releitura_recusada")
    saida["releitura"] = releitura
    if "recusada" not in releitura:
        if releitura["lacunas"]:
            achados.add("lacuna")
        if releitura["atraso_crescente"]:
            achados.add("atraso_crescente")
        if releitura["primeiro_invalido_ns"] is not None:
            achados.add("livro_invalido")
    saida["silencio_final"] = _silencio_final(status, relatorio["ultimo_ns"], saida["sessao"]["encerrada_normalmente"])
    if saida["silencio_final"] is not None:
        achados.add("silencio_final")
    if vigia is not None:
        lacunas_vivas = [e for e in vigia.eventos if e["evento"] == "lacuna"]
        ao_vivo = {"lacunas": lacunas_vivas, "silencios": sum(e["evento"] == "silencio" for e in vigia.eventos),
                   "atraso_crescente": sum(e["evento"] == "atraso_crescente" for e in vigia.eventos),
                   "livro_invalido": vigia.contagens["livro_invalido"], "contagens": dict(vigia.contagens),
                   "leitura": vigia.leitor.estado, "codigo": vigia.leitor.codigo}
        if "recusada" not in releitura and vigia.leitor.estado == ENCERRADA:
            saida["igual_ao_vivo"] = vigia.livro.assinatura() == releitura["assinatura"]
            if not saida["igual_ao_vivo"]:
                achados.add("releitura_diferente_do_vivo")
            ao_vivo["lacunas_coincidem"] = ({(e["desde_ns"], e["ate_ns"]) for e in lacunas_vivas}
                                            == {(l["desde_ns"], l["ate_ns"]) for l in releitura["lacunas"]})
            if not ao_vivo["lacunas_coincidem"]:
                achados.add("ao_vivo_diverge_da_releitura")
        else:
            saida["igual_ao_vivo"] = None
        saida["ao_vivo"] = ao_vivo
    saida["achados"] = [a for a in ACHADOS if a in achados]
    return saida


# ---- CLI -------------------------------------------------------------------------------------------------

def _imprimir(r: dict) -> None:
    print(f"fechamento {r['captura_id']}: verificada={r['verificada']} achados={','.join(r['achados']) or 'nenhum'}")
    if not r["verificada"]:
        print(f"  divergencias={r['divergencias']}")
        return
    s = r["sessao"]
    print(f"  sessao: estado={s['estado']} erro={s['erro']} logout={s['logout_confirmado']} "
          f"heartbeats={s['heartbeats_confirmados']} (esperados ~{s['heartbeats_esperados_aprox']}) "
          f"encerrada_normalmente={s['encerrada_normalmente']}")
    print(f"  quadros={r['quadros']} de {_iso(r['primeiro_ns'])} a {_iso(r['ultimo_ns'])} ({r['duracao_s']} s)")
    rel = r["releitura"]
    if "recusada" in rel:
        print(f"  releitura recusada: {rel['recusada']}")
    else:
        l = rel["livro"]
        print(f"  livro relido: fase={l['fase']} qualificado={l['qualificado']} suspeito={l['suspeito']} ordens={l['ordens']} "
              f"anomalias={sum(l['anomalias'].values())} assinatura={rel['assinatura'][:16]} igual_ao_vivo={r.get('igual_ao_vivo')}")
        fracao = "-" if rel["fracao_qualificada"] is None else f"{rel['fracao_qualificada']:.2%}"
        print(f"  qualificado em {rel['quadros_qualificados']} de {rel['quadros_depois_do_retrato']} quadros depois do retrato "
              f"({fracao}); primeiro invalido {_iso(rel['primeiro_invalido_ns'])}; suspeito desde {_iso(rel['suspeito_desde_ns'])}")
        for lacuna in rel["lacunas"]:
            print(f"  lacuna: {_iso(lacuna['desde_ns'])} a {_iso(lacuna['ate_ns'])} ({lacuna['duracao_s']} s), sessao viva")
        print(f"  atraso: mediana {rel['atraso_mediana_ms']} ms, maior minuto {rel['atraso_maximo_ms']} ms; "
              f"crescente {len(rel['atraso_crescente'])} vez(es)")
        for rampa in rel["atraso_crescente"]:
            print("    " + " -> ".join(f"{minuto} {mediana} ms" for minuto, mediana, _ in rampa))
    sf = r["silencio_final"]
    if sf is not None:
        print(f"  silencio final: {_iso(sf['desde_ns'])} a {_iso(sf['ate_ns'])} ({sf['duracao_s']} s), "
              f"sessao {'viva' if sf['sessao_viva'] else 'caida'}")
    if "ao_vivo" in r:
        v = r["ao_vivo"]
        print(f"  ao vivo: leitura={v['leitura']} silencios={v['silencios']} lacunas={len(v['lacunas'])} "
              f"coincidem={v.get('lacunas_coincidem')} atraso_crescente={v['atraso_crescente']} livro_invalido={v['livro_invalido']}")


def _sem_usuario(caminho: Path) -> str:
    casa = Path.home().resolve()
    return "~/" + str(caminho.relative_to(casa)) if casa in caminho.parents else str(caminho)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="qhos2.vigia", description=__doc__.splitlines()[0])
    alvo = ap.add_mutually_exclusive_group(required=True)
    alvo.add_argument("--captura", type=Path, help="pasta da captura, em andamento ou encerrada")
    alvo.add_argument("--em-andamento", action="store_true", help="a unica captura em andamento em <data_root>/capturas")
    ap.add_argument("--raiz", type=Path, help="raiz unica dos dados (padrao: QHOS2_DATA_ROOT); o diario vai em <raiz>/vigia")
    ap.add_argument("--tick", type=float, default=TICK_PADRAO, help="tick enquanto o manifesto nao existe (padrao 0,25)")
    ap.add_argument("--intervalo", type=float, default=0.5, help="segundos entre voltas sem quadro novo")
    ap.add_argument("--json", action="store_true", help="imprime o fechamento em JSON")
    args = ap.parse_args(argv)
    from qhos2.coletor import raiz_para_captura
    from qhos2.config import ConfigError
    from qhos2.operacao import OperacaoError, ler_identidade_captura
    try:
        raiz = raiz_para_captura(args.captura, args.raiz)
        pasta = args.captura.expanduser().resolve() if args.captura else captura_em_andamento(raiz / "capturas")
        identidade = ler_identidade_captura(pasta)
    except (ConfigError, LeituraError, OperacaoError, OSError) as erro:
        print(f"PARE: {erro if isinstance(erro, (ConfigError, LeituraError, OperacaoError)) else type(erro).__name__}")
        return 2
    pasta_diario = raiz / "vigia"
    pasta_diario.mkdir(mode=0o700, exist_ok=True)
    diario = pasta_diario / f"{pasta.name}.jsonl"
    if (pasta / "manifest.json").is_file():
        relatorio = fechamento(pasta)
        _escrever_atomico(diario.with_name(diario.stem + ".fechamento.json"),
                          json.dumps(relatorio, indent=2, sort_keys=True, allow_nan=False) + "\n")
        _anexar(diario, json.dumps({"esquema": ESQUEMA, "captura_id": pasta.name, "evento": "fechamento",
                                    "registrado_ns": time.time_ns(), "achados": relatorio["achados"], "igual_ao_vivo": None},
                                   sort_keys=True, separators=(",", ":")))
    else:
        if hasattr(os, "nice"):
            os.nice(10)  # a captura tem prioridade sobre a vigia
        try:
            leitor = Leitor(pasta, tick=args.tick, simbolo=identidade["requested_symbol"], bolsa=identidade["exchange"])
        except LeituraError as erro:
            print(f"PARE: {erro}")
            return 2
        vigia = Vigia(leitor, diario=diario)
        print(f"vigiando {pasta.name} ({identidade['requested_symbol']}/{identidade['exchange']}); "
              f"diario em {_sem_usuario(diario)}", flush=True)
        try:
            relatorio = vigia.seguir(intervalo_s=args.intervalo)
        except KeyboardInterrupt:
            print("interrompido: sem fechamento")
            return 130
    if args.json:
        print(json.dumps(relatorio, sort_keys=True, allow_nan=False))
    else:
        _imprimir(relatorio)
    return 2 if not relatorio["verificada"] else (1 if relatorio["achados"] else 0)


if __name__ == "__main__":
    sys.exit(main())
