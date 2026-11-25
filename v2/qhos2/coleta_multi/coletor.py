"""Laco diario do coletor multi-ativo: o pregao padrao de todo dia, sem falta, e o fechamento no DataSet.

    cd ~/qhos-coleta/qhos2 && PYTHONPATH=src:. ~/qhos-coleta-venv/bin/python -m coleta_multi.coletor \\
        --online --agreements-accepted       (em producao: servico qhos2-coleta-multi, senha pela credencial)

Janela: a mesma do agendador do ES, 09:00-16:15 de NY (10:00-17:15 de Brasilia no horario de verao dos EUA,
11:00-18:15 fora dele). Entra 60 s depois do inicio (o ES loga primeiro) e sai 120 s antes do fim.

Volta sozinho (decisao do autor, 29/10):
  * queda de conexao no meio do pregao: espera ESPERA_APOS_QUEDA_S (o servidor descarta a sessao morta), libera a
    propria guarda e captura o resto do pregao em outra parte (<BOLSA>_<CONTRATO>_p2, _p3...);
  * reinicio do PC/WSL no meio do pregao: o servico sobe, espera o mesmo tempo e captura o resto;
  * login recusado ou logout forcado: para tudo, NAO tenta de novo, nem depois de reiniciar (quarentena propria).
Uma parte nunca e regravada; o dia so conta como feito quando a janela termina.

Fechamento: FECHAMENTO_APOS_FIM_S (30 min) depois do fim da janela, com a prova do ES escrita: le o pregao de
todos os ativos, compacta, confere e publica em DataSet\\<dia do pregao>\\ com INDEX.json e SHA256SUMS.txt.

Protecao do ES: o coletor nunca adquire a guarda do ES; so le o estado do agendador. Se o agendador ENTRAR em
quarentena durante a sessao do coletor, o coletor sai na hora.

Reserva do ES (decisao do autor, 30/10): quando o agendador do ES nao esta e nao vai mais estar gravando o pregao
do dia (reserva_do_es), a parte seguinte do coletor assina tambem o ES, e ele vai ao DataSet como
<BOLSA>_<CONTRATO>_reserva[_pN].tar.xz, com origem "reserva_do_coletor". O codigo congelado do ES nao muda. Nunca
com o ES do agendador CAPTURANDO, nem depois de login recusado ou logout forcado dele. Se o ES cair com o coletor
gravando, a parte em curso sai e a seguinte comeca na hora, ja com o ES (os outros ativos perdem so os segundos do
novo login), desde que ainda caiba uma parte inteira; perto do fim nao troca. Se o agendador voltar a gravar, a
parte com o ES sai na hora e a seguinte vem sem ele. Limite conhecido: na semana da rolagem (dezembro) o front
month da Rithmic pode nao ser o contrato do agendador; o INDEX registra o contrato de cada arquivo.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import signal
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from qhos2 import agenda, captura, disco
from qhos2.agenda import Registro, _esperar, _iso
from qhos2.config import ConfigError, Settings, validar_data_root
from qhos2.operacao import GuardaCaptura, OperacaoError
from qhos2.sessao import SessaoError

from coleta_multi import aviso, fechamento, limpeza
from coleta_multi.lista import ES, QUOTE_BOARD, cobertura, conferir_lista
from coleta_multi.sessao_multi import SessaoMulti, falha_de_login

GIB = 1024 ** 3
ATRASO_ENTRADA_S = 60.0
FOLGA_S = agenda.FOLGA_S
DURACAO_MINIMA_S = agenda.DURACAO_MINIMA_S
VIGIA_ES_S = 15.0
ESPERA_APOS_QUEDA_S = 300.0
FECHAMENTO_APOS_FIM_S = 1800.0
ESPERA_PROVA_ES_S = 3 * 3600.0
FALHAS_DE_LOGIN = frozenset({"login_recusado", "logout_forcado"})
# Reserva do ES: "pulado_sem_espaco" nao a aciona (nao disputa disco com o ES); "capturado" so se foi o dia inteiro
SEM_RESERVA = frozenset({"pulado_sem_espaco"})
MARGEM_PARTIDA_ES_S = 60.0  # alem do atraso maximo do agendador: depois disso ele nao comeca mais o dia
MARGEM_TROCA_S = 60.0  # logout e novo login: so troca para a reserva se ainda couber uma parte inteira depois
PARA_A_RESERVA, ES_DO_AGENDADOR_VOLTOU = "es_reserva", "es_do_agendador_capturando"
TROCAS = {PARA_A_RESERVA: "encerrada_para_a_reserva_do_es",
          ES_DO_AGENDADOR_VOLTOU: "encerrada_porque_o_es_do_agendador_voltou"}
DATASET_PADRAO = Path("/mnt/c/Users/usuario/OneDrive/Desktop/DataSet")
# Janela de teste do pre-registro ("sem olhar"): o ES desses pregoes vai para DataSet\SELADO_ate_2026-11-21\<dia>\
# (decisao do autor, 29/10) e, a partir de 21/11, todos esses dias vao para DataSet\<dia>\ (timer das 07:30 NY).
SELADO_DE, SELADO_ATE = "2026-11-09", "2026-11-20"
SELADO_PASTA = "SELADO_ate_2026-11-21"
SELADO_LIBERA_EM = date(2026, 11, 21)
FIM_PADRAO_DO_DIA = datetime.strptime("16:15", "%H:%M").time()


def guarda_propria() -> Path:
    return Path.home() / ".local" / "state" / "qhos2" / "coleta_multi"


def ler_json(caminho: Path) -> Optional[dict]:
    try:
        return json.loads(Path(caminho).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def prova_do_es(provas: Path, dia: str) -> Optional[dict]:
    """Ultima linha de provas.jsonl do agendador do ES para `dia` (so leitura)."""
    try:
        linhas = Path(provas).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    achada = None
    for linha in linhas:
        try:
            valor = json.loads(linha)
        except ValueError:
            continue
        if isinstance(valor, dict) and valor.get("dia") == dia:
            achada = valor
    return achada


def em_quarentena_por_login(registro: Registro) -> Optional[str]:
    """Motivo se o coletor parou por login recusado ou logout forcado (vale ate liberacao manual), senao None."""
    estado = ler_json(registro.pasta / "estado.json") or {}
    return estado.get("motivo") if estado.get("estado") == "QUARENTENA" and estado.get("motivo") in FALHAS_DE_LOGIN \
        else None


def _dia_inteiro(prova: dict) -> bool:
    """Prova "capturado" registrada so no fim da janela. O agendador tambem marca "capturado" quando e parado de forma
    ordenada no meio do pregao (logout limpo); essa nao cobre o dia. Sem as datas na prova, conta como dia inteiro."""
    try:
        fim = datetime.fromisoformat(str(prova["janela_fim"]).replace("Z", "+00:00"))
        registrado = datetime.fromisoformat(str(prova["registrado_utc"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return True
    return registrado >= fim - timedelta(seconds=agenda.FOLGA_S + 60)


def reserva_do_es(estado_es: Path, provas_es: Path, dia: str, inicio: datetime, agora: datetime) -> Optional[str]:
    """Motivo para o coletor assinar o ES como reserva agora, ou None. So le o agendador do ES. Entra quando ele nao
    esta CAPTURANDO e o pregao de `dia` ja nao sera gravado por ele: a prova do dia existe e nao e de dia inteiro
    (queda, reinicio, guarda, parada no meio), ou nao ha prova e ja passou do atraso maximo com que ele ainda
    comecaria o dia. Nunca depois de login recusado ou logout forcado dele (a mesma conta: para tudo)."""
    estado = ler_json(estado_es) or {}
    if estado.get("estado") == "CAPTURANDO" or (estado.get("estado") == "QUARENTENA" and
                                               estado.get("motivo") in FALHAS_DE_LOGIN):
        return None
    prova = prova_do_es(provas_es, dia)
    if prova is not None:
        resultado = prova.get("resultado")
        if resultado == "capturado":
            return None if _dia_inteiro(prova) else "es_capturado_parcial"
        return None if resultado in SEM_RESERVA else f"es_{resultado}"
    if agora >= inicio + timedelta(seconds=agenda.ATRASO_MAX_S + MARGEM_PARTIDA_ES_S):
        return "es_sem_partida"
    return None


async def _vigiar_es(sessao: SessaoMulti, estado_es: Path, parar_vigia: asyncio.Event, intervalo_s: float, *,
                     com_es: bool = False, reserva: Optional[Callable[[], Optional[str]]] = None) -> None:
    """Se o agendador do ES ENTRAR em quarentena durante a sessao do coletor (pode ter sido por causa dele), o
    coletor sai na hora. Quarentena que ja existia antes nao para o coletor: o ES nem esta logado.
    Reserva: parte sem o ES sai quando a reserva passa a valer; parte com o ES sai se o agendador voltar a gravar."""
    inicial = (ler_json(estado_es) or {}).get("estado")
    while not parar_vigia.is_set():
        atual = (ler_json(estado_es) or {}).get("estado")
        motivo = None
        if atual == "QUARENTENA" and inicial != "QUARENTENA":
            motivo = "es_QUARENTENA"
        elif com_es and atual == "CAPTURANDO":
            motivo = ES_DO_AGENDADOR_VOLTOU
        elif not com_es and reserva is not None and reserva():
            motivo = PARA_A_RESERVA
        if motivo:
            if sessao.parada is None:
                sessao.parada = motivo
                sessao.parar()
            return
        await _esperar(parar_vigia, intervalo_s)


def _dia_de_pregao_es(dia: str) -> bool:
    from coleta_multi import calendario_continuo
    d = date.fromisoformat(dia)
    return d.weekday() < 5 and d not in calendario_continuo.feriados(d.year)


def _proximo_sufixo(pasta_dia: Path) -> str:
    """'' na primeira parte do dia; depois '_p2', '_p3'... Conta registros e pastas: uma parte morta por reinicio
    nao tem .status, mas a pasta dela existe e nunca e regravada."""
    usadas = set()
    if pasta_dia.is_dir():
        for p in pasta_dia.iterdir():
            if p.name.startswith(".status-p") and p.name.endswith(".json"):
                usadas.add(int(p.name[len(".status-p"):-len(".json")]))
            elif p.is_dir() and not p.name.startswith("."):
                cauda = p.name.rsplit("_p", 1)
                usadas.add(int(cauda[1]) if len(cauda) == 2 and cauda[1].isdigit() else 1)
    return "" if not usadas else f"_p{max(usadas) + 1}"


def _salvar_status(pasta_dia: Path, sufixo: str, ativos: list) -> None:
    pasta_dia.mkdir(parents=True, exist_ok=True)
    nome = f".status-{sufixo.lstrip('_') or 'p1'}.json"
    (pasta_dia / nome).write_text(json.dumps(ativos, sort_keys=True, default=str), encoding="utf-8")


def _ativos_salvos(pasta_dia: Path) -> list:
    saida = []
    for arquivo in sorted(pasta_dia.glob(".status-p*.json"), key=lambda p: int(p.stem.split("-p")[-1])):
        valor = ler_json(arquivo)
        saida.extend(valor if isinstance(valor, list) else [])
    return saida


async def _preparar_guarda(guarda_raiz: Path, registro: Registro, *, espera_s: float, parar, saida) -> Optional[str]:
    """Libera a guarda propria depois de queda ou reinicio (nunca depois de login recusado ou logout forcado).
    Devolve None se pode logar, senao o motivo."""
    motivo = em_quarentena_por_login(registro)
    if motivo:
        return f"quarentena_por_{motivo}"
    try:
        with GuardaCaptura(guarda_raiz) as guarda:
            estado = dict(guarda.estado)
    except (OperacaoError, OSError) as erro:
        return str(erro) if isinstance(erro, OperacaoError) else type(erro).__name__
    if estado.get("estado") == "LIVRE":
        return None
    referencia = estado.get("fim_ns") or 0  # EM_ANDAMENTO (processo morreu): conta a espera a partir de agora
    restante = espera_s - (time.time_ns() - referencia) / 1e9 if referencia else espera_s
    if restante > 0:
        saida(f"guarda propria em {estado.get('estado')} (queda ou reinicio): esperando {restante:.0f} s antes de voltar")
        await _esperar(parar, restante)
        if parar and parar.is_set():
            return "parado_pelo_operador"
    try:
        with GuardaCaptura(guarda_raiz) as guarda:
            if guarda.estado.get("estado") != "LIVRE":
                guarda.liberar(guarda.estado["geracao"])
    except (OperacaoError, OSError, KeyError) as erro:
        return "liberacao_falhou_" + (str(erro) if isinstance(erro, OperacaoError) else type(erro).__name__)
    saida("guarda propria liberada automaticamente depois de queda/reinicio")
    return None


async def rodar_coletor(calendario: dict, itens, fabrica, credenciais, registro: Registro, *, settings,
                        staging: Path, destino: Path, estado_es: Path, provas_es: Path, capturas_es: Path,
                        max_bytes_ativo: int, max_bytes_global: int, reserva_disco_bytes: int,
                        agora: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                        parar: Optional[asyncio.Event] = None, quando_pronta=None, guarda_raiz: Optional[Path] = None,
                        atraso_entrada_s: float = ATRASO_ENTRADA_S, folga_s: float = FOLGA_S,
                        duracao_minima_s: float = DURACAO_MINIMA_S, vigia_es_s: float = VIGIA_ES_S,
                        espera_apos_queda_s: float = ESPERA_APOS_QUEDA_S,
                        fechamento_apos_fim_s: float = FECHAMENTO_APOS_FIM_S,
                        espera_prova_es_s: float = ESPERA_PROVA_ES_S, processos: int = 8,
                        margem_troca_s: float = MARGEM_TROCA_S, saida: Callable[[str], None] = print,
                        avisar: Callable[[str], bool] = aviso.avisar) -> str:
    conferir_lista(itens)
    motivo = em_quarentena_por_login(registro)
    if motivo:
        saida(f"coletor em quarentena por {motivo}: nada sera tentado ate liberacao manual")
        return motivo
    lista = agenda.janelas(calendario)
    fins_do_dia = {agenda._dia_ny(a): b for a, b in lista}
    feitos = registro.dias_registrados()
    guarda_raiz = guarda_raiz if guarda_raiz is not None else guarda_propria()
    comum = dict(staging=staging, destino=destino, provas_es=provas_es, capturas_es=capturas_es, registro=registro,
                 agora=agora, parar=parar, espera_prova_es_s=espera_prova_es_s, processos=processos, saida=saida,
                 estado_es=estado_es, fechamento_apos_fim_s=fechamento_apos_fim_s)
    tentados = set()
    await _fechar_pendentes(fins_do_dia=fins_do_dia, tentados=tentados, **comum)
    for inicio, fim in lista:
        dia = agenda._dia_ny(inicio)
        if dia in feitos or fim <= agora():
            continue
        entrada = inicio + timedelta(seconds=atraso_entrada_s)
        while not (parar and parar.is_set()) and agora() < entrada:
            registro.estado("AGUARDANDO", proxima_janela=_iso(entrada), proximo_dia=dia)
            await _esperar(parar, min(60.0, (entrada - agora()).total_seconds()))
        if parar and parar.is_set():
            break
        falha = await _janela(dia, inicio, fim, itens, fabrica, credenciais, registro, settings=settings,
                              staging=staging, estado_es=estado_es, provas_es=provas_es, agora=agora,
                              guarda_raiz=guarda_raiz,
                              max_bytes_ativo=max_bytes_ativo, max_bytes_global=max_bytes_global,
                              reserva_disco_bytes=reserva_disco_bytes, quando_pronta=quando_pronta, folga_s=folga_s,
                              duracao_minima_s=duracao_minima_s, vigia_es_s=vigia_es_s,
                              espera_apos_queda_s=espera_apos_queda_s, margem_troca_s=margem_troca_s, parar=parar,
                              saida=saida, avisar=avisar)
        if falha in FALHAS_DE_LOGIN:
            return falha
        if parar and parar.is_set():
            break
        ativos = _ativos_salvos(Path(staging) / dia)
        if ativos and not (Path(destino) / dia / "INDEX.json").exists():
            tentados.add(dia)
            await _fechar(dia, fim, ativos, **comum)
        await _fechar_pendentes(fins_do_dia=fins_do_dia, tentados=tentados, **comum)
    if parar and parar.is_set():
        registro.estado("PARADO", motivo="pedido_do_operador")
        return "parado_pelo_operador"
    registro.estado("CALENDARIO_ESGOTADO", valido_ate=calendario.get("valido_ate"))
    return "calendario_esgotado"


async def _janela(dia, inicio, fim, itens, fabrica, credenciais, registro, *, settings, staging, estado_es, provas_es,
                  agora, guarda_raiz, max_bytes_ativo, max_bytes_global, reserva_disco_bytes, quando_pronta, folga_s,
                  duracao_minima_s, vigia_es_s, espera_apos_queda_s, margem_troca_s, parar, saida,
                  avisar) -> Optional[str]:
    """O pregao do dia: a primeira parte e, depois de cada queda ou troca da reserva do ES, a parte seguinte, ate o
    fim da janela. Devolve 'login_recusado' ou 'logout_forcado' se o laco deve parar."""
    pasta_dia = Path(staging) / dia
    partes = []

    def reserva_agora() -> Optional[str]:
        # troca so se ainda couber uma parte inteira depois do logout e do novo login; senao os outros ativos
        # perderiam o fim do pregao e o ES nao seria gravado de qualquer jeito
        if (fim - agora()).total_seconds() - folga_s < duracao_minima_s + margem_troca_s:
            return None
        return reserva_do_es(estado_es, provas_es, dia, inicio, agora())

    while not (parar and parar.is_set()):
        duracao = (fim - agora()).total_seconds() - folga_s
        if duracao < duracao_minima_s:
            break
        if disco.livre_bytes(staging) < max_bytes_global + reserva_disco_bytes:
            registro.provar(f"{dia}#sem_espaco", inicio, fim, "pulado_sem_espaco")
            saida(f"{dia}: sem espaco em disco; o coletor nao captura (o ES segue)")
            break
        impedimento = await _preparar_guarda(guarda_raiz, registro, espera_s=espera_apos_queda_s, parar=parar,
                                             saida=saida)
        if impedimento:
            registro.provar(f"{dia}#guarda", inicio, fim, "falhou_antes_do_login", motivo=impedimento)
            saida(f"{dia}: nao loga ({impedimento})")
            break
        duracao = (fim - agora()).total_seconds() - folga_s
        if duracao < duracao_minima_s:
            break
        sufixo = _proximo_sufixo(pasta_dia)
        parte = sufixo.lstrip("_") or "p1"
        reserva = reserva_do_es(estado_es, provas_es, dia, inicio, agora())
        itens_parte = tuple(itens) + (ES,) if reserva else tuple(itens)
        registro.estado("CAPTURANDO", dia=dia, parte=parte, janela_fim=_iso(fim), reserva_es=reserva)
        saida(f"{dia} {parte}: capturando ate {_iso(fim)} ({duracao:.0f} s)"
              + (f"; ES do agendador fora ({reserva}): o ES entra como reserva" if reserva else ""))
        resultado = await _um_dia(itens_parte, fabrica, credenciais, settings=settings, raiz=pasta_dia,
                                  dia=date.fromisoformat(dia), duracao=duracao, guarda_raiz=guarda_raiz,
                                  estado_es=estado_es, max_bytes_ativo=max_bytes_ativo,
                                  max_bytes_global=max_bytes_global, reserva_disco_bytes=reserva_disco_bytes,
                                  quando_pronta=quando_pronta, vigia_es_s=vigia_es_s, sufixo=sufixo,
                                  com_es=bool(reserva), reserva=None if reserva else reserva_agora)
        if resultado.get("erro_local"):
            registro.provar(f"{dia}#{parte}", inicio, fim, "falhou_antes_do_login", motivo=resultado["erro_local"])
            saida(f"{dia} {parte}: falhou antes do login ({resultado['erro_local']})")
            break
        st, falha = resultado["status"], resultado["falha"]
        _salvar_status(pasta_dia, sufixo, st["ativos"])
        cob = cobertura(st["ativos"])
        ate_o_fim = agora() >= fim - timedelta(seconds=folga_s + 60)
        completo = st["estado"] == "FECHADA" and st["logout_confirmado"] and st["parada"] is None and ate_o_fim
        troca = st["parada"] in TROCAS
        estado_parte = ("capturado" if completo else TROCAS[st["parada"]] if troca else
                        "interrompido_pelo_es" if (st["parada"] or "").startswith("es_") else "interrompido")
        registro.provar(f"{dia}#{parte}", inicio, fim, estado_parte, erro=st["erro"], tipo_de_falha=st["tipo_de_falha"],
                        parada=st["parada"], reserva_es=reserva, com_eventos=cob["com_eventos"], faltando=cob["faltando"],
                        bytes_gravados=st["bytes_gravados"], relogio=st["relogio"], relogio_serie=st["relogio_serie"],
                        ativos=[{k: a[k] for k in ("exibido", "bolsa", "contrato", "estado", "motivo",
                                                   "quadros_total", "bytes_brutos")} for a in st["ativos"]])
        partes.append((parte, estado_parte))
        saida(f"{dia} {parte}: {estado_parte} com_eventos={len(cob['com_eventos'])} bytes={st['bytes_gravados']} "
              f"erro={st['erro']}")
        if falha:
            registro.estado("QUARENTENA", motivo=falha, dia=dia)
            sobre_o_es = ("esta parte tambem assinava o ES como reserva (o agendador do ES ja estava fora)" if reserva
                          else "o ES nao foi tocado")
            saida(f"{dia}: {falha.upper()}: o coletor parou e nao tenta de novo; {sobre_o_es}")
            enviado = avisar(aviso.login_recusado(
                "coletor multi-ativo", falha, f"{dia} {parte}",
                f"O coletor dos 12 ativos parou e nao tenta de novo, nem depois de reiniciar; {sobre_o_es}."))
            registro.provar(f"{dia}#aviso", inicio, fim, "aviso_discord", enviado=enviado, motivo=falha)
            saida(f"aviso ao Discord: {'enviado' if enviado else 'FALHOU'}")
            return falha
        if completo or (st["parada"] and not troca) or (parar and parar.is_set()):
            break  # fim do pregao, teto/disco, quarentena do ES ou pedido do operador: nao retoma
        if troca:
            saida(f"{dia} {parte}: {estado_parte}; a parte seguinte comeca agora")
            continue
        saida(f"{dia} {parte}: queda ({st['erro']}); retoma o resto do pregao depois de {espera_apos_queda_s:.0f} s")
    if not (parar and parar.is_set()):
        registro.provar(dia, inicio, fim, "janela_encerrada", partes=partes)
    return None


async def _fechar_pendentes(*, fins_do_dia, tentados, staging, destino, agora, fechamento_apos_fim_s, **kw) -> None:
    """Recuperacao: fecha todo dia do staging cuja janela ja terminou e que ainda nao tem INDEX.json no DataSet
    (dia que ficou para tras por um reinicio, ou cujo fechamento falhou)."""
    staging = Path(staging)
    if not staging.is_dir():
        return
    for pasta in sorted(p for p in staging.iterdir() if p.is_dir() and len(p.name) == 10 and p.name[4] == "-"):
        dia = pasta.name
        fim = fins_do_dia.get(dia) or datetime.combine(date.fromisoformat(dia), FIM_PADRAO_DO_DIA, agenda.NY)
        if dia in tentados or (Path(destino) / dia / "INDEX.json").exists() or agora() < fim:
            continue
        ativos = _ativos_salvos(pasta)
        if not ativos:
            continue
        tentados.add(dia)
        await _fechar(dia, fim, ativos, staging=staging, destino=destino, agora=agora,
                      fechamento_apos_fim_s=fechamento_apos_fim_s, **kw)


async def _um_dia(itens, fabrica, credenciais, *, settings, raiz, dia, duracao, guarda_raiz, estado_es,
                  max_bytes_ativo, max_bytes_global, reserva_disco_bytes, quando_pronta, vigia_es_s,
                  sufixo: str = "", com_es: bool = False, reserva=None) -> dict:
    try:
        with GuardaCaptura(guarda_raiz) as guarda:
            guarda.conferir()
            sessao = SessaoMulti(itens, fabrica, settings=settings, raiz=raiz, dia=dia, duracao_s=duracao,
                                 max_bytes_ativo=max_bytes_ativo, max_bytes_global=max_bytes_global,
                                 reserva_disco_bytes=reserva_disco_bytes, sufixo=sufixo)
            if quando_pronta is not None:
                quando_pronta(sessao)
            guarda.iniciar()
            parar_vigia = asyncio.Event()
            vigia = asyncio.create_task(_vigiar_es(sessao, estado_es, parar_vigia, vigia_es_s, com_es=com_es,
                                                   reserva=reserva))
            try:
                await sessao.executar(credenciais)
            except SessaoError:
                pass
            finally:
                parar_vigia.set()
                await asyncio.gather(vigia, return_exceptions=True)
                st = sessao.status()
                normal = st["estado"] == "FECHADA" and st["logout_confirmado"]
                sessao.fechar_gravadores()
                guarda.finalizar(logout_confirmado=st["logout_confirmado"], fechamento_normal=normal)
            return {"status": sessao.status_multi(), "falha": falha_de_login(sessao)}
    except (OperacaoError, OSError) as erro:
        return {"erro_local": str(erro) if isinstance(erro, OperacaoError) else type(erro).__name__}


async def _fechar(dia, fim, ativos, *, staging, destino, provas_es, capturas_es, registro, agora, parar,
                  espera_prova_es_s, processos, saida, fechamento_apos_fim_s: float = FECHAMENTO_APOS_FIM_S,
                  estado_es: Optional[Path] = None) -> None:
    # Meia hora depois do fim do pregao (decisao do autor), com a prova do ES escrita e o agendador fora de
    # CAPTURANDO: compressao e trabalho pesado nunca correm com o ES capturando (pausa de 3,79 s em 29/10).
    momento = fim + timedelta(seconds=fechamento_apos_fim_s)
    limite = momento + timedelta(seconds=espera_prova_es_s)
    prova = prova_do_es(provas_es, dia)
    espera_es = _dia_de_pregao_es(dia)

    def pronto() -> bool:
        livre = estado_es is None or (ler_json(estado_es) or {}).get("estado") != "CAPTURANDO"
        return (prova is not None or not espera_es) and livre and agora() >= momento
    while not pronto() and agora() < limite and not (parar and parar.is_set()):
        registro.estado("ESPERANDO_O_FECHAMENTO", dia=dia, fechamento_as=_iso(momento))
        espera = (momento - agora()).total_seconds() if agora() < momento else (limite - agora()).total_seconds()
        await _esperar(parar, max(0.05, min(30.0, espera)))
        prova = prova_do_es(provas_es, dia)
    if parar and parar.is_set():
        return
    captura_es = None
    if prova and prova.get("captura") and prova.get("resultado") in {"capturado", "interrompido"}:
        captura_es = Path(capturas_es) / prova["captura"]
    selado = Path(destino) / SELADO_PASTA / dia if SELADO_DE <= dia <= SELADO_ATE else None
    registro.estado("FECHANDO", dia=dia)
    try:
        index = await asyncio.to_thread(fechamento.fechar_dia, dia, Path(staging) / dia, destino,
                                        ativos=ativos, captura_es=captura_es, prova_es=prova,
                                        processos=processos, selado_dia=selado)
    except Exception as erro:  # inclusive BrokenProcessPool: falha no fechamento nunca derruba o laco diario
        motivo = str(erro) if isinstance(erro, fechamento.FechamentoError) else type(erro).__name__
        registro.provar(f"{dia}#fechamento", fim, fim, "fechamento_falhou", motivo=motivo)
        saida(f"{dia}: fechamento falhou ({motivo}); capturas intactas no staging")
        return
    r = index["resumo"]
    registro.provar(f"{dia}#fechamento", fim, fim, "fechado", publicados=r["publicados"], ativos=r["ativos"],
                    bytes_brutos=r["bytes_brutos"], bytes_xz=r["bytes_xz"])
    saida(f"{dia}: DataSet {r['publicados']}/{r['ativos']} publicados, {r['bytes_brutos'] / GIB:.2f} GiB brutos, "
          f"{r['bytes_xz'] / GIB:.2f} GiB xz")
    hoje = agora().astimezone(agenda.NY).date()
    try:
        liberados = await asyncio.to_thread(fechamento.liberar_selados, destino, Path(destino) / SELADO_PASTA,
                                            hoje, SELADO_LIBERA_EM)
    except Exception as erro:
        liberados = [("*", type(erro).__name__)]
    if liberados:
        registro.provar(f"{dia}#selados", fim, fim, "selados_liberados", resultado_por_dia=dict(liberados))
        saida(f"{dia}: pasta selada -> {dict(liberados)}")
    # Bruto cuja copia no DataSet foi relida e conferida agora: so ele sai (o xz local fica).
    try:
        lib = await asyncio.to_thread(limpeza.liberar, Path(staging), destino, hoje)
    except Exception as erro:  # a limpeza nunca derruba o laco; na duvida nada e removido
        registro.provar(f"{dia}#limpeza", fim, fim, "limpeza_falhou", motivo=type(erro).__name__)
        return
    if lib["removidos"] or lib["mantidos"]:
        registro.provar(f"{dia}#limpeza", fim, fim, "brutos_liberados", removidos=lib["removidos"],
                        mantidos=lib["mantidos"], bytes_liberados=lib["bytes_liberados"])
        saida(f"{dia}: {len(lib['removidos'])} brutos liberados ({lib['bytes_liberados'] / GIB:.2f} GiB), "
              f"{len(lib['mantidos'])} mantidos")


def _travar(pasta: Path):
    import fcntl
    fd = os.open(pasta / "coletor.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise OperacaoError("coletor_ja_em_execucao") from None
    return fd


def main(argv=None, *, entrada: Callable[[str], str] = input, senha: Callable[[str], str] = getpass.getpass) -> int:
    ap = argparse.ArgumentParser(prog="coleta_multi.coletor", description=__doc__.splitlines()[0])
    ap.add_argument("--calendario", type=Path,
                    default=Path.home() / "qhos-v2-data" / "agenda" / "calendario-20261028-20261125.json")
    ap.add_argument("--destino", type=Path, default=DATASET_PADRAO)
    # Sonda de 29/10 (Globex a noite): 301 MiB/h somando os 12 ativos, NQ 150 MiB/h. O pregao e varias vezes
    # isso; o teto por ativo cobre o NQ com folga e o global para so este coletor muito antes de faltar disco ao ES.
    ap.add_argument("--max-gb-ativo", type=float, default=16.0)
    ap.add_argument("--max-gb-global", type=float, default=40.0)
    ap.add_argument("--reserva-gb", type=float, default=100.0, help="livre minimo (WSL e C:) para comecar e seguir")
    ap.add_argument("--online", action="store_true")
    ap.add_argument("--agreements-accepted", action="store_true")
    args = ap.parse_args(argv)
    try:
        conferir_lista(QUOTE_BOARD)
        settings = Settings.from_environ()
        staging = validar_data_root(settings.data_root / "coleta_multi" / "staging")
        calendario = json.loads(args.calendario.read_text(encoding="utf-8"))
        proximas = [(a, b) for a, b in agenda.janelas(calendario) if b > datetime.now(timezone.utc)]
    except (ConfigError, OSError, ValueError) as erro:
        print(f"coletor recusado: {erro if isinstance(erro, (ConfigError, ValueError)) else type(erro).__name__}")
        return 2
    for a, b in proximas[:5]:
        print(f"  {agenda._dia_ny(a)}: entra {(a + timedelta(seconds=ATRASO_ENTRADA_S)).astimezone(agenda.NY):%H:%M} NY, "
              f"sai {(b - timedelta(seconds=FOLGA_S)).astimezone(agenda.NY):%H:%M} NY, fecha no DataSet "
              f"{(b + timedelta(seconds=FECHAMENTO_APOS_FIM_S)).astimezone(agenda.NY):%H:%M} NY")
    print(f"{len(QUOTE_BOARD)} itens num login; staging {staging}; DataSet {args.destino}; tetos "
          f"{args.max_gb_ativo:g} GiB/ativo, {args.max_gb_global:g} GiB/dia, reserva {args.reserva_gb:g} GiB; "
          f"livre {disco.livre_bytes(settings.data_root) / GIB:.0f} GiB")
    if not proximas:
        print("nenhuma janela futura")
        return 2
    if not (args.online and args.agreements_accepted):
        print("nada foi conectado. Para rodar de verdade acrescente --online --agreements-accepted")
        return 0
    registro = Registro(settings.data_root / "coleta_multi" / "agenda")
    motivo = em_quarentena_por_login(registro)
    if motivo:
        print(f"coletor recusado antes das credenciais: quarentena por {motivo} (liberacao manual)")
        return 2
    try:
        trava = _travar(registro.pasta)
    except (OperacaoError, OSError) as erro:
        print("coletor recusado antes das credenciais: " + (str(erro) if isinstance(erro, OperacaoError) else type(erro).__name__))
        return 2
    credenciais = captura.obter_credenciais(entrada, senha)
    if not credenciais.user or not credenciais.password:
        print("usuario e senha sao obrigatorios; nada foi conectado")
        os.close(trava)
        return 2
    print("credenciais so na memoria deste processo. Ctrl+C encerra com seguranca.", flush=True)
    fabrica = captura._criar_fabrica(settings.endpoint)
    es = settings.data_root

    async def executar() -> str:
        parar = asyncio.Event()
        loop = asyncio.get_running_loop()

        def quando_pronta(sessao) -> None:
            def pedir() -> None:
                print("\nParando com seguranca (cancelando assinaturas e saindo)...", flush=True)
                parar.set()
                sessao.parar()
            for sinal in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sinal, pedir)
                except (NotImplementedError, RuntimeError):
                    pass

        for sinal in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sinal, parar.set)
            except (NotImplementedError, RuntimeError):
                pass
        return await rodar_coletor(calendario, QUOTE_BOARD, fabrica, credenciais, registro, settings=settings,
                                   staging=staging, destino=args.destino, estado_es=es / "agenda" / "estado.json",
                                   provas_es=es / "agenda" / "provas.jsonl", capturas_es=es / "capturas",
                                   max_bytes_ativo=int(args.max_gb_ativo * GIB),
                                   max_bytes_global=int(args.max_gb_global * GIB),
                                   reserva_disco_bytes=int(args.reserva_gb * GIB), parar=parar,
                                   quando_pronta=quando_pronta, saida=lambda t: print(t, flush=True))
    try:
        motivo = asyncio.run(executar())
    except KeyboardInterrupt:
        motivo = "abortado"
        registro.estado("PARADO", motivo="abortado")
    finally:
        del credenciais
        os.close(trava)
    print(f"coletor encerrado: {motivo}")
    return 3 if motivo in FALHAS_DE_LOGIN else 0


if __name__ == "__main__":
    sys.exit(main())
