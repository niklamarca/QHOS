#!/usr/bin/env python3
"""Arquiva as capturas fechadas da V2 num dataset compacto: xz sobre o JSONL, conferido pelo manifesto.

Roda no WSL com `python3 -I`, so com a biblioteca padrao (como heartbeat_v2_leitor.py). Nao importa o
pacote qhos2 e nao mora em src/qhos2: nao muda a identidade do codigo do pre-registro.

Fluxo diario (uma rodada depois do pregao):
  1. captura FECHADA ha pelo menos --espera-horas (padrao 1) e sem arquivo: comprime e publica no destino
     (--destino; padrao <raiz>/capturas_arquivadas; ex.: a pasta DataSet), com o original mantido;
  2. captura com arquivo conferido e fechada ha pelo menos --manter-dias (padrao 7): tira o original de
     capturas/ para liberar disco, depois de ler o arquivo de volta e conferir de novo.

Regras que nao mudam:
  * a captura em curso (sem manifest.json), a recente, a com link ou com conteudo desconhecido nunca e tocada;
  * antes de qualquer remocao o xz e lido de volta e o SHA-256, os bytes e a contagem de quadros precisam bater
    com o manifesto; manifest.json e capture_identity.json vao byte a byte;
  * a montagem acontece em <raiz>/.arquivador (mesmo disco das capturas) e so entao vai ao destino; se o destino
    esta em outro disco, cada arquivo copiado e relido e ARQUIVO.json e o ultimo a aparecer (marca de completo);
  * a pasta sai de capturas/ por rename atomico (ninguem enxerga meia captura) e so depois e apagada;
  * qualquer divergencia deixa o original onde esta, nao deixa meia gravacao e sai com codigo 1;
  * --selar DE ATE --liberar-em DATA: pregoes nessa faixa vao para <destino>/SELADO_ate_<DATA>/ e nao podem ser
    restaurados antes da data (janela de teste do pre-registro: nao olhar); depois da data sao movidos para a raiz;
  * sem --executar nada e escrito (simulacao).

Comandos: listar | arquivar | verificar | restaurar <id>. Codigos de saida: 0 ok, 1 divergencia ou falha,
2 uso incorreto.
"""
import argparse
import hashlib
import json
import lzma
import os
import re
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: sem trava de processo; o script e feito para o WSL
    fcntl = None

VERSAO_FERRAMENTA = "2"
ESQUEMA = "qhos2.arquivo_captura.v1"
CAPTURAS, ARQUIVADAS, ESTADO = "capturas", "capturas_arquivadas", ".arquivador"
BRUTO, COMPRIMIDO = "market_frames.jsonl", "market_frames.jsonl.xz"
MANIFESTO, IDENTIDADE, LATERAL = "manifest.json", "capture_identity.json", "ARQUIVO.json"
DIARIO, TRAVA, SELADO = "arquivamento.jsonl", "trava", "SELADO_ate_"
PARCIAL, REMOVENDO, RESTAURANDO, RESTAURADAS = "parcial", "removendo", "restaurando", "restauradas"
PERMITIDOS = frozenset({BRUTO, MANIFESTO, IDENTIDADE})
NOMES_DO_ARQUIVO = frozenset({COMPRIMIDO, MANIFESTO, IDENTIDADE, LATERAL})
BLOCO = 4 * 1024 ** 2
PRESET = 3
MANIFESTO_MAX = 8 * 1024 ** 2
MARGEM_DISCO = 256 * 1024 ** 2
MANTER_DIAS_PADRAO = 7.0
ESPERA_HORAS_PADRAO = 1.0
BRT = timezone(timedelta(hours=-3))
RAIZ_PADRAO = Path.home() / "qhos-v2-data"
GUARDA_PADRAO = Path.home() / ".local" / "state" / "qhos2" / "operacao" / "estado.json"
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_NOME = re.compile(r"(\d{4}-\d{2}-\d{2})_([A-Za-z0-9_-]{1,64})")  # nome da pasta do arquivo: AAAA-MM-DD_<id da captura>
_SHA = re.compile(r"[0-9a-f]{64}")


class ArquivamentoError(ValueError):
    """Motivo curto e estavel, no estilo do resto do projeto (ex.: destino_ja_existe)."""


# ---------------------------------------------------------------- utilidades

def _agora():
    return datetime.now(timezone.utc)


def _dia_brt(momento):
    return momento.astimezone(BRT).date()


def _separar(nome):
    """(data, id) de um nome AAAA-MM-DD_<id>; sem data, (None, nome)."""
    m = _NOME.fullmatch(nome)
    return (m.group(1), m.group(2)) if m else (None, nome)


def _nome_do_arquivo(id_, momento_fechada):
    return f"{_dia_brt(momento_fechada).isoformat()}_{id_}"


def _estado(raiz):
    return Path(raiz) / ESTADO


def _destino(raiz, destino):
    return Path(destino) if destino is not None else Path(raiz) / ARQUIVADAS


def _sha_e_tamanho(caminho):
    h, n = hashlib.sha256(), 0
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(BLOCO), b""):
            h.update(bloco)
            n += len(bloco)
    return h.hexdigest(), n


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


def _regular(info):
    import stat
    return stat.S_ISREG(info.st_mode)


def _inteiro(valor):
    return type(valor) is int and valor >= 0


def _escrever(caminho, dados):
    with open(caminho, "xb") as f:
        f.write(dados)
        f.flush()
        os.fsync(f.fileno())


