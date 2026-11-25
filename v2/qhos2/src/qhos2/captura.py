"""Captura real de uma sessao Paper: login digitado, sessao, gravacao e verificacao na hora.

    cd qhos2 && PYTHONPATH=src python -m qhos2.captura --symbol ESZ6 --exchange CME --duration 60 \\
        --online --agreements-accepted

Sem `--online` e `--agreements-accepted` nada conecta: so mostra o plano. Usuario e digitado no prompt e a
senha NAO aparece na tela; nenhum dos dois e gravado, impresso ou colocado em erro. Ctrl+C pede uma saida
segura (cancela a assinatura e faz logout); um segundo Ctrl+C aborta. Ao fim a captura e verificada pelo
catalogo e medida pelo modulo de atraso.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import signal
import sys
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from qhos2 import atraso, catalogo, conector, disco
from qhos2.config import ConfigError, Settings, validar_data_root
from qhos2.gravador import MAX_BYTES_PADRAO, MIN_LIVRE_PADRAO, Gravador, GravadorError
from qhos2.operacao import GuardaCaptura, OperacaoError, estado_padrao, janela_permitida, prazo_calendario
from qhos2.sessao import ConfigSessao, Credenciais, Sessao, SessaoError

_criar_fabrica = conector.fabrica_wss  # trocavel nos testes
MAX_GB_PADRAO = 12.0  # um pregao completo do ES passa de 1 GiB
FOLGA_DISCO = 2       # espaco livre desejado = FOLGA_DISCO x o teto


def obter_credenciais(entrada: Callable[[str], str] = input,
                      senha: Callable[[str], str] = getpass.getpass) -> Credenciais:
    usuario = entrada("Usuario Rithmic Paper: ").strip()
    segredo = senha("Senha (nao aparece na tela): ")
    return Credenciais(usuario, segredo)


async def capturar(config: ConfigSessao, fabrica, raiz, credenciais: Credenciais, *,
                   quando_pronta: Optional[Callable[[Sessao], None]] = None, max_bytes: int = MAX_BYTES_PADRAO,
                   min_livre_bytes: int = MIN_LIVRE_PADRAO, captura_id: Optional[str] = None,
                   calendario: Optional[dict] = None) -> dict:
    """Roda a sessao gravando tudo. O manifesto e escrito sempre, mesmo se a sessao falhar ou for cancelada."""
    raiz = validar_data_root(raiz)
    prazo = prazo_calendario(calendario, datetime.now(timezone.utc), config.duracao_s) if calendario is not None else None
    if calendario is not None and not prazo:
        raise OperacaoError("fora_da_janela_operacional")
    with GuardaCaptura(estado_padrao()) as guarda:
        guarda.conferir()
        return await _capturar_protegida(config, fabrica, raiz, credenciais, guarda=guarda,
                                        quando_pronta=quando_pronta, max_bytes=max_bytes,
                                        min_livre_bytes=min_livre_bytes, captura_id=captura_id, prazo= prazo)


async def _capturar_protegida(config, fabrica, raiz, credenciais, *, guarda, quando_pronta,
                             max_bytes, min_livre_bytes, captura_id, prazo):
    gravador = Gravador(raiz, identidade={"system_name": config.settings.system_name,
                                          "requested_symbol": config.symbol, "exchange": config.exchange},
                        max_bytes=max_bytes, min_livre_bytes=min_livre_bytes, captura_id=captura_id)
    sessao = Sessao(config, fabrica, ao_receber=gravador)
    erro = None
    geracao = None
    try:
        if quando_pronta is not None:
            quando_pronta(sessao)
        geracao = guarda.iniciar()
        if prazo is None:
            await sessao.executar(credenciais)
        else:
            # O prazo cobre descoberta, login, retrato, streaming e logout.
            restante = prazo - datetime.now(timezone.utc).timestamp()
            if restante <= 0:
                raise asyncio.TimeoutError
            await asyncio.wait_for(sessao.executar(credenciais), timeout=restante)
            if datetime.now(timezone.utc).timestamp() > prazo:
                raise asyncio.TimeoutError
    except asyncio.TimeoutError:
        erro = SessaoError("janela_operacional_expirada")
        sessao._falhar(erro.codigo, erro.tipo)
    except SessaoError as falha:
        erro = falha
    finally:
        estado = sessao.status()
        estado["geracao_conexao"] = geracao
        normal = estado["estado"] == "FECHADA" and estado["logout_confirmado"]
        try:
            manifesto = gravador.fechar(estado, fechamento_normal=normal)
        finally:
            if geracao is not None:
                guarda.finalizar(logout_confirmado=estado["logout_confirmado"], fechamento_normal=normal)
    return {"pasta": gravador.pasta, "manifesto": manifesto, "status": estado,
            "erro": erro.codigo if erro else None, "tipo_de_falha": erro.tipo if erro else None}


def _parada_segura(sessao: Sessao) -> None:
    loop = asyncio.get_running_loop()

    def parar() -> None:
        print("\nParando com seguranca (cancelando a assinatura e saindo)... Ctrl+C de novo aborta.", flush=True)
        sessao.parar()
        loop.remove_signal_handler(signal.SIGINT)
        loop.remove_signal_handler(signal.SIGTERM)

    try:
        loop.add_signal_handler(signal.SIGINT, parar)
        loop.add_signal_handler(signal.SIGTERM, parar)
    except (NotImplementedError, RuntimeError):
        pass


def _imprimir_sessao(resultado: dict) -> None:
    s = resultado["status"]
    print(f"\nsessao: estado={s['estado']} erro={resultado['erro']} tipo={resultado['tipo_de_falha']} "
          f"instrumento={s['instrumento']}")
    print(f"  login={s['login_confirmado']} assinatura={s['assinatura_confirmada']} retrato_completo={s['retrato_completo']} "
          f"cancelamento={s['cancelamento_confirmado']} logout={s['logout_confirmado']} "
          f"negocios={s['negocios_confirmados']} negocios_cancelados={s['negocios_cancelados']}")
    print(f"  quadros={s['quadros']} bytes={s['bytes']} heartbeats={s['heartbeats_confirmados']} "
          f"periodo_heartbeat_s={s['periodo_heartbeat_s']}")
    if s["referencia"]:
        ref = s["referencia"]
        print(f"  referencia: vencimento={ref.get('expiration_date')} tick={ref.get('min_qprice_change')} "
              f"valor_do_ponto={ref.get('single_point_value')} negociavel={ref.get('is_tradable')}")
    if s["relogio"]:
        print(f"  relogio: desvio={s['relogio']['desvio_ms']} ms rtt={s['relogio']['rtt_ms']} ms "
              f"amostras={s['relogio']['amostras']} alerta={s['relogio_alerta']}")
    else:
        print("  relogio: sem amostra (heartbeat sem carimbo)")
    if s["anomalias"]:
        print(f"  anomalias: {s['anomalias']}")


def avaliar_espaco(raiz: Path, max_bytes: int, min_livre_bytes: int = MIN_LIVRE_PADRAO) -> tuple:
    """('ok' | 'aviso' | 'recusa', livre_bytes) no disco onde a captura sera gravada (livre real, ver qhos2.disco)."""
    livre = disco.livre_bytes(raiz)
    if livre < max_bytes + min_livre_bytes:
        return "recusa", livre
    if livre < FOLGA_DISCO * max_bytes:
        return "aviso", livre
    return "ok", livre


def _texto_espaco(situacao: str, livre: int, max_bytes: int) -> str:
    gib = 1024 ** 3
    if situacao == "recusa":
        return (f"ESPACO INSUFICIENTE: livre {livre / gib:.1f} GiB nao comporta o teto de {max_bytes / gib:g} GiB "
                f"mais a reserva minima; libere espaco ou reduza --max-gb")
    return (f"AVISO: livre {livre / gib:.1f} GiB e menos que {FOLGA_DISCO}x o teto ({FOLGA_DISCO * max_bytes / gib:g} GiB); "
            f"a captura pode estourar o disco no meio do pregao; libere espaco ou reduza --max-gb")


def main(argv=None, *, entrada: Callable[[str], str] = input, senha: Callable[[str], str] = getpass.getpass) -> int:
    ap = argparse.ArgumentParser(prog="qhos2.captura", description=__doc__.splitlines()[0])
    ap.add_argument("--symbol", required=True, help="contrato exato, por exemplo ESZ6")
    ap.add_argument("--exchange", required=True, help="bolsa, por exemplo CME ou COMEX")
    ap.add_argument("--duration", type=float, default=60.0, help="segundos de transmissao (padrao 60)")
    ap.add_argument("--template-version", default="5.42")
    ap.add_argument("--app-version", default="rithmic-session-1",
                    help="valor que o V1 usa no login e a Rithmic ja aceitou; troque so se ela pedir")
    ap.add_argument("--sem-negocios", action="store_true", help="nao assina o ultimo negocio (so o livro MBO)")
    ap.add_argument("--max-gb", type=float, default=MAX_GB_PADRAO,
                    help="teto da captura em GiB (padrao 12; exige livre >= 2x o teto, senao avisa; recusa abaixo de 1x)")
    ap.add_argument("--raiz", help="pasta das capturas (padrao: <data_root>/capturas)")
    ap.add_argument("--calendario", type=Path, help="janelas UTC explicitas e datadas; a captura deve caber inteira")
    ap.add_argument("--online", action="store_true", help="autoriza conectar na Rithmic")
    ap.add_argument("--agreements-accepted", action="store_true", help="confirma que os acordos da Rithmic foram aceitos")
    args = ap.parse_args(argv)
    if not 0 < args.max_gb <= 100:  # tambem recusa nan e inf
        ap.error("configuracao invalida: max_gb_invalido")
    try:
        settings = Settings.from_environ()
        config = ConfigSessao(template_version=args.template_version, symbol=args.symbol, exchange=args.exchange,
                              duracao_s=args.duration, app_version=args.app_version, negocios=not args.sem_negocios,
                              settings=settings)
    except (ConfigError, ValueError) as erro:
        ap.error(f"configuracao invalida: {erro}")
    try:
        raiz = validar_data_root(args.raiz) if args.raiz else settings.data_root / "capturas"
        calendario = json.loads(args.calendario.read_text()) if args.calendario else None
        if calendario is not None and not janela_permitida(calendario, datetime.now(timezone.utc), args.duration):
            raise OperacaoError("fora_da_janela_operacional")
    except (ConfigError, OperacaoError, OSError, ValueError):
        ap.error("destino_ou_calendario_invalido")

    print(f"plano: {settings.system_name} em {settings.endpoint} como {settings.app_name}; "
          f"{args.exchange}/{args.symbol} por {args.duration:g} s, "
          f"{'so livro' if args.sem_negocios else 'livro e negocios'}; capturas em {raiz} (teto {args.max_gb:g} GiB)")
    max_bytes = int(args.max_gb * 1024 ** 3)
    situacao, livre = avaliar_espaco(raiz, max_bytes)
    if situacao != "ok":
        print(_texto_espaco(situacao, livre, max_bytes))
    if not (args.online and args.agreements_accepted):
        print("nada foi conectado. Para rodar de verdade acrescente --online --agreements-accepted")
        return 0
    if situacao == "recusa":
        print("captura recusada antes das credenciais: espaco_insuficiente")
        return 2

    try:
        with GuardaCaptura(estado_padrao()) as guarda:
            guarda.conferir()
    except (OperacaoError, OSError) as erro:
        print("captura recusada antes das credenciais: " + (str(erro) if isinstance(erro, OperacaoError) else type(erro).__name__))
        return 2

    credenciais = obter_credenciais(entrada, senha)
    if not credenciais.user or not credenciais.password:
        print("usuario e senha sao obrigatorios; nada foi conectado")
        return 2
    fabrica = _criar_fabrica(settings.endpoint)
    try:
        resultado = asyncio.run(capturar(config, fabrica, raiz, credenciais, quando_pronta=_parada_segura,
                                         max_bytes=max_bytes, calendario=calendario))
    except OperacaoError as erro:
        print(f"captura nao concluida: {erro}; confira a guarda operacional")
        return 2
    except (GravadorError, FileExistsError, PermissionError) as erro:
        print(f"captura nao concluida: {type(erro).__name__}; confira a guarda e os arquivos de captura")
        return 2
    except KeyboardInterrupt:
        print("\nabortado; a captura parcial foi fechada e pode ser verificada pelo catalogo")
        return 2
    _imprimir_sessao(resultado)

    relatorio = catalogo.verificar_captura(resultado["pasta"])
    print(f"\ncaptura: {resultado['pasta']}")
    print(f"verificacao: verificada={relatorio['verificada']} divergencias={relatorio['divergencias']} "
          f"quadros={relatorio.get('quadros')} ambiente={relatorio.get('ambiente')}")
    try:
        atraso._imprimir(atraso.medir(resultado["pasta"]))  # recusa sozinha captura nao verificada
    except atraso.AtrasoError as erro:
        print(f"atraso nao medido: {erro}")
    return 0 if resultado["erro"] is None and relatorio["verificada"] else 2


if __name__ == "__main__":
    sys.exit(main())
