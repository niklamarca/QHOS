#!/usr/bin/env python3
"""Libera o disco dos brutos do ES (capturas/<id>/) que ja estao no DataSet, conferidos, ha pelo menos 7 dias.

Roda no WSL com `python3 -I`, so com a biblioteca padrao. Nao importa o pacote qhos2 e nao mora em src/qhos2: nao
muda a identidade do codigo do pre-registro. O DataSet e o da coleta multiativo:
  DataSet/<AAAA-MM-DD>/<BOLSA>_<CONTRATO>[_HHMM].tar.xz, INDEX.json (qhos2.dataset.index.v1) e SHA256SUMS.txt.

Uma captura so sai de capturas/ quando, no momento da remocao:
  0. nao esta protegida pela Etapa 3 (PROTECAO_ETAPA3, decisao do autor em 30/10/2026): ate 1/1/2027 00:00 de Brasilia
     fica toda captura ESZ6 cujo primeiro quadro (received_ns, data de Nova York) cai entre 26/10 e 20/11/2026, porque
     o passo 11 e a avaliacao so leem <raiz>/capturas. Na duvida (contrato ausente, primeiro quadro ilegivel) fica;
  1. esta fechada (tem manifest.json) ha pelo menos --dias (padrao 7), contando do fechamento;
  2. o dia dela (data de Brasilia do fechamento) tem no DataSet INDEX.json e SHA256SUMS.txt, e o INDEX lista uma
     entrada publicada com o SHA-256 do manifest.json dela; o SHA-256 do .tar.xz bate com o INDEX e com o SHA256SUMS;
  3. o .tar.xz e lido de volta: os membros sao exatamente os arquivos da captura, o JSONL bate com o manifesto em
     SHA-256, bytes e quadros, e manifest.json e capture_identity.json batem byte a byte;
  4. existe uma segunda copia local do mesmo .tar.xz fora do OneDrive: a da coleta multiativo
     (<raiz>/coleta_multi/staging/<dia>/.xz/) ou a desta ferramenta (<raiz>/dataset_copia_local/<dia>/), criada e
     conferida aqui se faltar.
A pasta sai de capturas/ por rename atomico e so depois e apagada. Qualquer falha deixa o bruto onde esta.
O DataSet e a pasta da coleta so sao lidos. Sem --executar nada e escrito.
Codigo de saida: 0 ok (inclusive "sem copia no DataSet", que so fica registrado), 1 divergencia, 2 uso incorreto.
"""
import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tarfile
from collections import namedtuple
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: sem trava de processo; a ferramenta e feita para o WSL
    fcntl = None
try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:  # sem a base de fusos nao da para datar o quadro: a protecao guarda tudo o que pode ser ESZ6
    NY = None

BRT = timezone(timedelta(hours=-3))
Protecao = namedtuple("Protecao", "contrato de ate liberacao")
# Etapa 3: calibracao 26/10 a 6/11, teste 9 a 20/11, avaliacao depois. Mudar qualquer valor e decisao do autor.
PROTECAO_ETAPA3 = Protecao("ESZ6", date(2026, 10, 26), date(2026, 11, 20), datetime(2027, 1, 1, tzinfo=BRT))
PRIMEIRA_LINHA_MAX = 1024 ** 2
BLOCO = 4 * 1024 ** 2
ARQUIVOS = ("manifest.json", "capture_identity.json", "market_frames.jsonl")
PERMITIDOS = frozenset(ARQUIVOS)
MANIFESTO_MAX = 8 * 1024 ** 2
DIAS_PADRAO = 7.0
RAIZ_PADRAO = Path.home() / "qhos-v2-data"
DATASET_PADRAO = Path("/mnt/c/Users/usuario/OneDrive/Desktop/DataSet")
GUARDA_PADRAO = Path.home() / ".local" / "state" / "qhos2" / "operacao" / "estado.json"
ESTADO, COPIA_LOCAL = ".limpeza_es", "dataset_copia_local"


class LimpezaError(ValueError):
    """Motivo curto e estavel."""


def _agora():
    return datetime.now(timezone.utc)


def _sha_arquivo(caminho):
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(BLOCO), b""):
            h.update(bloco)
    return h.hexdigest()


