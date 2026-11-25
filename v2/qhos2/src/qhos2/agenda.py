"""Agendador diario da captura (opcao A): senha digitada uma vez, so na memoria; uma captura por janela.

    python -m qhos2.agenda calendario --de 2026-10-28 --ate 2026-11-25 --fonte "..." --saida cal.json
    python -m qhos2.agenda plano --calendario cal.json
    python -m qhos2.agenda rodar --calendario cal.json --symbol ESZ6 --exchange CME --online --agreements-accepted

`rodar` pede usuario e senha UMA vez e os mantem so na memoria deste processo (nada e gravado).
Em cada janela do calendario abre antes da abertura de NY, grava e faz logout antes do fim da janela.
Login recusado ou logout forcado esquecem as credenciais e encerram o agendador, sem nova tentativa (R6).
Guarda em quarentena, falta de espaco ou partida atrasada pulam o dia, que fica registrado. Uma captura que
cai no meio do dia nao e repetida no mesmo dia. Conecta so ao Ticker Plant: nunca envia ordens.
Se o PC ou o WSL reiniciar, o processo morre com as credenciais e precisa ser iniciado de novo.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import getpass
import json
import os
import signal
import sys
from datetime import date, datetime, time as hora, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from qhos2 import captura, catalogo
from qhos2.config import ConfigError, Settings, validar_data_root
from qhos2.gravador import GravadorError
from qhos2.operacao import GuardaCaptura, OperacaoError, _atomico, estado_padrao
from qhos2.sessao import LOGOUT_FORCADO, ConfigSessao, Credenciais

NY = ZoneInfo("America/New_York")
INICIO_NY = hora(9, 0)     # 30 min antes da abertura (09:30)
FIM_NY = hora(16, 15)      # 15 min depois do fechamento (16:00)
FOLGA_S = 120.0            # logout antes do fim da janela: a captura termina sozinha, nao por prazo
ATRASO_MAX_S = 1500.0      # depois de 09:25 de NY o dia ja nao comeca antes da abertura
DURACAO_MINIMA_S = 600.0
PROVA_ESQUEMA = "qhos2.agenda.prova.v1"


class AgendaError(ValueError):
    """Codigo estatico; nunca inclui credenciais."""


def _utc(valor: str) -> datetime:
    dt = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    if dt.utcoffset() is None:
        raise AgendaError("instante_sem_fuso")
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def gerar_calendario(de: date, ate: date, fonte: str, *, excluir=(), inicio_ny: hora = INICIO_NY,
                     fim_ny: hora = FIM_NY) -> dict:
    """Uma janela por dia util (seg-sex) no horario de NY, convertida para UTC; nada alem disso e presumido.

    Feriados da CME NAO sao deduzidos: precisam vir em `excluir`, conferidos na fonte oficial.
    """
    if not fonte or not fonte.strip():
        raise AgendaError("fonte_obrigatoria")
    if ate < de:
        raise AgendaError("intervalo_invalido")
    excluir = set(excluir)
    janelas, dia = [], de
    while dia <= ate:
        if dia.weekday() < 5 and dia not in excluir:
            ini = datetime.combine(dia, inicio_ny, NY)
            fim = datetime.combine(dia, fim_ny, NY)
            janelas.append({"dia": dia.isoformat(), "inicio": _iso(ini), "fim": _iso(fim)})
        dia += timedelta(days=1)
    return {"schema": "qhos2_calendario_v1", "fonte": fonte.strip(),
            "valido_de": _iso(datetime.combine(de, hora(0), NY)),
            "valido_ate": _iso(datetime.combine(ate + timedelta(days=1), hora(0), NY)),
            "janela_ny": f"{inicio_ny:%H:%M}-{fim_ny:%H:%M} America/New_York",
            "dias_excluidos": sorted(d.isoformat() for d in excluir), "janelas": janelas}


def janelas(calendario: dict, *, um_por_dia: bool = True) -> list:
    """[(inicio, fim)] em UTC, ordenadas e dentro da validade. Recusa calendario incompleto ou ambiguo."""
    try:
        if calendario.get("schema") != "qhos2_calendario_v1" or not str(calendario.get("fonte") or "").strip():
            raise AgendaError("calendario_sem_esquema_ou_fonte")
        de, ate = _utc(calendario["valido_de"]), _utc(calendario["valido_ate"])
        saida = sorted((_utc(j["inicio"]), _utc(j["fim"])) for j in calendario["janelas"])
    except (KeyError, TypeError, AttributeError, ValueError) as erro:
        raise AgendaError(str(erro) if isinstance(erro, AgendaError) else "calendario_invalido") from None
    if not saida:
        raise AgendaError("calendario_sem_janelas")
    for (a, b), seguinte in zip(saida, saida[1:] + [None]):
        if not de <= a < b <= ate:
            raise AgendaError("janela_fora_da_validade")
        if seguinte is not None and seguinte[0] < b:
            raise AgendaError("janelas_sobrepostas")
    if um_por_dia and len({a.astimezone(NY).date() for a, _ in saida}) != len(saida):
        raise AgendaError("mais_de_uma_janela_no_dia")
    return saida


def _dia_ny(inicio: datetime) -> str:
    return inicio.astimezone(NY).date().isoformat()


class Registro:
    """estado.json (ultimo estado) e provas.jsonl (uma linha por dia), sem credenciais."""

    def __init__(self, pasta: Path):
        self.pasta = pasta
        pasta.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.provas = pasta / "provas.jsonl"

    def dias_registrados(self) -> set:
        if not self.provas.exists():
            return set()
        dias = set()
        for linha in self.provas.read_text(encoding="utf-8").splitlines():
            try:
                dias.add(json.loads(linha)["dia"])
            except (ValueError, KeyError, TypeError):
                continue
        return dias

    def estado(self, estado: str, **campos) -> None:
        _atomico(self.pasta / "estado.json", {"schema": "qhos2.agenda.estado.v1", "estado": estado,
                                               "atualizado_utc": _iso(datetime.now(timezone.utc)), **campos})

    def provar(self, dia: str, inicio: datetime, fim: datetime, resultado: str, **campos) -> dict:
        prova = {"schema": PROVA_ESQUEMA, "dia": dia, "janela_inicio": _iso(inicio), "janela_fim": _iso(fim),
                 "resultado": resultado, "registrado_utc": _iso(datetime.now(timezone.utc)), **campos}
        fd = os.open(self.provas, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as arquivo:
            arquivo.write(json.dumps(prova, sort_keys=True, allow_nan=False) + "\n")
            arquivo.flush()
            os.fsync(arquivo.fileno())
        return prova


async def _esperar(parar: Optional[asyncio.Event], segundos: float) -> None:
    if segundos <= 0:
        return
    if parar is None:
        await asyncio.sleep(segundos)
        return
    try:
        await asyncio.wait_for(parar.wait(), timeout=segundos)
    except asyncio.TimeoutError:
        pass


def _login_recusado(resultado: dict) -> bool:
    erro = resultado.get("erro") or ""
    return not resultado["status"].get("login_confirmado") and erro.startswith("resposta_recusada_11")


async def rodar_agenda(calendario: dict, config_base: ConfigSessao, fabrica, raiz, credenciais: Credenciais,
                       registro: Registro, *, max_bytes: int, agora: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                       parar: Optional[asyncio.Event] = None, quando_pronta=None, apos_captura=None,
                       folga_s: float = FOLGA_S, atraso_max_s: float = ATRASO_MAX_S,
                       duracao_minima_s: float = DURACAO_MINIMA_S, guarda_raiz: Optional[Path] = None,
                       chave_dia: Callable[[datetime], str] = _dia_ny, um_por_dia: bool = True,
                       saida: Callable[[str], None] = print) -> str:
    """Percorre as janelas do calendario. Devolve o motivo do fim; nunca relanca credenciais em erro."""
    lista = janelas(calendario, um_por_dia=um_por_dia)
    feitos = registro.dias_registrados()
    partida = agora()
    for inicio, fim in lista:
        dia = chave_dia(inicio)
        if dia in feitos or fim <= partida:
            continue  # ja registrado, ou terminou antes de o agendador existir
        while not (parar and parar.is_set()) and agora() < inicio:
            registro.estado("AGUARDANDO", proxima_janela=_iso(inicio), proximo_dia=dia)
            await _esperar(parar, min(60.0, (inicio - agora()).total_seconds()))
        if parar and parar.is_set():
            break
        if (agora() - inicio).total_seconds() > atraso_max_s:
            registro.provar(dia, inicio, fim, "pulado_partida_tardia")
            saida(f"{dia}: pulado (partida tardia)")
            continue
        situacao, _livre = captura.avaliar_espaco(Path(raiz), max_bytes)
        if situacao == "recusa":
            registro.provar(dia, inicio, fim, "pulado_sem_espaco")
            saida(f"{dia}: pulado (sem espaco em disco)")
            continue
        try:
            with GuardaCaptura(guarda_raiz if guarda_raiz is not None else estado_padrao()) as guarda:
                guarda.conferir()
        except OperacaoError as erro:
            registro.provar(dia, inicio, fim, "pulado_guarda", motivo=str(erro))
            saida(f"{dia}: pulado (guarda: {erro}); confira `python -m qhos2.operacao estado`")
            continue
        duracao = (fim - agora()).total_seconds() - folga_s
        if duracao < duracao_minima_s:
            registro.provar(dia, inicio, fim, "pulado_janela_curta")
            continue
        config = dataclasses.replace(config_base, duracao_s=duracao)
        registro.estado("CAPTURANDO", dia=dia, janela_fim=_iso(fim))
        saida(f"{dia}: capturando ate {_iso(fim)} ({duracao:.0f} s)")
        try:
            resultado = await captura.capturar(config, fabrica, raiz, credenciais, quando_pronta=quando_pronta,
                                               max_bytes=max_bytes, calendario=calendario)
        except OperacaoError as erro:
            registro.provar(dia, inicio, fim, "falhou_antes_do_login", motivo=str(erro))
            continue
        except (GravadorError, FileExistsError, PermissionError) as erro:
            registro.provar(dia, inicio, fim, "falhou_antes_do_login", motivo=type(erro).__name__)
            continue
        finally:
            if apos_captura is not None:
                apos_captura()
        relatorio = catalogo.verificar_captura(resultado["pasta"])
        completa = resultado["erro"] is None and relatorio["verificada"] and relatorio.get("fechamento_normal") is True
        registro.provar(dia, inicio, fim, "capturado" if completa else "interrompido",
                        captura=Path(resultado["pasta"]).name, erro=resultado["erro"],
                        tipo_de_falha=resultado["tipo_de_falha"], verificada=relatorio["verificada"],
                        fechamento_normal=relatorio.get("fechamento_normal"), quadros=relatorio.get("quadros"),
                        primeiro_ns=relatorio.get("primeiro_ns"), ultimo_ns=relatorio.get("ultimo_ns"))
        saida(f"{dia}: {'capturado' if completa else 'interrompido'} quadros={relatorio.get('quadros')} "
              f"verificada={relatorio['verificada']} erro={resultado['erro']}")
        if _login_recusado(resultado) or resultado["tipo_de_falha"] == LOGOUT_FORCADO:
            motivo = "login_recusado" if _login_recusado(resultado) else "logout_forcado"
            registro.estado("QUARENTENA", motivo=motivo, dia=dia)
            return motivo
    if parar and parar.is_set():
        registro.estado("PARADO", motivo="pedido_do_operador")
        return "parado_pelo_operador"
    registro.estado("CALENDARIO_ESGOTADO", valido_ate=calendario.get("valido_ate"))
    return "calendario_esgotado"


def _plano(calendario: dict, agora: datetime) -> list:
    return [(_dia_ny(a), a, b) for a, b in janelas(calendario) if b > agora]


def _travar(pasta: Path):
    import fcntl
    fd = os.open(pasta / "agenda.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise AgendaError("agendador_ja_em_execucao") from None
    return fd


def main(argv=None, *, entrada: Callable[[str], str] = input, senha: Callable[[str], str] = getpass.getpass) -> int:
    ap = argparse.ArgumentParser(prog="qhos2.agenda", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="acao", required=True)
    cal = sub.add_parser("calendario", help="gera um calendario explicito (so dias uteis; feriados via --excluir)")
    cal.add_argument("--de", type=date.fromisoformat, required=True)
    cal.add_argument("--ate", type=date.fromisoformat, required=True)
    cal.add_argument("--fonte", required=True, help="de onde vieram horario e feriados (ex.: pagina oficial da CME)")
    cal.add_argument("--excluir", type=date.fromisoformat, action="append", default=[], help="feriado (repita)")
    cal.add_argument("--saida", type=Path, required=True)
    pl = sub.add_parser("plano", help="mostra as proximas janelas; nao conecta")
    pl.add_argument("--calendario", type=Path, required=True)
    rd = sub.add_parser("rodar", help="pede a senha uma vez e captura cada janela")
    rd.add_argument("--calendario", type=Path, required=True)
    rd.add_argument("--symbol", required=True)
    rd.add_argument("--exchange", required=True)
    rd.add_argument("--max-gb", type=float, default=captura.MAX_GB_PADRAO)
    rd.add_argument("--template-version", default="5.42")
    rd.add_argument("--app-version", default="rithmic-session-1")
    rd.add_argument("--online", action="store_true")
    rd.add_argument("--agreements-accepted", action="store_true")
    args = ap.parse_args(argv)

    if args.acao == "calendario":
        try:
            valor = gerar_calendario(args.de, args.ate, args.fonte, excluir=args.excluir)
            janelas(valor)
        except AgendaError as erro:
            ap.error(str(erro))
        fd = os.open(args.saida, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as arquivo:
            json.dump(valor, arquivo, indent=2, sort_keys=True)
            arquivo.write("\n")
        print(f"calendario: {len(valor['janelas'])} janelas de {args.de} a {args.ate}; excluidos: {valor['dias_excluidos']}")
        return 0

    try:
        calendario = json.loads(args.calendario.read_text(encoding="utf-8"))
        proximas = _plano(calendario, datetime.now(timezone.utc))
    except (OSError, ValueError, AgendaError) as erro:
        print(f"calendario recusado: {erro if isinstance(erro, AgendaError) else type(erro).__name__}")
        return 2
    for dia, a, b in proximas[:10]:
        print(f"  {dia}: {a.astimezone(NY):%H:%M}-{b.astimezone(NY):%H:%M} NY  ({_iso(a)} a {_iso(b)})")
    print(f"janelas restantes: {len(proximas)}; calendario valido ate {calendario['valido_ate']}")
    if args.acao == "plano":
        return 0

    if not 0 < args.max_gb <= 100:
        ap.error("configuracao invalida: max_gb_invalido")
    try:
        settings = Settings.from_environ()
        config = ConfigSessao(template_version=args.template_version, symbol=args.symbol, exchange=args.exchange,
                              duracao_s=None, app_version=args.app_version, negocios=True, settings=settings)
        raiz = validar_data_root(settings.data_root / "capturas")
    except (ConfigError, ValueError) as erro:
        ap.error(f"configuracao invalida: {erro}")
    if not proximas:
        print("nenhuma janela futura: gere um calendario novo")
        return 2
    if not (args.online and args.agreements_accepted):
        print("nada foi conectado. Para rodar de verdade acrescente --online --agreements-accepted")
        return 0
    registro = Registro(settings.data_root / "agenda")
    try:
        trava = _travar(registro.pasta)
    except AgendaError as erro:
        print(f"agendador recusado: {erro}")
        return 2
    try:
        with GuardaCaptura(estado_padrao()) as guarda:
            guarda.conferir()
    except (OperacaoError, OSError) as erro:
        print("agendador recusado antes das credenciais: " + (str(erro) if isinstance(erro, OperacaoError) else type(erro).__name__))
        os.close(trava)
        return 2
    credenciais = captura.obter_credenciais(entrada, senha)
    if not credenciais.user or not credenciais.password:
        print("usuario e senha sao obrigatorios; nada foi conectado")
        os.close(trava)
        return 2
    print("credenciais so na memoria deste processo. Ctrl+C encerra com seguranca; nao feche sem isso durante uma captura.")
    fabrica = captura._criar_fabrica(settings.endpoint)

    async def executar() -> str:
        parar = asyncio.Event()
        loop = asyncio.get_running_loop()

        def instalar() -> None:
            for sinal in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sinal, parar.set)
                except (NotImplementedError, RuntimeError):
                    pass

        def quando_pronta(sessao) -> None:
            def pedir() -> None:
                print("\nParando com seguranca (cancelando a assinatura e saindo)... Ctrl+C de novo aborta.", flush=True)
                parar.set()
                sessao.parar()
                for sinal in (signal.SIGINT, signal.SIGTERM):
                    loop.remove_signal_handler(sinal)
            for sinal in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sinal, pedir)
                except (NotImplementedError, RuntimeError):
                    pass

        instalar()
        return await rodar_agenda(calendario, config, fabrica, raiz, credenciais, registro,
                                  max_bytes=int(args.max_gb * 1024 ** 3), parar=parar,
                                  quando_pronta=quando_pronta, apos_captura=instalar, folga_s=FOLGA_S,
                                  atraso_max_s=ATRASO_MAX_S, duracao_minima_s=DURACAO_MINIMA_S,
                                  saida=lambda texto: print(texto, flush=True))
    try:
        motivo = asyncio.run(executar())
    except KeyboardInterrupt:
        motivo = "abortado"
        registro.estado("PARADO", motivo="abortado")
    finally:
        del credenciais
        os.close(trava)
    print(f"agendador encerrado: {motivo}")
    return 3 if motivo in {"login_recusado", "logout_forcado"} else 0


if __name__ == "__main__":
    sys.exit(main())
