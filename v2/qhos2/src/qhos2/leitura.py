"""Leitura ao vivo de uma captura da V2 (integracao, etapa 3): segue o arquivo enquanto a captura grava.

So le. Nao importa a sessao, o conector, a captura nem o gravador, nao abre conexao e nao escreve nada: roda em
outro processo, com prioridade baixa, e pode cair ou travar sem afetar a gravacao (regras 16 e 21).

O livro e montado como no replay (`livro.livro_da_captura`): mesmo `eventos.normalizar`, mesmo `Livro`, quadro a
quadro, na ordem do arquivo. So linha completa (terminada em quebra de linha) e lida; o resto espera a proxima
volta. O tick, o simbolo e a bolsa vem de quem chama, porque o manifesto so e escrito no fim; quando ele aparece,
a leitura confere quadros, bytes, SHA-256, tick, simbolo e bolsa contra o que leu, e so entao fica ENCERRADA.

Estados: AGUARDANDO (arquivo ainda nao existe), LENDO, ENCERRADA (manifesto conferido) e FALHA (com codigo).
Fail-closed: indice com buraco, linha invalida ou truncada, quadro que nao vira evento, arquivo trocado ou que
encolheu e manifesto divergente param a leitura de vez. Nada continua a partir de um livro duvidoso.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import stat
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

from qhos2 import eventos as ev
from qhos2.catalogo import ARQUIVO_LEGADO, LINHA_MAX_BYTES, _ler_manifesto
from qhos2.livro import Livro

AGUARDANDO, LENDO, ENCERRADA, FALHA = "AGUARDANDO", "LENDO", "ENCERRADA", "FALHA"
ESQUEMA = "qhos2.leitura.v1"
BLOCO_BYTES = 4 * 1024 ** 2
CONFERIR_SIMBOLO_A_CADA = 1             # compatibilidade; a verificacao agora e incondicional
RECENTE_S = 60                          # captura em andamento: arquivo alterado ha menos que isso


class LeituraError(ValueError):
    """Uso invalido da leitura (a falha de uma captura vira estado FALHA, nao excecao)."""


Quadro = Tuple[int, int, Tuple[ev.Evento, ...]]   # (indice, recebido_ns, eventos)


class Leitor:
    def __init__(self, pasta, *, tick: float, simbolo: str, bolsa: str, relogio=time.time_ns):
        if not (isinstance(tick, float) and math.isfinite(tick) and tick > 0):
            raise LeituraError("tick_invalido")
        for valor in (simbolo, bolsa):
            if not isinstance(valor, str) or not valor:
                raise LeituraError("simbolo_ou_bolsa_invalido")
        self.pasta = Path(pasta)
        self.tick, self.simbolo, self.bolsa, self._relogio = tick, simbolo, bolsa, relogio
        self.livro = Livro()
        self.estado, self.codigo = AGUARDANDO, None
        self.quadros = self.bytes = 0
        self.ultimo_recebido_ns: Optional[int] = None
        self.ultima_novidade_ns: Optional[int] = None    # relogio local quando chegou o ultimo quadro novo
        self.tamanho = 0
        self._digest = hashlib.sha256()
        self._arquivo = None
        self._identidade = None
        self._resto = b""

    # ---- leitura ------------------------------------------------------------------------------------

    def avancar(self, max_bytes: int = BLOCO_BYTES) -> List[Quadro]:
        """Le o que foi gravado desde a ultima volta (ate `max_bytes`) e devolve os quadros aplicados ao livro."""
        if self.estado in (ENCERRADA, FALHA):
            return []
        caminho = self.pasta / ARQUIVO_LEGADO
        if self._arquivo is None:
            try:
                fd = os.open(caminho, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            except FileNotFoundError:
                return []
            except OSError:
                self._falhar("arquivo_nao_regular_ou_inacessivel")
                return []
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                os.close(fd)
                self._falhar("arquivo_nao_regular_ou_inacessivel")
                return []
            self._arquivo = os.fdopen(fd, "rb")
            self._identidade = (info.st_dev, info.st_ino)
            self.estado = LENDO
            if not (self.pasta / "manifest.json").is_file() and (self.pasta / "capture_identity.json").exists():
                from qhos2.operacao import ler_identidade_captura, OperacaoError
                try:
                    identidade = ler_identidade_captura(self.pasta)
                    if (identidade["requested_symbol"], identidade["exchange"]) != (self.simbolo, self.bolsa):
                        raise OperacaoError("simbolo_ou_bolsa_diverge")
                except OperacaoError as erro:
                    self._falhar(str(erro))
                    return []
        try:
            atual = os.stat(caminho)
        except FileNotFoundError:
            self._falhar("arquivo_sumiu")
            return []
        if (atual.st_dev, atual.st_ino) != self._identidade:
            self._falhar("arquivo_trocado")
            return []
        # o manifesto e olhado ANTES de ler: o gravador fecha o arquivo antes de escrever o manifesto, entao, se
        # ele ja existia, tudo o que foi gravado ja esta no arquivo
        terminou = (self.pasta / "manifest.json").is_file()
        self.tamanho = os.fstat(self._arquivo.fileno()).st_size
        if self.tamanho < self.bytes + len(self._resto):
            self._falhar("arquivo_encolheu")
            return []
        bloco = self._arquivo.read(max_bytes)
        saida: List[Quadro] = []
        linhas = (self._resto + bloco).split(b"\n")
        self._resto = linhas.pop()
        for linha in linhas:
            quadro = self._quadro(linha + b"\n")
            if quadro is None:              # falhou: os quadros anteriores ja valiam
                return saida
            saida.append(quadro)
        if len(self._resto) > LINHA_MAX_BYTES:
            self._falhar("linha_grande_demais")
            return saida
        if saida:
            self.ultima_novidade_ns = self._relogio()
        if terminou and len(bloco) < max_bytes:
            if self._resto:
                self._falhar("linha_truncada")
                return saida
            self._conferir_fim()
        return saida

    def _quadro(self, linha: bytes) -> Optional[Quadro]:
        if len(linha) > LINHA_MAX_BYTES:
            return self._falhar("linha_grande_demais")
        try:
            obj = json.loads(linha)
            indice, template, recebido = obj["index"], obj["template_id"], obj["received_ns"]
            if not all(isinstance(x, int) and not isinstance(x, bool) for x in (indice, template, recebido)):
                raise TypeError
            dados = base64.b64decode(obj["protobuf_base64"], validate=True)
        except (ValueError, KeyError, TypeError):
            return self._falhar("linha_invalida")
        if indice != self.quadros:
            return self._falhar("indice_com_buracos")
        try:
            eventos = ev.normalizar(indice, template, recebido, dados, self.tick,
                                    simbolo=self.simbolo, bolsa=self.bolsa)
        except ev.EventoError as erro:
            codigo = str(erro)
            return self._falhar("simbolo_ou_bolsa_diverge" if codigo.startswith("simbolo_ou_bolsa_diverge:") else codigo)
        self.livro.aplicar_quadro(eventos)
        self._digest.update(linha)
        self.quadros += 1
        self.bytes += len(linha)
        self.ultimo_recebido_ns = recebido
        return indice, recebido, eventos

    def _conferir_fim(self) -> None:
        divergencias: list = []
        manifesto = _ler_manifesto(self.pasta, divergencias)
        if manifesto is None:
            self._falhar(divergencias[0])
            return
        if manifesto.get("segments") is not None:
            self._falhar("segmentos_nao_suportados")
            return
        if (manifesto.get("frames"), manifesto.get("bytes")) != (self.quadros, self.bytes):
            self._falhar("manifesto_diverge_contagem")
            return
        if manifesto.get("sha256") != self._digest.hexdigest():
            self._falhar("manifesto_diverge_sha256")
            return
        try:
            tick = ev.tick_da_captura(manifesto)
        except ev.EventoError as erro:
            self._falhar(str(erro))
            return
        if tick != self.tick:
            self._falhar("tick_diverge")
            return
        status = manifesto.get("status") if isinstance(manifesto.get("status"), dict) else {}
        if (status.get("requested_symbol"), status.get("exchange")) != (self.simbolo, self.bolsa):
            self._falhar("simbolo_ou_bolsa_diverge")
            return
        try:
            ev.conferir_status_captura(manifesto, pasta=self.pasta)
        except ev.EventoError as erro:
            self._falhar(str(erro))
            return
        self.estado = ENCERRADA
        self._fechar()

    def _falhar(self, codigo: str) -> None:
        self.estado, self.codigo = FALHA, codigo
        self.livro.suspeito = True
        self._fechar()

    def _fechar(self) -> None:
        if self._arquivo is not None:
            self._arquivo.close()
            self._arquivo = None

    # ---- consulta -----------------------------------------------------------------------------------

    @property
    def livro_vale(self) -> bool:
        """O livro so serve para mostrar ou decidir se a leitura nao falhou e o livro esta qualificado."""
        return self.estado in (LENDO, ENCERRADA) and self.livro.qualificado

    def resumo(self) -> dict:
        """So contagens, tempos e o resumo do livro (distancias em ticks): nada de preco, quantidade ou id."""
        agora = self._relogio()
        idade = lambda ns: None if ns is None else round((agora - ns) / 1e9, 3)  # noqa: E731
        return {"esquema": ESQUEMA, "estado": self.estado, "codigo": self.codigo, "quadros": self.quadros,
                "bytes": self.bytes, "pendente_bytes": max(0, self.tamanho - self.bytes - len(self._resto)),
                "atraso_s": idade(self.ultimo_recebido_ns), "sem_novidade_s": idade(self.ultima_novidade_ns),
                "livro_vale": self.livro_vale, "livro": self.livro.resumo()}


def captura_em_andamento(raiz, *, agora: Optional[float] = None) -> Path:
    """A unica captura sem manifesto cujo arquivo mudou nos ultimos RECENTE_S segundos."""
    raiz = Path(raiz)
    if not raiz.is_dir():
        raise LeituraError("raiz_nao_e_pasta")
    agora = time.time() if agora is None else agora
    achadas = []
    for pasta in sorted(p for p in raiz.iterdir() if p.is_dir()):
        arquivo = pasta / ARQUIVO_LEGADO
        if not (pasta / "manifest.json").exists() and arquivo.is_file() and agora - arquivo.stat().st_mtime < RECENTE_S:
            achadas.append(pasta)
    if not achadas:
        raise LeituraError("nenhuma_captura_em_andamento")
    if len(achadas) > 1:
        raise LeituraError("mais_de_uma_captura_em_andamento")
    return achadas[0]


def _linha(r: dict) -> str:
    livro = r["livro"]
    return (f"estado={r['estado']}{'' if r['codigo'] is None else ' codigo=' + r['codigo']} quadros={r['quadros']} "
            f"atraso_s={r['atraso_s']} pendente_kb={r['pendente_bytes'] // 1024} livro_vale={r['livro_vale']} "
            f"fase={livro['fase']} spread_ticks={livro['spread_ticks']} ordens={livro['ordens']} "
            f"anomalias={sum(livro['anomalias'].values())}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="qhos2.leitura", description=__doc__.splitlines()[0])
    alvo = ap.add_mutually_exclusive_group(required=True)
    alvo.add_argument("--captura", help="pasta da captura a seguir")
    alvo.add_argument("--em-andamento", action="store_true", help="a unica captura em andamento em <data_root>/capturas")
    ap.add_argument("--tick", type=float, required=True)
    ap.add_argument("--simbolo", required=True)
    ap.add_argument("--bolsa", required=True)
    ap.add_argument("--intervalo", type=float, default=1.0, help="segundos entre linhas de estado")
    args = ap.parse_args(argv)
    os.nice(10)  # a captura tem prioridade sobre a leitura
    try:
        if args.captura:
            pasta = Path(args.captura)
        else:
            from qhos2.config import Settings
            pasta = captura_em_andamento(Settings.from_environ().data_root / "capturas")
        leitor = Leitor(pasta, tick=args.tick, simbolo=args.simbolo, bolsa=args.bolsa)
    except LeituraError as erro:
        print(f"PARE: {erro}")
        return 2
    print(f"seguindo {pasta.name}")
    inicio, proxima = time.monotonic(), 0.0
    while True:
        lidos = leitor.avancar()
        r = leitor.resumo()
        if time.monotonic() >= proxima or r["estado"] in (ENCERRADA, FALHA):
            print(_linha(r), flush=True)
            proxima = time.monotonic() + args.intervalo
        if r["estado"] == ENCERRADA:
            gasto = time.monotonic() - inicio
            print(f"assinatura={leitor.livro.assinatura()[:16]} (a mesma de python -m qhos2.livro na captura)  "
                  f"lida em {gasto:.1f} s, {leitor.quadros / max(gasto, 1e-9):.0f} quadros/s")
            return 0
        if r["estado"] == FALHA:
            return 1
        if not lidos and not r["pendente_bytes"]:
            time.sleep(0.05)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
