"""Guardas locais da captura: exclusividade, quarentena e copias verificadas.

Nao conecta, nao guarda credenciais, nao reinicia processos e nao altera o relogio.
A guarda e compartilhada por todas as raizes de dados do mesmo usuario Linux.
Um encerramento sem logout confirmado exige liberacao manual antes de novo login.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from qhos2 import disco
from qhos2.config import validar_data_root


class OperacaoError(RuntimeError):
    """Codigo estatico; nunca inclui credenciais ou conteudo externo."""


def estado_padrao() -> Path:
    return Path.home() / ".local" / "state" / "qhos2" / "operacao"


def _fsync_pasta(pasta: Path) -> None:
    fd = os.open(pasta, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomico(caminho: Path, valor: dict) -> None:
    temporario = caminho.with_name("." + caminho.name + "." + uuid.uuid4().hex)
    try:
        fd = os.open(temporario, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as arquivo:
            json.dump(valor, arquivo, sort_keys=True, allow_nan=False)
            arquivo.write("\n")
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(temporario, caminho)
        _fsync_pasta(caminho.parent)
    finally:
        if temporario.exists():
            temporario.unlink()


class GuardaCaptura:
    """Uma captura por usuario local; falhas persistem mesmo apos SIGKILL/reboot.

    `raiz` e injetavel para testes. A CLI usa sempre estado_padrao(), independente
    de QHOS2_DATA_ROOT e --raiz. A guarda nao controla outros usuarios/aplicativos.
    """

    def __init__(self, raiz: Path | None = None):
        self.raiz = Path(raiz) if raiz is not None else estado_padrao()
        self.fd = None
        self.estado = None

    def __enter__(self):
        if sys.platform != "linux":
            raise OperacaoError("guarda_requer_linux")
        import fcntl
        if not self.raiz.is_absolute() or any(p.is_symlink() for p in (self.raiz, *self.raiz.parents)):
            raise OperacaoError("raiz_operacional_invalida")
        self.raiz.mkdir(parents=True, mode=0o700, exist_ok=True)
        st = self.raiz.stat()
        if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
            raise OperacaoError("estado_operacional_permissoes_inseguras")
        try:
            self.fd = os.open(self.raiz / "captura.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            st = os.fstat(self.fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
                raise OperacaoError("lock_inseguro")
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise OperacaoError("captura_ja_em_execucao") from None
            self.estado = self._ler()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def _ler(self) -> dict:
        caminho = self.raiz / "estado.json"
        try:
            fd = os.open(caminho, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return {"schema": "qhos2_operacao_v1", "estado": "LIVRE"}
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_size > 16384:
                raise OperacaoError("estado_operacional_invalido")
            with os.fdopen(fd, "r", encoding="utf-8") as arquivo:
                fd = None
                valor = json.load(arquivo)
        except (ValueError, UnicodeError):
            raise OperacaoError("estado_operacional_invalido") from None
        finally:
            if fd is not None:
                os.close(fd)
        if (not isinstance(valor, dict) or valor.get("schema") != "qhos2_operacao_v1"
                or valor.get("estado") not in {"LIVRE", "EM_ANDAMENTO", "QUARENTENA"}):
            raise OperacaoError("estado_operacional_invalido")
        if valor["estado"] != "LIVRE" and (not isinstance(valor.get("geracao"), str)
                                          or len(valor["geracao"]) != 32):
            raise OperacaoError("estado_operacional_invalido")
        return valor

    def conferir(self) -> None:
        if self.fd is None:
            raise OperacaoError("guarda_nao_adquirida")
        if self.estado["estado"] != "LIVRE":
            raise OperacaoError("captura_em_quarentena")

    def iniciar(self) -> str:
        self.conferir()
        # Nao persiste usuario, senha, endpoint nem hash da identidade da conta.
        # Uma unica guarda conservadora impede tambem concorrencia entre endpoints.
        self.estado = {"schema": "qhos2_operacao_v1", "estado": "EM_ANDAMENTO",
                       "geracao": uuid.uuid4().hex, "inicio_ns": time.time_ns()}
        _atomico(self.raiz / "estado.json", self.estado)
        return self.estado["geracao"]

    def finalizar(self, *, logout_confirmado: bool, fechamento_normal: bool) -> None:
        if self.fd is None or self.estado.get("estado") != "EM_ANDAMENTO":
            raise OperacaoError("tentativa_nao_iniciada")
        self.estado.update(estado="LIVRE" if logout_confirmado and fechamento_normal else "QUARENTENA",
                           fim_ns=time.time_ns(), motivo="logout_confirmado" if logout_confirmado and fechamento_normal
                           else "encerramento_nao_confirmado")
        _atomico(self.raiz / "estado.json", self.estado)

    def liberar(self, geracao: str) -> None:
        if self.fd is None:
            raise OperacaoError("guarda_nao_adquirida")
        if self.estado.get("estado") == "LIVRE" or geracao != self.estado.get("geracao"):
            raise OperacaoError("confirmacao_de_liberacao_divergente")
        anterior = dict(self.estado)
        anterior.update(liberado_ns=time.time_ns(), acao="liberacao_manual_sem_partida")
        historico = self.raiz / ("liberacao-" + uuid.uuid4().hex + ".json")
        _atomico(historico, anterior)
        self.estado = {"schema": "qhos2_operacao_v1", "estado": "LIVRE",
                       "liberacao_anterior": historico.name}
        _atomico(self.raiz / "estado.json", self.estado)


def prazo_calendario(calendario: dict, inicio: datetime, duracao_s: float) -> float | None:
    """Calendario explicito UTC do operador; nenhum feriado/horario e presumido.

    O arquivo precisa conter validade, fonte e intervalos UTC completos. Uma captura
    deve caber inteiramente numa janela. O calendario nao afirma disponibilidade do feed.
    """
    import math
    try:
        if not math.isfinite(duracao_s) or duracao_s <= 0 or inicio.utcoffset() is None:
            return False
        if calendario.get("schema") != "qhos2_calendario_v1" or not calendario.get("fonte"):
            return False
        def instante(valor):
            dt = datetime.fromisoformat(valor.replace("Z", "+00:00"))
            if dt.utcoffset() is None:
                raise ValueError
            return dt.timestamp()
        t = inicio.timestamp()
        if not instante(calendario["valido_de"]) <= t < t + duracao_s <= instante(calendario["valido_ate"]):
            return False
        janelas = [(instante(j["inicio"]), instante(j["fim"])) for j in calendario["janelas"]]
        if any(a >= b for a, b in janelas):
            return False
        finais = [min(b, instante(calendario["valido_ate"])) for a, b in janelas if a <= t and t + duracao_s <= b]
        return max(finais) if finais else None
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
        return None


def janela_permitida(calendario: dict, inicio: datetime, duracao_s: float) -> bool:
    return bool(prazo_calendario(calendario, inicio, duracao_s))



IDENTIDADE_ARQUIVO = "capture_identity.json"
IDENTIDADE_ESQUEMA = "qhos2.capture_identity.v1"
_IDENTIDADE_CHAVES = {"schema", "capture_id", "system_name", "requested_symbol", "exchange", "raw_dev", "raw_ino"}


def ler_identidade_captura(pasta, *, manifesto=None) -> dict:
    """Metadado publico vinculado a captura, nao autenticacao do fornecedor.

    Em curso: captura/id e inode atuais. Encerrada: hash e campos do manifesto;
    o inode antigo nao precisa coincidir apos backup/restauracao em outro destino.
    Capturas legadas sem referencia devem ser tratadas pelo chamador como legadas.
    """
    import re
    pasta = Path(pasta)
    fd = None
    try:
        if any(x.is_symlink() for x in (pasta, *pasta.parents)):
            raise ValueError
        fd = os.open(pasta / IDENTIDADE_ARQUIVO, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
            raise ValueError
        with os.fdopen(fd, "rb") as arquivo:
            fd = None
            raw = arquivo.read(4097)
        if len(raw) > 4096:
            raise ValueError
        valor = json.loads(raw)
        if not isinstance(valor, dict) or set(valor) != _IDENTIDADE_CHAVES or valor["schema"] != IDENTIDADE_ESQUEMA:
            raise ValueError
        if not isinstance(valor["capture_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", valor["capture_id"]):
            raise ValueError
        for campo in ("system_name", "requested_symbol", "exchange"):
            if not isinstance(valor[campo], str) or not 0 < len(valor[campo]) <= 128 or any(ord(c)<32 for c in valor[campo]):
                raise ValueError
        if any(type(valor[c]) is not int or valor[c] < 0 for c in ("raw_dev", "raw_ino")):
            raise ValueError
        if manifesto is None:
            info = (pasta / "market_frames.jsonl").lstat()
            if (not stat.S_ISREG(info.st_mode) or valor["capture_id"] != pasta.name or
                    (valor["raw_dev"], valor["raw_ino"]) != (info.st_dev, info.st_ino)):
                raise ValueError
        else:
            if not isinstance(manifesto, dict) or manifesto.get("capture_identity_sha256") != hashlib.sha256(raw).hexdigest():
                raise ValueError
            status = manifesto.get("status", {})
            if (manifesto.get("capture_id") != valor["capture_id"] or not isinstance(status, dict) or
                    any(status.get(c) != valor[c] for c in ("system_name", "requested_symbol", "exchange"))):
                raise ValueError
        return valor
    except (OSError, ValueError, TypeError, KeyError, OverflowError, UnicodeError):
        raise OperacaoError("identidade_de_captura_invalida") from None
    finally:
        if fd is not None:
            os.close(fd)


def _hash(caminho: Path) -> str:
    if caminho.is_symlink() or not caminho.is_file():
        raise OperacaoError("arquivo_regular_esperado")
    h = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


def copiar_captura(origem, destino) -> dict:
    """Copia/restore sem sobrescrita: verifica bytes antes e depois. Nunca qualifica livro."""
    from qhos2.catalogo import verificar_captura
    origem = Path(origem)
    destino = validar_data_root(destino)
    if any(p.is_symlink() for p in (origem, *origem.parents)):
        raise OperacaoError("origem_com_link")
    origem = origem.resolve()
    if origem == destino or origem in destino.parents or destino in origem.parents:
        raise OperacaoError("destinos_sobrepostos")
    try:
        fd = os.open(origem / "manifest.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as arquivo:
            info = os.fstat(arquivo.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 8 * 1024 ** 2:
                raise ValueError
            raw = arquivo.read(8 * 1024 ** 2 + 1)
        if len(raw) > 8 * 1024 ** 2:
            raise ValueError
        manifesto = json.loads(raw)
    except (OSError, ValueError):
        raise OperacaoError("captura_nao_verificada") from None
    if isinstance(manifesto, dict) and "capture_identity_sha256" in manifesto:
        ler_identidade_captura(origem, manifesto=manifesto)
        arquivos = ("market_frames.jsonl", IDENTIDADE_ARQUIVO, "manifest.json")
    else:
        arquivos = ("market_frames.jsonl", "manifest.json")
    antes = {nome: _hash(origem / nome) for nome in arquivos}
    if not verificar_captura(origem)["verificada"]:
        raise OperacaoError("captura_nao_verificada")
    destino.mkdir(parents=True, mode=0o700, exist_ok=False)
    # O manifesto e o ultimo arquivo publicado; falha de copia deixa incompleta.
    for nome in arquivos:
        with (origem / nome).open("rb") as entrada, (destino / nome).open("xb") as saida:
            shutil.copyfileobj(entrada, saida, 1024 * 1024)
            saida.flush()
            os.fsync(saida.fileno())
        if _hash(destino / nome) != antes[nome] or _hash(origem / nome) != antes[nome]:
            # Manifesto divergente nao pode conferir aparencia de copia concluida.
            if (destino / "manifest.json").exists():
                (destino / "manifest.json").rename(destino / "manifest.incompleto.json")
            raise OperacaoError("copia_divergente")
    _fsync_pasta(destino)
    _fsync_pasta(destino.parent)
    if not verificar_captura(destino)["verificada"]:
        raise OperacaoError("copia_nao_verificada")
    if "capture_identity_sha256" in manifesto:
        ler_identidade_captura(destino, manifesto=manifesto)
    return {"copiada": True, "sha256": antes, "livro_qualificado": "nao_avaliado"}


def diagnosticar(raiz) -> dict:
    """Inventario de disco/capturas incompletas, sem reparar/promover ou conectar."""
    raiz = validar_data_root(raiz)
    livre = disco.livre_bytes(raiz)
    capturas = raiz / "capturas"
    incompletas = sorted(p.name for p in capturas.iterdir()
                        if p.is_dir() and not p.is_symlink() and not (p / "manifest.json").is_file()) if capturas.exists() else []
    return {"data_root": str(raiz), "livre_bytes": livre,"capturas_incompletas": incompletas,
            "relogio_utc_qualificado": False, "calendario_validado": False,
            "aviso": "relogio_e_calendario_exigem_evidencias_proprias"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="acao", required=True)
    sub.add_parser("estado", help="consulta a guarda; nao inicia captura")
    liberar = sub.add_parser("liberar", help="libera depois de investigacao e exclusividade confirmada")
    liberar.add_argument("--geracao", required=True, help="geracao exata mostrada em estado")
    liberar.add_argument("--confirmo-sessoes-encerradas", action="store_true", required=True)
    copia = sub.add_parser("copiar", help="backup ou restore em novo destino; nunca sobrescreve")
    copia.add_argument("origem", type=Path)
    copia.add_argument("destino", type=Path)
    diag = sub.add_parser("diagnosticar")
    diag.add_argument("--raiz", type=Path)
    args = ap.parse_args(argv)
    try:
        if args.acao in {"estado", "liberar"}:
            with GuardaCaptura() as guarda:
                if args.acao == "liberar":
                    guarda.liberar(args.geracao)
                print(json.dumps(guarda.estado, sort_keys=True))
        elif args.acao == "copiar":
            print(json.dumps(copiar_captura(args.origem, args.destino), sort_keys=True))
        else:
            from qhos2.config import Settings
            print(json.dumps(diagnosticar(args.raiz or Settings.from_environ().data_root), sort_keys=True))
    except (OperacaoError, OSError, ValueError) as erro:
        print("operacao_recusada: " + (str(erro) if isinstance(erro, OperacaoError) else type(erro).__name__))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