def _mesmo_disco(a, b):
    return os.stat(a).st_dev == os.stat(b).st_dev


def _diario(estado, evento):
    Path(estado).mkdir(parents=True, mode=0o700, exist_ok=True)
    linha = json.dumps({"t": _agora().isoformat(timespec="seconds"), **evento}, sort_keys=True, ensure_ascii=False)
    with open(Path(estado) / DIARIO, "a", encoding="utf-8") as f:
        f.write(linha + "\n")
        f.flush()
        os.fsync(f.fileno())


# ---------------------------------------------------------------- selo da janela de teste

def _selo(valor):
    """None ou {"de", "ate", "libera"} em datas; aceita ISO ou date."""
    if valor is None:
        return None
    try:
        de, ate, libera = (v if isinstance(v, date) and not isinstance(v, datetime) else date.fromisoformat(v)
                           for v in (valor["de"], valor["ate"], valor["libera"]))
    except (KeyError, TypeError, ValueError):
        raise ArquivamentoError("selo_invalido") from None
    if not de <= ate < libera:
        raise ArquivamentoError("selo_invalido")
    return {"de": de, "ate": ate, "libera": libera}


def _base_de(alvo, selo, momento_fechada, agora):
    """Pasta onde o arquivo deste pregao entra: a raiz do destino ou a subpasta selada."""
    if selo is not None and selo["de"] <= _dia_brt(momento_fechada) <= selo["ate"] and _dia_brt(agora) < selo["libera"]:
        return Path(alvo) / (SELADO + selo["libera"].isoformat())
    return Path(alvo)


def _liberacao(pasta):
    try:
        return date.fromisoformat(Path(pasta).name[len(SELADO):])
    except ValueError:
        return None


def _selados(alvo):
    alvo = Path(alvo)
    if not alvo.is_dir():
        return []
    return sorted(p for p in alvo.iterdir() if p.is_dir() and p.name.startswith(SELADO) and _liberacao(p) is not None)


def _a_liberar(alvo, agora):
    return [s for s in _selados(alvo) if _dia_brt(agora) >= _liberacao(s)]


def _liberar_selos(alvo, agora, relatorio, extras):
    for selo in _a_liberar(alvo, agora):
        for filho in sorted(p for p in selo.iterdir() if p.is_dir()):
            id_ = _separar(filho.name)[1]
            if (Path(alvo) / filho.name).exists():
                extras.append({"id": id_, "motivo": "liberacao_destino_ja_existe"})
                continue
            try:
                os.rename(filho, Path(alvo) / filho.name)
            except OSError:  # ex.: o OneDrive esta usando a pasta; a proxima rodada tenta de novo
                extras.append({"id": id_, "motivo": "liberacao_falhou"})
                continue
            relatorio["liberadas"].append(id_)
        try:
            selo.rmdir()
        except OSError:
            pass


# ---------------------------------------------------------------- achar e listar o que ja foi arquivado

def _entradas(alvo):
    """(id, pasta) de cada arquivo feito (ou meio feito) no destino, inclusive nas subpastas seladas."""
    alvo, saida = Path(alvo), []
    if not alvo.is_dir():
        return saida

    def parece(p):
        return p.is_dir() and not p.name.startswith(".") and (_NOME.fullmatch(p.name) or _ID.fullmatch(p.name)) and (
            (p / LATERAL).exists() or (p / COMPRIMIDO).exists())

    for p in sorted(alvo.iterdir()):
        if p.is_dir() and p.name.startswith(SELADO):
            saida += [(_separar(c.name)[1], c) for c in sorted(p.iterdir()) if parece(c)]
        elif parece(p):
            saida.append((_separar(p.name)[1], p))
    return saida


def _achar(alvo, id_):
    for nome, pasta in _entradas(alvo):
        if nome == id_:
            return pasta
    return None


def _incompletas(alvo):
    """Pastas que NOS deixamos sem terminar no destino: so arquivos nossos e sem ARQUIVO.json."""
    achadas = []
    for nome, pasta in _entradas(alvo):
        filhos = {p.name for p in pasta.iterdir()}
        if LATERAL not in filhos and filhos <= NOMES_DO_ARQUIVO:
            achadas.append(pasta)
    return achadas


# ---------------------------------------------------------------- leitura e conferencia

def _ler_manifesto(pasta):
    """(dict, bytes) do manifest.json; recusa arquivo grande, link ou JSON que nao e objeto."""
    caminho = Path(pasta) / MANIFESTO
    try:
        info = caminho.lstat()
        if not _regular(info) or info.st_size > MANIFESTO_MAX:
            raise ArquivamentoError("manifesto_invalido")
        bruto = caminho.read_bytes()
        valor = json.loads(bruto)
    except (OSError, ValueError):
        raise ArquivamentoError("manifesto_invalido") from None
    if not isinstance(valor, dict):
        raise ArquivamentoError("manifesto_invalido")
    return valor, bruto


def _hashes_do_manifesto(manifesto):
    """(sha256, bytes, quadros) declarados no manifesto, ou ArquivamentoError se nao der para conferir."""
    sha, tamanho, quadros = manifesto.get("sha256"), manifesto.get("bytes"), manifesto.get("frames")
    if not isinstance(sha, str) or not _SHA.fullmatch(sha) or not _inteiro(tamanho) or not _inteiro(quadros):
        raise ArquivamentoError("manifesto_sem_hash")
    return sha, tamanho, quadros


