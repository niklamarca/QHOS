"""Teste ao vivo curto do coletor multi-ativo (etapa 3), so FORA da janela do ES e com o agendador em AGUARDANDO.

    cd ~/qhos-coleta/qhos2 && PYTHONPATH=src:. ~/qhos-coleta-venv/bin/python -m coleta_multi.sonda \\
        --minutos 10 --substituto --online --agreements-accepted

Pede usuario e senha UMA vez (so na memoria). `--substituto` abre antes um segundo login, sem assinatura
nenhuma, no papel do agendador: R|Trader + substituto + coletor = os 3 logins da janela de amanha.
Login recusado ou logout forcado em qualquer um: para tudo, nao tenta de novo, sai com codigo 3.

Nunca adquire a guarda do ES (so le estado.json dela); usa guarda propria em ~/.local/state/qhos2/coleta_multi.
Grava em <data_root>/coleta_multi/sonda/<instante>/, fora do DataSet, e mede volume, relogio e atraso do laco.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from qhos2 import agenda, captura, catalogo, disco
from qhos2.config import ConfigError, Settings, validar_data_root
from qhos2.operacao import GuardaCaptura, OperacaoError
from qhos2.sessao import SessaoError

from coleta_multi.lista import QUOTE_BOARD, cobertura, conferir_lista
from coleta_multi.sessao_multi import SessaoMulti, falha_de_login

GIB = 1024 ** 3
MARGEM_ANTES_S = 20 * 60     # termina pelo menos 20 min antes da janela do ES
MARGEM_DEPOIS_S = 10 * 60    # e so comeca 10 min depois do fim dela


def guarda_propria() -> Path:
    return Path.home() / ".local" / "state" / "qhos2" / "coleta_multi"


def conferir_janela(calendario: dict, agora: datetime, duracao_s: float) -> Optional[str]:
    """Motivo de recusa se a sonda encostar numa janela do ES (com margens), senao None."""
    for inicio, fim in agenda.janelas(calendario):
        if agora < fim + timedelta(seconds=MARGEM_DEPOIS_S) and \
                agora + timedelta(seconds=duracao_s + MARGEM_ANTES_S) > inicio:
            return f"perto_da_janela_do_es_{agenda._dia_ny(inicio)}"
    return None


def conferir_es(estado_agenda: Path, estado_guarda_es: Path) -> Optional[str]:
    """Le (sem travar nada) o estado do agendador e da guarda do ES."""
    try:
        agendador = json.loads(estado_agenda.read_text(encoding="utf-8")).get("estado")
        guarda = json.loads(estado_guarda_es.read_text(encoding="utf-8")).get("estado")
    except (OSError, ValueError):
        return "estado_do_es_ilegivel"
    if agendador != "AGUARDANDO":
        return f"agendador_em_{agendador}"
    if guarda != "LIVRE":
        return f"guarda_do_es_em_{guarda}"
    return None


async def _medir_laco(parar: asyncio.Event, medida: dict, passo: float = 0.25) -> None:
    loop = asyncio.get_running_loop()
    atrasos = []
    while not parar.is_set():
        t0 = loop.time()
        await asyncio.sleep(passo)
        atrasos.append(max(0.0, loop.time() - t0 - passo))
        atrasos = atrasos[-20000:]
        ordenado = sorted(atrasos)
        medida.update(amostras=len(atrasos), max_ms=round(ordenado[-1] * 1000, 1),
                      p99_ms=round(ordenado[min(len(ordenado) - 1, int(0.99 * len(ordenado)))] * 1000, 1))


async def executar_sonda(itens, fabrica, credenciais, *, settings, raiz: Path, duracao_s: float,
                         substituto: bool, intervalo_substituto_s: float = 20.0,
                         max_bytes_ativo: int = 4 * GIB, max_bytes_global: int = 20 * GIB,
                         reserva_disco_bytes: int = 50 * GIB, quando_prontas=None,
                         saida: Callable[[str], None] = print) -> dict:
    dia = datetime.now(agenda.NY).date()
    relatorio = {"schema": "qhos2.coleta_multi.sonda.v1", "inicio_utc": datetime.now(timezone.utc).isoformat(),
                 "duracao_pedida_s": duracao_s, "substituto": substituto, "raiz": str(raiz)}
    sub, tarefa_sub = None, None
    if substituto:
        sub = SessaoMulti((), fabrica, settings=settings, raiz=raiz, dia=dia, max_bytes_ativo=1, max_bytes_global=1,
                          reserva_disco_bytes=0, gravar=False)
        tarefa_sub = asyncio.create_task(sub.executar(credenciais))
        espera = asyncio.create_task(sub.logado.wait())
        await asyncio.wait({tarefa_sub, espera}, timeout=30, return_when=asyncio.FIRST_COMPLETED)
        espera.cancel()
        if not sub.logado.is_set():
            sub.parar()
            await asyncio.gather(tarefa_sub, return_exceptions=True)
            relatorio["substituto_status"] = sub.status()
            relatorio["resultado"] = falha_de_login(sub) or "substituto_nao_logou"
            saida(f"substituto nao logou: {sub.status()['erro']}; o coletor NAO foi iniciado")
            return relatorio
        saida(f"substituto logado (papel do agendador); coletor em {intervalo_substituto_s:g} s")
        await asyncio.sleep(intervalo_substituto_s)

    principal = SessaoMulti(itens, fabrica, settings=settings, raiz=raiz, dia=dia, duracao_s=duracao_s,
                            max_bytes_ativo=max_bytes_ativo, max_bytes_global=max_bytes_global,
                            reserva_disco_bytes=reserva_disco_bytes)
    if quando_prontas is not None:
        quando_prontas([s for s in (principal, sub) if s is not None])
    parar_medida, laco = asyncio.Event(), {}
    medidor = asyncio.create_task(_medir_laco(parar_medida, laco))
    tarefa = asyncio.create_task(principal.executar(credenciais))
    vigias = {tarefa} | ({tarefa_sub} if tarefa_sub else set())
    inicio = time.monotonic()
    feito, _ = await asyncio.wait(vigias, return_when=asyncio.FIRST_COMPLETED)
    if tarefa_sub in feito and not tarefa.done():
        saida("o substituto caiu durante o coletor: parando o coletor (a recusa pode derrubar outra sessao)")
        relatorio["substituto_caiu_durante_o_coletor"] = True
        principal.parar()
    await asyncio.gather(tarefa, return_exceptions=True)
    duracao_real = time.monotonic() - inicio
    if sub is not None:
        sub.parar()
        await asyncio.gather(tarefa_sub, return_exceptions=True)
        relatorio["substituto_status"] = sub.status()
    parar_medida.set()
    await asyncio.gather(medidor, return_exceptions=True)

    fechados = principal.fechar_gravadores()
    relatorio.update(status=principal.status_multi(), laco=laco, duracao_real_s=round(duracao_real, 1),
                     resultado=falha_de_login(principal) or (falha_de_login(sub) if sub else None)
                     or ("ok" if principal.status()["estado"] == "FECHADA" else "interrompido"))
    verificacoes = {}
    for ativo, manifesto, erro in fechados:
        verif = catalogo.verificar_captura(ativo.gravador.pasta) if manifesto else {"verificada": False, "erro": erro}
        verificacoes[ativo.captura_id] = {k: verif.get(k) for k in ("verificada", "divergencias", "quadros", "bytes",
                                                                   "fechamento_normal", "quadros_por_segundo", "erro")}
    relatorio["verificacoes"] = verificacoes
    relatorio["cobertura"] = cobertura(relatorio["status"]["ativos"])
    return relatorio


def imprimir(relatorio: dict, saida: Callable[[str], None] = print) -> None:
    st = relatorio.get("status")
    saida(f"\nresultado: {relatorio.get('resultado')}  duracao: {relatorio.get('duracao_real_s')} s  "
          f"laco: {relatorio.get('laco')}")
    if relatorio.get("substituto_status"):
        s = relatorio["substituto_status"]
        saida(f"substituto: estado={s['estado']} login={s['login_confirmado']} logout={s['logout_confirmado']} "
              f"erro={s['erro']}")
    if not st:
        return
    saida(f"coletor: estado={st['estado']} erro={st['erro']} tipo={st['tipo_de_falha']} login={st['login_confirmado']} "
          f"logout={st['logout_confirmado']} parada={st['parada']} sem_dono={st['sem_dono']}")
    saida(f"relogio: {st['relogio']}  serie: {st['relogio_serie']}")
    cob = relatorio.get("cobertura") or {}
    saida(f"cobertura do print: {cob.get('itens_distintos')} itens ({cob.get('linhas_do_print')} linhas, ES por copia); "
          f"com eventos: {len(cob.get('com_eventos') or [])} {cob.get('com_eventos')}; "
          f"faltando: {cob.get('faltando')}; assinados sem eventos: {cob.get('gravando_sem_eventos')}")
    horas = max(relatorio.get("duracao_real_s") or 1, 1) / 3600
    saida(f"{'item':6} {'bolsa':6} {'contrato':9} {'estado':14} {'mbo':6} {'neg':6} {'retr':6} "
          f"{'quadros':>9} {'MiB':>8} {'MiB/h':>8} verif  motivo/tentativas")
    for a in st["ativos"]:
        v = relatorio.get("verificacoes", {}).get(a["captura_id"] or "", {})
        mib = a["bytes_brutos"] / 1024 ** 2
        saida(f"{a['exibido']:6} {str(a['bolsa']):6} {str(a['contrato']):9} {a['estado']:14} {str(a['mbo'])[:6]:6} "
              f"{str(a['negocios'])[:6]:6} {str(a['retrato'])[:6]:6} {a['quadros_total']:>9} {mib:>8.1f} "
              f"{mib / horas:>8.1f} {str(v.get('verificada', '-')):5}  {a['motivo'] or ''} {a['tentativas'] or ''}")
        if a["referencia"]:
            r = a["referencia"]
            saida(f"       ref: {r.get('symbol_name')} venc={r.get('expiration_date')} tradable={r.get('is_tradable')} "
                  f"produto={r.get('product_code')} front={a['front_month']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="coleta_multi.sonda", description=__doc__.splitlines()[0])
    ap.add_argument("--minutos", type=float, default=10.0)
    ap.add_argument("--substituto", action="store_true", help="segundo login sem assinatura, no papel do agendador")
    ap.add_argument("--calendario", type=Path,
                    default=Path.home() / "qhos-v2-data" / "agenda" / "calendario-20261028-20261125.json")
    ap.add_argument("--online", action="store_true")
    ap.add_argument("--agreements-accepted", action="store_true")
    args = ap.parse_args(argv)
    if not 1 <= args.minutos <= 30:
        ap.error("minutos entre 1 e 30")
    try:
        settings = Settings.from_environ()
        raiz = validar_data_root(settings.data_root / "coleta_multi" / "sonda" /
                                 datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        calendario = json.loads(args.calendario.read_text(encoding="utf-8"))
    except (ConfigError, OSError, ValueError) as erro:
        ap.error(f"configuracao invalida: {type(erro).__name__}")
    try:
        conferir_lista(QUOTE_BOARD)
    except ValueError as erro:
        print(f"sonda recusada antes das credenciais: {erro}")
        return 2
    duracao_s = args.minutos * 60
    total_s = duracao_s + (60 if args.substituto else 0) + 120
    recusa = (conferir_janela(calendario, datetime.now(timezone.utc), total_s)
              or conferir_es(settings.data_root / "agenda" / "estado.json",
                             Path.home() / ".local" / "state" / "qhos2" / "operacao" / "estado.json"))
    livre = disco.livre_bytes(settings.data_root)
    print(f"plano: {len(QUOTE_BOARD)} itens num login{' + substituto' if args.substituto else ''}, "
          f"{args.minutos:g} min, em {raiz}; livre {livre / GIB:.0f} GiB")
    if recusa:
        print(f"sonda recusada antes das credenciais: {recusa}")
        return 2
    if livre < 60 * GIB:
        print("sonda recusada antes das credenciais: espaco_insuficiente")
        return 2
    try:
        with GuardaCaptura(guarda_propria()) as guarda:
            guarda.conferir()
    except (OperacaoError, OSError) as erro:
        print("sonda recusada antes das credenciais: " + (str(erro) if isinstance(erro, OperacaoError) else type(erro).__name__))
        return 2
    if not (args.online and args.agreements_accepted):
        print("nada foi conectado. Para rodar de verdade acrescente --online --agreements-accepted")
        return 0
    credenciais = captura.obter_credenciais()
    if not credenciais.user or not credenciais.password:
        print("usuario e senha sao obrigatorios; nada foi conectado")
        return 2
    # De novo depois da senha: o prompt pode ter ficado aberto ate perto da janela do ES.
    recusa = (conferir_janela(calendario, datetime.now(timezone.utc), total_s)
              or conferir_es(settings.data_root / "agenda" / "estado.json",
                             Path.home() / ".local" / "state" / "qhos2" / "operacao" / "estado.json"))
    if recusa:
        del credenciais
        print(f"sonda recusada depois da senha (nada foi conectado): {recusa}")
        return 2
    fabrica = captura._criar_fabrica(settings.endpoint)

    async def rodar() -> dict:
        loop = asyncio.get_running_loop()

        def prontas(sessoes) -> None:
            def pedir() -> None:
                print("\nParando com seguranca (cancelando assinaturas e saindo)...", flush=True)
                for s in sessoes:
                    s.parar()
            for sinal in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sinal, pedir)
                except (NotImplementedError, RuntimeError):
                    pass

        with GuardaCaptura(guarda_propria()) as guarda:
            guarda.conferir()
            guarda.iniciar()
            relatorio = None
            try:
                relatorio = await executar_sonda(QUOTE_BOARD, fabrica, credenciais, settings=settings, raiz=raiz,
                                                 duracao_s=duracao_s, substituto=args.substituto,
                                                 quando_prontas=prontas, saida=lambda t: print(t, flush=True))
            finally:
                st = (relatorio or {}).get("status") or {}
                sub = (relatorio or {}).get("substituto_status") or {}
                logout = bool(st.get("logout_confirmado")) and (not args.substituto or bool(sub.get("logout_confirmado")))
                guarda.finalizar(logout_confirmado=logout, fechamento_normal=st.get("estado") == "FECHADA")
            return relatorio

    try:
        relatorio = asyncio.run(rodar())
    except (OperacaoError, SessaoError) as erro:
        print(f"sonda nao concluida: {erro}")
        return 2
    finally:
        del credenciais
    raiz.mkdir(parents=True, exist_ok=True)
    (raiz / "sonda.json").write_text(json.dumps(relatorio, indent=2, sort_keys=True, default=str) + "\n",
                                     encoding="utf-8")
    imprimir(relatorio)
    print(f"\nrelatorio: {raiz / 'sonda.json'}")
    if relatorio.get("resultado") in {"login_recusado", "logout_forcado"}:
        print("LOGIN RECUSADO OU LOGOUT FORCADO: nada foi tentado de novo. Decisao do autor.")
        return 3
    return 0 if relatorio.get("resultado") == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