def _fsync_pasta(pasta):
    try:
        fd = os.open(pasta, os.O_RDONLY)
    except (OSError, AttributeError):
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _diario(estado, evento):
    Path(estado).mkdir(parents=True, mode=0o700, exist_ok=True)
    linha = json.dumps({"t": _agora().isoformat(timespec="seconds"), **evento}, sort_keys=True, ensure_ascii=False)
    with open(Path(estado) / "limpeza.jsonl", "a", encoding="utf-8") as f:
        f.write(linha + "\n")
        f.flush()
        os.fsync(f.fileno())


# ---------------------------------------------------------------- a captura

def _manifesto(pasta):
    caminho = Path(pasta) / "manifest.json"
    info = caminho.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MANIFESTO_MAX:
        raise LimpezaError("manifesto_invalido")
    bruto = caminho.read_bytes()
    try:
        valor = json.loads(bruto)
    except ValueError:
        raise LimpezaError("manifesto_invalido") from None
    if not isinstance(valor, dict):
        raise LimpezaError("manifesto_invalido")
    return valor, bruto


def _fechada_em(manifesto):
    texto = (manifesto.get("status") or {}).get("criado_em")
    try:
        momento = datetime.fromisoformat(str(texto).replace("Z", "+00:00"))
    except ValueError:
        raise LimpezaError("manifesto_sem_fechamento") from None
    if momento.tzinfo is None:
        raise LimpezaError("manifesto_sem_fechamento")
    return momento


def _inspecionar(pasta):
    """('aberta' | 'ignorada' | 'fechada', motivo, membros)."""
    pasta = Path(pasta)
    if pasta.is_symlink():
        return "ignorada", "link_simbolico", None
    try:
        entradas = {e.name: e for e in os.scandir(pasta)}
    except OSError:
        return "ignorada", "pasta_ilegivel", None
    if "manifest.json" not in entradas:
        return "aberta", None, None
    if (not set(entradas) <= PERMITIDOS or "market_frames.jsonl" not in entradas
            or any(e.is_symlink() or not e.is_file(follow_symlinks=False) for e in entradas.values())):
        return "ignorada", "conteudo_inesperado", None
    return "fechada", None, [a for a in ARQUIVOS if a in entradas]


