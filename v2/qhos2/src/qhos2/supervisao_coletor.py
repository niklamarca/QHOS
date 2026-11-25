"""Supervisiona somente seu processo de terminal; nunca conecta/captura ou envia ordens.

Seleciona uma captura recente sem manifesto; sem ela, o manifesto mais recente
por criado_em (empate recusa). Manifesto presente nao prova qualidade: o Leitor
do filho valida todos os bytes e mostra a falha. Mais de uma captura em curso,
metadados inseguros ou ambiguos deixam terminal ocioso e alerta no journal.

Cada troca encerra o proprio filho antes de iniciar outro: livro e WebSockets
nao atravessam geracoes. Nenhum PID externo, shell, login ou servico e controlado.
Leitura limitada a metadados/stat; este supervisor nao abre dados de mercado.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time
from typing import Optional

from qhos2.config import Settings, validar_data_root
from qhos2.leitura import RECENTE_S

MAX_ENTRADAS = 4096
MAX_MANIFESTO = 64 * 1024
MAX_METADADOS = 4 * 1024 * 1024
SCHEMA_MANIFESTO = "qhos_rithmic_depth_capture_v1"
ESQUEMA = "qhos2.supervisao_coletor.v1"


@dataclass(frozen=True)
class Selecao:
    pasta: Optional[Path]
    codigo: str
    identidade: tuple = ()

    @property
    def chave(self):
        return (str(self.pasta) if self.pasta else None, self.codigo, self.identidade)


def _inativo(codigo):
    return Selecao(None, codigo)


def _manifesto(caminho):
    fd = os.open(caminho, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as arquivo:
        info = os.fstat(arquivo.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_MANIFESTO:
            raise ValueError("manifesto_invalido")
        bruto = arquivo.read(MAX_MANIFESTO + 1)
    if len(bruto) > MAX_MANIFESTO:
        raise ValueError("manifesto_invalido")
    valor = json.loads(bruto)
    if not isinstance(valor, dict) or valor.get("schema") != SCHEMA_MANIFESTO:
        raise ValueError("manifesto_invalido")
    status = valor.get("status")
    if not isinstance(status, dict):
        raise ValueError("manifesto_invalido")
    if not all(isinstance(status.get(k), str) and status[k] for k in ("requested_symbol", "exchange")):
        raise ValueError("identidade_invalida")
    momento = datetime.fromisoformat(status["criado_em"].replace("Z", "+00:00"))
    if momento.utcoffset() is None:
        raise ValueError("manifesto_sem_fuso")
    return status, momento.timestamp(), hashlib.sha256(bruto).hexdigest(), len(bruto)


def selecionar(raiz, *, agora=None) -> Selecao:
    """Inspecao conservadora e limitada. Nao infere qualidade a partir do manifesto."""
    raiz = validar_data_root(raiz)
    agora = time.time() if agora is None else agora
    if type(agora) not in (int, float) or not math.isfinite(agora):
        return _inativo("relogio_local_invalido")
    pasta = raiz / "capturas"
    if not pasta.exists():
        return _inativo("sem_captura")
    if pasta.is_symlink() or not pasta.is_dir():
        return _inativo("pasta_de_capturas_insegura")
    ativas, encerradas, lidos, incompletas = [], [], 0, []
    try:
        with os.scandir(pasta) as entradas:
            for n, entrada in enumerate(entradas, 1):
                if n > MAX_ENTRADAS:
                    return _inativo("limite_de_capturas_excedido")
                if entrada.is_symlink():
                    return _inativo("captura_com_link")
                if not entrada.is_dir(follow_symlinks=False):
                    continue
                captura = Path(entrada.path)
                bruto = captura / "market_frames.jsonl"
                try:
                    info = bruto.lstat()
                except FileNotFoundError:
                    incompletas.append(None)  # sem raw, nao ha ultima atividade confiavel
                    continue
                if not stat.S_ISREG(info.st_mode):
                    return _inativo("arquivo_de_captura_nao_regular")
                identidade = (info.st_dev, info.st_ino)
                manifesto = captura / "manifest.json"
                try:
                    manifesto.lstat()
                except FileNotFoundError:
                    idade = agora - info.st_mtime
                    if idade < -5:
                        return _inativo("arquivo_com_horario_futuro")
                    if idade < RECENTE_S:
                        ativas.append(Selecao(captura, "captura_em_curso", identidade))
                    else:
                        incompletas.append(info.st_mtime)
                    continue
                status, momento, digest, tamanho = _manifesto(manifesto)
                lidos += tamanho
                if lidos > MAX_METADADOS:
                    return _inativo("limite_de_metadados_excedido")
                if momento > agora + 5:
                    return _inativo("manifesto_com_horario_futuro")
                if (status["requested_symbol"], status["exchange"]) == ("ESZ6", "CME"):
                    encerradas.append((momento, Selecao(captura, "captura_encerrada", (*identidade, digest))))
    except (OSError, ValueError, KeyError, TypeError, OverflowError, AttributeError):
        return _inativo("metadados_de_captura_invalidos")
    if len(ativas) > 1:
        return _inativo("mais_de_uma_captura_em_curso")
    if ativas:
        from qhos2.operacao import ler_identidade_captura, OperacaoError
        try:
            identidade = ler_identidade_captura(ativas[0].pasta)
        except OperacaoError:
            return _inativo("identidade_da_captura_ativa_invalida")
        if (identidade["requested_symbol"], identidade["exchange"]) != ("ESZ6", "CME"):
            return _inativo("captura_ativa_de_outro_instrumento")
        return ativas[0]
    if encerradas:
        ultima = max(m for m, _ in encerradas)
        if any(m is None or m >= ultima for m in incompletas):
            return _inativo("captura_incompleta_nao_resolvida")
        candidatas = [s for m, s in encerradas if m == ultima]
        if len(candidatas) != 1:
            return _inativo("encerradas_com_horario_ambiguo")
        return candidatas[0]
    return _inativo("captura_incompleta_nao_resolvida" if incompletas else "sem_captura")


def comando_coletor(raiz, selecao: Selecao) -> list:
    raiz = validar_data_root(raiz)
    comando = [sys.executable, "-m", "qhos2.coletor", "--raiz", str(raiz)]
    if selecao.pasta is not None:
        if selecao.pasta.parent != raiz / "capturas" or selecao.pasta.is_symlink():
            raise ValueError("selecao_fora_da_raiz")
        comando.extend(["--captura", str(selecao.pasta)])
    return comando


class Supervisor:
    """No maximo N partidas por janela, com backoff; processos filhos sao exclusivos."""

    def __init__(self, raiz, *, intervalo=5.0, max_partidas=6, janela_s=60.0,
                 espera_saida_s=5.0, popen=subprocess.Popen, relogio=time.monotonic, avisar=print):
        self.raiz = validar_data_root(raiz)
        if (not all(type(x) in (int, float) and math.isfinite(x) and x > 0
                    for x in (intervalo, janela_s, espera_saida_s)) or not 0.05 <= intervalo <= 60
                or janela_s > 3600 or espera_saida_s > 30
                or type(max_partidas) is not int or not 1 <= max_partidas <= 100):
            raise ValueError("limites_de_supervisao_invalidos")
        self.intervalo, self.max_partidas, self.janela_s = intervalo, max_partidas, janela_s
        self.espera_saida_s, self._popen, self._relogio, self._avisar = espera_saida_s, popen, relogio, avisar
        self._filho = self._chave = None
        self._partidas = []
        self._falhas = 0
        self._proxima = 0.0
        self._ultimo_aviso = None

    def _aviso(self, codigo):
        if codigo != self._ultimo_aviso:
            self._avisar(json.dumps({"schema": ESQUEMA, "estado": codigo}, separators=(",", ":")))
            self._ultimo_aviso = codigo

    def encerrar(self):
        filho, self._filho = self._filho, None
        if filho is not None and filho.poll() is None:
            filho.terminate()
            try:
                filho.wait(timeout=self.espera_saida_s)
            except subprocess.TimeoutExpired:
                filho.kill()
                filho.wait(timeout=self.espera_saida_s)

    def passo(self, selecao: Selecao) -> None:
        agora = self._relogio()
        chave = selecao.chave
        if self._filho is not None and self._filho.poll() is not None:
            self._filho = None
            self._falhas = min(self._falhas + 1, 6)
            self._proxima = agora + min(2 ** self._falhas, 60)
            self._aviso("coletor_encerrou_backoff")
        if chave != self._chave:
            self.encerrar()
            self._chave = chave
            self._aviso(selecao.codigo)
        if self._filho is not None or agora < self._proxima:
            return
        self._partidas = [t for t in self._partidas if agora - t < self.janela_s]
        if len(self._partidas) >= self.max_partidas:
            self._proxima = self._partidas[0] + self.janela_s
            self._aviso("limite_de_partidas_aguardando")
            return
        self._partidas.append(agora)
        try:
            # argv fixo, sem shell/env extra. O filho so serve loopback; nao faz login.
            self._filho = self._popen(comando_coletor(self.raiz, selecao), stdin=subprocess.DEVNULL)
        except OSError:
            self._falhas = min(self._falhas + 1, 6)
            self._proxima = agora + min(2 ** self._falhas, 60)
            self._aviso("coletor_nao_iniciou_backoff")

    def executar(self, parar=lambda: False, dormir=time.sleep):
        try:
            while not parar():
                self.passo(selecionar(self.raiz))
                dormir(self.intervalo)
        finally:
            self.encerrar()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raiz", type=Path)
    ap.add_argument("--intervalo", type=float, default=5.0)
    args = ap.parse_args(argv)
    if sys.platform != "linux":
        print("supervisao_requer_linux")
        return 2
    parado = False
    def parar(*_):
        nonlocal parado
        parado = True
    anteriores = {s: signal.signal(s, parar) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        supervisor = Supervisor(args.raiz or Settings.from_environ().data_root, intervalo=args.intervalo)
        supervisor.executar(lambda: parado)
    except (ValueError, OSError):
        print("supervisao_recusada")
        return 2
    finally:
        for s, handler in anteriores.items():
            signal.signal(s, handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