def _fechada_em(manifesto, pasta):
    status = manifesto.get("status")
    texto = status.get("criado_em") if isinstance(status, dict) else None
    if isinstance(texto, str):
        try:
            momento = datetime.fromisoformat(texto.replace("Z", "+00:00"))
            if momento.tzinfo is not None:
                return momento
        except ValueError:
            pass
    return datetime.fromtimestamp((Path(pasta) / MANIFESTO).stat().st_mtime, timezone.utc)


def _hash_do_xz(caminho):
    """SHA-256, bytes e quadros do conteudo DESCOMPRIMIDO, lido em blocos."""
    h, n, quadros = hashlib.sha256(), 0, 0
    try:
        with lzma.open(caminho, "rb") as f:
            for bloco in iter(lambda: f.read(BLOCO), b""):
                h.update(bloco)
                n += len(bloco)
                quadros += bloco.count(b"\n")
    except (lzma.LZMAError, EOFError, OSError):
        raise ArquivamentoError("xz_ilegivel") from None
    return h.hexdigest(), n, quadros


# ---------------------------------------------------------------- inspecao

def _inspecionar(pasta, alvo, estado, manter_dias, espera_horas, agora):
    """Estado de uma pasta de capturas/. Nao le o arquivo de quadros, so metadados e o manifesto."""
    pasta = Path(pasta)
    info = {"id": pasta.name, "estado": None, "motivo": None, "bytes": 0, "fechada_em": None, "dias": None,
            "vencida": False, "restaurada": False}
    if pasta.is_symlink():
        return {**info, "estado": "ignorada", "motivo": "link_simbolico"}
    try:
        entradas = {e.name: e for e in os.scandir(pasta)}
    except OSError:
        return {**info, "estado": "ignorada", "motivo": "pasta_ilegivel"}
    try:
        if BRUTO in entradas:
            info["bytes"] = entradas[BRUTO].stat(follow_symlinks=False).st_size
    except OSError:
        pass
    if MANIFESTO not in entradas:
        return {**info, "estado": "aberta"}
    if (not set(entradas) <= PERMITIDOS or BRUTO not in entradas
            or any(e.is_symlink() or not e.is_file(follow_symlinks=False) for e in entradas.values())):
        return {**info, "estado": "ignorada", "motivo": "conteudo_inesperado"}
    try:
        manifesto, _ = _ler_manifesto(pasta)
        _hashes_do_manifesto(manifesto)
    except ArquivamentoError as erro:
        return {**info, "estado": "ignorada", "motivo": str(erro)}
    momento = _fechada_em(manifesto, pasta)
    info["fechada_em"] = momento.isoformat(timespec="seconds")
    info["dias"] = round((agora - momento).total_seconds() / 86400, 4)
    feito = _achar(alvo, pasta.name)
    if feito is not None:
        if not (feito / LATERAL).is_file():
            return {**info, "estado": "divergente", "motivo": "arquivo_sem_lateral"}
        info["restaurada"] = (Path(estado) / RESTAURADAS / (pasta.name + ".json")).is_file()
        info["vencida"] = info["dias"] >= manter_dias and not info["restaurada"]
        motivo = "restaurada" if info["restaurada"] else ("vencida" if info["vencida"] else "no_prazo")
        return {**info, "estado": "com_original", "motivo": motivo}
    return {**info, "estado": "recente" if info["dias"] * 24 < espera_horas else "elegivel"}


def listar(raiz, *, destino=None, manter_dias=MANTER_DIAS_PADRAO, espera_horas=ESPERA_HORAS_PADRAO, agora=None):
    raiz, agora = Path(raiz), agora or _agora()
    capturas = raiz / CAPTURAS
    if not capturas.is_dir():
        raise ArquivamentoError("sem_pasta_de_capturas")
    alvo = _destino(raiz, destino)
    return [_inspecionar(p, alvo, _estado(raiz), manter_dias, espera_horas, agora)
            for p in sorted(capturas.iterdir()) if p.is_dir() or p.is_symlink()]


# ---------------------------------------------------------------- criar o arquivo

def _comprimir(origem, destino):
    """Copia `origem` para um .xz novo lendo uma vez; devolve (sha256, bytes, quadros) do ORIGINAL."""
    h, n, quadros = hashlib.sha256(), 0, 0
    with open(origem, "rb") as entrada, open(destino, "xb") as saida:
        with lzma.LZMAFile(saida, "wb", format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, preset=PRESET) as xz:
            for bloco in iter(lambda: entrada.read(BLOCO), b""):
                h.update(bloco)
                n += len(bloco)
                quadros += bloco.count(b"\n")
                xz.write(bloco)
        saida.flush()
        os.fsync(saida.fileno())
    return h.hexdigest(), n, quadros