def _primeiro_quadro_ny(pasta):
    """Data de Nova York do received_ns do primeiro quadro (a mesma conta do avaliador), ou None se nao der."""
    if NY is None:
        return None
    try:
        with open(Path(pasta) / "market_frames.jsonl", "rb") as f:
            ns = json.loads(f.readline(PRIMEIRA_LINHA_MAX))["received_ns"]
        if not isinstance(ns, int) or isinstance(ns, bool):
            return None
        return datetime.fromtimestamp(ns // 10 ** 9, NY).date()
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return None


def _protegida(manifesto, pasta, agora, protecao):
    """Motivo para guardar a captura ({'motivo', 'dia_ny'}) ou None. Na duvida, guarda."""
    if protecao is None or agora >= protecao.liberacao:
        return None
    status = manifesto.get("status")
    simbolo = status.get("requested_symbol") if isinstance(status, dict) else None
    if isinstance(simbolo, str) and simbolo != protecao.contrato:
        return None
    dia = _primeiro_quadro_ny(pasta)
    if dia is None:
        return {"motivo": "etapa3_primeiro_quadro_ilegivel", "dia_ny": None}
    if protecao.de <= dia <= protecao.ate:
        return {"motivo": "etapa3", "dia_ny": dia.isoformat()}
    return None


# ---------------------------------------------------------------- o DataSet

def _somas(caminho):
    saida = {}
    for linha in Path(caminho).read_text(encoding="utf-8").splitlines():
        if linha.strip():
            sha, nome = linha.split("  ", 1)
            saida[nome] = sha
    return saida


def _no_dataset(dataset, dia, sha_manifesto):
    """(caminho do .tar.xz, sha256 esperado) ou ('sem_copia', motivo) ou LimpezaError se o DataSet se contradiz."""
    pasta = Path(dataset) / dia
    if not (pasta / "INDEX.json").is_file() or not (pasta / "SHA256SUMS.txt").is_file():
        return None, "dia_sem_index_no_dataset"
    try:
        index = json.loads((pasta / "INDEX.json").read_text(encoding="utf-8"))
        somas = _somas(pasta / "SHA256SUMS.txt")
    except (OSError, ValueError):
        raise LimpezaError("index_ou_somas_ilegiveis") from None
    achadas = [e for e in index.get("ativos") or [] if isinstance(e, dict) and e.get("sha256_manifesto") == sha_manifesto]
    publicadas = [e for e in achadas if str(e.get("estado", "")).startswith("publicado")]
    if not publicadas:
        return None, "nao_publicada_no_dia" if achadas else "nao_esta_no_index_do_dia"
    entrada = publicadas[0]
    nome = entrada.get("arquivo")
    if not isinstance(nome, str) or "/" in nome or not nome.endswith(".tar.xz"):
        raise LimpezaError("index_invalido")
    if somas.get(nome) != entrada.get("sha256_xz"):
        raise LimpezaError("somas_divergem_do_index")
    xz = pasta / nome
    if not xz.is_file():
        raise LimpezaError("arquivo_ausente_no_dataset")
    return xz, entrada["sha256_xz"]


def _conferir_xz(xz, sha_esperado, pasta, membros, manifesto):
    """Le o .tar.xz de volta e confere contra a captura. Levanta LimpezaError com o motivo."""
    if _sha_arquivo(xz) != sha_esperado:
        raise LimpezaError("sha256_do_xz_diverge")
    nome = Path(xz).name[: -len(".tar.xz")]
    esperados = {f"{nome}/{a}" for a in membros}
    vistos = {}
    try:
        with tarfile.open(xz, mode="r|xz") as tar:
            for membro in tar:
                if not membro.isfile() or membro.name not in esperados or membro.name in vistos:
                    raise LimpezaError("membro_inesperado")
                f = tar.extractfile(membro)
                h, total, linhas = hashlib.sha256(), 0, 0
                for bloco in iter(lambda: f.read(BLOCO), b""):
                    h.update(bloco)
                    total += len(bloco)
                    linhas += bloco.count(b"\n")
                vistos[membro.name] = (h.hexdigest(), total, linhas)
    except (tarfile.TarError, EOFError, OSError):
        raise LimpezaError("xz_ilegivel") from None
    if set(vistos) != esperados:
        raise LimpezaError("membro_ausente")
    if vistos[f"{nome}/market_frames.jsonl"] != (manifesto.get("sha256"), manifesto.get("bytes"), manifesto.get("frames")):
        raise LimpezaError("jsonl_diverge_do_manifesto")
    for a in membros:
        if a != "market_frames.jsonl" and vistos[f"{nome}/{a}"][0] != _sha_arquivo(Path(pasta) / a):
            raise LimpezaError(a.replace(".json", "") + "_diverge")


def _segunda_copia(raiz, copia_local, dia, xz, sha, executar):
    """'coleta' | 'local' | 'criada' | 'a_criar'. Nunca escreve na pasta da coleta."""
    da_coleta = Path(raiz) / "coleta_multi" / "staging" / dia / ".xz" / Path(xz).name
    if da_coleta.is_file() and _sha_arquivo(da_coleta) == sha:
        return "coleta"
    nossa = Path(copia_local) / dia / Path(xz).name
    if nossa.is_file():
        if _sha_arquivo(nossa) != sha:
            raise LimpezaError("copia_local_diverge")
        return "local"
    if not executar:
        return "a_criar"
    nossa.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    parcial = nossa.with_name(nossa.name + ".parcial")
    if parcial.exists():
        parcial.unlink()
    with open(xz, "rb") as entrada, open(parcial, "xb") as saida:
        for bloco in iter(lambda: entrada.read(BLOCO), b""):
            saida.write(bloco)
        saida.flush()
        os.fsync(saida.fileno())
    if _sha_arquivo(parcial) != sha:
        parcial.unlink()
        raise LimpezaError("copia_local_diverge")
    os.rename(parcial, nossa)
    _fsync_pasta(nossa.parent)
    return "criada"


def _avaliar(pasta, raiz, dataset, copia_local, dias, agora, executar, protecao):
    """Decide uma captura fechada. Devolve (categoria, detalhe, contexto)."""
    estado, motivo, membros = _inspecionar(pasta)
    if estado != "fechada":
        return estado, motivo, None
    try:
        manifesto, bruto = _manifesto(pasta)
        fechada = _fechada_em(manifesto)
    except (LimpezaError, OSError) as erro:
        return "ignorada", str(erro), None
    guardar = _protegida(manifesto, pasta, agora, protecao)
    if guardar is not None:
        return "protegida", guardar, None
    if (agora - fechada).total_seconds() < dias * 86400:
        return "no_prazo", None, None
    dia = fechada.astimezone(BRT).date().isoformat()
    try:
        xz, sha = _no_dataset(dataset, dia, hashlib.sha256(bruto).hexdigest())
        if xz is None:
            return "sem_copia", sha, None
        _conferir_xz(xz, sha, pasta, membros, manifesto)
        copia = _segunda_copia(raiz, copia_local, dia, xz, sha, executar)
    except LimpezaError as erro:
        return "divergente", str(erro), None
    return "pronta", copia, {"dia": dia, "xz": str(xz), "bytes": manifesto.get("bytes") or 0}


# ---------------------------------------------------------------- a rodada

def _guarda_em_andamento(guarda):
    if guarda is None:
        return False
    try:
        valor = json.loads(Path(guarda).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(valor, dict) and valor.get("estado") == "EM_ANDAMENTO"


def limpar(raiz, *, dataset, dias=DIAS_PADRAO, executar=False, agora=None, guarda=None, copia_local=None,
           protecao=PROTECAO_ETAPA3):
    if not isinstance(dias, (int, float)) or isinstance(dias, bool) or not dias >= 0:
        raise LimpezaError("dias_invalido")
    raiz, dataset, agora = Path(raiz), Path(dataset), agora or _agora()
    if not dataset.is_dir():
        raise LimpezaError("dataset_inexistente")
    copia_local = Path(copia_local) if copia_local is not None else raiz / COPIA_LOCAL
    estado = raiz / ESTADO
    rel = {"simulacao": not executar, "parada": None, "prontas": [], "removidas": [], "no_prazo": [], "abertas": [],
           "protegidas": [], "sem_copia": [], "ignoradas": [], "divergentes": [], "copias_criadas": [],
           "bytes_liberados": 0, "protecao": None}
    if protecao is not None:
        rel["protecao"] = {"contrato": protecao.contrato, "de": protecao.de.isoformat(), "ate": protecao.ate.isoformat(),
                           "liberacao": protecao.liberacao.isoformat(), "ativa": agora < protecao.liberacao}
    if executar and _guarda_em_andamento(guarda):
        rel["parada"] = "captura_em_andamento"
        return rel
    capturas = raiz / "capturas"
    pastas = sorted(p for p in capturas.iterdir() if p.is_dir() or p.is_symlink()) if capturas.is_dir() else []
    removendo = estado / "removendo"
    restos = sorted(removendo.iterdir()) if removendo.is_dir() else []

    def rodada(executar_agora):
        for k in ("prontas", "no_prazo", "abertas", "protegidas", "sem_copia", "ignoradas", "divergentes"):
            rel[k] = []
        decisoes = {}
        for pasta in pastas:
            if not os.path.lexists(pasta):
                continue
            cat, det, ctx = _avaliar(pasta, raiz, dataset, copia_local, dias, agora, executar_agora, protecao)
            if cat == "pronta":
                rel["prontas"].append(pasta.name)
                decisoes[pasta.name] = (det, ctx)
            elif cat in ("no_prazo", "abertas", "aberta"):
                rel["abertas" if cat == "aberta" else cat].append(pasta.name)
            elif cat == "protegida":
                rel["protegidas"].append({"id": pasta.name, **det})
            elif cat == "sem_copia":
                rel["sem_copia"].append({"id": pasta.name, "motivo": det})
            elif cat == "divergente":
                rel["divergentes"].append({"id": pasta.name, "motivo": det})
            else:
                rel["ignoradas"].append({"id": pasta.name, "motivo": det})
        return decisoes

    decisoes = rodada(False)
    if not executar or not (decisoes or restos):
        return rel
    estado.mkdir(parents=True, mode=0o700, exist_ok=True)
    trava = open(estado / "trava", "a")
    try:
        if fcntl is not None:
            try:
                fcntl.flock(trava, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                rel["parada"] = "ocupado"
                return rel
        extras = []  # divergencias dos restos: entram depois, porque a rodada zera as listas
        _limpar_restos(raiz, dataset, copia_local, removendo, agora, rel, extras, protecao)
        decisoes = rodada(True)  # com a trava, e criando a segunda copia local que faltar
        rel["divergentes"] += extras
        for id_, (copia, ctx) in decisoes.items():
            if copia == "criada":
                rel["copias_criadas"].append(id_)
            try:
                removendo.mkdir(parents=True, mode=0o700, exist_ok=True)
                os.rename(capturas / id_, removendo / id_)
                _fsync_pasta(capturas)
                shutil.rmtree(removendo / id_)
            except OSError as erro:
                rel["divergentes"].append({"id": id_, "motivo": "remocao_falhou", "detalhe": str(erro)[:200]})
                continue
            rel["removidas"].append(id_)
            rel["bytes_liberados"] += ctx["bytes"]
            _diario(estado, {"acao": "removida", "id": id_, "dia": ctx["dia"], "xz": ctx["xz"], "segunda_copia": copia})
        try:
            removendo.rmdir()
        except OSError:
            pass
    finally:
        trava.close()
    return rel


def _limpar_restos(raiz, dataset, copia_local, removendo, agora, rel, extras, protecao):
    """Pasta que ficou em removendo/ por um corte: so apaga se a copia do DataSet confere e ha segunda copia."""
    if not removendo.is_dir():
        return
    for resto in sorted(removendo.iterdir()):
        cat, det, _ctx = _avaliar(resto, raiz, dataset, copia_local, 0, agora, True, protecao)
        if cat == "protegida":  # fora de capturas/ o passo 11 nao a ve: fica e vai ao log para alguem olhar
            extras.append({"id": resto.name, "motivo": "resto_protegido_etapa3"})
        elif cat == "pronta":
            shutil.rmtree(resto, ignore_errors=True)
            if det == "criada":
                rel["copias_criadas"].append(resto.name)
            _diario(raiz / ESTADO, {"acao": "removida_resto", "id": resto.name})
        else:  # nunca apaga a unica copia
            extras.append({"id": resto.name, "motivo": "resto_de_remocao_sem_copia_valida:" + str(det)})


# ---------------------------------------------------------------- linha de comando

def _momento(texto):
    try:
        valor = datetime.fromisoformat(texto)
    except ValueError:
        raise argparse.ArgumentTypeError("data_invalida") from None
    if valor.tzinfo is None:
        raise argparse.ArgumentTypeError("data_sem_fuso")
    return valor


def main(argv=None):
    ap = argparse.ArgumentParser(prog="limpar_brutos_es", description=__doc__.splitlines()[0])
    ap.add_argument("--raiz", type=Path, default=RAIZ_PADRAO, help="pasta de dados (padrao ~/qhos-v2-data)")
    ap.add_argument("--dataset", type=Path, default=DATASET_PADRAO, help="pasta do DataSet (tem de existir)")
    ap.add_argument("--dias", type=float, default=DIAS_PADRAO, help="so remove captura fechada ha pelo menos isto (padrao 7)")
    ap.add_argument("--executar", action="store_true", help="remove de verdade (sem isto e simulacao)")
    ap.add_argument("--guarda", type=Path, default=GUARDA_PADRAO, help="estado.json da guarda; EM_ANDAMENTO suspende")
    ap.add_argument("--copia-local", type=Path, default=None, help="segunda copia local (padrao <raiz>/dataset_copia_local)")
    ap.add_argument("--agora", type=_momento, default=None, help=argparse.SUPPRESS)
    try:
        args = ap.parse_args(argv)
    except SystemExit as saida:
        return int(saida.code or 0)
    if not args.dias >= 0:
        print("dias_invalido", file=sys.stderr)
        return 2
    if args.executar:
        os.umask(0o077)
        try:
            os.nice(19)
        except (OSError, AttributeError):
            pass
    try:
        rel = limpar(args.raiz, dataset=args.dataset, dias=args.dias, executar=args.executar, agora=args.agora,
                     guarda=args.guarda, copia_local=args.copia_local)
    except LimpezaError as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 1
    except OSError as erro:
        print(f"erro_de_disco: {str(erro)[:200]}", file=sys.stderr)
        return 1
    print(json.dumps(rel, indent=2, sort_keys=True, ensure_ascii=False))
    return 1 if rel["divergentes"] else 0


if __name__ == "__main__":
    sys.exit(main())