def _montar(pasta, estado):
    """Monta e confere a pasta do arquivo em <estado>/parcial/<id>. Devolve (caminho, ARQUIVO.json)."""
    pasta, estado = Path(pasta), Path(estado)
    manifesto, bruto_manifesto = _ler_manifesto(pasta)
    sha_m, bytes_m, quadros_m = _hashes_do_manifesto(manifesto)
    bruto = pasta / BRUTO
    if bruto.stat().st_size != bytes_m:
        raise ArquivamentoError("bytes_diverge_do_manifesto")
    identidade = pasta / IDENTIDADE
    dados_identidade = identidade.read_bytes() if identidade.is_file() else None
    esperado = manifesto.get("capture_identity_sha256")
    if esperado is not None and (dados_identidade is None
                                 or hashlib.sha256(dados_identidade).hexdigest() != esperado):
        raise ArquivamentoError("identidade_diverge_do_manifesto")
    momento = _fechada_em(manifesto, pasta)
    parcial = estado / PARCIAL / _nome_do_arquivo(pasta.name, momento)
    parcial.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    parcial.mkdir(mode=0o700)
    try:
        sha, n, quadros = _comprimir(bruto, parcial / COMPRIMIDO)
        if n != bytes_m:
            raise ArquivamentoError("bytes_diverge_do_manifesto")
        if sha != sha_m:
            raise ArquivamentoError("sha256_diverge_do_manifesto")
        if quadros != quadros_m:
            raise ArquivamentoError("quadros_divergem_do_manifesto")
        _escrever(parcial / MANIFESTO, bruto_manifesto)
        if dados_identidade is not None:
            _escrever(parcial / IDENTIDADE, dados_identidade)
        sha_xz, bytes_xz = _sha_e_tamanho(parcial / COMPRIMIDO)
        lido = _hash_do_xz(parcial / COMPRIMIDO)  # le o xz de volta antes de publicar
        if lido != (sha_m, bytes_m, quadros_m):
            raise ArquivamentoError("arquivo_comprimido_nao_confere")
        lateral = {
            "esquema": ESQUEMA, "captura_id": pasta.name, "data_pregao": _dia_brt(momento).isoformat(),
            "ferramenta": VERSAO_FERRAMENTA,
            "arquivado_em": _agora().isoformat(timespec="seconds"),
            "metodo": {"formato": "xz", "preset": PRESET},
            "original": {"arquivo": BRUTO, "sha256": sha_m, "bytes": bytes_m, "frames": quadros_m},
            "comprimido": {"arquivo": COMPRIMIDO, "sha256": sha_xz, "bytes": bytes_xz},
            "manifest_sha256": hashlib.sha256(bruto_manifesto).hexdigest(),
            "identidade_sha256": hashlib.sha256(dados_identidade).hexdigest() if dados_identidade is not None else None,
        }
        _escrever(parcial / LATERAL, (json.dumps(lateral, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode())
        _fsync_pasta(parcial)
        return parcial, lateral
    except BaseException:
        shutil.rmtree(parcial, ignore_errors=True)
        raise


def _copiar_arquivo(origem, destino):
    """Copia em blocos, grava com fsync e RELE o destino: pega erro de disco de rede ou de sincronizacao."""
    h = hashlib.sha256()
    with open(origem, "rb") as entrada, open(destino, "xb") as saida:
        for bloco in iter(lambda: entrada.read(BLOCO), b""):
            h.update(bloco)
            saida.write(bloco)
        saida.flush()
        os.fsync(saida.fileno())
    if _sha_e_tamanho(destino)[0] != h.hexdigest():
        raise ArquivamentoError("copia_diverge")


def _publicar(parcial, final):
    """Leva a pasta montada ao destino. Mesmo disco: rename atomico. Outro disco: copia conferida, ARQUIVO.json por ultimo."""
    final = Path(final)
    final.parent.mkdir(parents=True, exist_ok=True)
    if _mesmo_disco(parcial, final.parent):
        os.rename(parcial, final)
        _fsync_pasta(final.parent)
        return
    final.mkdir(mode=0o700)
    try:
        for nome in (COMPRIMIDO, MANIFESTO, IDENTIDADE, LATERAL):  # o marcador de completo e o ultimo
            if (Path(parcial) / nome).is_file():
                _copiar_arquivo(Path(parcial) / nome, final / nome)
        _fsync_pasta(final)
        motivo = _verificar_uma(final)  # confere o que ficou NO destino, lendo de volta
        if motivo is not None:
            raise ArquivamentoError("destino_nao_confere_" + motivo)
    except BaseException:
        shutil.rmtree(final, ignore_errors=True)
        raise
    shutil.rmtree(parcial, ignore_errors=True)


def _verificar_uma(pasta, *, rapido=False):
    """Motivo da divergencia de uma pasta do arquivo, ou None se estiver integra."""
    pasta = Path(pasta)
    try:
        lateral = json.loads((pasta / LATERAL).read_bytes())
        orig, comp = lateral["original"], lateral["comprimido"]
        data, id_ = _separar(pasta.name)
        if lateral["esquema"] != ESQUEMA or lateral["captura_id"] != id_ or (data is not None and lateral.get("data_pregao") != data):
            return "lateral_invalido"
        nomes = {p.name for p in pasta.iterdir()}
        if nomes != {LATERAL, COMPRIMIDO, MANIFESTO} | ({IDENTIDADE} if lateral["identidade_sha256"] else set()):
            return "conteudo_inesperado"
        if _sha_e_tamanho(pasta / MANIFESTO)[0] != lateral["manifest_sha256"]:
            return "manifesto_alterado"
        if lateral["identidade_sha256"] and _sha_e_tamanho(pasta / IDENTIDADE)[0] != lateral["identidade_sha256"]:
            return "identidade_alterada"
        manifesto = json.loads((pasta / MANIFESTO).read_bytes())
        if _hashes_do_manifesto(manifesto) != (orig["sha256"], orig["bytes"], orig["frames"]):
            return "lateral_diverge_do_manifesto"
        if _sha_e_tamanho(pasta / COMPRIMIDO) != (comp["sha256"], comp["bytes"]):
            return "xz_alterado"
        if not rapido and _hash_do_xz(pasta / COMPRIMIDO) != (orig["sha256"], orig["bytes"], orig["frames"]):
            return "conteudo_nao_confere"
    except ArquivamentoError as erro:
        return str(erro)
    except (OSError, ValueError, KeyError, TypeError):
        return "lateral_invalido"
    return None


def _tirar_de_capturas(pasta, estado):
    """Rename atomico para fora de capturas/ e so depois apaga; um corte no meio nao deixa meia captura."""
    removendo = Path(estado) / REMOVENDO
    removendo.mkdir(parents=True, mode=0o700, exist_ok=True)
    destino = removendo / Path(pasta).name
    os.rename(pasta, destino)
    _fsync_pasta(Path(pasta).parent)
    shutil.rmtree(destino)
    try:
        removendo.rmdir()
    except OSError:
        pass


def _guarda_em_andamento(guarda):
    if guarda is None:
        return False
    try:
        valor = json.loads(Path(guarda).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False  # sem guarda legivel: a regra do manifesto ja protege a captura em curso
    return isinstance(valor, dict) and valor.get("estado") == "EM_ANDAMENTO"


def _restos(estado, alvo):
    """(montagens interrompidas, sobras de remocao, pastas incompletas no destino), tudo que e nosso."""
    estado = Path(estado)

    def filhos(nome):
        pasta = estado / nome
        return sorted(pasta.iterdir()) if pasta.is_dir() else []

    return filhos(PARCIAL) + filhos(RESTAURANDO), filhos(REMOVENDO), _incompletas(alvo)


# ---------------------------------------------------------------- arquivar

def arquivar(raiz, *, destino=None, manter_dias=MANTER_DIAS_PADRAO, espera_horas=ESPERA_HORAS_PADRAO,
             executar=False, manter_original=False, recuperar=False, agora=None, guarda=None, selo=None, apenas=None,
             desde=None):
    """Publica o que esta elegivel e libera o disco do que ja venceu. Sem `executar` so simula e nao escreve nada.

    `apenas`: lista de ids de captura; so essas entram na rodada (as outras nem aparecem no relatorio).
    `desde`: data; captura fechada antes desse dia (Brasilia) nem entra na rodada."""
    if desde is not None and (not isinstance(desde, date) or isinstance(desde, datetime)):
        raise ArquivamentoError("desde_invalido")
    for valor in (manter_dias, espera_horas):
        if not isinstance(valor, (int, float)) or isinstance(valor, bool) or not valor >= 0:
            raise ArquivamentoError("prazo_invalido")
    raiz, agora, selo = Path(raiz), agora or _agora(), _selo(selo)
    alvo, estado = _destino(raiz, destino), _estado(raiz)
    if destino is not None and not alvo.is_dir():
        raise ArquivamentoError("destino_inexistente")  # nunca cria a pasta do usuario por engano
    apenas = None if apenas is None else set(apenas)
    if apenas is not None:
        existentes = {p.name for p in (raiz / CAPTURAS).iterdir()} if (raiz / CAPTURAS).is_dir() else set()
        if not apenas or not apenas <= existentes:
            raise ArquivamentoError("captura_inexistente")  # id com erro de digitacao nao vira "nada a fazer" em silencio
    relatorio = {"simulacao": not executar, "parada": None, "destino": str(alvo), "manter_dias": manter_dias,
                 "elegiveis": [], "arquivadas": [], "removidas": [], "abertas": [], "recentes": [],
                 "com_original": [], "vencidas": [], "ignoradas": [], "divergentes": [], "liberadas": [],
                 "economia_bytes": 0}
    if executar and _guarda_em_andamento(guarda):
        relatorio["parada"] = "captura_em_andamento"
        return relatorio

    def classificar():
        for k in ("elegiveis", "abertas", "recentes", "com_original", "vencidas", "ignoradas", "divergentes"):
            relatorio[k] = []
        achados = [c for c in listar(raiz, destino=alvo, manter_dias=manter_dias, espera_horas=espera_horas, agora=agora)
                   if (apenas is None or c["id"] in apenas)
                   and (desde is None or c["fechada_em"] is None or _dia_brt(datetime.fromisoformat(c["fechada_em"])) >= desde)]
        for c in achados:
            e = c["estado"]
            if e == "elegivel":
                relatorio["elegiveis"].append(c["id"])
            elif e == "aberta":
                relatorio["abertas"].append(c["id"])
            elif e == "recente":
                relatorio["recentes"].append(c["id"])
            elif e == "com_original":
                relatorio["com_original"].append(c["id"])
                if c["vencida"]:
                    relatorio["vencidas"].append(c["id"])
            elif e == "divergente":
                relatorio["divergentes"].append({"id": c["id"], "motivo": c["motivo"]})
            else:
                relatorio["ignoradas"].append({"id": c["id"], "motivo": c["motivo"]})
        return {c["id"]: c for c in achados}

    achados = classificar()
    montagens, sobras, incompletas = _restos(estado, alvo)
    a_remover = relatorio["com_original"] if recuperar else ([] if manter_original else relatorio["vencidas"])
    trabalho = bool(relatorio["elegiveis"] or a_remover or montagens or sobras or incompletas or _a_liberar(alvo, agora))
    if not executar or not trabalho:
        return relatorio

    estado.mkdir(parents=True, mode=0o700, exist_ok=True)
    alvo.mkdir(parents=True, exist_ok=True)
    trava = open(estado / TRAVA, "a")
    try:
        if fcntl is not None:
            try:
                fcntl.flock(trava, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                relatorio["parada"] = "ocupado"
                return relatorio
        extras = []  # divergencias achadas na limpeza; entram no relatorio depois de classificar de novo
        _limpar_restos(estado, alvo, extras)
        _liberar_selos(alvo, agora, relatorio, extras)
        achados = classificar()  # de novo com a trava: outra rodada pode ter andado entre as duas leituras
        relatorio["divergentes"] += extras
        for id_ in list(relatorio["elegiveis"]):
            _processar(raiz / CAPTURAS / id_, alvo, estado, achados[id_], relatorio, manter_original, manter_dias, selo, agora)
        for id_ in list(relatorio["com_original"]):
            if recuperar or (achados[id_]["vencida"] and not manter_original):
                _remover_com_arquivo(raiz / CAPTURAS / id_, alvo, estado, relatorio)
    finally:
        trava.close()
    return relatorio


def _limpar_restos(estado, alvo, extras):
    montagens, sobras, incompletas = _restos(estado, alvo)
    for p in montagens + incompletas:
        shutil.rmtree(p, ignore_errors=True)
    for sobra in sobras:
        feito = _achar(alvo, sobra.name)
        if feito is not None and _verificar_uma(feito) is None:
            shutil.rmtree(sobra, ignore_errors=True)
        else:  # nao apaga a unica copia boa
            extras.append({"id": sobra.name, "motivo": "resto_de_remocao_sem_arquivo_valido"})
    try:
        (Path(estado) / REMOVENDO).rmdir()
    except OSError:
        pass


def _livre_suficiente(caminho, bytes_brutos):
    return shutil.disk_usage(caminho).free >= bytes_brutos // 4 + MARGEM_DISCO


def _processar(pasta, alvo, estado, info, relatorio, manter_original, manter_dias, selo, agora):
    id_ = pasta.name
    base = _base_de(alvo, selo, datetime.fromisoformat(info["fechada_em"]), agora)
    try:
        base.mkdir(parents=True, exist_ok=True)
        if not (_livre_suficiente(estado.parent, info["bytes"]) and _livre_suficiente(base, info["bytes"])):
            relatorio["ignoradas"].append({"id": id_, "motivo": "sem_espaco"})
            return
        parcial, lateral = _montar(pasta, estado)
        try:
            _publicar(parcial, base / parcial.name)
        except BaseException:
            shutil.rmtree(parcial, ignore_errors=True)
            raise
    except ArquivamentoError as erro:
        relatorio["divergentes"].append({"id": id_, "motivo": str(erro)})
        _diario(estado, {"acao": "divergente", "id": id_, "motivo": str(erro)})
        return
    except (OSError, lzma.LZMAError) as erro:
        relatorio["divergentes"].append({"id": id_, "motivo": "falha_ao_arquivar", "detalhe": str(erro)[:200]})
        _diario(estado, {"acao": "divergente", "id": id_, "motivo": "falha_ao_arquivar"})
        return
    relatorio["arquivadas"].append(id_)
    _diario(estado, {"acao": "arquivada", "id": id_, "destino": str(base), "original_bytes": lateral["original"]["bytes"],
                     "comprimido_bytes": lateral["comprimido"]["bytes"]})
    if manter_original or info["dias"] < manter_dias:
        return  # o original fica ate vencer; uma rodada futura tira de capturas/
    _remover_com_arquivo(pasta, alvo, estado, relatorio)


def _remover_com_arquivo(pasta, alvo, estado, relatorio):
    """Tira de capturas/ a pasta que ja tem arquivo, depois de reler o arquivo e os arquivos pequenos."""
    id_ = pasta.name
    feito = _achar(alvo, id_)
    motivo = "arquivo_inexistente" if feito is None else _verificar_uma(feito)
    if motivo is None:
        try:
            lateral = json.loads((feito / LATERAL).read_bytes())
            if _sha_e_tamanho(pasta / MANIFESTO)[0] != lateral["manifest_sha256"]:
                motivo = "manifesto_da_pasta_diverge"
            elif (pasta / IDENTIDADE).is_file() and _sha_e_tamanho(pasta / IDENTIDADE)[0] != lateral["identidade_sha256"]:
                motivo = "identidade_da_pasta_diverge"
            elif (pasta / BRUTO).is_file() and (pasta / BRUTO).stat().st_size != lateral["original"]["bytes"]:
                motivo = "bytes_da_pasta_diverge"
        except (OSError, ValueError, KeyError, TypeError):
            motivo = "lateral_invalido"
    if motivo is not None:
        relatorio["divergentes"].append({"id": id_, "motivo": motivo})
        return
    try:
        _tirar_de_capturas(pasta, estado)
    except OSError as erro:
        relatorio["divergentes"].append({"id": id_, "motivo": "original_nao_removido", "detalhe": str(erro)[:200]})
        return
    (Path(estado) / RESTAURADAS / (id_ + ".json")).unlink(missing_ok=True)
    if id_ in relatorio["com_original"]:
        relatorio["com_original"].remove(id_)
    if id_ in relatorio["vencidas"]:
        relatorio["vencidas"].remove(id_)
    relatorio["removidas"].append(id_)
    relatorio["economia_bytes"] += lateral["original"]["bytes"] - lateral["comprimido"]["bytes"]
    _diario(estado, {"acao": "removida", "id": id_})


# ---------------------------------------------------------------- verificar e restaurar

def verificar_arquivadas(raiz, *, destino=None, rapido=False):
    resultado = {"ok": [], "divergentes": []}
    for id_, pasta in _entradas(_destino(raiz, destino)):
        motivo = _verificar_uma(pasta, rapido=rapido)
        if motivo is None:
            resultado["ok"].append(id_)
        else:
            resultado["divergentes"].append({"id": id_, "motivo": motivo})
    return resultado


def resumo_arquivadas(raiz, *, destino=None):
    """Contagem e tamanhos pelo ARQUIVO.json de cada pasta; nao le o xz (barato mesmo com centenas de dias)."""
    alvo = _destino(raiz, destino)
    resumo = {"total": 0, "seladas": 0, "original_bytes": 0, "comprimido_bytes": 0, "ilegiveis": []}
    for id_, pasta in _entradas(alvo):
        try:
            lateral = json.loads((pasta / LATERAL).read_bytes())
            original, comprimido = lateral["original"]["bytes"], lateral["comprimido"]["bytes"]
            if not (_inteiro(original) and _inteiro(comprimido)):
                raise ValueError
        except (OSError, ValueError, KeyError, TypeError):
            resumo["ilegiveis"].append(id_)
            continue
        resumo["total"] += 1
        resumo["seladas"] += 1 if pasta.parent.name.startswith(SELADO) else 0
        resumo["original_bytes"] += original
        resumo["comprimido_bytes"] += comprimido
    return resumo


def restaurar(raiz, captura_id, para=None, *, destino=None, agora=None):
    """Recria a captura byte a byte. Nao gasta o arquivo, nao sobrescreve nada e respeita o selo."""
    if not isinstance(captura_id, str) or not _ID.fullmatch(captura_id):
        raise ArquivamentoError("captura_id_invalido")
    raiz, agora = Path(raiz), agora or _agora()
    origem = _achar(_destino(raiz, destino), captura_id)
    if origem is None or not (origem / LATERAL).is_file():
        raise ArquivamentoError("arquivo_inexistente")
    if origem.parent.name.startswith(SELADO) and _dia_brt(agora) < _liberacao(origem.parent):
        raise ArquivamentoError("captura_selada_ate_" + _liberacao(origem.parent).isoformat())
    base = Path(para) if para is not None else raiz / CAPTURAS
    alvo = base / captura_id
    if os.path.lexists(alvo):
        raise ArquivamentoError("destino_ja_existe")
    motivo = _verificar_uma(origem)
    if motivo is not None:
        raise ArquivamentoError(motivo)
    lateral = json.loads((origem / LATERAL).read_bytes())
    estado = _estado(raiz)
    # Na pasta viva a montagem fica fora de capturas/: o supervisor trata raw sem manifesto como captura em curso.
    montagem = (estado / RESTAURANDO / captura_id) if para is None else (base / ("." + captura_id + ".restaurando"))
    shutil.rmtree(montagem, ignore_errors=True)
    montagem.mkdir(parents=True, mode=0o700)
    try:
        h = hashlib.sha256()
        with lzma.open(origem / COMPRIMIDO, "rb") as entrada, open(montagem / BRUTO, "xb") as saida:
            for bloco in iter(lambda: entrada.read(BLOCO), b""):
                h.update(bloco)
                saida.write(bloco)
            saida.flush()
            os.fsync(saida.fileno())
        if h.hexdigest() != lateral["original"]["sha256"]:
            raise ArquivamentoError("conteudo_nao_confere")
        for nome in (IDENTIDADE, MANIFESTO):  # o manifesto e o ultimo a aparecer
            if (origem / nome).is_file():
                _escrever(montagem / nome, (origem / nome).read_bytes())
        _fsync_pasta(montagem)
        base.mkdir(parents=True, exist_ok=True)
        os.rename(montagem, alvo)
        _fsync_pasta(base)
    except BaseException:
        shutil.rmtree(montagem, ignore_errors=True)
        raise
    if para is None:  # a rodada diaria nao apaga a captura que voce restaurou de proposito
        (estado / RESTAURADAS).mkdir(parents=True, mode=0o700, exist_ok=True)
        (estado / RESTAURADAS / (captura_id + ".json")).write_text(
            json.dumps({"restaurada_em": _agora().isoformat(timespec="seconds")}), encoding="utf-8")
        try:
            (estado / RESTAURANDO).rmdir()
        except OSError:
            pass
    return alvo


# ---------------------------------------------------------------- linha de comando

def _gib(n):
    return f"{n / 1024 ** 3:6.2f} GiB"


def _momento(texto):
    try:
        valor = datetime.fromisoformat(texto)
    except ValueError:
        raise argparse.ArgumentTypeError("data_invalida") from None
    if valor.tzinfo is None:
        raise argparse.ArgumentTypeError("data_sem_fuso")
    return valor


def _dia(texto):
    try:
        return date.fromisoformat(texto)
    except ValueError:
        raise argparse.ArgumentTypeError("data_invalida_use_AAAA-MM-DD") from None


def _parser():
    ap = argparse.ArgumentParser(prog="arquivar_capturas", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="acao", required=True)
    for nome, ajuda in (("listar", "mostra o estado de cada pasta de capturas/ e o que ja foi arquivado"),
                        ("arquivar", "comprime, publica no destino e libera disco; sem --executar so simula"),
                        ("verificar", "confere todos os arquivos ja feitos contra o manifesto"),
                        ("restaurar", "recria uma captura a partir do arquivo")):
        p = sub.add_parser(nome, help=ajuda)
        p.add_argument("--raiz", type=Path, default=RAIZ_PADRAO, help="pasta de dados (padrao ~/qhos-v2-data)")
        p.add_argument("--destino", type=Path, default=None,
                       help="onde ficam os arquivos (padrao <raiz>/capturas_arquivadas); a pasta tem de existir")
        if nome in ("listar", "arquivar"):
            p.add_argument("--manter-dias", type=float, default=MANTER_DIAS_PADRAO,
                           help="mantem a pasta bruta em capturas/ por este tempo depois de fechada (padrao 7)")
            p.add_argument("--espera-horas", type=float, default=ESPERA_HORAS_PADRAO,
                           help="nao mexe em captura fechada ha menos que isto (padrao 1)")
            p.add_argument("--agora", type=_momento, default=None, help=argparse.SUPPRESS)
        if nome == "arquivar":
            p.add_argument("--executar", action="store_true", help="grava de verdade (sem isto e simulacao)")
            p.add_argument("--manter-original", action="store_true", help="cria o arquivo mas nunca tira a pasta de capturas/")
            p.add_argument("--recuperar", action="store_true",
                           help="tambem tira de capturas/ toda pasta que ja tem arquivo conferido (ex.: depois de restaurar)")
            p.add_argument("--guarda", type=Path, default=GUARDA_PADRAO,
                           help="estado.json da guarda; EM_ANDAMENTO suspende a rodada")
            p.add_argument("--selar", nargs=2, type=_dia, metavar=("DE", "ATE"), default=None,
                           help="pregoes desta faixa (dias de Brasilia, inclusive) entram em SELADO_ate_<--liberar-em>")
            p.add_argument("--liberar-em", type=_dia, default=None, help="data em que o selo cai (AAAA-MM-DD)")
            p.add_argument("--so", action="append", metavar="ID", default=None,
                           help="publica so esta captura (pode repetir); as outras nao entram na rodada")
            p.add_argument("--desde", type=_dia, default=None,
                           help="so entram capturas fechadas neste dia (Brasilia) ou depois (AAAA-MM-DD)")
        if nome == "verificar":
            p.add_argument("--rapido", action="store_true", help="so confere os hashes dos arquivos, sem descomprimir")
        if nome == "restaurar":
            p.add_argument("captura_id")
            p.add_argument("--para", type=Path, default=None, help="pasta onde criar <id> (padrao capturas/)")
    return ap


def main(argv=None):
    ap = _parser()
    try:
        args = ap.parse_args(argv)
    except SystemExit as saida:
        return int(saida.code or 0)
    try:
        if args.acao in ("listar", "arquivar") and not (args.manter_dias >= 0 and args.espera_horas >= 0):
            print("prazo_invalido", file=sys.stderr)
            return 2
        if args.acao == "arquivar" and bool(args.selar) != bool(args.liberar_em):
            print("--selar e --liberar-em andam juntos", file=sys.stderr)
            return 2
        if args.acao == "listar":
            itens = listar(args.raiz, destino=args.destino, manter_dias=args.manter_dias,
                           espera_horas=args.espera_horas, agora=args.agora)
            for c in itens:
                print(f"{c['id']:34s} {c['estado']:12s} {_gib(c['bytes'])}  "
                      f"{'' if c['dias'] is None else str(round(c['dias'], 2)) + ' d':>8s}  {c['motivo'] or ''}")
            elegiveis = [c for c in itens if c["estado"] == "elegivel"]
            bruto = sum(c["bytes"] for c in elegiveis)
            print(f"\nelegiveis: {len(elegiveis)} ({_gib(bruto).strip()}); economia estimada com razao 8x: "
                  f"{_gib(bruto - bruto // 8).strip()}")
            resumo = resumo_arquivadas(args.raiz, destino=args.destino)
            print(f"ja arquivadas: {resumo['total']} ({_gib(resumo['comprimido_bytes']).strip()} no disco, "
                  f"{_gib(resumo['original_bytes']).strip()} de origem), seladas: {resumo['seladas']}"
                  + (f"; ilegiveis: {', '.join(resumo['ilegiveis'])}" if resumo["ilegiveis"] else ""))
            return 0
        if args.acao == "arquivar":
            if args.executar:
                os.umask(0o077)
                try:
                    os.nice(19)
                except (OSError, AttributeError):
                    pass
            selo = {"de": args.selar[0], "ate": args.selar[1], "libera": args.liberar_em} if args.selar else None
            relatorio = arquivar(args.raiz, destino=args.destino, manter_dias=args.manter_dias,
                                 espera_horas=args.espera_horas, executar=args.executar,
                                 manter_original=args.manter_original, recuperar=args.recuperar,
                                 agora=args.agora, guarda=args.guarda, selo=selo, apenas=args.so, desde=args.desde)
            print(json.dumps(relatorio, indent=2, sort_keys=True, ensure_ascii=False))
            return 1 if relatorio["divergentes"] else 0
        if args.acao == "verificar":
            resultado = verificar_arquivadas(args.raiz, destino=args.destino, rapido=args.rapido)
            for d in resultado["divergentes"]:
                print(f"DIVERGENTE {d['id']}: {d['motivo']}")
            print(f"integras: {len(resultado['ok'])}  divergentes: {len(resultado['divergentes'])}")
            return 1 if resultado["divergentes"] else 0
        alvo = restaurar(args.raiz, args.captura_id, para=args.para, destino=args.destino)
        print(f"restaurada em {alvo}")
        return 0
    except ArquivamentoError as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 1
    except OSError as erro:
        print(f"erro_de_disco: {str(erro)[:200]}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
